"""Incremental file sync engine and the on-disk index (cache/index.json).

Index layout (version 1)::

    {"version": 1,
     "codex":  {"files": {path: FileState}, "seen": {key: [owner_path, epoch]}},
     "claude": {"files": {path: FileState}, "seen": {key: [owner_path, epoch]}}}

FileState holds: inode, size, offset (bytes consumed, always ending on a newline), mtime,
head (sha256 of the first 256 bytes, detects in-place rewrites), keys {dedupe_key: epoch}
(keys this file owns), tables (per-file day tables, see aggregate.py), provider parse
state (e.g. current Codex model), last_ts, parse_errors.

A file's whole contribution is its tables, so replacing a file = dropping its tables and
its owned dedupe keys, then re-parsing from byte 0. Nothing is ever subtracted.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .aggregate import DayTable, add_day
from .state import read_json

INDEX_VERSION = 1
HEAD_BYTES = 256

ParseLine = Callable[[bytes, Dict[str, Any], "Ctx"], None]
NewState = Callable[[], Dict[str, Any]]


@dataclass
class SyncStats:
    scanned: int = 0
    changed: int = 0
    reparsed: int = 0
    removed: int = 0
    events_added: int = 0
    read_errors: int = 0

    @property
    def dirty(self) -> bool:
        return bool(self.changed or self.removed or self.reparsed)


class Ctx:
    """Per-file parse context: dedupe bookkeeping and counters for one sync pass."""

    def __init__(self, seen: Dict[str, List[Any]], path: str, fs: Dict[str, Any]) -> None:
        self.seen = seen
        self.path = path
        self.fs = fs
        self.added = 0

    def accept(self, key: Optional[str], ts: float) -> bool:
        """True if the event should be counted; registers ownership of ``key``."""
        if key is None:
            return True
        if key in self.seen:
            return False
        self.seen[key] = [self.path, ts]
        self.fs.setdefault("keys", {})[key] = ts
        return True

    def add(self, table: DayTable, day: str, model: str, counters: Dict[str, int]) -> None:
        add_day(table, day, model, counters)
        self.added += 1

    def bad_line(self) -> None:
        self.fs["parse_errors"] = self.fs.get("parse_errors", 0) + 1


def empty_index() -> Dict[str, Any]:
    return {
        "version": INDEX_VERSION,
        "codex": {"files": {}, "seen": {}},
        "claude": {"files": {}, "seen": {}},
    }


def load_index(path: Path) -> Dict[str, Any]:
    """Load the index; anything missing, corrupt or of another version → empty (full rebuild)."""
    raw = read_json(path)
    if not _valid_index(raw):
        return empty_index()
    return raw


def _valid_index(raw: Any) -> bool:
    if not isinstance(raw, dict) or raw.get("version") != INDEX_VERSION:
        return False
    for provider in ("codex", "claude"):
        section = raw.get(provider)
        if not isinstance(section, dict):
            return False
        if not isinstance(section.get("files"), dict) or not isinstance(section.get("seen"), dict):
            return False
    return True


def save_index(path: Path, index: Dict[str, Any]) -> None:
    from .state import atomic_write_json

    atomic_write_json(path, index)


def _head_digest(path: str, size: int) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read(min(size, HEAD_BYTES))).hexdigest()


def _drop_file(section: Dict[str, Any], path: str) -> None:
    fs = section["files"].pop(path, None)
    if not fs:
        return
    seen = section["seen"]
    for key in fs.get("keys", {}):
        owner = seen.get(key)
        if owner and owner[0] == path:
            del seen[key]


def sync_provider(
    section: Dict[str, Any],
    files: Sequence[str],
    parse_line: ParseLine,
    new_state: NewState,
    now: float,
    seen_retention_s: float,
    oldest_day: str,
) -> SyncStats:
    """Bring ``section`` up to date with the given files, reading only new complete lines."""
    stats = SyncStats()
    file_states: Dict[str, Dict[str, Any]] = section["files"]
    seen: Dict[str, List[Any]] = section["seen"]

    wanted = set(files)
    for path in [p for p in file_states if p not in wanted]:
        _drop_file(section, path)
        stats.removed += 1

    for path in files:
        stats.scanned += 1
        try:
            st = os.stat(path)
        except OSError:
            stats.read_errors += 1
            continue

        fs = file_states.get(path)
        if fs is not None and fs.get("inode") == st.st_ino and fs.get("offset") == st.st_size:
            continue  # nothing new

        reset = fs is None or fs.get("inode") != st.st_ino or st.st_size < fs.get("offset", 0)
        if not reset and fs is not None and fs.get("offset", 0) > 0:
            try:
                reset = _head_digest(path, fs["offset"]) != fs.get("head")
            except OSError:
                stats.read_errors += 1
                continue
        if fs is not None and reset:
            _drop_file(section, path)
            stats.reparsed += 1
        if fs is None or reset:
            fs = new_state()
            fs["keys"] = {}
            fs["parse_errors"] = 0
            fs["offset"] = 0
            file_states[path] = fs

        state: Dict[str, Any] = fs
        ctx = Ctx(seen, path, state)
        pos = int(state["offset"])
        try:
            with open(path, "rb") as fh:
                fh.seek(pos)
                while True:
                    line = fh.readline()
                    if not line or not line.endswith(b"\n"):
                        break  # EOF or partial trailing line: keep for the next run
                    pos += len(line)
                    parse_line(line[:-1], state, ctx)
        except OSError:
            stats.read_errors += 1

        if pos != state["offset"] or reset:
            stats.changed += 1
        state["offset"] = pos
        state["size"] = st.st_size
        state["inode"] = st.st_ino
        state["mtime"] = st.st_mtime
        try:
            state["head"] = _head_digest(path, pos) if pos else ""
        except OSError:
            stats.read_errors += 1
        for table in state.get("tables", {}).values():
            _prune(table, oldest_day)
        stats.events_added += ctx.added

    cutoff = now - seen_retention_s
    for key in [k for k, owner in seen.items() if owner[1] < cutoff]:
        del seen[key]
    for fs in file_states.values():
        keys = fs.get("keys", {})
        if keys:
            fs["keys"] = {k: t for k, t in keys.items() if t >= cutoff}
    return stats


def _prune(table: DayTable, oldest_day: str) -> None:
    for day in [d for d in table if d < oldest_day]:
        del table[day]


def merged_table(section: Dict[str, Any], effective: Callable[[Dict[str, Any]], DayTable]) -> DayTable:
    from .aggregate import merge_table

    out: DayTable = {}
    for fs in section["files"].values():
        merge_table(out, effective(fs))
    return out


def file_parse_errors(section: Dict[str, Any]) -> int:
    return sum(int(fs.get("parse_errors", 0)) for fs in section["files"].values())


def latest_timestamp(section: Dict[str, Any]) -> Optional[float]:
    latest: Optional[float] = None
    for fs in section["files"].values():
        ts = fs.get("last_ts")
        if isinstance(ts, (int, float)) and (latest is None or ts > latest):
            latest = float(ts)
    return latest
