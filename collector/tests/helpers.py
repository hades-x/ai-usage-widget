"""Shared helpers for the collector tests. Synthetic data only; no network, no real home."""


from __future__ import annotations

import _pathfix  # noqa: F401 - must precede ai_usage imports

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

import os
import time
import unittest

from ai_usage.collector import collect  # noqa: F401 - re-exported for tests
from ai_usage.config import Paths

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ParisTZ(unittest.TestCase):
    """Pins the local timezone to Europe/Paris for day-boundary tests."""

    def setUp(self) -> None:
        self._old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/Paris"
        time.tzset()

    def tearDown(self) -> None:
        if self._old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._old_tz
        time.tzset()

NOW_ISO = "2026-10-08T12:00:00Z"
FAKE_TOKEN = "FAKE-ACCESS-TOKEN-FOR-TESTS-0001"


def jline(obj: Any) -> bytes:
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def usage(inp: int, cached: int = 0, cw: int = 0, out: int = 0, reason: int = 0) -> Dict[str, int]:
    return {
        "input_tokens": inp,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": cw,
        "output_tokens": out,
        "reasoning_output_tokens": reason,
        "total_tokens": inp + out,
    }


def codex_turn_context(ts: str, model: str) -> Dict[str, Any]:
    return {"timestamp": ts, "type": "turn_context", "payload": {"turn_id": "t", "model": model}}


def codex_tur(ts: str, response_id: str, u: Dict[str, int], ordinal: int = 1, thread: str = "th1") -> Dict[str, Any]:
    return {
        "timestamp": ts,
        "ordinal": ordinal,
        "type": "token_usage_record",
        "payload": {
            "thread_id": thread,
            "turn_id": "turn",
            "session_id": "sess",
            "root_turn_id": "root",
            "response_id": response_id,
            "usage": u,
            # cumulative fields must never be used for counting
            "turn_token_usage": {"input_tokens": 10**9},
            "thread_token_usage": {"input_tokens": 10**9},
        },
    }


def rate_limits(
    used: float = 10.0,
    minutes: Optional[int] = 10080,
    resets_at: Optional[int] = 1800000000,
    plan: str = "prolite",
    secondary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "limit_id": "codex",
        "limit_name": None,
        "primary": {"used_percent": used, "window_minutes": minutes, "resets_at": resets_at},
        "secondary": secondary,
        "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
        "individual_limit": None,
        "spend_control_reached": None,
        "plan_type": plan,
        "rate_limit_reached_type": None,
    }


def codex_token_count(ts: str, last: Dict[str, int], rl: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "timestamp": ts,
        "ordinal": 1,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": last, "last_token_usage": last, "model_context_window": 258400},
            "rate_limits": rl,
        },
    }


def claude_assistant(ts: str, msg_id: str, req_id: str, model: str, u: Dict[str, int]) -> Dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": ts,
        "requestId": req_id,
        "sessionId": "sess",
        "message": {
            "id": msg_id,
            "model": model,
            "usage": {
                "input_tokens": u.get("input", 0),
                "cache_creation_input_tokens": u.get("cw", 0),
                "cache_read_input_tokens": u.get("cached", 0),
                "output_tokens": u.get("out", 0),
            },
        },
    }


def write_bytes(path: Path, chunks: Iterable[bytes], append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab" if append else "wb") as fh:
        for chunk in chunks:
            fh.write(chunk)


def write_lines(path: Path, objs: Sequence[Any], append: bool = False) -> None:
    write_bytes(path, [jline(o) for o in objs], append=append)


class TempPaths:
    """Creates an isolated Codex/Claude home layout in a temporary directory."""

    def __init__(self, testcase: Any) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="aiu-test-"))
        testcase.addCleanup(shutil.rmtree, self.root, True)

    def paths(self) -> Paths:
        return Paths(
            codex_home=self.root / "codex",
            claude_homes=(self.root / "claude",),
            state=self.root / "run" / "state.json",
            cache_dir=self.root / "cache",
        )

    def codex_file(self, name: str = "rollout-a.jsonl", day: str = "2026/10/08") -> Path:
        return self.root / "codex" / "sessions" / day / name

    def claude_file(self, name: str = "session.jsonl", project: str = "proj-a") -> Path:
        return self.root / "claude" / "projects" / project / name

    def credentials(self, expires_ms: int = 4102444800000, sub: str = "max", tier: str = "default_claude_max_5x") -> Path:
        path = self.root / "claude" / ".credentials.json"
        data = {
            "claudeAiOauth": {
                "accessToken": FAKE_TOKEN,
                "refreshToken": "FAKE-REFRESH-TOKEN-FOR-TESTS-0001",
                "expiresAt": expires_ms,
                "scopes": ["user:inference"],
                "subscriptionType": sub,
                "rateLimitTier": tier,
            }
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path
