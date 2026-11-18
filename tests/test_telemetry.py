"""Attribution and compressed-stream regressions, independent of media files."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from telemetry import usage_logs, Error, union_ms

START = "2026-01-01T00:00:00Z"
END = "2026-01-01T00:01:00Z"


class Telemetry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def log(self, name="one.jsonl", owner="one", responses=None):
        responses = (
            responses
            if responses is not None
            else [("a", "root", 10), ("b", "later", 20)]
        )
        rows = [{"type": "session_meta", "payload": {"id": owner}}]
        total = {}
        for i, (response, root, n) in enumerate(responses):
            usage = {
                "input_tokens": n,
                "cached_input_tokens": n // 2,
                "output_tokens": 2,
                "reasoning_output_tokens": 1,
            }
            total = {k: total.get(k, 0) + v for k, v in usage.items()}
            rows.append(
                {
                    "timestamp": f"2026-01-01T00:00:{i + 1:02}Z",
                    "type": "token_usage_record",
                    "payload": {
                        "thread_id": owner,
                        "turn_id": response,
                        "root_turn_id": root,
                        "response_id": response,
                        "usage": usage,
                        "thread_token_usage": total,
                    },
                }
            )
        p = self.root / name
        p.write_text("\n".join(json.dumps(x) for x in rows) + "\n")
        return p

    def read(self, *paths, roots=()):
        return usage_logs(paths, START, END, roots)

    def test_native_root_selection(self):
        r = self.read(self.log(), roots=["root"])
        self.assertTrue(r["complete"])
        self.assertEqual(r["usage"]["input_tokens"], 10)
        self.assertEqual(r["usage"]["output_tokens"], 2)
        self.assertEqual(r["usage"]["uncached_input_tokens"], 5)

    def test_duplicate_alias_and_response_are_not_summed(self):
        p = self.log()
        q = self.root / "alias.jsonl"
        shutil.copy(p, q)
        r = self.read(p, q)
        self.assertEqual(r["usage"]["input_tokens"], 30)

    def test_inherited_other_thread_records_excluded(self):
        p = self.log()
        x = json.loads(p.read_text().splitlines()[1])
        x["payload"]["thread_id"] = "parent"
        with p.open("a") as f:
            f.write(json.dumps(x) + "\n")
        self.assertEqual(self.read(p)["usage"]["input_tokens"], 30)

    def test_repeated_event_not_a_request(self):
        p = self.log()
        line = p.read_text().splitlines()[1]
        with p.open("a") as f:
            f.write(line + "\n")
        self.assertEqual(self.read(p)["usage"]["input_tokens"], 30)

    def test_unreconciled_native_is_not_a_pass(self):
        p = self.log()
        rows = p.read_text().splitlines()
        x = json.loads(rows[-1])
        x["payload"]["thread_token_usage"]["input_tokens"] = 100
        rows[-1] = json.dumps(x)
        p.write_text("\n".join(rows))
        r = self.read(p)
        self.assertFalse(r["complete"])
        self.assertEqual(r["usage"], "not_measured")

    def test_conflicting_response_rejected(self):
        p = self.log()
        x = json.loads(p.read_text().splitlines()[1])
        x["payload"]["usage"]["input_tokens"] = 999
        with p.open("a") as f:
            f.write(json.dumps(x) + "\n")
        with self.assertRaises(Error):
            self.read(p)

    def test_worker_from_other_root_is_zero_not_missing(self):
        a = self.log()
        b = self.log("two.jsonl", "two", [("c", "other", 20)])
        r = self.read(a, b, roots=["root"])
        self.assertTrue(r["complete"])
        self.assertEqual(r["usage"]["input_tokens"], 10)

    def test_missing_requested_root_is_not_a_pass(self):
        r = self.read(self.log(), roots=["root", "missing"])
        self.assertFalse(r["complete"])
        self.assertEqual(r["usage"], "not_measured")

    def test_empty_file_not_measured(self):
        p = self.root / "empty"
        p.touch()
        self.assertEqual(self.read(p)["usage"], "not_measured")

    def test_missing_file_fails(self):
        with self.assertRaises(FileNotFoundError):
            self.read(self.root / "missing")

    def test_counter_only_cannot_claim_root_attribution(self):
        p = self.root / "counter"
        p.write_text(
            json.dumps(
                {
                    "timestamp": START,
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": {"input_tokens": 10}},
                    },
                }
            )
        )
        self.assertFalse(self.read(p, roots=["root"])["complete"])

    def test_reset_then_recovery_still_rejected(self):
        p = self.root / "counter"
        p.write_text(
            "\n".join(
                json.dumps(
                    {
                        "timestamp": f"2026-01-01T00:00:{i:02}Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {"total_token_usage": {"input_tokens": n}},
                        },
                    }
                )
                for i, n in enumerate([100, 20, 200])
            )
        )
        with self.assertRaises(Error):
            self.read(p)

    def test_time_validation(self):
        p = self.log()
        for start, end in [
            (END, START),
            ("2026-01-01T00:00:00", END),
            ("2026-01-01T00:00:00+09:00", END),
        ]:
            with self.assertRaises(Error):
                usage_logs([p], start, end)

    def test_interval_union(self):
        self.assertEqual(union_ms([(0, 10), (5, 20), (30, 40)]), 30)

    @unittest.skipUnless(shutil.which("zstd"), "zstd unavailable")
    def test_compressed_matches_plain_without_duplicate_usage(self):
        p = self.log()
        q = self.root / "one.jsonl.zst"
        with q.open("wb") as out:
            subprocess.run(["zstd", "-qc", str(p)], stdout=out, check=True)
        self.assertEqual(self.read(p, q)["usage"]["input_tokens"], 30)

    @unittest.skipUnless(shutil.which("zstd"), "zstd unavailable")
    def test_corrupt_compressed_fails(self):
        p = self.root / "bad.zst"
        p.write_bytes(b"broken")
        with self.assertRaises(Error):
            self.read(p)

    def test_cli_without_project(self):
        p = self.log()
        result = subprocess.run(
            [
                sys.executable,
                str((Path(__file__).resolve().parents[1] / "skills/korean-subtitles/scripts/subtitles.py")),
                "telemetry",
                "--session-log",
                str(p),
                "--root-turn-id",
                "root",
                "--start",
                START,
                "--end",
                END,
            ],
            cwd=self.root,
            capture_output=True,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout)["usage"]["input_tokens"], 10)

class ForeignHostUsage(unittest.TestCase):
    def test_unrecognized_host_log_is_not_measured_not_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'foreign.jsonl'
            path.write_text(json.dumps({'provider':'synthetic-host','usage':{'input':42}})+'\n')
            result=usage_logs([str(path)], START, END)
            self.assertEqual(result['usage'],'not_measured')
            self.assertFalse(result['complete'])
