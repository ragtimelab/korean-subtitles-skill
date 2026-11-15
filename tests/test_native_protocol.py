"""Run the existing supplied-judgment protocol cases through native page/delta APIs.

The fixture sync deliberately supports legacy tests that edit in-memory state.
It is test setup only, outside any timing, and is not a production fallback.
"""

import argparse
from pathlib import Path
import unittest
from domain_store import Store
import test_subtitles
import test_decisions
import test_study
import test_admission

w = test_subtitles.w


class NativeProtocol:
    def sync_fixture(self):
        work = Path(self.s["work"])
        w.write_record(work / "state.json", w.packed(self.s))
        return work

    def view(self, role="reviewer", worker=None, **extra):
        work = self.sync_fixture()
        args = argparse.Namespace(
            **{
                "role": role,
                "worker": worker or ("author" if role == "author" else "reader"),
                "ids": None,
                "offset": 0,
                "limit": 200,
                "pending": False,
                "reassign": None,
                **extra,
            }
        )
        if role == "study":
            result = w.view(self.s, args)
        else:
            with w.transaction(w.store_root(work / "state.json")) as db:
                store = Store(db, vars(w))
                result = store.view(store.header(work), args)
        self.s, _ = w.load(work)
        return result

    def do(self, response):
        work = self.sync_fixture()
        packet = self.s["receipts"].get(response.get("packet"), {})
        if "_epochs" not in packet.get("pins", {}):
            result = w.submit(self.s, response, self.work)
        else:
            with w.transaction(w.store_root(work / "state.json")) as db:
                store = Store(db, vars(w))
                state = store.header(work)
                if packet["kind"] == "metadata" or (
                    packet["kind"] == "reviewer"
                    and not any(
                        response.get(k)
                        for k in (
                            "corrections",
                            "findings",
                            "resolutions",
                            "supplements",
                            "remove_supplements",
                            "impact",
                        )
                    )
                ):
                    result = store.acknowledge(state, response)
                else:
                    result = store.submit_rows(state, response)
        self.s, _ = w.load(work)
        return result


def load_tests(loader, tests, pattern):
    result = unittest.TestSuite()
    for original in (
        test_subtitles.Core,
        test_subtitles.Review,
        test_subtitles.Placement,
        test_decisions.Decisions,
        test_decisions.Supplement,
        test_decisions.Season,
        test_study.Study,
        test_admission.CompactDisplay,
    ):
        cls = type(
            "Native" + original.__name__,
            (NativeProtocol, original),
            {"__module__": __name__},
        )
        result.addTests(loader.loadTestsFromTestCase(cls))
    return result
