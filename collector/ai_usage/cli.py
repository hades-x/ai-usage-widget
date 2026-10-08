"""Command line: ``python3 -m ai_usage {collect|doctor|print}``."""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import claude as claude_logs
from . import claude_api
from . import codex as codex_logs
from .collector import collect, configure_logging, read_state
from .config import Paths, resolve_paths
from .index import load_index
from .timeutil import parse_iso, utc_now_epoch

EXIT_OK = 0
EXIT_CRASH = 1


def _add_path_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--codex-home", help="Codex home (default $CODEX_HOME or ~/.codex)")
    parser.add_argument("--claude-home", help="Claude config dir (default $CLAUDE_CONFIG_DIR or ~/.claude)")
    parser.add_argument("--state", help="state.json path (default $XDG_RUNTIME_DIR/ai-usage/state.json)")
    parser.add_argument("--cache-dir", help="cache dir (default $XDG_CACHE_HOME/ai-usage)")


def _paths_from(args: argparse.Namespace) -> Paths:
    return resolve_paths(
        codex_home=args.codex_home,
        claude_home=args.claude_home,
        state=args.state,
        cache_dir=args.cache_dir,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai_usage", description="Claude Code / Codex usage collector")
    sub = parser.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser("collect", help="parse logs, query plan API, write state.json")
    _add_path_args(p_collect)
    p_collect.add_argument("--no-api", action="store_true", help="do not call the Claude plan API")
    p_collect.add_argument("--now", help="ISO 8601 instant to use as 'now' (testing)")

    p_doctor = sub.add_parser("doctor", help="human-readable diagnostics (no secrets)")
    _add_path_args(p_doctor)

    p_print = sub.add_parser("print", help="pretty summary of state.json")
    _add_path_args(p_print)
    return parser


def _count_files(directories: List[Path]) -> int:
    total = 0
    for directory in directories:
        if directory.is_dir():
            for _root, _dirs, files in os.walk(directory):
                total += sum(1 for name in files if name.endswith(".jsonl"))
    return total


def _fmt_ts(epoch: Optional[float]) -> str:
    if epoch is None:
        return "-"
    return datetime.fromtimestamp(epoch).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def credentials_status(path: Optional[Path], now: float) -> str:
    """One-line credentials state for doctor. Never prints the token or its length."""
    if path is None:
        return "missing"
    creds = claude_api.read_credentials(path)
    if creds is None:
        return "malformed"
    if creds.token == "":
        return "logged out (empty token)"
    if creds.expires_at_ms == 0:
        return "logged out (expiresAt 0)"
    if creds.expires_at_ms is None:
        return "present, expiry unknown"
    expires = creds.expires_at_ms / 1000.0
    if claude_api.token_expired(creds.expires_at_ms, now):
        return f"expired since {_fmt_ts(expires)}"
    return f"present, expires {_fmt_ts(expires)}"


def doctor(paths: Paths, now: float) -> str:
    lines: List[str] = []
    lines.append("ai-usage doctor")
    lines.append("")
    lines.append("Codex")
    codex_dirs = list(paths.codex_session_dirs)
    lines.append(f"  home: {paths.codex_home}")
    lines.append(f"  session dirs found: {sum(1 for d in codex_dirs if d.is_dir())}/{len(codex_dirs)}")
    lines.append(f"  rollout files: {_count_files(codex_dirs)}")
    index = load_index(paths.index_path)
    codex_section = index.get("codex", {})
    lines.append(f"  indexed files: {len(codex_section.get('files', {}))}")
    lines.append(f"  parse errors (indexed): {sum(int(f.get('parse_errors', 0)) for f in codex_section.get('files', {}).values())}")
    lines.append(f"  last event: {_fmt_ts(_last_epoch(codex_section))}")

    lines.append("")
    lines.append("Claude Code")
    claude_dirs = list(paths.claude_project_dirs)
    lines.append(f"  project dirs found: {sum(1 for d in claude_dirs if d.is_dir())}/{len(claude_dirs)}")
    lines.append(f"  session files: {_count_files(claude_dirs)}")
    claude_section = index.get("claude", {})
    lines.append(f"  indexed files: {len(claude_section.get('files', {}))}")
    lines.append(f"  parse errors (indexed): {sum(int(f.get('parse_errors', 0)) for f in claude_section.get('files', {}).values())}")
    lines.append(f"  last event: {_fmt_ts(_last_epoch(claude_section))}")

    creds_path = paths.claude_credentials()
    lines.append(f"  credentials: {credentials_status(creds_path, now)}")
    creds = claude_api.read_credentials(creds_path)
    if creds is not None:
        lines.append(f"  subscription: {claude_api.plan_label(creds.subscription_type, creds.rate_limit_tier) or '-'}")

    lines.append("")
    lines.append("Plan API cache")
    cache = claude_api.load_cache(paths.claude_cache_path)
    lines.append(f"  last success: {_fmt_ts(cache.get('fetched_at'))}")
    lines.append(f"  last attempt: {_fmt_ts(cache.get('last_attempt_at'))}")
    lines.append(f"  consecutive failures: {cache.get('failures') or 0}")
    nxt = cache.get("next_attempt_at")
    lines.append(f"  next attempt not before: {_fmt_ts(nxt if isinstance(nxt, (int, float)) else None)}")
    lines.append(f"  last error: {cache.get('error') or 'none'}")

    lines.append("")
    lines.append(f"State file: {paths.state} ({'present' if paths.state.exists() else 'missing'})")
    return "\n".join(lines)


def _last_epoch(section: dict) -> Optional[float]:
    best: Optional[float] = None
    for fs in section.get("files", {}).values():
        for key in ("last_usage", "last_legacy"):
            value = fs.get(key)
            if isinstance(value, (int, float)) and (best is None or value > best):
                best = float(value)
    return best


def pretty(state: dict) -> str:
    lines: List[str] = []
    lines.append(f"AI usage  (generated {state.get('generated_at')}, schema v{state.get('schema_version')})")
    for pid, prov in (state.get("providers") or {}).items():
        plan = prov.get("plan") or {}
        lines.append("")
        lines.append(f"{prov.get('label', pid)}  plan={plan.get('label') or '-'}  source={plan.get('source')}"
                     f"  stale={plan.get('stale')}  error={plan.get('error')}")
        for win in prov.get("windows") or []:
            lines.append(f"  {win.get('label')} ({win.get('id')}): {win.get('used_percent')}% used, "
                         f"elapsed {win.get('elapsed_percent')}%, resets {win.get('resets_at')}")
        tokens = prov.get("tokens") or {}
        for key in ("today", "last_7d", "last_30d"):
            c = tokens.get(key) or {}
            lines.append(f"  {key:<9} total={c.get('total', 0):>12,}  in={c.get('input', 0):,}  "
                         f"cached={c.get('cached_input', 0):,}  out={c.get('output', 0):,}  req={c.get('requests', 0):,}")
        for item in tokens.get("by_model_7d") or []:
            lines.append(f"  model {item.get('model')}: total={item.get('total', 0):,} share={item.get('share', 0):.2f}")
        lines.append(f"  last activity: {prov.get('last_activity_at')}  errors={prov.get('errors')}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    try:
        paths = _paths_from(args)
        if args.command == "collect":
            now = parse_iso(args.now) if args.now else None
            if args.now and now is None:
                print(f"invalid --now value: {args.now!r}", file=sys.stderr)
                return 2
            collect(paths, now=now, use_api=not args.no_api)
            return EXIT_OK
        if args.command == "doctor":
            print(doctor(paths, utc_now_epoch()))
            return EXIT_OK
        if args.command == "print":
            state = read_state(paths.state)
            if state is None:
                print(f"no readable state at {paths.state}", file=sys.stderr)
                return 1
            print(pretty(state))
            return EXIT_OK
    except KeyboardInterrupt:
        return 130
    except Exception:  # noqa: BLE001 - unexpected crash: report type + location only
        exc_type, exc, tb = sys.exc_info()
        last = traceback.extract_tb(tb)[-1] if tb else None
        where = f"{last.filename}:{last.lineno}" if last else "?"
        print(f"ai-usage: unexpected {exc_type.__name__} at {where}", file=sys.stderr)
        return EXIT_CRASH
    return 2


__all__ = ["main", "build_parser", "doctor", "pretty", "codex_logs", "claude_logs"]
