"""Claude Code parsing + plan API rules (ARCHITECTURE.md §3) — synthetic data only."""

import _pathfix  # noqa: F401 - must precede ai_usage imports


import json
import unittest
from pathlib import Path

from ai_usage import claude_api
from ai_usage.claude import parse_line
from ai_usage.collector import collect
from ai_usage.index import Ctx
from ai_usage.state import atomic_write_json
from ai_usage.timeutil import parse_iso

from helpers import (
    FAKE_TOKEN, FIXTURES, ParisTZ, TempPaths, claude_assistant, collect,
    write_bytes, write_lines,
)

NOW = float(parse_iso("2026-10-08T12:00:00Z") or 0.0)
TS = "2026-10-08T10:00:00.000Z"


class FakeHttp:
    """Injectable HTTP: records calls, returns scripted responses. Never touches the network."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def resp(status, body=b"", headers=None):
    return claude_api.HttpResponse(status, headers or {}, body)


OK_BODY = (FIXTURES / "api_usage_200.json").read_bytes()


class ClaudeLogTests(ParisTZ):
    def run_collect(self, tmp, now=NOW):
        return collect(tmp.paths(), now=now, use_api=False)

    def test_dedupe_message_id_and_request_id(self):
        tmp = TempPaths(self)
        u = {"input": 10, "cached": 100, "cw": 5, "out": 20}
        write_lines(tmp.claude_file(), [
            claude_assistant(TS, "msg_1", "req_1", "claude-opus-5-5", u),
            claude_assistant(TS, "msg_1", "req_1", "claude-opus-5-5", u),  # streaming repeat
            claude_assistant(TS, "msg_1", "req_2", "claude-opus-5-5", u),  # different request => kept
        ])
        write_lines(tmp.claude_file("sidechain.jsonl", project="proj-b"), [
            claude_assistant(TS, "msg_1", "req_1", "claude-opus-5-5", u),  # sub-agent repeat
        ])
        today = self.run_collect(tmp)["providers"]["claude"]["tokens"]["today"]
        self.assertEqual(today["requests"], 2)
        self.assertEqual(today["input"], 20)
        self.assertEqual(today["cached_input"], 200)

    def test_synthetic_model_skipped(self):
        tmp = TempPaths(self)
        write_lines(tmp.claude_file(), [
            claude_assistant(TS, "m1", "r1", "<synthetic>", {"input": 99, "out": 99}),
            claude_assistant(TS, "m2", "r2", "claude-haiku-5-5", {"input": 1, "out": 2}),
        ])
        claude = self.run_collect(tmp)["providers"]["claude"]
        self.assertEqual(claude["tokens"]["today"]["requests"], 1)
        self.assertEqual([m["model"] for m in claude["tokens"]["by_model_7d"]], ["claude-haiku-5-5"])

    def test_committed_fixture_totals(self):
        tmp = TempPaths(self)
        write_bytes(tmp.claude_file(), [(FIXTURES / "claude-session-sample.jsonl").read_bytes()])
        claude = self.run_collect(tmp)["providers"]["claude"]
        today = claude["tokens"]["today"]
        self.assertEqual(today["requests"], 2)
        self.assertEqual(today["input"], 17)
        self.assertEqual(today["cached_input"], 40100)
        self.assertEqual(today["cache_write"], 1500)
        self.assertEqual(today["output"], 370)
        self.assertEqual(today["reasoning"], 0)
        self.assertEqual(today["total"], 41987)
        models = claude["tokens"]["by_model_7d"]
        self.assertEqual(models[0]["model"], "claude-opus-5-5")
        self.assertEqual(models[0]["total"], 41862)
        self.assertAlmostEqual(models[0]["share"] + models[1]["share"], 1.0, places=6)

    def test_parse_line_ignores_other_types_and_bad_json(self):
        tmp = TempPaths(self)
        write_bytes(tmp.claude_file(), [
            b'{"type":"user","timestamp":"%s","message":{"content":"secret prompt"}}\n' % TS.encode(),
            b'{"type":"assistant", truncated\n',
        ])
        claude = self.run_collect(tmp)["providers"]["claude"]
        self.assertIn("parse_errors:1", claude["errors"])
        self.assertEqual(claude["tokens"]["today"]["requests"], 0)
        self.assertIsNone(claude["last_activity_at"])

    def test_source_missing_and_empty_defaults(self):
        tmp = TempPaths(self)
        claude = self.run_collect(tmp)["providers"]["claude"]
        self.assertFalse(claude["available"])
        self.assertIn("source_missing", claude["errors"])
        self.assertEqual(len(claude["tokens"]["daily_14d"]), 14)
        self.assertEqual(claude["tokens"]["by_model_7d"], [])

    def test_ctx_accept_registers_ownership(self):
        seen = {}
        fs = {"keys": {}}
        ctx = Ctx(seen, "/f1", fs)
        self.assertTrue(ctx.accept("k", NOW))
        self.assertFalse(Ctx(seen, "/f2", {"keys": {}}).accept("k", NOW))
        self.assertEqual(seen["k"][0], "/f1")


class ClaudeApiTests(ParisTZ):
    def test_200_populates_windows_and_plan(self):
        tmp = TempPaths(self)
        creds = claude_api.read_credentials(tmp.credentials())
        http = FakeHttp(resp(200, OK_BODY))
        cache = claude_api.update_cache(claude_api.empty_cache(), creds, NOW, http)
        self.assertEqual(len(http.calls), 1)
        url, headers, timeout = http.calls[0]
        self.assertEqual(url, "https://api.anthropic.com/api/oauth/usage")
        self.assertEqual(headers["Authorization"], "Bearer " + FAKE_TOKEN)
        self.assertEqual(headers["anthropic-beta"], "oauth-2025-04-20")
        self.assertTrue(headers["User-Agent"].startswith("ai-usage-widget/"))
        self.assertEqual(timeout, 10)
        plan, windows = claude_api.plan_and_windows(cache, NOW)
        self.assertEqual(plan["label"], "Max 5x")
        self.assertEqual(plan["source"], "oauth_api")
        self.assertFalse(plan["stale"])
        self.assertIsNone(plan["error"])
        ids = [w["id"] for w in windows]
        self.assertEqual(ids, ["five_hour", "seven_day", "seven_day_sonnet"])  # opus null skipped
        self.assertEqual(windows[0]["label"], "Session 5 h")
        self.assertEqual(windows[2]["label"], "Semaine Sonnet")
        self.assertEqual(windows[0]["used_percent"], 62.0)
        self.assertEqual(windows[0]["resets_at"], "2026-10-08T16:00:00Z")

    def test_429_honours_retry_after(self):
        tmp = TempPaths(self)
        creds = claude_api.read_credentials(tmp.credentials())
        http = FakeHttp(resp(429, b"", {"retry-after": "900"}))
        cache = claude_api.update_cache(claude_api.empty_cache(), creds, NOW, http)
        self.assertEqual(cache["error"], "http_429")
        self.assertEqual(cache["next_attempt_at"], NOW + 900)
        self.assertEqual(cache["failures"], 1)

    def test_429_without_retry_after_uses_exponential_backoff(self):
        tmp = TempPaths(self)
        creds = claude_api.read_credentials(tmp.credentials())
        cache = claude_api.empty_cache()
        now = NOW
        expected = [300, 600, 1200, 1800, 1800]
        for want in expected:
            http = FakeHttp(resp(429))
            cache = claude_api.update_cache(cache, creds, now, http)
            self.assertEqual(cache["next_attempt_at"] - now, want)
            now = cache["next_attempt_at"]

    def test_expired_token_makes_no_call(self):
        tmp = TempPaths(self)
        expired_ms = int(NOW * 1000) - 1000
        creds = claude_api.read_credentials(tmp.credentials(expires_ms=expired_ms))
        http = FakeHttp()
        cache = claude_api.update_cache(claude_api.empty_cache(), creds, NOW, http)
        self.assertEqual(http.calls, [])
        self.assertEqual(cache["error"], "token_expired")
        plan, _ = claude_api.plan_and_windows(cache, NOW)
        self.assertEqual(plan["error"], "token_expired")

    def test_missing_credentials(self):
        cache = claude_api.update_cache(claude_api.empty_cache(), None, NOW, FakeHttp())
        self.assertEqual(cache["error"], "no_credentials")
        plan, windows = claude_api.plan_and_windows(cache, NOW)
        self.assertEqual(plan["source"], "none")
        self.assertEqual(windows, [])

    def test_malformed_credentials_are_treated_as_missing(self):
        tmp = TempPaths(self)
        path = tmp.root / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(claude_api.read_credentials(path))
        path.write_text(json.dumps({"claudeAiOauth": {"accessToken": ""}}), encoding="utf-8")
        # empty token is no longer "malformed": it is a present object => logged_out (test_logged_out.py)
        creds = claude_api.read_credentials(path)
        assert creds is not None
        self.assertEqual(creds.token, "")

    def test_malformed_json_response(self):
        tmp = TempPaths(self)
        creds = claude_api.read_credentials(tmp.credentials())
        cache = claude_api.update_cache(claude_api.empty_cache(), creds, NOW, FakeHttp(resp(200, b"<html>")))
        self.assertEqual(cache["error"], "bad_response")
        self.assertEqual(cache["windows"], {})

    def test_5xx_and_network_errors(self):
        tmp = TempPaths(self)
        creds = claude_api.read_credentials(tmp.credentials())
        cache = claude_api.update_cache(claude_api.empty_cache(), creds, NOW, FakeHttp(resp(503)))
        self.assertEqual(cache["error"], "http_5xx")
        cache = claude_api.update_cache(claude_api.empty_cache(), creds, NOW, FakeHttp(claude_api.NetworkError("x")))
        self.assertEqual(cache["error"], "network")

    def test_401_maps_to_http_401(self):
        tmp = TempPaths(self)
        creds = claude_api.read_credentials(tmp.credentials())
        cache = claude_api.update_cache(claude_api.empty_cache(), creds, NOW, FakeHttp(resp(401)))
        self.assertEqual(cache["error"], "http_401")

    def test_error_keeps_last_good_windows_flagged_stale(self):
        tmp = TempPaths(self)
        creds = claude_api.read_credentials(tmp.credentials())
        good = claude_api.update_cache(claude_api.empty_cache(), creds, NOW, FakeHttp(resp(200, OK_BODY)))
        later = NOW + 301
        bad = claude_api.update_cache(good, creds, later, FakeHttp(resp(500)))
        plan, windows = claude_api.plan_and_windows(bad, later)
        self.assertTrue(plan["stale"])
        self.assertEqual(plan["error"], "http_5xx")
        self.assertEqual(len(windows), 3)

    def test_observed_older_than_15_min_is_stale(self):
        tmp = TempPaths(self)
        creds = claude_api.read_credentials(tmp.credentials())
        good = claude_api.update_cache(claude_api.empty_cache(), creds, NOW, FakeHttp(resp(200, OK_BODY)))
        plan, _ = claude_api.plan_and_windows(good, NOW + 15 * 60 + 1)
        self.assertTrue(plan["stale"])
        plan, _ = claude_api.plan_and_windows(good, NOW + 14 * 60)
        self.assertFalse(plan["stale"])

    def test_min_interval_respected(self):
        tmp = TempPaths(self)
        creds = claude_api.read_credentials(tmp.credentials())
        http = FakeHttp(resp(200, OK_BODY), resp(200, OK_BODY))
        cache = claude_api.update_cache(claude_api.empty_cache(), creds, NOW, http)
        cache = claude_api.update_cache(cache, creds, NOW + 60, http)
        self.assertEqual(len(http.calls), 1)
        cache = claude_api.update_cache(cache, creds, NOW + 300, http)
        self.assertEqual(len(http.calls), 2)

    def test_cache_on_disk_has_no_token_and_roundtrips(self):
        tmp = TempPaths(self)
        creds = claude_api.read_credentials(tmp.credentials())
        cache = claude_api.update_cache(claude_api.empty_cache(), creds, NOW, FakeHttp(resp(200, OK_BODY)))
        path = tmp.paths().claude_cache_path
        atomic_write_json(path, claude_api.cache_for_disk(cache))
        text = path.read_text(encoding="utf-8")
        self.assertNotIn(FAKE_TOKEN, text)
        self.assertNotIn("refreshToken", text)
        loaded = claude_api.load_cache(path)
        self.assertEqual(loaded["windows"], cache["windows"])

    def test_collect_writes_claude_plan_from_api_and_never_leaks_token(self):
        tmp = TempPaths(self)
        tmp.credentials()
        state = collect(tmp.paths(), now=NOW, use_api=True, http_get=FakeHttp(resp(200, OK_BODY)))
        raw = tmp.paths().state.read_text(encoding="utf-8")
        self.assertNotIn(FAKE_TOKEN, raw)
        self.assertNotIn("refreshToken", raw)
        self.assertEqual(state["providers"]["claude"]["plan"]["source"], "oauth_api")

    def test_plan_label_mapping(self):
        self.assertEqual(claude_api.plan_label("max", "default_claude_max_5x"), "Max 5x")
        self.assertEqual(claude_api.plan_label("max", "default_claude_max_20x"), "Max 20x")
        self.assertEqual(claude_api.plan_label("pro", None), "Pro")
        self.assertEqual(claude_api.plan_label("team", None), "Team")
        self.assertIsNone(claude_api.plan_label(None, None))

    def test_retry_after_parsing(self):
        self.assertEqual(claude_api.parse_retry_after("120"), 120.0)
        self.assertIsNone(claude_api.parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT"))
        self.assertIsNone(claude_api.parse_retry_after(None))


if __name__ == "__main__":
    unittest.main()
