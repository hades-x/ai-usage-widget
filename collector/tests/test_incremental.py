"""Incremental parsing, index robustness, and the atomic state write — synthetic data only."""

import _pathfix  # noqa: F401 - must precede ai_usage imports


import json
import os
import unittest
from pathlib import Path

from ai_usage import index as index_mod
from ai_usage.collector import collect
from ai_usage.state import atomic_write_json
from ai_usage.timeutil import parse_iso

from helpers import ParisTZ, TempPaths, codex_tur, usage, write_bytes, write_lines, jline

NOW = float(parse_iso("2026-10-08T12:00:00Z") or 0.0)
TS = "2026-10-08T10:00:00.000Z"


class IncrementalTests(ParisTZ):
    def run_collect(self, tmp, now=NOW):
        return collect(tmp.paths(), now=now, use_api=False)

    def today_requests(self, state):
        return state["providers"]["codex"]["tokens"]["today"]["requests"]

    def test_append_only_new_lines_are_counted(self):
        tmp = TempPaths(self)
        f = tmp.codex_file()
        write_lines(f, [codex_tur(TS, "r1", usage(10, out=1))])
        self.assertEqual(self.today_requests(self.run_collect(tmp)), 1)
        write_lines(f, [codex_tur(TS, "r2", usage(10, out=1))], append=True)
        self.assertEqual(self.today_requests(self.run_collect(tmp)), 2)
        self.assertEqual(self.today_requests(self.run_collect(tmp)), 2)  # nothing new => no double count

    def test_partial_trailing_line_is_kept_for_next_run(self):
        tmp = TempPaths(self)
        f = tmp.codex_file()
        full = jline(codex_tur(TS, "r1", usage(10, out=1)))
        second = jline(codex_tur(TS, "r2", usage(20, out=2)))
        write_bytes(f, [full + second[:25]])  # second line incomplete
        self.assertEqual(self.today_requests(self.run_collect(tmp)), 1)
        write_bytes(f, [second[25:]], append=True)
        self.assertEqual(self.today_requests(self.run_collect(tmp)), 2)

    def test_truncate_reparses_without_double_counting(self):
        tmp = TempPaths(self)
        f = tmp.codex_file()
        write_lines(f, [codex_tur(TS, "r1", usage(10, out=1)), codex_tur(TS, "r2", usage(10, out=1))])
        self.assertEqual(self.today_requests(self.run_collect(tmp)), 2)
        write_lines(f, [codex_tur(TS, "r1", usage(10, out=1))])  # same inode, shorter => reparse
        self.assertEqual(self.today_requests(self.run_collect(tmp)), 1)

    def test_inode_change_reparses_without_double_counting(self):
        tmp = TempPaths(self)
        f = tmp.codex_file()
        write_lines(f, [codex_tur(TS, "r1", usage(10, out=1))])
        self.assertEqual(self.today_requests(self.run_collect(tmp)), 1)
        replacement = f.with_name("replacement.tmp")
        write_lines(replacement, [codex_tur(TS, "r1", usage(10, out=1)), codex_tur(TS, "r3", usage(5, out=1))])
        os.replace(replacement, f)  # new inode at the same path
        self.assertEqual(self.today_requests(self.run_collect(tmp)), 2)

    def test_rewritten_in_place_same_size_detected_by_head_digest(self):
        tmp = TempPaths(self)
        f = tmp.codex_file()
        write_lines(f, [codex_tur(TS, "rAAAA", usage(10, out=1))])
        self.run_collect(tmp)
        write_lines(f, [codex_tur(TS, "rBBBB", usage(10, out=1))])
        state = self.run_collect(tmp)
        self.assertEqual(self.today_requests(state), 1)

    def test_deleted_file_contribution_removed(self):
        tmp = TempPaths(self)
        write_lines(tmp.codex_file("a.jsonl"), [codex_tur(TS, "r1", usage(10, out=1))])
        write_lines(tmp.codex_file("b.jsonl"), [codex_tur(TS, "r2", usage(10, out=1))])
        self.assertEqual(self.today_requests(self.run_collect(tmp)), 2)
        tmp.codex_file("b.jsonl").unlink()
        self.assertEqual(self.today_requests(self.run_collect(tmp)), 1)

    def test_corrupt_index_triggers_full_rebuild(self):
        tmp = TempPaths(self)
        write_lines(tmp.codex_file(), [codex_tur(TS, "r1", usage(10, out=1))])
        self.run_collect(tmp)
        tmp.paths().index_path.write_text("{corrupt", encoding="utf-8")
        self.assertEqual(self.today_requests(self.run_collect(tmp)), 1)
        self.assertEqual(json.loads(tmp.paths().index_path.read_text())["version"], 1)

    def test_missing_index_triggers_full_rebuild(self):
        tmp = TempPaths(self)
        write_lines(tmp.codex_file(), [codex_tur(TS, "r1", usage(10, out=1))])
        self.run_collect(tmp)
        tmp.paths().index_path.unlink()
        self.assertEqual(self.today_requests(self.run_collect(tmp)), 1)

    def test_claude_dedupe_keys_survive_across_runs(self):
        tmp = TempPaths(self)
        line = {"type": "assistant", "timestamp": TS, "requestId": "req_1",
                "message": {"id": "msg_1", "model": "m", "usage": {"input_tokens": 3, "output_tokens": 1}}}
        f = tmp.claude_file()
        write_lines(f, [line])
        self.run_collect(tmp)
        write_lines(f, [line], append=True)  # re-written streaming copy in a later run
        claude = self.run_collect(tmp)["providers"]["claude"]
        self.assertEqual(claude["tokens"]["today"]["requests"], 1)

    def test_seen_keys_older_than_48h_are_dropped(self):
        tmp = TempPaths(self)
        line = {"type": "assistant", "timestamp": "2026-10-01T10:00:00.000Z", "requestId": "r",
                "message": {"id": "m", "model": "x", "usage": {"input_tokens": 1}}}
        write_lines(tmp.claude_file(), [line])
        self.run_collect(tmp)
        index = json.loads(tmp.paths().index_path.read_text())
        self.assertEqual(index["claude"]["seen"], {})

    def test_index_roundtrip_is_stable(self):
        tmp = TempPaths(self)
        write_lines(tmp.codex_file(), [codex_tur(TS, "r1", usage(10, out=1))])
        self.run_collect(tmp)
        first = tmp.paths().index_path.read_text()
        self.run_collect(tmp)
        self.assertEqual(tmp.paths().index_path.read_text(), first)

    def test_day_older_than_35_days_is_pruned_from_index(self):
        tmp = TempPaths(self)
        old = "2026-08-01T10:00:00.000Z"
        write_lines(tmp.codex_file(), [codex_tur(old, "r_old", usage(10, out=1)),
                                       codex_tur(TS, "r_new", usage(10, out=1))])
        self.run_collect(tmp)
        index = json.loads(tmp.paths().index_path.read_text())
        tables = index["codex"]["files"][str(tmp.codex_file())]["tables"]["usage"]
        self.assertNotIn("2026-08-01", tables)
        self.assertIn("2026-10-08", tables)

    def test_index_validation(self):
        self.assertFalse(index_mod._valid_index(None))
        self.assertFalse(index_mod._valid_index({"version": 99}))
        self.assertTrue(index_mod._valid_index(index_mod.empty_index()))


class StateWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TempPaths(self)

    def test_atomic_write_mode_and_content(self):
        path = self.tmp.root / "dir" / "state.json"
        atomic_write_json(path, {"a": 1})
        self.assertEqual(json.loads(path.read_text()), {"a": 1})
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(path.parent).st_mode & 0o777), "0o700")
        self.assertFalse((path.parent / "state.json.tmp").exists())

    def test_overwrite_keeps_old_file_if_write_fails(self):
        path = self.tmp.root / "state.json"
        atomic_write_json(path, {"v": 1})
        original = os.replace

        def boom(src, dst):
            raise OSError("simulated crash before rename")

        os.replace = boom
        try:
            with self.assertRaises(OSError):
                atomic_write_json(path, {"v": 2})
        finally:
            os.replace = original
        self.assertEqual(json.loads(path.read_text()), {"v": 1})


if __name__ == "__main__":
    unittest.main()
