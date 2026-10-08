"""Day boundaries, window aggregation, schema conformance, and CLI — synthetic data only."""

import _pathfix  # noqa: F401 - must precede ai_usage imports


import io
import json
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

from ai_usage import cli
from ai_usage.aggregate import build_tokens
from ai_usage.collector import collect
from ai_usage.timeutil import parse_iso

from helpers import FAKE_TOKEN, FIXTURES, ParisTZ, TempPaths, codex_tur, usage, write_lines, claude_assistant

ROOT = Path(__file__).resolve().parents[2]
NOW = float(parse_iso("2026-10-08T12:00:00Z") or 0.0)
TODAY = date(2026, 10, 8)


class DayBoundaryTests(ParisTZ):
    def tokens_for(self, ts_list):
        tmp = TempPaths(self)
        write_lines(tmp.codex_file(), [codex_tur(ts, f"r{i}", usage(10, out=1)) for i, ts in enumerate(ts_list)])
        return collect(tmp.paths(), now=NOW, use_api=False)["providers"]["codex"]["tokens"]

    def test_summer_local_day_boundary_paris(self):
        # Paris is UTC+2 in October 2026 (CEST). 21:59Z = 23:59 local (still 08th),
        # 22:01Z = 00:01 local on the 9th.
        tokens = self.tokens_for(["2026-10-08T21:59:00.000Z", "2026-10-08T22:01:00.000Z"])
        daily = {d["date"]: d["total"] for d in tokens["daily_14d"]}
        self.assertEqual(daily["2026-10-08"], 11)
        self.assertEqual(daily.get("2026-10-09", 0), 0)  # 9th is in the future relative to NOW (12:00Z)

    def test_boundary_shifts_with_timezone_utc(self):
        import os
        import time
        os.environ["TZ"] = "UTC"
        time.tzset()
        try:
            tokens = self.tokens_for(["2026-10-08T21:59:00.000Z", "2026-10-08T22:01:00.000Z"])
            daily = {d["date"]: d["total"] for d in tokens["daily_14d"]}
            self.assertEqual(daily["2026-10-08"], 22)
        finally:
            os.environ["TZ"] = "Europe/Paris"
            time.tzset()


class AggregationTests(unittest.TestCase):
    def test_daily_14d_exactly_14_oldest_first(self):
        tokens = build_tokens({}, TODAY)
        days = [d["date"] for d in tokens["daily_14d"]]
        self.assertEqual(len(days), 14)
        self.assertEqual(days, sorted(days))
        self.assertEqual(days[0], "2026-09-25")
        self.assertEqual(days[-1], "2026-10-08")

    def test_windows_today_7d_30d(self):
        table = {
            "2026-10-08": {"m": {"input": 1, "cached_input": 0, "cache_write": 0, "output": 1, "reasoning": 0, "requests": 1}},
            "2026-10-02": {"m": {"input": 10, "cached_input": 0, "cache_write": 0, "output": 0, "reasoning": 0, "requests": 1}},
            "2026-10-01": {"m": {"input": 100, "cached_input": 0, "cache_write": 0, "output": 0, "reasoning": 0, "requests": 1}},
            "2026-09-08": {"m": {"input": 1000, "cached_input": 0, "cache_write": 0, "output": 0, "reasoning": 0, "requests": 1}},
        }
        tokens = build_tokens(table, TODAY)
        self.assertEqual(tokens["today"]["total"], 2)
        self.assertEqual(tokens["last_7d"]["total"], 2 + 10)       # 10-02 is within today-6
        self.assertEqual(tokens["last_30d"]["total"], 2 + 10 + 100)  # 09-08 is outside 30 days

    def test_by_model_7d_sorted_desc_with_shares(self):
        table = {
            "2026-10-08": {
                "small": {"input": 10, "cached_input": 0, "cache_write": 0, "output": 0, "reasoning": 0, "requests": 1},
                "big": {"input": 30, "cached_input": 0, "cache_write": 0, "output": 0, "reasoning": 0, "requests": 1},
                "mid": {"input": 20, "cached_input": 0, "cache_write": 0, "output": 0, "reasoning": 0, "requests": 1},
            }
        }
        models = build_tokens(table, TODAY)["by_model_7d"]
        self.assertEqual([m["model"] for m in models], ["big", "mid", "small"])
        self.assertAlmostEqual(sum(m["share"] for m in models), 1.0, places=9)
        self.assertAlmostEqual(models[0]["share"], 0.5, places=9)

    def test_empty_model_list_when_nothing(self):
        self.assertEqual(build_tokens({}, TODAY)["by_model_7d"], [])


