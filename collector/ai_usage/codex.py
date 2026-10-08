"""Codex CLI rollout parser (local logs only: tokens, model, rate limits).

Only usage counters, ids, timestamps, model and rate-limit numbers are read. All other
line types are ignored without touching their content fields.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .aggregate import DayTable, build_tokens, merge_table, nonneg_int
from .config import Paths
from .index import Ctx
from .quota import codex_plan_label, is_number, make_window, window_label
from .timeutil import epoch_to_iso, local_day, parse_iso

PROVIDER_ID = "codex"
LABEL = "Codex"
MARKERS = (b'"token_usage_record"', b'"turn_context"', b'"token_count"')


def new_state() -> Dict[str, Any]:
    return {
        "model": "unknown",
        "has_tur": False,
        "tables": {"usage": {}, "legacy": {}},
        "rl": None,
        "last_usage": None,
        "last_legacy": None,
        "keys": {},
        "parse_errors": 0,
        "offset": 0,
    }


def counters_from_usage(usage: Mapping[str, Any]) -> Dict[str, int]:
    cached = nonneg_int(usage.get("cached_input_tokens"))
    raw_input = nonneg_int(usage.get("input_tokens"))
    return {
        "input": max(0, raw_input - cached),
        "cached_input": cached,
        "cache_write": nonneg_int(usage.get("cache_write_input_tokens")),
        "output": nonneg_int(usage.get("output_tokens")),
        "reasoning": nonneg_int(usage.get("reasoning_output_tokens")),
        "requests": 1,
    }


def _pick_window(window: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(window, dict) or not is_number(window.get("used_percent")):
        return None
    minutes = window.get("window_minutes")
    resets = window.get("resets_at")
    return {
        "used_percent": float(window["used_percent"]),
        "window_minutes": int(minutes) if is_number(minutes) else None,
        "resets_at": float(resets) if is_number(resets) else None,
    }


def _pick_rate_limits(rl: Mapping[str, Any]) -> Dict[str, Any]:
    credits = rl.get("credits")
    kept: Dict[str, Any] = {}
    if isinstance(credits, dict):
        for key in ("has_credits", "unlimited", "balance"):
            if key in credits:
                kept[key] = credits[key]
    plan_type = rl.get("plan_type")
    return {
        "primary": _pick_window(rl.get("primary")),
        "secondary": _pick_window(rl.get("secondary")),
        "credits": kept or None,
        "plan_type": plan_type if isinstance(plan_type, str) else None,
    }


def _usage_record(obj: Mapping[str, Any], payload: Mapping[str, Any], fs: Dict[str, Any], ctx: Ctx) -> None:
    ts = parse_iso(obj.get("timestamp"))
    usage = payload.get("usage")
    if ts is None or not isinstance(usage, dict):
        return
    response_id = payload.get("response_id")
    if isinstance(response_id, str) and response_id:
        key: Optional[str] = "r:" + response_id
    elif payload.get("thread_id") is not None and obj.get("ordinal") is not None:
        key = f"t:{payload.get('thread_id')}:{obj.get('ordinal')}"
    else:
        key = None
    if not ctx.accept(key, ts):
        return
    model = fs.get("model") or "unknown"
    ctx.add(fs["tables"]["usage"], local_day(ts), model, counters_from_usage(usage))
    fs["has_tur"] = True
    fs["last_usage"] = max(ts, fs.get("last_usage") or ts)


def _token_count(obj: Mapping[str, Any], payload: Mapping[str, Any], fs: Dict[str, Any], ctx: Ctx) -> None:
    ts = parse_iso(obj.get("timestamp"))
    if ts is None:
        return
    info = payload.get("info")
    if isinstance(info, dict):
        last = info.get("last_token_usage")
        if isinstance(last, dict):
            model = fs.get("model") or "unknown"
            ctx.add(fs["tables"]["legacy"], local_day(ts), model, counters_from_usage(last))
            fs["last_legacy"] = max(ts, fs.get("last_legacy") or ts)
    rate_limits = payload.get("rate_limits")
    if isinstance(rate_limits, dict):
        current = fs.get("rl")
        if current is None or ts >= current["ts"]:
            fs["rl"] = {"ts": ts, **_pick_rate_limits(rate_limits)}


def parse_line(line: bytes, fs: Dict[str, Any], ctx: Ctx) -> None:
    """Parse one complete JSONL line into the per-file state. Never raises on bad data."""
    if not line.strip():
        return
    # Cheap pre-filter: lines without any relevant marker are skipped unparsed.
    if not any(marker in line for marker in MARKERS):
        return
    try:
        obj = json.loads(line)
    except ValueError:
        ctx.bad_line()
        return
    if not isinstance(obj, dict):
        ctx.bad_line()
        return
    kind = obj.get("type")
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return
    if kind == "turn_context":
        model = payload.get("model")
        if isinstance(model, str) and model:
            fs["model"] = model
    elif kind == "token_usage_record":
        _usage_record(obj, payload, fs, ctx)
    elif kind == "event_msg" and payload.get("type") == "token_count":
        _token_count(obj, payload, fs, ctx)


def effective_table(fs: Dict[str, Any]) -> DayTable:
    """Per file: token_usage_record wins; legacy token_count only for files without any."""
    tables = fs.get("tables", {})
    return tables.get("usage" if fs.get("has_tur") else "legacy", {})


def list_session_files(paths: Paths) -> List[str]:
    found: List[str] = []
    for directory in paths.codex_session_dirs:
        if not directory.is_dir():
            continue
        for root, dirs, files in os.walk(directory):
            dirs.sort()
            for name in sorted(files):
                if name.endswith(".jsonl"):
                    found.append(os.path.join(root, name))
    return found


def sources_present(paths: Paths) -> bool:
    return any(directory.is_dir() for directory in paths.codex_session_dirs)


def build_provider(
    section: Mapping[str, Any],
    now: float,
    today: date,
    available: bool,
    errors: List[str],
) -> Dict[str, Any]:
    files: Mapping[str, Dict[str, Any]] = section["files"]
    tables: DayTable = {}
    last_activity: Optional[float] = None
    latest_rl: Optional[Dict[str, Any]] = None
    for fs in files.values():
        merge_table(tables, effective_table(fs))
        last = fs.get("last_usage") if fs.get("has_tur") else fs.get("last_legacy")
        if isinstance(last, (int, float)) and (last_activity is None or last > last_activity):
            last_activity = float(last)
        rl = fs.get("rl")
        if rl and (latest_rl is None or rl["ts"] > latest_rl["ts"]):
            latest_rl = rl

    windows: List[Dict[str, Any]] = []
    plan: Dict[str, Any] = {
        "name": None,
        "label": None,
        "source": "none",
        "observed_at": None,
        "stale": False,
        "error": None,
        "credits": None,
    }
    if latest_rl is not None:
        for wid in ("primary", "secondary"):
            window = latest_rl.get(wid)
            if not window:
                continue
            minutes = window["window_minutes"]
            windows.append(
                make_window(
                    wid,
                    window_label(minutes),
                    window["used_percent"],
                    minutes,
                    window["resets_at"],
                    now,
                )
            )
        plan.update(
            {
                "name": latest_rl["plan_type"],
                "label": codex_plan_label(latest_rl["plan_type"]),
                "source": "local_logs",
                "observed_at": epoch_to_iso(latest_rl["ts"]),
                "credits": latest_rl.get("credits"),
            }
        )

    return {
        "id": PROVIDER_ID,
        "label": LABEL,
        "available": available,
        "plan": plan,
        "windows": windows,
        "tokens": build_tokens(tables, today),
        "last_activity_at": epoch_to_iso(last_activity),
        "errors": errors,
    }


