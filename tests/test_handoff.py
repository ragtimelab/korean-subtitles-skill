"""Machine-owned dispatch and response paths; all linguistic judgments supplied."""

import argparse
import json
from pathlib import Path
import subprocess
from unittest.mock import patch
from domain_store import Store
from test_subtitles import Fixture, w


class Handoff(Fixture):
    def group(self, count=2, sealed=(1,)):
        media = self.root / "handoff-media"
        media.mkdir()
        for number in range(count):
            (media / f"item-{number}.mkv").write_bytes(b"synthetic")
        parent = self.root / "handoff-work"
        with patch.object(
            w,
            "command",
            return_value=json.dumps(
                {"format": {"duration": "30"}, "streams": []}
            ).encode(),
        ):
            prepared = w.prepare(
                argparse.Namespace(media=str(media), work=str(parent), target=None)
            )
        items = [Path(item["project"]) for item in prepared["items"]]
        for item in items:
            self.work = item
            self.s, _ = w.load(item)
            self.select("en", self.en)
            self.select("ko", self.ko)
            self.save()
        self.work = items[0]
        self.s, _ = w.load(self.work)
        self.study()
        self.save()
        for number in sealed:
            self.work = items[number]
            self.s, _ = w.load(self.work)
            self.author()
            self.seal()
            self.save()
        return parent, items

    def handoff(self, parent, items, worker):
        with w.transaction(parent) as db:
            return Store(db, vars(w)).handoff([str(item) for item in items], worker)

    def test_ready_independent_review_precedes_new_correction(self):
        parent, items = self.group()
        previous = w.load(items[1])[0]["metrics"]["presented_bytes"]
        result = self.handoff(parent, items, "reader")
        self.assertEqual(result["assignment"]["role"], "reviewer")
        self.assertEqual(result["assignment"]["project"], str(items[1]))
        self.assertTrue(result["comparisons"])
        template = Path(result["response_template"])
        self.assertEqual(template.parent, items[1] / "responses")
        self.assertEqual(json.loads(template.read_text())["packet"], result["packet"])
        self.assertNotIn("submit", result)
        with w.transaction(parent) as db:
            state = Store(db, vars(w)).header(items[1])
            self.assertEqual(
                state["metrics"]["presented_bytes"] - previous, len(w.packed(result))
            )

    def test_assigned_job_continues_and_other_worker_cannot_duplicate_it(self):
        parent, items = self.group()
        self.handoff(parent, items, "reader")
        same = self.handoff(parent, items, "reader")
        self.assertEqual(same["assignment"]["project"], str(items[1]))
        other = self.handoff(parent, items, "writer")
        self.assertEqual(other["assignment"]["project"], str(items[0]))
        self.assertEqual(other["assignment"]["role"], "author")

    def test_first_corrector_cannot_claim_own_review(self):
        result = self.handoff(self.work, [self.work], "author")
        self.assertEqual(result["action"], "wait")
        self.assertIsNone(w.load(self.work)[0]["reviewer"])

    def test_third_distinct_episode_requires_fresh_session(self):
        parent, items = self.group(count=3, sealed=(0, 1))
        for _ in range(2):
            packet = self.handoff(parent, items, "limited")
            item = Path(packet["assignment"]["project"])
            with w.transaction(parent) as db:
                store = Store(db, vars(w))
                store.acknowledge(
                    store.header(item),
                    json.loads(Path(packet["response_template"]).read_text()),
                )
            state, _ = w.load(item)
            w.submit(state, {"kind": "approval", "worker": "limited"}, item)
        result = self.handoff(parent, items, "limited")
        self.assertEqual(result["action"], "retire")
        self.assertEqual(result["session_items"], 2)
        narrowed = self.handoff(parent, [items[2]], "limited")
        self.assertEqual(narrowed["action"], "retire")
        self.assertIsNone(w.load(items[2])[0]["author"])
        fresh = self.handoff(parent, items, "fresh")
        self.assertEqual(fresh["assignment"]["project"], str(items[2]))
        self.assertEqual(fresh["assignment"]["role"], "author")

    def test_cli_auto_template_uses_actual_item_responses(self):
        self.save()
        result = subprocess.run(
            [
                "python3",
                w.__file__,
                "view",
                str(self.work),
                "--role",
                "author",
                "--worker",
                "author",
                "--ids",
                "E4,K3",
                "--template",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        packet = json.loads(result.stdout)
        self.assertEqual(
            Path(packet["response_template"]).parent,
            (self.work / "responses").resolve(),
        )
        self.assertEqual(
            json.loads(Path(packet["response_template"]).read_text())["packet"],
            packet["packet"],
        )

    def test_handoff_does_not_bypass_study_or_original_guards(self):
        parent, items = self.group(sealed=())
        with w.transaction(parent) as db:
            store = Store(db, vars(w))
            state = store.header(items[0])
            ctx = store.context_header(state)
            for study in ctx["studies"].values():
                study["complete"] = False
            w.write_record(Path(state["context_path"]), w.packed(ctx))
        result = self.handoff(parent, items, "reader")
        self.assertEqual(result["action"], "wait")
        self.assertFalse(any(w.load(item)[0]["author"] for item in items))
        self.en.write_bytes(self.en.read_bytes() + b"\n")
        with self.assertRaises(w.Error):
            self.handoff(parent, items, "reader")

    def test_cli_handoff_uses_parent_and_canonical_first_packet(self):
        parent, items = self.group()
        result = subprocess.run(
            ["python3", w.__file__, "handoff", str(parent), "--worker", "reader"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        packet = json.loads(result.stdout)
        self.assertEqual(packet["mode"], "reviewer")
        self.assertEqual(packet["assignment"]["project"], str(items[1]))
        self.assertTrue(Path(packet["response_template"]).is_file())

    def test_concurrent_workers_claim_different_eligible_jobs(self):
        parent, items = self.group()
        processes = [
            subprocess.Popen(
                ["python3", w.__file__, "handoff", str(parent), "--worker", worker],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for worker in ("one", "two")
        ]
        packets = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr.decode())
            packets.append(json.loads(stdout))
        self.assertEqual(
            {p["assignment"]["project"] for p in packets}, set(map(str, items))
        )
        self.assertEqual(
            {p["assignment"]["role"] for p in packets}, {"author", "reviewer"}
        )

    def cli_submit(self, item, record, next_job=True):
        path = item / "responses" / "terminal.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(record))
        return subprocess.run(
            ["python3", w.__file__, "submit", str(item), str(path)]
            + (["--next"] if next_job else []),
            capture_output=True, text=True,
        )

    def test_approval_returns_next_packet_without_another_claim_or_source_read(self):
        parent, items = self.group()
        packet = self.handoff(parent, items, "reader")
        with w.transaction(parent) as db:
            store = Store(db, vars(w))
            store.acknowledge(store.header(items[1]),
                              json.loads(Path(packet["response_template"]).read_text()))
        result = self.cli_submit(items[1], {"kind": "approval", "worker": "reader"})
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertIn("approved_sha256", response)
        self.assertEqual(response["next"]["assignment"]["project"], str(items[0]))
        self.assertEqual(response["next"]["assignment"]["role"], "author")
        self.assertEqual(response["next"]["assignment"]["session_items"], 2)
        self.assertTrue(Path(response["next"]["response_template"]).is_file())
        self.assertTrue(w.load(items[1])[0]["approval"])
        self.assertFalse(w.load(items[0])[0]["sealed"])

    def test_seal_returns_wait_without_self_review_or_automatic_approval(self):
        self.save()
        result = self.cli_submit(self.work, {"kind": "seal", "worker": "author"})
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertTrue(response["draft_ready"])
        self.assertEqual(response["next"]["action"], "wait")
        state, _ = w.load(self.work)
        self.assertIsNone(state["reviewer"])
        self.assertFalse(state["approval"])

    def test_next_rejects_nonterminal_before_consuming_packet(self):
        self.save()
        packet = self.handoff(self.work, [self.work], "reader")
        record = json.loads(Path(packet["response_template"]).read_text())
        result = self.cli_submit(self.work, record)
        self.assertEqual(result.returncode, 2)
        self.assertIn("explicit seal or approval", result.stderr)
        with w.transaction(self.work) as db:
            self.assertIsNotNone(db.execute("SELECT id FROM receipt WHERE id=?", (record["packet"],)).fetchone())
        state, _ = w.load(self.work)
        self.assertFalse(state["approval"])

    def test_invalid_approval_cannot_claim_next_job(self):
        parent, items = self.group()
        self.handoff(parent, items, "reader")
        result = self.cli_submit(items[1], {"kind": "approval", "worker": "reader"})
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(w.load(items[0])[0]["author"])
        self.assertFalse(w.load(items[1])[0]["approval"])

    def test_failed_next_guard_rolls_back_terminal_phase(self):
        parent, items = self.group()
        packet = self.handoff(parent, items, "reader")
        with w.transaction(parent) as db:
            store = Store(db, vars(w))
            store.acknowledge(store.header(items[1]),
                              json.loads(Path(packet["response_template"]).read_text()))
        # Item 1 remains valid, but the next item's own immutable original fails.
        state, _ = w.load(items[0])
        Path(state['media']).write_bytes(b'changed')
        result = self.cli_submit(items[1], {"kind": "approval", "worker": "reader"})
        self.assertEqual(result.returncode, 2)
        with w.transaction(parent) as db:
            store = Store(db, vars(w))
            self.assertFalse(store.header(items[1])["approval"])
            self.assertIsNone(store.header(items[0])["author"])
