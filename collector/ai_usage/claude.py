"""Claude Code JSONL parser (local usage counters only)."""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any, Dict, List, Mapping, Optional

from .aggregate import DayTable, build_tokens, merge_table, nonneg_int
from .config import Paths
from .index import Ctx
from .timeutil import epoch_to_iso, local_day, parse_iso

PROVIDER_ID = "claude"
LABEL = "Claude Code"
SYNTHETIC_MODEL = "<synthetic>"


def new_state() -> Dict[str, Any]:
    return {
        "tables": {"usage": {}},
        "last_usage": None,
        "keys": {},
        "parse_errors": 0,
        "offset": 0,
    }


def parse_line(line: bytes, fs: Dict[str, Any], ctx: Ctx) -> None:
    if b'"assistant"' not in line:
        return
    try:
        obj = json.loads(line)
    except ValueError:
        ctx.bad_line()
        return
    if not isinstance(obj, dict) or obj.get("type") != "assistant":
        return
    message = obj.get("message")
    if not isinstance(message, dict):
        return
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return
    model = message.get("model")
    if model == SYNTHETIC_MODEL:
        return
    if not isinstance(model, str) or not model:
        model = "unknown"
    ts = parse_iso(obj.get("timestamp"))
    if ts is None:
        return
    message_id = message.get("id")
    request_id = obj.get("requestId")
    key: Optional[str] = None
    if isinstance(message_id, str) and isinstance(request_id, str) and message_id and request_id:
        key = f"{message_id}:{request_id}"
    if not ctx.accept(key, ts):
        return
    counters = {
        "input": nonneg_int(usage.get("input_tokens")),
        "cached_input": nonneg_int(usage.get("cache_read_input_tokens")),
        "cache_write": nonneg_int(usage.get("cache_creation_input_tokens")),
        "output": nonneg_int(usage.get("output_tokens")),
        "reasoning": 0,
        "requests": 1,
    }
    ctx.add(fs["tables"]["usage"], local_day(ts), model, counters)
    fs["last_usage"] = max(ts, fs.get("last_usage") or ts)


def list_session_files(paths: Paths) -> List[str]:
    found: List[str] = []
    for directory in paths.claude_project_dirs:
        if not directory.is_dir():
            continue
        for root, dirs, files in os.walk(directory):
            dirs.sort()
            for name in sorted(files):
                if name.endswith(".jsonl"):
                    found.append(os.path.join(root, name))
    return found


def sources_present(paths: Paths) -> bool:
    return any(directory.is_dir() for directory in paths.claude_project_dirs)


def build_tables(section: Mapping[str, Any]) -> tuple:
    """Return (merged day table, latest usage epoch) across all files of the section."""
    tables: DayTable = {}
    last: Optional[float] = None
    for fs in section["files"].values():
        merge_table(tables, fs.get("tables", {}).get("usage", {}))
        value = fs.get("last_usage")
        if isinstance(value, (int, float)) and (last is None or value > last):
            last = float(value)
    return tables, last


def tokens_for(section: Mapping[str, Any], today: date) -> Dict[str, Any]:
    tables, _ = build_tables(section)
    return build_tokens(tables, today)


def last_activity(section: Mapping[str, Any]) -> Optional[str]:
    _, last = build_tables(section)
    return epoch_to_iso(last)
