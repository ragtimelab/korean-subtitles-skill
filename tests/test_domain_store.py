"""Native storage invariants and scoped page behavior, no model judgments."""

import argparse
import json
import sqlite3
from unittest.mock import patch
from test_subtitles import Fixture, w
from domain_store import Store


class DomainStore(Fixture):
    def native_view(
        self, role="reviewer", ids=None, limit=200, pending=False, worker=None
    ):
        with w.transaction(self.work) as db:
            store = Store(db, vars(w))
            state = store.header(self.work)
            return store.view(
                state,
                argparse.Namespace(
                    role=role,
                    worker=worker or ("author" if role == "author" else "reader"),
                    ids=ids,
                    offset=0,
                    limit=limit,
                    pending=pending,
                    reassign=None,
                ),
            )

    def test_episode_header_is_not_a_shadow_state_document(self):
        with w.transaction(self.work) as db:
            header = json.loads(
                db.execute(
                    "SELECT body FROM records WHERE path=?",
                    (str((self.work / "state.json").resolve()),),
                ).fetchone()[0]
            )
            for key in (
                "sources",
                "outputs",
                "supplements",
                "receipts",
                "readings",
                "reading_workers",
                "presented",
                "_analysis",
            ):
                self.assertNotIn(key, header)
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM source_cue").fetchone()[0], 7
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM output_unit").fetchone()[0], 4
            )

    def test_wide_export_retains_original_and_output_bytes(self):
        saved, _ = w.load(self.work)
        self.assertEqual(saved["sources"], self.s["sources"])
        self.assertEqual(saved["outputs"], self.s["outputs"])
        self.assertEqual(w.output_raw(saved), w.output_raw(self.s))

    def test_selected_cue_rows_survive_an_unrelated_save(self):
        with w.transaction(self.work) as db:
            db.execute(
                "CREATE TRIGGER deny_source_rewrite BEFORE DELETE ON source_cue BEGIN SELECT RAISE(ABORT,'immutable source rows'); END"
            )
            w.write_record(self.work / "state.json", w.packed(self.s))

    def test_native_reviewer_page_matches_wide_page(self):
        wide = self.view(ids="E1")
        # Refresh the fixture with its already committed owner/receipt.
        self.s, _ = w.load(self.work)
        native = self.native_view(ids="E1")
        for key in (
            "comparisons",
            "korean_sources",
            "supplements",
            "remaining",
            "context_changes",
            "findings",
        ):
            self.assertEqual(native[key], wide[key], key)

    def test_native_author_page_matches_wide_page(self):
        wide = self.view("author", ids="E4,K3")
        native = self.native_view("author", ids="E4,K3")
        for key in ("cues", "remaining", "context_changes"):
            self.assertEqual(native[key], wide[key], key)
        self.assertEqual(native["submit"]["rows"], wide["submit"]["rows"])

    def test_native_page_never_exports_whole_episode(self):
        with patch.object(
            Store, "export_state", side_effect=AssertionError("full export")
        ):
            self.assertTrue(self.native_view(ids="E4")["comparisons"])

    def test_native_page_does_not_read_other_cue_bodies(self):
        # Real SQL observations: source bodies cannot be read by the page builder;
        # its materialized comparison rows already carry exactly the emitted text.
        with w.transaction(self.work) as db:
            reads = []

            def observe(action, table, column, *rest):
                if action == sqlite3.SQLITE_READ:
                    reads.append((table, column))
                return sqlite3.SQLITE_OK

            db.set_authorizer(observe)
            self.native_view(ids="E4")
            db.set_authorizer(None)
            self.assertNotIn(("source_cue", "body"), reads)

    def test_native_template_is_not_yet_a_release_approval(self):
        view = self.native_view(ids="E4")
        self.assertIn("packet", view)
        self.assertFalse(w.load(self.work)[0]["approval"])

    def native_ack(self, response):
        with w.transaction(self.work) as db:
            store = Store(db, vars(w))
            return store.acknowledge(store.header(self.work), response)

    def test_native_ack_matches_full_pending_and_hash(self):
        v = self.native_view(ids="E4")
        out = self.native_ack(v["submit"])
        saved, _ = w.load(self.work)
        self.assertEqual(out["pending"], w.compact(w.pending(saved, worker="reader")))
        self.assertEqual(out["output_sha256"], w.Analysis(saved).output_sha256)
        self.assertNotIn(v["packet"], saved["receipts"])

    def test_native_ack_does_not_read_cue_or_output_bodies(self):
        v = self.native_view(ids="E4")
        with w.transaction(self.work) as db:

            def deny(action, table, column, *rest):
                if (
                    action == sqlite3.SQLITE_READ
                    and table
                    in {"comparison", "source_cue", "output_unit", "supplement"}
                    and column == "body"
                ):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            db.set_authorizer(deny)
            try:
                with patch.object(
                    Store, "export_state", side_effect=AssertionError("whole export")
                ):
                    self.native_ack(v["submit"])
            finally:
                db.set_authorizer(None)

    def test_native_ack_replay_rejects(self):
        v = self.native_view(ids="E4")
        self.native_ack(v["submit"])
        with self.assertRaises(w.Error):
            self.native_ack(v["submit"])

    def test_native_ack_stale_output_rejects(self):
        v = self.native_view(ids="E4")
        self.s, _ = w.load(self.work)
        self.revise()
        with self.assertRaises(w.Error):
            self.native_ack(v["submit"])

    def test_native_ack_full_review_can_approve_through_same_engine(self):
        v = self.native_view()
        self.native_ack(v["submit"])
        self.s, _ = w.load(self.work)
        self.approve()
        self.assertTrue(w.approval_valid(self.s))

    def native_rows(self, response):
        with w.transaction(self.work) as db:
            store = Store(db, vars(w))
            return store.submit_rows(store.header(self.work), response)

    def test_native_correction_preserves_unaffected_personal_coverage(self):
        self.native_ack(self.native_view()["submit"])
        v = self.native_view(ids="E4")
        p = v["submit"]
        p["corrections"] = [{"id": "E4", "text": "문 좀 닫아."}]
        p["impact"] = {
            "scope": "local",
            "ids": ["E4"],
            "reason": "Supplied actual local correction.",
        }
        out = self.native_rows(p)
        saved, _ = w.load(self.work)
        self.assertEqual(out["pending"], w.compact(w.pending(saved, worker="reader")))
        self.assertIn("E4", w.pending(saved, worker="reader"))
        self.assertNotIn("E1", w.pending(saved, worker="reader"))
        self.assertEqual(out["output_sha256"], w.digest(w.output_raw(saved)))
        self.native_ack(self.native_view(pending=True)["submit"])
        self.s, _ = w.load(self.work)
        self.approve()

    def test_native_author_revision_uses_same_mechanical_validator(self):
        v = self.native_view("author", ids="E4")
        p = v["submit"]
        p["rows"][0]["text"] = "문 좀 닫아."
        p["impact"] = {
            "scope": "local",
            "ids": ["E4"],
            "reason": "Supplied actual correction.",
        }
        out = self.native_rows(p)
        saved, _ = w.load(self.work)
        self.assertEqual(saved["outputs"]["E4"]["texts"]["E4"], "문 좀 닫아.")
        self.assertEqual(out["output_sha256"], w.digest(w.output_raw(saved)))

    def test_native_failed_revision_rolls_back_rows_and_receipt(self):
        v = self.native_view(ids="E4")
        p = v["submit"]
        p["corrections"] = [{"id": "E4", "text": "- 구조 변경."}]
        p["impact"] = {
            "scope": "local",
            "ids": ["E4"],
            "reason": "Supplied invalid structure.",
        }
        with self.assertRaises(w.Error):
            self.native_rows(p)
        saved, _ = w.load(self.work)
        self.assertIn(v["packet"], saved["receipts"])
        self.assertEqual(saved["outputs"]["E4"]["texts"]["E4"], "문을 닫아.")

    def test_native_local_revision_never_exports_full_state(self):
        v = self.native_view(ids="E4")
        p = v["submit"]
        p["corrections"] = [{"id": "E4", "text": "문 좀 닫아."}]
        p["impact"] = {
            "scope": "local",
            "ids": ["E4"],
            "reason": "Supplied local revision.",
        }
        with patch.object(
            Store, "export_state", side_effect=AssertionError("full export")
        ):
            self.native_rows(p)

    def test_pending_page_uses_ordered_agenda_not_full_cue_scan(self):
        self.native_view()
        with w.transaction(self.work) as db:
            plan = db.execute(
                "EXPLAIN QUERY PLAN SELECT c.id FROM pending_cue p JOIN comparison c ON c.work=p.work AND c.id=p.id WHERE p.work=? AND p.worker=? ORDER BY p.start,p.kind,p.number LIMIT 200",
                (str(self.work.resolve()), "reader"),
            ).fetchall()
        detail = " ".join(str(row[-1]) for row in plan)
        self.assertIn("pending_order", detail)
        self.assertNotIn("SCAN c", detail)
        self.assertNotIn("TEMP B-TREE", detail)

    def test_native_ack_never_reconstructs_semantic_touch_map(self):
        v = self.native_view(ids="E4")
        with w.transaction(self.work) as db:
            db.executemany(
                "INSERT INTO semantic_touch VALUES(?,?,?,?)",
                [
                    (str(self.work.resolve()), "other-media", "E" + str(i), 1)
                    for i in range(1, 4001)
                ],
            )

            def deny(action, table, column, *rest):
                if action == sqlite3.SQLITE_READ and table == "semantic_touch":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            db.set_authorizer(deny)
            try:
                self.native_ack(v["submit"])
            finally:
                db.set_authorizer(None)

    def test_native_local_revision_never_uses_wide_context_reader(self):
        v = self.native_view(ids="E4")
        p = v["submit"]
        p["corrections"] = [{"id": "E4", "text": "문 좀 닫아."}]
        p["impact"] = {
            "scope": "local",
            "ids": ["E4"],
            "reason": "Supplied local revision.",
        }
        with patch.object(w, "context", side_effect=AssertionError("wide context")):
            self.native_rows(p)

    def test_declared_additional_touch_invalidates_its_correspondences(self):
        self.native_ack(self.native_view()["submit"])
        v = self.native_view(ids="E4")
        p = v["submit"]
        p["corrections"] = [{"id": "E4", "text": "문 좀 닫아."}]
        p["impact"] = {
            "scope": "local",
            "ids": ["E1"],
            "reason": "Supplied additional impact on another utterance.",
        }
        result = self.native_rows(p)
        state, _ = w.load(self.work)
        self.assertEqual(
            result["pending"], w.compact(w.pending(state, worker="reader"))
        )
        self.assertIn("O1", w.pending(state, worker="reader"))

    def test_comparison_index_is_derived_from_canonical_original_rows(self):
        original = self.s["sources"]["en"]["cues"][0]["text"]
        self.s["sources"]["en"]["cues"][0]["text"] = "An unselected parallel copy."
        w.write_record(self.work / "state.json", w.packed(self.s))
        with w.transaction(self.work) as db:
            store = Store(db, vars(w))
            self.assertEqual(
                store.comparison_rows(str(self.work.resolve()), ["E1"])["E1"][0][
                    "text"
                ],
                original,
            )

    def test_hash_guard_rejects_artifact_mutation_during_read(self):
        real_open = type(self.en).open
        target = self.en.resolve()

        class Stream:
            def __init__(inner, stream):
                inner.stream = stream
                inner.changed = False

            def __enter__(inner):
                return inner

            def __exit__(inner, *args):
                inner.stream.close()

            def fileno(inner):
                return inner.stream.fileno()

            def read(inner, size):
                result = inner.stream.read(size)
                if result and not inner.changed:
                    inner.changed = True
                    with real_open(target, "ab") as output:
                        output.write(b"\n")
                return result

        def opened(path, *args, **kwargs):
            stream = real_open(path, *args, **kwargs)
            return (
                Stream(stream)
                if path.resolve() == target and args and args[0] == "rb"
                else stream
            )

        with patch.object(type(self.en), "open", opened), self.assertRaises(w.Error):
            w.filehash(self.en)

    def test_changed_engine_cannot_reuse_old_project_or_approval(self):
        with patch.object(w, "ENGINE_HASH", "changed"):
            with self.assertRaises(w.Error):
                w.load(self.work)
            with self.assertRaises(w.Error):
                self.native_view()

    def test_phase_commits_do_not_rewrite_linguistic_rows(self):
        import subprocess
        import sys
        from pathlib import Path

        self.native_ack(self.native_view()["submit"])
        self.s, _ = w.load(self.work)
        with w.transaction(self.work) as db:
            db.execute(
                "CREATE TRIGGER protect_outputs BEFORE DELETE ON output_unit BEGIN SELECT RAISE(ABORT,'unrelated output rewrite'); END"
            )
            db.execute(
                "CREATE TRIGGER protect_cues BEFORE DELETE ON source_cue BEGIN SELECT RAISE(ABORT,'immutable cue rewrite'); END"
            )
        self.seal()
        self.approve()
        result = subprocess.run(
            [sys.executable, w.__file__, "finalize", str(self.work)],
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        saved, _ = w.load(self.work)
        self.assertEqual(saved["target_hash"], w.filehash(Path(saved["target"])))

    def test_unrelated_new_fact_does_not_discard_current_comparison(self):
        view = self.native_view(ids="E1")
        self.s, _ = w.load(self.work)
        self.fact(
            facts={
                "later": {
                    "text": "Additional supplied work fact, unchanged earlier meaning.",
                    "status": "established",
                    "global": True,
                    "evidence": ["script"],
                }
            }
        )
        self.native_ack(view["submit"])
        saved, _ = w.load(self.work)
        self.assertNotIn("E1", w.pending(saved, worker="reader"))
        self.assertNotIn("fact:later", w.registry(saved)["metadata"]["reader"])
        self.assertIn("fact:later", w.metadata_pending(saved, "reader"))

    def test_old_packet_cannot_downgrade_fresh_metadata_acknowledgement(self):
        self.fact(
            facts={
                "unused": {
                    "text": "Original supplied unused fact.",
                    "status": "established",
                    "global": True,
                    "evidence": ["script"],
                }
            }
        )
        view = self.native_view(ids="E1")
        self.s, _ = w.load(self.work)
        self.fact(
            facts={
                "unused": {
                    "text": "Revised supplied unused fact.",
                    "status": "established",
                    "global": True,
                    "evidence": ["script"],
                }
            },
            impact={
                "scope": "local",
                "ids": ["E4"],
                "reason": "Actual impact on a different utterance.",
            },
        )
        self.native_ack(self.native_view("metadata")["submit"])
        self.native_ack(view["submit"])
        saved, _ = w.load(self.work)
        self.assertEqual(
            w.registry(saved)["metadata"]["reader"]["fact:unused"],
            w.digest(w.context(saved)["facts"]["unused"]),
        )
        self.assertNotIn("E1", w.pending(saved, worker="reader"))

    def test_author_can_finish_unchanged_scope_after_unrelated_fact_addition(self):
        view = self.native_view("author", ids="E4")
        self.s, _ = w.load(self.work)
        self.fact(
            facts={
                "later": {
                    "text": "Additional later information.",
                    "status": "established",
                    "global": False,
                    "evidence": ["script"],
                }
            }
        )
        self.native_rows(view["submit"])
        saved, _ = w.load(self.work)
        self.assertNotIn(view["packet"], saved["receipts"])

    def native_status(self):
        with w.transaction(self.work) as db:
            store = Store(db, vars(w))
            return store.status(store.header(self.work))

    def test_native_status_matches_current_full_validation(self):
        for action in (
            lambda: None,
            lambda: self.native_ack(self.native_view()["submit"]),
            lambda: (setattr(self, "s", w.load(self.work)[0]), self.approve()),
        ):
            action()
            self.s, _ = w.load(self.work)
            wide = w.status(self.s)
            native = self.native_status()
            for key in (
                "phase",
                "approved",
                "approved_sha256",
                "pending",
                "pending_count",
                "output_cues",
                "draft_ready",
            ):
                self.assertEqual(native[key], wide[key], key)

    def test_native_status_never_exports_cue_bodies(self):
        with patch.object(
            Store, "export_state", side_effect=AssertionError("full export")
        ):
            self.assertEqual(self.native_status()["phase"], "review_ready")

    def test_same_byte_semantic_revision_requires_new_explicit_approval(self):
        self.fact()
        self.native_ack(self.native_view("metadata")["submit"])
        self.native_ack(self.native_view()["submit"])
        self.s, _ = w.load(self.work)
        self.approve()
        original_hash = self.s["approval"]["hash"]
        self.assertTrue(self.native_status()["approved"])
        p = self.native_view(ids="E4")["submit"]
        p["corrections"] = [{"id": "E4", "facts": ["name"]}]
        p["impact"] = {
            "scope": "local",
            "ids": ["E4"],
            "reason": "Supplied changed factual dependency, same output bytes.",
        }
        self.native_rows(p)
        self.native_ack(self.native_view(pending=True)["submit"])
        state, _ = w.load(self.work)
        self.assertEqual(w.digest(w.output_raw(state)), original_hash)
        self.assertFalse(self.native_status()["approved"])

    def test_inverse_edges_do_not_scan_unrelated_episode_rows(self):
        with w.transaction(self.work) as db:
            store = Store(db, vars(w))
            work = str(self.work.resolve())
            db.execute(
                "INSERT INTO unit_reference VALUES(?,?,?,?)",
                (work, "E4", "fact", "scoped-test"),
            )

            def measured():
                steps = [0]

                def tick():
                    steps[0] += 1
                    return 0

                db.set_progress_handler(tick, 1)
                try:
                    members = list(store.linked_members(work, "K3"))
                    units = list(store.linked_units(work, "K3"))
                    refs = list(store.reference_members(work, "fact", "scoped-test"))
                finally:
                    db.set_progress_handler(None, 0)
                self.assertEqual(set(members), {("E4",), ("K3",), ("O4",)})
                self.assertEqual([r[0] for r in units], ["E4"])
                self.assertEqual(set(refs), set(members))
                return steps[0]

            small = measured()
            observations = []
            for begin, end in ((100, 1100), (1100, 4100)):
                db.executemany(
                    "INSERT INTO output_unit VALUES(?,?,?)",
                    [(work, "E" + str(i), b"{}") for i in range(begin, end)],
                )
                db.executemany(
                    "INSERT INTO membership VALUES(?,?,?)",
                    [
                        (work, "E" + str(i), prefix + str(i))
                        for i in range(begin, end)
                        for prefix in "EKO"
                    ],
                )
                observations.append(measured())
            self.assertTrue(
                all(value <= small * 2 for value in observations), (small, observations)
            )

    def test_collected_supplement_can_equal_its_original_source(self):
        # A collected source is evidence, never a duplicate final output.
        view = self.native_view(ids="E4,K3")
        response = view["submit"]
        response["supplements"] = [
            {
                "id": "S1",
                "origin": "collected",
                "kind": "screen_text",
                "start": 8500,
                "end": 10500,
                "text": "문을 닫아.",
                "links": ["K3"],
                "reason": "Supplied collected text.",
            }
        ]
        response["impact"] = {
            "scope": "local",
            "ids": ["K3"],
            "reason": "Retain collected-only text at its own interval.",
        }
        result = self.native_rows(response)
        self.assertEqual(result["supplements_added"], ["S1"])
        self.assertEqual(
            w.load(self.work)[0]["supplements"]["S1"]["text"], "문을 닫아."
        )
        # Another actual S at identical time/text still is a duplicate output.
        response = self.native_view(ids="K3,S1")["submit"]
        response["supplements"] = [
            {
                "id": "S2",
                "origin": "collected",
                "kind": "screen_text",
                "start": 8500,
                "end": 10500,
                "text": "문을 닫아.",
                "links": ["K3"],
                "reason": "Supplied duplicate.",
            }
        ]
        response["impact"] = {
            "scope": "local",
            "ids": ["K3"],
            "reason": "Supplied duplicate attempt.",
        }
        with self.assertRaisesRegex(w.Error, "Duplicate supplement"):
            self.native_rows(response)
        self.assertNotIn("S2", w.load(self.work)[0]["supplements"])

    def test_collected_supplement_still_rejects_existing_dialogue_output(self):
        response = self.native_view(ids="E4,K3")["submit"]
        response["supplements"] = [
            {
                "id": "S1",
                "origin": "collected",
                "kind": "dialogue",
                "start": 8000,
                "end": 10000,
                "text": "문을 닫아.",
                "links": ["K3"],
                "reason": "Supplied duplicate of existing O4.",
            }
        ]
        response["impact"] = {
            "scope": "local",
            "ids": ["K3"],
            "reason": "Supplied duplicate-output attempt.",
        }
        with self.assertRaisesRegex(w.Error, "Duplicate supplement"):
            self.native_rows(response)
        self.assertNotIn("S1", w.load(self.work)[0]["supplements"])
