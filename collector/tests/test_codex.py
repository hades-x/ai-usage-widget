"""Codex parsing rules (ARCHITECTURE.md §3) — synthetic data only."""

import _pathfix  # noqa: F401 - must precede ai_usage imports


import unittest

from ai_usage.codex import counters_from_usage
from ai_usage.quota import codex_plan_label, window_label
from ai_usage.timeutil import parse_iso

from helpers import (
    FIXTURES, ParisTZ, TempPaths, codex_tur, codex_token_count, codex_turn_context, collect,
    rate_limits, usage, write_bytes, write_lines,
)

TODAY_TS = "2026-10-08T10:00:00.000Z"
NOW = float(parse_iso("2026-10-08T12:00:00Z") or 0.0)


class CodexTests(ParisTZ):
    def run_collect(self, tmp: TempPaths, now: float = NOW):
        return collect(tmp.paths(), now=now, use_api=False)

    def codex(self, state):
        return state["providers"]["codex"]

    def test_dedupe_by_response_id_across_files(self):
        tmp = TempPaths(self)
        u = usage(1000, cached=400, out=50)
        write_lines(tmp.codex_file("a.jsonl"), [
            codex_turn_context(TODAY_TS, "gpt-6.1-sol"),
            codex_tur(TODAY_TS, "resp_1", u),
            codex_tur(TODAY_TS, "resp_1", u),  # duplicate in same file
        ])
        write_lines(tmp.codex_file("b.jsonl"), [
            codex_tur(TODAY_TS, "resp_1", u),  # duplicate across files
            codex_tur(TODAY_TS, "resp_2", u),
        ])
        today = self.codex(self.run_collect(tmp))["tokens"]["today"]
        self.assertEqual(today["requests"], 2)
        self.assertEqual(today["input"], 2 * 600)
        self.assertEqual(today["cached_input"], 2 * 400)

    def test_token_usage_record_uses_per_response_usage_not_cumulative(self):
        tmp = TempPaths(self)
        write_lines(tmp.codex_file(), [codex_tur(TODAY_TS, "resp_x", usage(100, out=10))])
        today = self.codex(self.run_collect(tmp))["tokens"]["today"]
        self.assertEqual(today["input"], 100)
        self.assertEqual(today["total"], 110)

    def test_legacy_file_uses_token_count_last_usage(self):
        tmp = TempPaths(self)
        last = usage(1000, cached=250, out=100, reason=30)
        write_lines(tmp.codex_file("legacy.jsonl"), [
            codex_turn_context(TODAY_TS, "gpt-5.5"),
            codex_token_count(TODAY_TS, last),
            codex_token_count("2026-10-08T10:05:00.000Z", usage(200, out=20)),
        ])
        today = self.codex(self.run_collect(tmp))["tokens"]["today"]
        self.assertEqual(today["requests"], 2)
        self.assertEqual(today["input"], 750 + 200)
        self.assertEqual(today["cached_input"], 250)
        self.assertEqual(today["reasoning"], 30)

    def test_file_with_token_usage_record_ignores_token_count_for_tokens(self):
        tmp = TempPaths(self)
        write_lines(tmp.codex_file(), [
            codex_token_count(TODAY_TS, usage(9999, out=9999)),
            codex_tur(TODAY_TS, "resp_real", usage(100, out=10)),
        ])
        today = self.codex(self.run_collect(tmp))["tokens"]["today"]
        self.assertEqual(today["requests"], 1)
        self.assertEqual(today["total"], 110)

    def test_model_attribution_from_turn_context(self):
        tmp = TempPaths(self)
        write_lines(tmp.codex_file(), [
            codex_turn_context(TODAY_TS, "gpt-A"),
            codex_tur(TODAY_TS, "r1", usage(100, out=1)),
            codex_turn_context("2026-10-08T10:01:00.000Z", "gpt-B"),
            codex_tur("2026-10-08T10:02:00.000Z", "r2", usage(200, out=2)),
            codex_tur("2026-10-08T10:03:00.000Z", "r3", usage(300, out=3)),
        ])
        models = {m["model"]: m for m in self.codex(self.run_collect(tmp))["tokens"]["by_model_7d"]}
        self.assertEqual(models["gpt-A"]["total"], 101)
        self.assertEqual(models["gpt-B"]["total"], 505)

    def test_unknown_model_when_no_turn_context(self):
        tmp = TempPaths(self)
        write_lines(tmp.codex_file(), [codex_tur(TODAY_TS, "r1", usage(5, out=5))])
        models = self.codex(self.run_collect(tmp))["tokens"]["by_model_7d"]
        self.assertEqual(models[0]["model"], "unknown")

    def test_irrelevant_line_types_are_ignored(self):
        tmp = TempPaths(self)
        write_lines(tmp.codex_file(), [
            {"timestamp": TODAY_TS, "type": "response_item",
             "payload": {"type": "message", "model": "fake-model-from-content",
                         "content": "mentions token_usage_record and turn_context"}},
            codex_tur(TODAY_TS, "r1", usage(10, out=1)),
        ])
        state = self.run_collect(tmp)
        models = state["providers"]["codex"]["tokens"]["by_model_7d"]
        self.assertEqual([m["model"] for m in models], ["unknown"])
        self.assertEqual(state["providers"]["codex"]["errors"], [])

    def test_bad_json_line_counted_as_parse_error(self):
        tmp = TempPaths(self)
        write_bytes(tmp.codex_file(), [
            b'{"type":"token_usage_record", broken\n',
            (b'{"timestamp":"%s","type":"token_usage_record","payload":{"response_id":"ok",'
             b'"usage":{"input_tokens":5,"cached_input_tokens":0,"output_tokens":1}}}\n' % TODAY_TS.encode()),
        ])
        state = self.run_collect(tmp)
        self.assertIn("parse_errors:1", state["providers"]["codex"]["errors"])
        self.assertEqual(state["providers"]["codex"]["tokens"]["today"]["requests"], 1)

    def test_rate_limits_latest_across_files(self):
        tmp = TempPaths(self)
        write_lines(tmp.codex_file("old.jsonl"), [
            codex_token_count("2026-10-08T09:00:00.000Z", usage(1), rate_limits(used=50.0)),
        ])
        write_lines(tmp.codex_file("new.jsonl"), [
            codex_token_count("2026-10-08T11:00:00.000Z", usage(1), rate_limits(used=30.0)),
        ])
        write_lines(tmp.codex_file("mid.jsonl"), [
            codex_token_count("2026-10-08T10:00:00.000Z", usage(1), rate_limits(used=10.0)),
        ])
        codex = self.codex(self.run_collect(tmp))
        self.assertEqual(codex["windows"][0]["used_percent"], 30.0)
        self.assertEqual(codex["plan"]["observed_at"], "2026-10-08T11:00:00Z")

    def test_reset_in_past_zeroes_usage(self):
        tmp = TempPaths(self)
        past = int(NOW) - 60
        write_lines(tmp.codex_file(), [
            codex_token_count(TODAY_TS, usage(1), rate_limits(used=88.0, resets_at=past)),
        ])
        window = self.codex(self.run_collect(tmp))["windows"][0]
        self.assertEqual(window["used_percent"], 0.0)
        self.assertTrue(window["reset_since_observation"])

    def test_primary_weekly_window_label_and_elapsed(self):
        tmp = TempPaths(self)
        resets = int(NOW) + 7 * 24 * 3600 // 2  # half the week left => 50 % elapsed
        write_lines(tmp.codex_file(), [
            codex_token_count(TODAY_TS, usage(1), rate_limits(used=10.0, resets_at=resets)),
        ])
        window = self.codex(self.run_collect(tmp))["windows"][0]
        self.assertEqual(window["id"], "primary")
        self.assertEqual(window["label"], "Semaine")
        self.assertEqual(window["window_minutes"], 10080)
        self.assertAlmostEqual(window["elapsed_percent"], 50.0, places=1)
        self.assertFalse(window["reset_since_observation"])

    def test_window_labels_by_minutes(self):
        self.assertEqual(window_label(300), "Session 5 h")
        self.assertEqual(window_label(10080), "Semaine")
        self.assertEqual(window_label(1440), "Fenêtre 24 h")
        self.assertEqual(window_label(None), "Fenêtre")

    def test_plan_labels(self):
        self.assertEqual(codex_plan_label("prolite"), "Pro Lite")
        self.assertEqual(codex_plan_label("plus"), "Plus")
        self.assertEqual(codex_plan_label("pro"), "Pro")
        self.assertEqual(codex_plan_label("team"), "Team")
        self.assertEqual(codex_plan_label("enterprise"), "Enterprise")
        self.assertIsNone(codex_plan_label(None))

    def test_normalisation_formula(self):
        counters = counters_from_usage({
            "input_tokens": 24754, "cached_input_tokens": 23296, "cache_write_input_tokens": 0,
            "output_tokens": 272, "reasoning_output_tokens": 0, "total_tokens": 25026,
        })
        self.assertEqual(counters["input"], 1458)
        self.assertEqual(counters["cached_input"], 23296)
        self.assertEqual(counters["output"], 272)
        self.assertEqual(counters["requests"], 1)
        total = counters["input"] + counters["cached_input"] + counters["cache_write"] + counters["output"]
        self.assertEqual(total, 24754 + 272)

    def test_negative_or_bogus_values_are_clamped(self):
        counters = counters_from_usage({"input_tokens": 5, "cached_input_tokens": 9, "output_tokens": "x"})
        self.assertEqual(counters["input"], 0)
        self.assertEqual(counters["cached_input"], 9)
        self.assertEqual(counters["output"], 0)

    def test_committed_fixture_totals(self):
        tmp = TempPaths(self)
        data = (FIXTURES / "codex-rollout-sample.jsonl").read_bytes()
        write_bytes(tmp.codex_file("sample.jsonl"), [data])
        codex = self.codex(self.run_collect(tmp))
        today = codex["tokens"]["today"]
        self.assertEqual(today["requests"], 2)
        self.assertEqual(today["input"], 900)
        self.assertEqual(today["cached_input"], 2100)
        self.assertEqual(today["output"], 300)
        self.assertEqual(today["reasoning"], 50)
        self.assertEqual(today["total"], 3300)
        self.assertEqual(codex["plan"]["label"], "Pro Lite")
        self.assertEqual(codex["windows"][0]["label"], "Semaine")
        self.assertIn("parse_errors:1", codex["errors"])

    def test_source_missing_is_reported(self):
        tmp = TempPaths(self)
        codex = self.codex(self.run_collect(tmp))
        self.assertFalse(codex["available"])
        self.assertIn("source_missing", codex["errors"])
        self.assertEqual(codex["tokens"]["today"]["total"], 0)
        self.assertEqual(len(codex["tokens"]["daily_14d"]), 14)


if __name__ == "__main__":
    unittest.main()