# ---- schema validation (docs/STATE_SCHEMA.md) -------------------------------------------

COUNTER_KEYS = {"input", "cached_input", "cache_write", "output", "reasoning", "total", "requests"}


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _iso_z(v):
    return isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z", v) is not None


def validate_state(state):
    """Return a list of violations of the v1 contract (empty list = valid)."""
    errs = []

    def need(cond, msg):
        if not cond:
            errs.append(msg)

    need(state.get("schema_version") == 1, "schema_version must be 1")
    need(isinstance(state.get("collector_version"), str), "collector_version str")
    need(_iso_z(state.get("generated_at")), "generated_at ISO Z")
    need(_is_int(state.get("refresh_interval_s")), "refresh_interval_s int")
    providers = state.get("providers")
    need(isinstance(providers, dict) and set(providers) == {"claude", "codex"}, "providers claude+codex")
    for pid in ("claude", "codex"):
        p = (providers or {}).get(pid, {})
        need(p.get("id") == pid, f"{pid}.id")
        need(p.get("label") in ("Claude Code", "Codex"), f"{pid}.label")
        need(isinstance(p.get("available"), bool), f"{pid}.available bool")
        plan = p.get("plan")
        need(isinstance(plan, dict), f"{pid}.plan object")
        if isinstance(plan, dict):
            need(plan.get("name") is None or isinstance(plan.get("name"), str), f"{pid}.plan.name")
            need(plan.get("label") is None or isinstance(plan.get("label"), str), f"{pid}.plan.label")
            need(plan.get("source") in ("oauth_api", "local_logs", "none"), f"{pid}.plan.source enum")
            need(plan.get("observed_at") is None or _iso_z(plan.get("observed_at")), f"{pid}.plan.observed_at")
            need(isinstance(plan.get("stale"), bool), f"{pid}.plan.stale bool")
            need(plan.get("error") is None or isinstance(plan.get("error"), str), f"{pid}.plan.error")
            need(plan.get("credits") is None or isinstance(plan.get("credits"), dict), f"{pid}.plan.credits")
        windows = p.get("windows")
        need(isinstance(windows, list), f"{pid}.windows list")
        for w in windows or []:
            need(isinstance(w.get("id"), str) and isinstance(w.get("label"), str), f"{pid} window id/label")
            need(_is_num(w.get("used_percent")), f"{pid} window used_percent")
            need(w.get("window_minutes") is None or _is_int(w.get("window_minutes")), f"{pid} window_minutes")
            need(w.get("resets_at") is None or _iso_z(w.get("resets_at")), f"{pid} resets_at")
            ep = w.get("elapsed_percent")
            need(ep is None or (_is_num(ep) and 0 <= ep <= 100), f"{pid} elapsed_percent range")
            need(isinstance(w.get("reset_since_observation"), bool), f"{pid} reset_since_observation")
        tokens = p.get("tokens")
        need(isinstance(tokens, dict), f"{pid}.tokens object")
        if isinstance(tokens, dict):
            for key in ("today", "last_7d", "last_30d"):
                c = tokens.get(key)
                need(isinstance(c, dict) and set(c) == COUNTER_KEYS, f"{pid}.tokens.{key} keys")
                if isinstance(c, dict):
                    need(all(_is_int(c.get(k)) and c.get(k) >= 0 for k in COUNTER_KEYS), f"{pid}.tokens.{key} ints")
            models = tokens.get("by_model_7d")
            need(isinstance(models, list), f"{pid} by_model_7d list")
            shares = [m.get("share") for m in models or []]
            need(all(isinstance(m.get("model"), str) and _is_int(m.get("total")) and _is_num(m.get("share"))
                     and 0 <= m.get("share") <= 1 for m in models or []), f"{pid} by_model_7d entries")
            need(all(shares[i] >= shares[i + 1] - 1e-12 for i in range(len(shares) - 1)) if shares else True,
                 f"{pid} by_model_7d sorted desc")
            daily = tokens.get("daily_14d")
            need(isinstance(daily, list) and len(daily) == 14, f"{pid} daily_14d has 14 entries")
            if isinstance(daily, list) and len(daily) == 14:
                dates = [d.get("date") for d in daily]
                need(dates == sorted(dates), f"{pid} daily_14d oldest first")
                need(all(_is_int(d.get("total")) for d in daily), f"{pid} daily totals int")
                need(all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", x or "") for x in dates), f"{pid} daily date format")
        need(p.get("last_activity_at") is None or _iso_z(p.get("last_activity_at")), f"{pid}.last_activity_at")
        need(isinstance(p.get("errors"), list) and all(isinstance(e, str) for e in p.get("errors", [])),
             f"{pid}.errors string[]")
    return errs


class SchemaTests(ParisTZ):
    def test_example_document_validates(self):
        example = json.loads((ROOT / "docs" / "examples" / "state.example.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_state(example), [])

    def test_validator_rejects_broken_documents(self):
        example = json.loads((ROOT / "docs" / "examples" / "state.example.json").read_text(encoding="utf-8"))
        example["schema_version"] = 2
        example["providers"]["codex"]["tokens"]["daily_14d"].pop()
        errors = validate_state(example)
        self.assertTrue(any("schema_version" in e for e in errors))
        self.assertTrue(any("14" in e for e in errors))

    def test_collect_output_validates_empty_and_full(self):
        empty = TempPaths(self)
        self.assertEqual(validate_state(collect(empty.paths(), now=NOW, use_api=False)), [])

        full = TempPaths(self)
        write_lines(full.codex_file(), [codex_tur("2026-10-08T10:00:00.000Z", "r1", usage(10, cached=3, out=2))])
        write_lines(full.claude_file(), [claude_assistant("2026-10-08T10:00:00.000Z", "m", "q", "claude-opus-5-5",
                                                          {"input": 1, "out": 1, "cached": 4, "cw": 2})])
        state = collect(full.paths(), now=NOW, use_api=False)
        self.assertEqual(validate_state(state), [])
        self.assertTrue(full.paths().state.exists())


class CliTests(ParisTZ):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def paths_args(self, tmp):
        p = tmp.paths()
        return ["--codex-home", str(p.codex_home), "--claude-home", str(p.claude_homes[0]),
                "--state", str(p.state), "--cache-dir", str(p.cache_dir)]

    def test_collect_then_print_and_doctor(self):
        tmp = TempPaths(self)
        write_lines(tmp.codex_file(), [codex_tur("2026-10-08T10:00:00.000Z", "resp_test_0001", usage(10, out=1))])
        code, _out, err = self.run_cli(["collect", "--no-api", "--now", "2026-10-08T12:00:00Z"] + self.paths_args(tmp))
        self.assertEqual(code, 0)
        self.assertTrue(err.startswith("ai-usage collect: duration="))
        self.assertEqual(err.count("\n"), 1)
        code, out, _ = self.run_cli(["print"] + self.paths_args(tmp))
        self.assertEqual(code, 0)
        self.assertIn("Codex", out)
        self.assertNotIn("resp_test_0001", out)
        code, out, _ = self.run_cli(["doctor"] + self.paths_args(tmp))
        self.assertEqual(code, 0)
        self.assertIn("Codex", out)
        self.assertNotIn("resp_test_0001", out)
        self.assertNotIn(FAKE_TOKEN, out)

    def test_print_without_state_is_nonzero(self):
        tmp = TempPaths(self)
        code, _out, err = self.run_cli(["print"] + self.paths_args(tmp))
        self.assertEqual(code, 1)
        self.assertIn("no readable state", err)

    def test_bad_now_is_usage_error(self):
        tmp = TempPaths(self)
        code, _out, _err = self.run_cli(["collect", "--no-api", "--now", "not-a-date"] + self.paths_args(tmp))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
