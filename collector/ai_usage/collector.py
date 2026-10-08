"""Orchestration: one collect run = sync logs, refresh plan API (optional), write state.json."""

from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from . import claude as claude_logs
from . import claude_api
from . import codex as codex_logs
from .aggregate import prune_table
from .config import Paths
from .index import SyncStats, load_index, save_index, sync_provider
from .state import atomic_write_json, read_json
from .timeutil import epoch_to_iso, local_date, utc_now_epoch

SCHEMA_VERSION = 1
REFRESH_INTERVAL_S = 60
DAYS_KEPT = 35
SEEN_RETENTION_S = 48 * 3600
log = logging.getLogger("ai_usage")


@dataclass
class RunReport:
    duration_s: float = 0.0
    stats: Dict[str, SyncStats] = field(default_factory=dict)
    api_status: str = "skipped"
    provider_status: Dict[str, str] = field(default_factory=dict)

    def summary_line(self) -> str:
        parts = [f"duration={self.duration_s:.2f}s"]
        for name in ("codex", "claude"):
            st = self.stats.get(name)
            if st is not None:
                parts.append(
                    f"{name}[files={st.scanned} changed={st.changed} reparsed={st.reparsed} "
                    f"removed={st.removed} events={st.events_added} status={self.provider_status.get(name, '?')}]"
                )
        parts.append(f"claude_api={self.api_status}")
        return "ai-usage collect: " + " ".join(parts)


def _oldest_day(today: date) -> str:
    return (today - timedelta(days=DAYS_KEPT - 1)).isoformat()


def _prune_all(section: Dict[str, Any], oldest: str) -> None:
    for fs in section["files"].values():
        for table in fs.get("tables", {}).values():
            prune_table(table, oldest)


def _sync_codex(paths: Paths, index: Dict[str, Any], now: float, oldest: str, report: RunReport) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    available = codex_logs.sources_present(paths)
    if not available:
        errors.append("source_missing")
    files = codex_logs.list_session_files(paths)
    stats = sync_provider(
        index["codex"], files, codex_logs.parse_line, codex_logs.new_state, now, SEEN_RETENTION_S, oldest
    )
    report.stats["codex"] = stats
    _prune_all(index["codex"], oldest)
    return available, errors


def _sync_claude(paths: Paths, index: Dict[str, Any], now: float, oldest: str, report: RunReport) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    available = claude_logs.sources_present(paths)
    if not available:
        errors.append("source_missing")
    files = claude_logs.list_session_files(paths)
    stats = sync_provider(
        index["claude"], files, claude_logs.parse_line, claude_logs.new_state, now, SEEN_RETENTION_S, oldest
    )
    report.stats["claude"] = stats
    _prune_all(index["claude"], oldest)
    return available, errors


def _claude_plan(paths: Paths, now: float, use_api: bool, http_get: Any, report: RunReport) -> Dict[str, Any]:
    cache = claude_api.load_cache(paths.claude_cache_path)
    if use_api:
        creds = claude_api.read_credentials(paths.claude_credentials())
        updated = claude_api.update_cache(cache, creds, now, http_get)
        if updated != cache:
            atomic_write_json(paths.claude_cache_path, claude_api.cache_for_disk(updated))
        cache = updated
        report.api_status = str(cache.get("error") or "ok")
    return cache


def collect(
    paths: Paths,
    *,
    now: Optional[float] = None,
    use_api: bool = True,
    http_get: Any = None,
) -> Dict[str, Any]:
    """Run one collection and write state.json. Returns the state dict (also written)."""
    started = time.monotonic()
    now = utc_now_epoch() if now is None else float(now)
    http_get = claude_api.default_http_get if http_get is None else http_get
    today = local_date(now)
    oldest = _oldest_day(today)
    report = RunReport()

    index = load_index(paths.index_path)
    index_before = json.dumps(index, sort_keys=True)

    codex_available, codex_errors = _sync_codex(paths, index, now, oldest, report)
    claude_available, claude_errors = _sync_claude(paths, index, now, oldest, report)

    cache = _claude_plan(paths, now, use_api, http_get, report)
    plan, windows = claude_api.plan_and_windows(cache, now)
    claude_errors_all = list(claude_errors)
    parse_errors = claude_logs_parse_errors(index["claude"])
    if parse_errors:
        claude_errors_all.append(f"parse_errors:{parse_errors}")

    providers = {
        "claude": {
            "id": "claude",
            "label": claude_logs.LABEL,
            "available": claude_available,
            "plan": plan,
            "windows": windows,
            "tokens": claude_logs.tokens_for(index["claude"], today),
            "last_activity_at": claude_logs.last_activity(index["claude"]),
            "errors": claude_errors_all,
        },
        "codex": codex_logs.build_provider(
            index["codex"],
            now,
            today,
            codex_available,
            list(codex_errors) + _codex_parse_errors(index["codex"]),
        ),
    }
    for name, prov in providers.items():
        report.provider_status[name] = "ok" if not prov["errors"] else ",".join(prov["errors"])

    state = {
        "schema_version": SCHEMA_VERSION,
        "collector_version": __version__,
        "generated_at": epoch_to_iso(now),
        "refresh_interval_s": REFRESH_INTERVAL_S,
        "providers": providers,
    }
    atomic_write_json(paths.state, state)

    if json.dumps(index, sort_keys=True) != index_before:
        save_index(paths.index_path, index)

    report.duration_s = time.monotonic() - started
    log.info(report.summary_line())
    return state


def claude_logs_parse_errors(section: Dict[str, Any]) -> int:
    return sum(int(fs.get("parse_errors", 0)) for fs in section["files"].values())


def _codex_parse_errors(section: Dict[str, Any]) -> List[str]:
    count = sum(int(fs.get("parse_errors", 0)) for fs in section["files"].values())
    return [f"parse_errors:{count}"] if count else []


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.handlers[:] = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False


def read_state(path: Path) -> Optional[Dict[str, Any]]:
    data = read_json(path)
    return data if isinstance(data, dict) else None
