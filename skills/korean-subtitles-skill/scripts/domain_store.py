"""Domain rows are authoritative; wide exports are only for whole-work operations.

No flattened JSON paths, field cache, or second active episode document. Scoped
commands access these tables directly rather than calling export_state().
"""

import json
from pathlib import Path
from types import SimpleNamespace

SCHEMA = (
    "CREATE TABLE root_version(root TEXT PRIMARY KEY,context_epoch INTEGER NOT NULL DEFAULT 0,request_epoch INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE evidence_guard(work TEXT NOT NULL,id TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,PRIMARY KEY(work,id))",
    "CREATE TABLE semantic_touch(root TEXT NOT NULL,media TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(root,media,id))",
    "CREATE TABLE unit_reference(work TEXT NOT NULL,anchor TEXT NOT NULL,kind TEXT NOT NULL,ref TEXT NOT NULL,PRIMARY KEY(work,anchor,kind,ref))",
    "CREATE INDEX reference_consumer ON unit_reference(work,kind,ref,anchor)",
    "CREATE TABLE selected_source(work TEXT NOT NULL,language TEXT NOT NULL,body BLOB NOT NULL,PRIMARY KEY(work,language))",
    "CREATE TABLE source_cue(work TEXT NOT NULL,id TEXT NOT NULL,start INTEGER NOT NULL,body BLOB NOT NULL,PRIMARY KEY(work,id))",
    "CREATE INDEX source_order ON source_cue(work,start,id)",
    "CREATE TABLE output_unit(work TEXT NOT NULL,id TEXT NOT NULL,body BLOB NOT NULL,PRIMARY KEY(work,id))",
    "CREATE TABLE supplement(work TEXT NOT NULL,id TEXT NOT NULL,body BLOB NOT NULL,PRIMARY KEY(work,id))",
    "CREATE INDEX supplement_event ON supplement(work,json_extract(body,'$.event'))",
    "CREATE TABLE receipt(work TEXT NOT NULL,id TEXT NOT NULL,body BLOB NOT NULL,PRIMARY KEY(work,id))",
    "CREATE TABLE finding(work TEXT NOT NULL,id TEXT NOT NULL,body BLOB NOT NULL,PRIMARY KEY(work,id))",
    "CREATE TABLE coverage(work TEXT NOT NULL,role TEXT NOT NULL,worker TEXT NOT NULL,id TEXT NOT NULL,signature TEXT NOT NULL,PRIMARY KEY(work,role,worker,id))",
    "CREATE TABLE presentation(work TEXT NOT NULL,worker TEXT NOT NULL,id TEXT NOT NULL,signature TEXT NOT NULL,PRIMARY KEY(work,worker,id))",
    "CREATE TABLE comparison(work TEXT NOT NULL,id TEXT NOT NULL,start INTEGER NOT NULL,kind INTEGER NOT NULL,number INTEGER NOT NULL,body BLOB NOT NULL,signature TEXT NOT NULL,source_signature TEXT,PRIMARY KEY(work,id))",
    "CREATE INDEX comparison_order ON comparison(work,start,kind,number)",
    "CREATE TABLE membership(work TEXT NOT NULL,anchor TEXT NOT NULL,target TEXT NOT NULL,PRIMARY KEY(work,anchor,target))",
    "CREATE INDEX membership_target ON membership(work,target,anchor)",
    "CREATE TABLE supplement_link(work TEXT NOT NULL,supplement TEXT NOT NULL,target TEXT NOT NULL,PRIMARY KEY(work,supplement,target))",
    "CREATE INDEX supplement_target ON supplement_link(work,target,supplement)",
    "CREATE TABLE pending_cue(work TEXT NOT NULL,worker TEXT NOT NULL,id TEXT NOT NULL,start INTEGER NOT NULL,kind INTEGER NOT NULL,number INTEGER NOT NULL,PRIMARY KEY(work,worker,id))",
    "CREATE INDEX pending_order ON pending_cue(work,worker,start,kind,number)",
    "CREATE TABLE pending_total(work TEXT NOT NULL,worker TEXT NOT NULL,count INTEGER NOT NULL CHECK(count>=0),PRIMARY KEY(work,worker))",
    "CREATE TRIGGER pending_insert AFTER INSERT ON pending_cue BEGIN INSERT INTO pending_total VALUES(NEW.work,NEW.worker,1) ON CONFLICT(work,worker) DO UPDATE SET count=count+1; END",
    "CREATE TRIGGER pending_delete AFTER DELETE ON pending_cue BEGIN UPDATE pending_total SET count=count-1 WHERE work=OLD.work AND worker=OLD.worker; END",
    "CREATE TABLE pending_interval(work TEXT NOT NULL,worker TEXT NOT NULL,prefix TEXT NOT NULL,first INTEGER NOT NULL,last INTEGER NOT NULL,PRIMARY KEY(work,worker,prefix,first),CHECK(first<=last))",
    "CREATE INDEX pending_last ON pending_interval(work,worker,prefix,last)",
    "CREATE TABLE metadata(work TEXT NOT NULL,role TEXT NOT NULL,id TEXT NOT NULL,body BLOB NOT NULL,signature TEXT NOT NULL,PRIMARY KEY(work,role,id))",
    "CREATE TABLE metadata_ack(root TEXT NOT NULL,worker TEXT NOT NULL,id TEXT NOT NULL,signature TEXT NOT NULL,PRIMARY KEY(root,worker,id))",
    "CREATE TABLE episode_index(work TEXT PRIMARY KEY,output_hash TEXT,source_epoch INTEGER NOT NULL DEFAULT 0,author_metadata_epoch INTEGER NOT NULL DEFAULT 0,reviewer_metadata_epoch INTEGER NOT NULL DEFAULT 0,expected_outputs INTEGER NOT NULL DEFAULT 0,completed_outputs INTEGER NOT NULL DEFAULT 0,source_count INTEGER NOT NULL DEFAULT 0,supplement_count INTEGER NOT NULL DEFAULT 0,comparison_count INTEGER NOT NULL DEFAULT 0,comparison_epoch INTEGER NOT NULL DEFAULT 0,prerequisite_context INTEGER NOT NULL DEFAULT -1,prerequisite_request INTEGER NOT NULL DEFAULT -1,prerequisite_source INTEGER NOT NULL DEFAULT -1,studied INTEGER NOT NULL DEFAULT 0,study_block TEXT,study_binding TEXT,global_epoch INTEGER NOT NULL DEFAULT 0)",
)
STATE_MAPS = (
    "outputs",
    "supplements",
    "receipts",
    "findings",
    "author_readings",
    "readings",
    "reading_workers",
    "presented",
)


class Store:
    def __init__(self, db, api):
        self.db, self.m = db, SimpleNamespace(**api)

    def rows(self, table, work):
        return {
            key: json.loads(body)
            for key, body in self.db.execute(
                f"SELECT id,body FROM {table} WHERE work=? ORDER BY id", (work,)
            )
        }

    def replace_rows(self, table, work, values):
        self.db.execute(f"DELETE FROM {table} WHERE work=?", (work,))
        self.db.executemany(
            f"INSERT INTO {table}(work,id,body) VALUES(?,?,?)",
            [(work, key, self.m.packed(value)) for key, value in values.items()],
        )

    def export_state(self, path, head):
        """Explicit whole-episode export for full validation/history and Python API."""
        work = str(Path(path).parent)
        result = dict(head)
        sources = {}
        for language, body in self.db.execute(
            "SELECT language,body FROM selected_source WHERE work=?", (work,)
        ):
            value = json.loads(body)
            prefix = "E" if language == "en" else "K"
            value["cues"] = [
                json.loads(r[0])
                for r in self.db.execute(
                    "SELECT body FROM source_cue WHERE work=? AND substr(id,1,1)=? ORDER BY CAST(substr(id,2) AS INTEGER)",
                    (work, prefix),
                )
            ]
            sources[language] = value
        result["sources"] = sources
        for key, table in (
            ("outputs", "output_unit"),
            ("supplements", "supplement"),
            ("receipts", "receipt"),
            ("findings", "finding"),
        ):
            result[key] = self.rows(table, work)
        result.update(author_readings={}, readings={}, reading_workers={}, presented={})
        for role, worker, key, sig in self.db.execute(
            "SELECT role,worker,id,signature FROM coverage WHERE work=?", (work,)
        ):
            if role == "assigned":
                continue
            target = (
                result["author_readings"]
                if role == "author"
                else result["readings"]
                if role == "aggregate"
                else result["reading_workers"].setdefault(worker, {})
            )
            target[key] = sig
        for worker, key, sig in self.db.execute(
            "SELECT worker,id,signature FROM presentation WHERE work=?", (work,)
        ):
            result["presented"].setdefault(worker, {})[key] = sig
        return result

    def save_state(self, path, value):
        """Wide replacement, used only when the caller intentionally handles all state."""
        work = str(Path(path).parent)
        value = dict(value)
        value.pop("_analysis", None)
        source_values = value.pop("sources")
        maps = {key: value.pop(key, {}) for key in STATE_MAPS}
        old_sources = dict(
            self.db.execute(
                "SELECT language,body FROM selected_source WHERE work=?", (work,)
            )
        )
        self.db.execute("DELETE FROM selected_source WHERE work=?", (work,))
        for language, source in source_values.items():
            source = dict(source)
            cues = source.pop("cues")
            body = self.m.packed(source)
            self.db.execute(
                "INSERT INTO selected_source VALUES(?,?,?)", (work, language, body)
            )
            # Selected originals are immutable after study; do not rewrite their cues.
            previous_source = (
                json.loads(old_sources[language]) if language in old_sources else {}
            )
            if previous_source.get("hash") != source.get("hash"):
                prefix = "E" if language == "en" else "K"
                self.db.execute(
                    "DELETE FROM source_cue WHERE work=? AND substr(id,1,1)=?",
                    (work, prefix),
                )
                self.db.executemany(
                    "INSERT INTO source_cue VALUES(?,?,?,?)",
                    [
                        (work, cue["id"], cue["start"], self.m.packed(cue))
                        for cue in cues
                    ],
                )
        for key, table in (
            ("outputs", "output_unit"),
            ("supplements", "supplement"),
            ("receipts", "receipt"),
            ("findings", "finding"),
        ):
            self.replace_rows(table, work, maps[key])
        self.db.execute("DELETE FROM coverage WHERE work=?", (work,))
        readings = [
            (work, "author", "", k, v) for k, v in maps["author_readings"].items()
        ]
        readings += [(work, "aggregate", "", k, v) for k, v in maps["readings"].items()]
        readings += [
            (work, "reviewer", worker, k, v)
            for worker, seen in maps["reading_workers"].items()
            for k, v in seen.items()
        ]
        owners = set(maps["reading_workers"]) | (
            {value["reviewer"]} if value.get("reviewer") else set()
        )
        readings += [(work, "assigned", worker, "", "") for worker in owners]
        self.db.executemany("INSERT INTO coverage VALUES(?,?,?,?,?)", readings)
        self.db.execute("DELETE FROM presentation WHERE work=?", (work,))
        self.db.executemany(
            "INSERT INTO presentation VALUES(?,?,?,?)",
            [
                (work, worker, k, v)
                for worker, seen in maps["presented"].items()
                for k, v in seen.items()
            ],
        )
        self.db.execute(
            "INSERT INTO records VALUES(?,?) ON CONFLICT(path) DO UPDATE SET body=excluded.body",
            (str(path), self.m.packed(value)),
        )
        # Derive indexes from the authoritative domain rows, including unchanged
        # immutable cue rows, never an incoming parallel copy of those cues.
        full = self.export_state(path, value)
        self.rebuild(full)
        current_sources = dict(
            self.db.execute(
                "SELECT language,body FROM selected_source WHERE work=?", (work,)
            )
        )
        if old_sources != current_sources:
            self.db.execute(
                "UPDATE episode_index SET source_epoch=source_epoch+1 WHERE work=?",
                (work,),
            )

    def sync_metadata(self, state, ctx=None):
        work = state["work"]
        state = dict(state)
        refs = list(
            self.db.execute(
                "SELECT DISTINCT kind,ref FROM unit_reference WHERE work=?", (work,)
            )
        )
        state["outputs"] = {
            "facts": {"facts": [ref for kind, ref in refs if kind == "fact"]}
        }
        state["outputs"].update(
            {
                str(n): {"artist_lookup": ref}
                for n, (kind, ref) in enumerate(refs)
                if kind == "lookup"
            }
        )
        state["supplements"] = {
            str(n): {"event": event}
            for n, (event,) in enumerate(
                self.db.execute(
                    "SELECT DISTINCT json_extract(body,'$.event') FROM supplement WHERE work=?",
                    (work,),
                )
            )
            if event
        }
        ctx = self.context_header(state) if ctx is None else ctx
        self.refresh_prerequisites(state, ctx)
        self.db.execute("DELETE FROM evidence_guard WHERE work=?", (work,))
        self.db.executemany(
            "INSERT INTO evidence_guard VALUES(?,?,?,?)",
            [
                (work, key, value["path"], value["hash"])
                for key, value in self.m.input_evidence(state, ctx).items()
                if value.get("path")
            ],
        )
        for role, entries in (
            ("author", self.m.author_metadata(state, ctx)),
            ("reviewer", self.m.metadata(state, ctx)),
        ):
            old = dict(
                self.db.execute(
                    "SELECT id,signature FROM metadata WHERE work=? AND role=?",
                    (work, role),
                )
            )
            new = {k: self.m.digest(v) for k, v in entries.items()}
            if old == new:
                continue
            self.db.execute(
                "DELETE FROM metadata WHERE work=? AND role=?", (work, role)
            )
            self.db.executemany(
                "INSERT INTO metadata VALUES(?,?,?,?,?)",
                [(work, role, k, self.m.packed(v), new[k]) for k, v in entries.items()],
            )
            column = role + "_metadata_epoch"
            self.db.execute(
                f"UPDATE episode_index SET {column}={column}+1 WHERE work=?", (work,)
            )

    def rebuild(self, state):
        """Rebuild derived comparison indexes at an explicitly wide operation."""
        m = self.m
        work = state["work"]
        analysis = m.Analysis(state)
        previous_signatures = dict(
            self.db.execute("SELECT id,signature FROM comparison WHERE work=?", (work,))
        )
        self.db.execute(
            "INSERT INTO episode_index(work,output_hash) VALUES(?,?) ON CONFLICT(work) DO UPDATE SET output_hash=excluded.output_hash",
            (work, analysis.output_sha256),
        )
        if previous_signatures != analysis.sig:
            self.db.execute(
                "UPDATE episode_index SET comparison_epoch=comparison_epoch+1 WHERE work=?",
                (work,),
            )
        self.db.execute("DELETE FROM comparison WHERE work=?", (work,))
        self.db.executemany(
            "INSERT INTO comparison VALUES(?,?,?,?,?,?,?,?)",
            [
                (
                    work,
                    k,
                    v["start"],
                    "EKOS".index(k[0]),
                    int(k[1:]),
                    m.packed(v),
                    analysis.sig[k],
                    m.digest(state["supplements"][k] if k[0] == "S" else v)
                    if k[0] in "EKS"
                    else None,
                )
                for k, v in analysis.u.items()
            ],
        )
        self.db.execute("DELETE FROM membership WHERE work=?", (work,))
        self.db.executemany(
            "INSERT INTO membership VALUES(?,?,?)",
            [
                (work, en, k)
                for en, u in state["outputs"].items()
                for k in {en, "O" + en[1:]} | set(m.expand(u["ko"]))
            ],
        )
        self.db.execute("DELETE FROM supplement_link WHERE work=?", (work,))
        self.db.executemany(
            "INSERT INTO supplement_link VALUES(?,?,?)",
            [
                (work, key, target)
                for key, s in state["supplements"].items()
                for target in m.expand(s["links"])
            ],
        )
        self.db.execute("DELETE FROM unit_reference WHERE work=?", (work,))
        for en, unit in state["outputs"].items():
            self.unit_references(work, en, unit)
        self.db.execute(
            "UPDATE episode_index SET expected_outputs=?,completed_outputs=?,source_count=?,supplement_count=?,comparison_count=? WHERE work=?",
            (
                sum(k.startswith("E") for k in self.m.source_ids(state)),
                len(state["outputs"]),
                sum(k[0] in "EK" for k in analysis.u),
                len(state["supplements"]),
                len(analysis.u),
                work,
            ),
        )
        self.sync_metadata(state, analysis.ctx)
        self.db.execute("DELETE FROM pending_interval WHERE work=?", (work,))
        self.db.execute("DELETE FROM pending_cue WHERE work=?", (work,))
        readers = dict(state["reading_workers"])
        if state.get("reviewer"):
            readers.setdefault(state["reviewer"], {})
        author_hashes = {
            key: m.digest(state["supplements"][key] if key[0] == "S" else value)
            for key, value in analysis.u.items()
            if key[0] in "EKS"
        }
        for worker, seen, current in [
            ("", state["readings"], analysis.sig),
            ("\x00author", state["author_readings"], author_hashes),
            *((worker, seen, analysis.sig) for worker, seen in readers.items()),
        ]:
            pending = {k for k, sig in current.items() if seen.get(k) != sig}
            self.db.executemany(
                "INSERT INTO pending_cue VALUES(?,?,?,?,?,?)",
                [
                    (
                        work,
                        worker,
                        key,
                        analysis.u[key]["start"],
                        "EKOS".index(key[0]),
                        int(key[1:]),
                    )
                    for key in pending
                ],
            )
            for chunk in m.compact(pending):
                ends = chunk.split("-")
                first, last = int(ends[0][1:]), int(ends[-1][1:])
                self.db.execute(
                    "INSERT INTO pending_interval VALUES(?,?,?,?,?)",
                    (work, worker, chunk[0], first, last),
                )

    def roles(self, path, value):
        root = str(Path(path).parent)
        value = dict(value)
        metadata = value.pop("metadata", {})
        self.db.execute("DELETE FROM metadata_ack WHERE root=?", (root,))
        self.db.executemany(
            "INSERT INTO metadata_ack VALUES(?,?,?,?)",
            [(root, w, k, v) for w, seen in metadata.items() for k, v in seen.items()],
        )
        return value

    def export_roles(self, path, value):
        result = dict(value)
        result["metadata"] = {}
        for worker, key, sig in self.db.execute(
            "SELECT worker,id,signature FROM metadata_ack WHERE root=?",
            (str(Path(path).parent),),
        ):
            result["metadata"].setdefault(worker, {})[key] = sig
        return result

    def subtract_pending(self, work, worker, prefix, number):
        self.db.execute(
            "DELETE FROM pending_cue WHERE work=? AND worker=? AND id=?",
            (work, worker, prefix + str(number)),
        )
        row = self.db.execute(
            "SELECT first,last FROM pending_interval WHERE work=? AND worker=? AND prefix=? AND first<=? AND last>=? ORDER BY first DESC LIMIT 1",
            (work, worker, prefix, number, number),
        ).fetchone()
        if row is None:
            return
        first, last = row
        self.db.execute(
            "DELETE FROM pending_interval WHERE work=? AND worker=? AND prefix=? AND first=?",
            (work, worker, prefix, first),
        )
        if first < number:
            self.db.execute(
                "INSERT INTO pending_interval VALUES(?,?,?,?,?)",
                (work, worker, prefix, first, number - 1),
            )
        if number < last:
            self.db.execute(
                "INSERT INTO pending_interval VALUES(?,?,?,?,?)",
                (work, worker, prefix, number + 1, last),
            )

    def pending(self, work, worker):
        rows = self.db.execute(
            "SELECT prefix,first,last FROM pending_interval WHERE work=? AND worker=? ORDER BY prefix,first",
            (work, worker),
        )
        return [f"{p}{a}" + (f"-{p}{b}" if a != b else "") for p, a, b in rows]

    def header(self, work):
        work = str(Path(work).resolve())
        row = self.db.execute(
            "SELECT body FROM records WHERE path=?", (str(Path(work) / "state.json"),)
        ).fetchone()
        self.m.require(row is not None, "Run prepare first")
        state = json.loads(row[0])
        self.m.require(
            state.get("engine") == self.m.ENGINE_HASH,
            "Engine changed; prepare fresh without approval migration",
        )
        self.m.require(
            state["contract"] == self.m.CONTRACT, "Old contract; prepare fresh"
        )
        state["sources"] = {
            lang: {**json.loads(body), "cues": []}
            for lang, body in self.db.execute(
                "SELECT language,body FROM selected_source WHERE work=?", (work,)
            )
        }
        return state

    def save_header(self, state):
        # Sources/cues and active domain rows never enter this scalar header.
        head = {
            k: v
            for k, v in state.items()
            if k not in {*STATE_MAPS, "sources", "_analysis", "history"}
        }
        self.db.execute(
            "UPDATE records SET body=? WHERE path=?",
            (self.m.packed(head), str(Path(state["work"]) / "state.json")),
        )

    def comparison_rows(self, work, ids):
        found = {}
        for key in ids:
            row = self.db.execute(
                "SELECT body,signature FROM comparison WHERE work=? AND id=?",
                (work, key),
            ).fetchone()
            self.m.require(
                row is not None, "Unknown or unavailable comparison ID: " + key
            )
            found[key] = (json.loads(row[0]), row[1])
        return found

    def meta_entries(self, state, role, worker, changed=False):
        root = str(Path(state["context_path"]).parent)
        query = "SELECT m.id,m.body,m.signature FROM metadata m LEFT JOIN metadata_ack a ON a.root=? AND a.worker=? AND a.id=m.id WHERE m.work=? AND m.role=?"
        if changed:
            query += " AND (a.signature IS NULL OR a.signature!=m.signature)"
        return {
            key: (json.loads(body), signature)
            for key, body, signature in self.db.execute(
                query, (root, worker, state["work"], role)
            )
        }

    def metadata_ack(self, state, worker, pins):
        root = str(Path(state["context_path"]).parent)
        current = [
            (root, worker, key, sig)
            for key, sig in pins.items()
            if self.db.execute(
                "SELECT 1 FROM metadata WHERE work=? AND id=? AND signature=?",
                (state["work"], key, sig),
            ).fetchone()
        ]
        self.db.executemany(
            "INSERT INTO metadata_ack VALUES(?,?,?,?) ON CONFLICT(root,worker,id) DO UPDATE SET signature=excluded.signature",
            current,
        )

    def current_metadata_pins(self, state, pins):
        return {
            key: sig
            for key, sig in pins.items()
            if self.db.execute(
                "SELECT 1 FROM metadata WHERE work=? AND id=? AND signature=?",
                (state["work"], key, sig),
            ).fetchone()
        }

    def issue(self, state, kind, worker, body, pins, template):
        import time

        m = self.m
        issued = time.time()
        index = self.db.execute(
            "SELECT source_epoch,author_metadata_epoch,reviewer_metadata_epoch FROM episode_index WHERE work=?",
            (state["work"],),
        ).fetchone()
        pins["_epochs"] = {
            "source": index[0],
            "metadata": index[1] if kind == "author" else index[2],
        }
        receipt = {"kind": kind, "worker": worker, "pins": pins, "issued": issued}
        key = m.digest(receipt)[:20]
        self.db.execute(
            "INSERT INTO receipt VALUES(?,?,?)", (state["work"], key, m.packed(receipt))
        )
        result = {
            "mode": kind,
            **body,
            "packet": key,
            "submit": {"packet": key, **template},
        }
        metrics = state["metrics"]
        metrics["view_calls"] += 1
        metrics["cue_presentations"] += len(m.displayed_cues(body))
        size = len(m.packed(result))
        metrics["presented_bytes"] += size
        if kind == "metadata":
            metrics["metadata_calls"] += 1
            metrics["metadata_bytes"] += size
        self.save_header(state)
        return result

    def choose_ids(self, work, args, role, worker):
        m = self.m
        m.require(
            args.offset >= 0 and args.limit > 0,
            "Positive page size and nonnegative offset required",
        )
        if args.pending:
            owner = "\x00author" if role == "author" else worker
            base = "FROM pending_cue p JOIN comparison c ON c.work=p.work AND c.id=p.id WHERE p.work=? AND p.worker=?"
            params = [work, owner]
            order = "p.start,p.kind,p.number"
            row = self.db.execute(
                "SELECT count FROM pending_total WHERE work=? AND worker=?",
                (work, owner),
            ).fetchone()
            count = row[0] if row else 0
        else:
            base = "FROM comparison c WHERE c.work=?"
            params = [work]
            order = "c.start,c.kind,c.number"
            if role == "author":
                base += " AND substr(c.id,1,1) IN ('E','K','S')"
            row = self.db.execute(
                "SELECT source_count+supplement_count,comparison_count FROM episode_index WHERE work=?",
                (work,),
            ).fetchone()
            count = row[0] if role == "author" else row[1]
        if args.ids:
            ids = m.expand(args.ids.split(","))
            for key in ids:
                m.require(
                    self.db.execute(
                        "SELECT 1 " + base + " AND c.id=?", (*params, key)
                    ).fetchone(),
                    "Unknown or unavailable requested ID: " + key,
                )
        else:
            ids = [
                row[0]
                for row in self.db.execute(
                    "SELECT c.id " + base + " ORDER BY " + order + " LIMIT ? OFFSET ?",
                    (*params, args.limit, args.offset),
                )
            ]
        return ids, count

    def handoff(self, items, worker, limit=200):
        """Choose an eligible job from canonical phases, never a language verdict.

        Issue the actual first packet atomically with ownership. The caller is
        an already running worker; no coordinator consumes its linguistic body.
        """
        m = self.m
        m.require(
            m.nonempty(worker) and limit > 0,
            "Actual worker and positive page size required",
        )
        m.require(items, "No prepared items")
        first = self.header(items[0])
        # An item locator cannot narrow the request or reset session job counts.
        headers = [self.header(work) for work in m.request_manifest(first)["items"]]
        self.collection_metadata(headers[0])
        observed = [(state, self.status(state)) for state in headers]
        used = {
            state["work"]
            for state in headers
            if worker in (state["author"], state["reviewer"])
        }
        choices = []
        for number, (state, status) in enumerate(observed):
            if status["approved"]:
                continue
            role = None
            priority = 3
            if status["phase"] == "authoring" and state["author"] == worker:
                role, priority = "author", 0
            elif status["draft_ready"] and state["reviewer"] == worker:
                role, priority = "reviewer", 0
            elif (
                status["draft_ready"]
                and state["reviewer"] is None
                and state["author"] != worker
            ):
                role, priority = "reviewer", 1
            elif status["phase"] == "authoring" and state["author"] is None:
                role, priority = "author", 2
            if role and (state["work"] in used or len(used) < 2):
                choices.append((priority, number, role, state))
        ready = [
            {"project": state["work"], "role": "reviewer", "author": state["author"]}
            if status["draft_ready"]
            else {"project": state["work"], "role": "author", "author": None}
            for state, status in observed
            if not status["approved"]
            and (
                (status["draft_ready"] and state["reviewer"] is None)
                or (status["phase"] == "authoring" and state["author"] is None)
            )
        ]
        if not choices:
            return {
                "ready": ready,
                "action": "done"
                if all(status["approved"] for _, status in observed)
                else "retire"
                if len(used) >= 2
                else "wait",
                "worker": worker,
                "session_items": len(used),
                "remaining_items": sum(
                    not status["approved"] for _, status in observed
                ),
                "blocking": [
                    {
                        "project": state["work"],
                        "phase": status["phase"],
                        "reasons": status["blocking"],
                    }
                    for state, status in observed
                    if status["phase"]
                    in {"collecting", "study_required", "studying", "held"}
                ],
            }
        _, _, role, state = min(choices, key=lambda row: row[:2])
        args = SimpleNamespace(
            role=role,
            worker=worker,
            ids=None,
            offset=0,
            limit=limit,
            pending=True,
            reassign=None,
        )
        result = self.view(state, args)
        before = len(m.packed(result))
        result["assignment"] = {
            "project": state["work"],
            "role": role,
            "worker": worker,
            "session_items": len(used | {state["work"]}),
        }
        state["metrics"]["presented_bytes"] += len(m.packed(result)) - before
        m.save_template(state["work"], True, result, [state], persist=False)
        self.save_header(state)
        return result

    def view(self, state, args):
        m = self.m
        work = state["work"]
        role = args.role
        worker = args.worker
        m.require(
            m.nonempty(worker) and role in {"author", "metadata", "reviewer"},
            "Use actual worker and supported role",
        )
        m.verify_inputs(state, evidence=self.guard_evidence(state, None))
        prerequisite = self.require_study(state)
        if role == "author":
            m.require(
                state["author"] in (None, worker), "Keep the assigned episode author"
            )
            m.require(
                set(state["sources"]) == {"en", "ko"}, "Select both sources first"
            )
            if not self.db.execute(
                "SELECT 1 FROM coverage WHERE work=? AND role='author' LIMIT 1", (work,)
            ).fetchone():
                manifest = m.request_manifest(state)
                m.require(
                    not any(
                        x["decision"] == "held" for x in manifest.get("excluded", [])
                    ),
                    "Inspection-held items remain",
                )
                for other in manifest["items"]:
                    header = self.header(other)
                    m.require(
                        set(header["sources"]) == {"en", "ko"},
                        "Complete whole-request collection before correction",
                    )
                    m.verify_inputs(header, evidence={})
            state["author"] = worker
            chosen, count = self.choose_ids(work, args, role, worker)
            actual = self.comparison_rows(work, chosen)
            for key in chosen:
                if key.startswith("S"):
                    actual[key] = (
                        json.loads(
                            self.db.execute(
                                "SELECT body FROM supplement WHERE work=? AND id=?",
                                (work, key),
                            ).fetchone()[0]
                        ),
                        actual[key][1],
                    )
            rows = []
            pins = {}
            for key in chosen:
                if key.startswith("E"):
                    row = self.db.execute(
                        "SELECT body FROM output_unit WHERE work=? AND id=?",
                        (work, key),
                    ).fetchone()
                    unit = json.loads(row[0]) if row else None
                    rows.append(m.editable_row(key, unit))
                    pins[key] = m.digest(unit)
            meta = self.meta_entries(state, "author", worker)
            return self.issue(
                state,
                role,
                worker,
                {
                    "context_changes": {
                        k: v[0]
                        for k, v in self.meta_entries(
                            state, "author", worker, True
                        ).items()
                    },
                    "cues": [actual[k][0] for k in chosen],
                    "remaining": 0 if count is None else count - len(chosen),
                },
                {
                    "sources": {k: m.digest(actual[k][0]) for k in chosen},
                    "versions": {k: actual[k][1] for k in chosen},
                    "outputs": pins,
                    "global": prerequisite["global_epoch"],
                    "metadata": {k: v[1] for k, v in meta.items()},
                },
                {"default_kind": None, "rows": rows},
            )
        m.independent(state, worker)
        m.require(
            self.sealed(state),
            "Complete episode correction and seal before review",
        )
        if role == "metadata":
            entries = {
                **self.meta_entries(state, "author", worker, True),
                **self.meta_entries(state, "reviewer", worker, True),
            }
            keys = m.choose(args, sorted(entries))
            return self.issue(
                state,
                role,
                worker,
                {
                    "entries": {k: entries[k][0] for k in keys},
                    "remaining": len(entries) - len(keys),
                },
                {k: entries[k][1] for k in keys},
                {},
            )
        if state["reviewer"] not in (None, worker):
            m.require(
                m.nonempty(getattr(args, "reassign", None)),
                "Episode reviewer already assigned",
            )
            state["approval"] = None
            self.event(
                state,
                {
                    "kind": "reviewer_handover",
                    "previous": state["reviewer"],
                    "worker": worker,
                    "reason": args.reassign,
                },
            )
        state["reviewer"] = worker
        # Initialize personal intervals once; an empty interval set is valid completion.
        exists = self.db.execute(
            "SELECT 1 FROM coverage WHERE work=? AND role='assigned' AND worker=?",
            (work, worker),
        ).fetchone()
        if not exists:
            self.db.execute(
                "INSERT INTO coverage VALUES(?,'assigned',?,'', '')", (work, worker)
            )
            self.db.execute(
                "INSERT INTO pending_cue SELECT work,?,id,start,kind,number FROM comparison WHERE work=?",
                (worker, work),
            )
            ids = [
                r[0]
                for r in self.db.execute(
                    "SELECT id FROM comparison WHERE work=?", (work,)
                )
            ]
            for chunk in m.compact(ids):
                ends = chunk.split("-")
                self.db.execute(
                    "INSERT INTO pending_interval VALUES(?,?,?,?,?)",
                    (work, worker, chunk[0], int(ends[0][1:]), int(ends[-1][1:])),
                )
        chosen, count = self.choose_ids(work, args, role, worker)
        anchors = {k if k[0] == "E" else "E" + k[1:] for k in chosen if k[0] in "EO"}
        visible = set(chosen)
        supplements = {}
        for key in chosen:
            anchors.update(
                r[0]
                for r in self.db.execute(
                    "SELECT anchor FROM membership WHERE work=? AND target=?",
                    (work, key),
                )
            )
            suppids = [
                r[0]
                for r in self.db.execute(
                    "SELECT supplement FROM supplement_link WHERE work=? AND target=?",
                    (work, key),
                )
            ]
            if key.startswith("S"):
                suppids.append(key)
            for sid in suppids:
                row = self.db.execute(
                    "SELECT body FROM supplement WHERE work=? AND id=?", (work, sid)
                ).fetchone()
                if row:
                    supplements[sid] = json.loads(row[0])
        for sid, supp in supplements.items():
            visible.update({sid} | set(m.expand(supp["links"])))
        units = {}
        for en in anchors:
            row = self.db.execute(
                "SELECT body FROM output_unit WHERE work=? AND id=?", (work, en)
            ).fetchone()
            m.require(row is not None, "Unknown comparison anchor")
            units[en] = json.loads(row[0])
            visible.update({en} | set(m.expand(units[en]["ko"])))
            if self.db.execute(
                "SELECT 1 FROM comparison WHERE work=? AND id=?", (work, "O" + en[1:])
            ).fetchone():
                visible.add("O" + en[1:])
        actual = self.comparison_rows(work, visible)
        u = {k: v[0] for k, v in actual.items()}
        findings = {
            k: f
            for k, f in self.rows("finding", work).items()
            if f["status"] == "open" and set(f["ids"]) & visible
        }
        body = {
            **m.comparison_body(u, units, supplements, anchors, visible),
            "context_changes": {
                k: v[0]
                for k, v in self.meta_entries(state, "reviewer", worker, True).items()
            },
            "findings": findings,
            "remaining": 0,
        }
        actual_display = m.displayed_cues(body)
        if args.pending:
            pending_display = sum(
                self.db.execute(
                    "SELECT 1 FROM coverage WHERE work=? AND role='reviewer' AND worker=? AND id=? AND signature=?",
                    (work, worker, key, actual[key][1]),
                ).fetchone()
                is None
                for key in actual_display
            )
            body["remaining"] = max(0, count - pending_display)
        else:
            body["remaining"] = max(0, count - len(actual_display))
        for key in actual_display:
            old = self.db.execute(
                "SELECT signature FROM presentation WHERE work=? AND worker=? AND id=?",
                (work, worker, key),
            ).fetchone()
            state["metrics"]["repeat_presentations"] += bool(
                old and old[0] == actual[key][1]
            )
            self.db.execute(
                "INSERT INTO presentation VALUES(?,?,?,?) ON CONFLICT(work,worker,id) DO UPDATE SET signature=excluded.signature",
                (work, worker, key, actual[key][1]),
            )
        return self.issue(
            state,
            role,
            worker,
            body,
            {
                "cues": {k: actual[k][1] for k in actual_display},
                "anchors": sorted(anchors),
                "metadata": {
                    k: v[1]
                    for k, v in self.meta_entries(state, "reviewer", worker).items()
                },
            },
            {"corrections": [], "findings": [], "resolutions": {}, "impact": None},
        )

    def event(self, state, event):
        path = str(Path(state["work"]) / "state.json")
        index = state.get("_history_count", 0)
        self.db.execute(
            "INSERT INTO events VALUES(?,?,?)", (path, index, self.m.packed(event))
        )
        state["_history_count"] = index + 1

    def acknowledge(self, state, response):
        """Version-only reviewer/metadata acknowledgement, proportional to receipt."""
        import time

        m = self.m
        work = state["work"]
        key = response.get("packet")
        row = self.db.execute(
            "SELECT body FROM receipt WHERE work=? AND id=?", (work, key)
        ).fetchone()
        m.require(row is not None, "Use a current generated packet")
        rec = json.loads(row[0])
        kind, worker, pins = rec["kind"], rec["worker"], rec["pins"]
        m.require(
            kind in {"metadata", "reviewer"},
            "Author output requires its scoped submission",
        )
        m.require(
            set(response)
            <= {"packet", "corrections", "findings", "resolutions", "impact"}
            and not any(
                response.get(k)
                for k in ("corrections", "findings", "resolutions", "impact")
            ),
            "A changed judgment requires scoped revision",
        )
        m.verify_inputs(state, evidence=self.guard_evidence(state, None))
        self.require_study(state)
        m.independent(state, worker)
        m.require(
            self.sealed(state),
            "Current completed source correction required",
        )
        epochs = self.db.execute(
            "SELECT source_epoch,reviewer_metadata_epoch,output_hash FROM episode_index WHERE work=?",
            (work,),
        ).fetchone()
        m.require(
            pins["_epochs"]["source"] == epochs[0],
            "Stale packet context",
        )
        if kind == "metadata":
            m.require(
                set(response) == {"packet"},
                "Metadata acknowledgement contains no judgments",
            )
            actual = {k: v for k, v in pins.items() if k != "_epochs"}
            for ident, sig in actual.items():
                m.require(
                    self.db.execute(
                        "SELECT 1 FROM metadata WHERE work=? AND id=? AND signature=?",
                        (work, ident, sig),
                    ).fetchone(),
                    "Stale metadata receipt",
                )
            self.metadata_ack(state, worker, actual)
            result = {"metadata_acknowledged": list(actual)}
        else:
            m.require(
                worker == state["reviewer"], "Assigned independent reviewer required"
            )
            for ident, sig in pins["cues"].items():
                row = self.db.execute(
                    "SELECT signature FROM comparison WHERE work=? AND id=?",
                    (work, ident),
                ).fetchone()
                m.require(row and row[0] == sig, "Stale comparison packet")
            self.db.executemany(
                "INSERT INTO coverage VALUES(?,?,?,?,?) ON CONFLICT(work,role,worker,id) DO UPDATE SET signature=excluded.signature",
                [
                    (work, role, who, ident, sig)
                    for ident, sig in pins["cues"].items()
                    for role, who in (("reviewer", worker), ("aggregate", ""))
                ],
            )
            for ident in pins["cues"]:
                self.subtract_pending(work, worker, ident[0], int(ident[1:]))
                self.subtract_pending(work, "", ident[0], int(ident[1:]))
            self.metadata_ack(state, worker, pins["metadata"])
            result = {
                "changed": [],
                "supplements_added": [],
                "supplements_changed": [],
                "supplements_removed": [],
                "supplements_current": [
                    r[0]
                    for r in self.db.execute(
                        "SELECT id FROM supplement WHERE work=? ORDER BY id", (work,)
                    )
                ],
                "pending": self.pending(work, worker),
                "output_sha256": epochs[2],
            }
        result["packet_elapsed_ms"] = round((time.time() - rec["issued"]) * 1000, 1)
        self.db.execute("DELETE FROM receipt WHERE work=? AND id=?", (work, key))
        state["revision"] += 1
        self.event(
            state,
            {
                "revision": state["revision"],
                "kind": kind,
                "worker": worker,
                "judgment": response,
                "result": result,
                "previous_outputs": {},
                "previous_supplements": {},
                "previous_sources": {},
            },
        )
        self.save_header(state)
        return result

    def unit_references(self, work, en, unit):
        self.db.execute(
            "DELETE FROM unit_reference WHERE work=? AND anchor=?", (work, en)
        )
        self.db.executemany(
            "INSERT INTO unit_reference VALUES(?,?,?,?)",
            [(work, en, "fact", ref) for ref in unit.get("facts", [])]
            + [(work, en, "lookup", ref) for ref in self.m.lookup_refs(unit)],
        )

    def add_pending(self, work, worker, key):
        number = int(key[1:])
        prefix = key[0]
        self.db.execute(
            "INSERT INTO pending_cue SELECT work,?,id,start,kind,number FROM comparison WHERE work=? AND id=? ON CONFLICT(work,worker,id) DO UPDATE SET start=excluded.start,kind=excluded.kind,number=excluded.number",
            (worker, work, key),
        )
        rows = self.db.execute(
            "SELECT first,last FROM pending_interval WHERE work=? AND worker=? AND prefix=? AND first<=? AND last>=?",
            (work, worker, prefix, number + 1, number - 1),
        ).fetchall()
        first = min([number] + [r[0] for r in rows])
        last = max([number] + [r[1] for r in rows])
        for old, _ in rows:
            self.db.execute(
                "DELETE FROM pending_interval WHERE work=? AND worker=? AND prefix=? AND first=?",
                (work, worker, prefix, old),
            )
        self.db.execute(
            "INSERT INTO pending_interval VALUES(?,?,?,?,?)",
            (work, worker, prefix, first, last),
        )

    def linked_members(self, work, target):
        # Materialize the small inverse edge set before following anchors. A
        # free self-join may choose a work-wide scan without ANALYZE statistics.
        return self.db.execute(
            "SELECT target FROM membership WHERE work=? AND anchor IN (SELECT anchor FROM membership WHERE work=? AND target=?)",
            (work, work, target),
        )

    def linked_units(self, work, target):
        return self.db.execute(
            "SELECT id,body FROM output_unit WHERE work=? AND id IN (SELECT anchor FROM membership WHERE work=? AND target=?) ORDER BY id",
            (work, work, target),
        )

    def reference_members(self, work, kind, ref):
        return self.db.execute(
            "SELECT target FROM membership WHERE work=? AND anchor IN (SELECT anchor FROM unit_reference WHERE work=? AND kind=? AND ref=?)",
            (work, work, kind, ref),
        )

    def refresh_signatures(self, state, ids, ctx):
        m = self.m
        work = state["work"]
        # A declared touch participates in its unit marker. Close through actual
        # unit/supplement edges, then compare signatures before invalidating.
        ids = set(ids)
        for ident in list(ids):
            if ident.startswith("S"):
                ids.update(
                    row[0]
                    for row in self.db.execute(
                        "SELECT target FROM supplement_link WHERE work=? AND supplement=?",
                        (work, ident),
                    )
                )
        for ident in list(ids):
            ids.update(row[0] for row in self.linked_members(work, ident))
        self.load_signature_touches(state, ids, ctx)
        touches = ctx["touches"].get(state["media"], {})
        workers = {""} | {
            r[0]
            for r in self.db.execute(
                "SELECT DISTINCT worker FROM coverage WHERE work=? AND role IN ('reviewer','assigned')",
                (work,),
            )
        }
        changed = False
        for key in ids:
            row = self.db.execute(
                "SELECT body,signature FROM comparison WHERE work=? AND id=?",
                (work, key),
            ).fetchone()
            if not row:
                for worker in workers:
                    self.subtract_pending(work, worker, key[0], int(key[1:]))
                self.db.execute(
                    "DELETE FROM coverage WHERE work=? AND id=?", (work, key)
                )
                continue
            signature = m.digest(
                {
                    "cue": json.loads(row[0]),
                    "global": ctx["global_revision"],
                    "touch": touches.get(key, 0),
                }
            )
            for en, body in self.linked_units(work, key):
                unit = json.loads(body)
                marker = m.unit_marker(ctx, state["media"], unit)
                signature = m.digest([signature, marker])
            supplements = {
                r[0]
                for r in self.db.execute(
                    "SELECT supplement FROM supplement_link WHERE work=? AND target=?",
                    (work, key),
                )
            }
            if key.startswith("S"):
                supplements.add(key)
            for sid in sorted(supplements):
                record = self.db.execute(
                    "SELECT body FROM supplement WHERE work=? AND id=?", (work, sid)
                ).fetchone()
                if record:
                    supp = json.loads(record[0])
                    signature = m.digest([signature, m.supplement_marker(ctx, supp)])
            if signature != row[1]:
                changed = True
                self.db.execute(
                    "UPDATE comparison SET signature=? WHERE work=? AND id=?",
                    (signature, work, key),
                )
                for worker in workers:
                    self.add_pending(work, worker, key)

        if changed:
            self.db.execute(
                "UPDATE episode_index SET comparison_epoch=comparison_epoch+1 WHERE work=?",
                (work,),
            )

    def put_comparison(self, work, key, body, source=None):
        if not self.db.execute(
            "SELECT 1 FROM comparison WHERE work=? AND id=?", (work, key)
        ).fetchone():
            self.db.execute(
                "UPDATE episode_index SET comparison_count=comparison_count+1,supplement_count=supplement_count+? WHERE work=?",
                (int(key[0] == "S"), work),
            )
        self.db.execute(
            "INSERT INTO comparison VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(work,id) DO UPDATE SET start=excluded.start,body=excluded.body,source_signature=excluded.source_signature",
            (
                work,
                key,
                body["start"],
                "EKOS".index(key[0]),
                int(key[1:]),
                self.m.packed(body),
                "",
                self.m.digest(source) if source is not None else None,
            ),
        )

    def scoped_state(self, state, source_ids, anchors, supplements):
        result = dict(state)
        sources = {
            lang: {**value, "cues": []} for lang, value in state["sources"].items()
        }
        for ident in source_ids:
            if ident[0] not in "EK":
                continue
            row = self.db.execute(
                "SELECT body FROM source_cue WHERE work=? AND id=?",
                (state["work"], ident),
            ).fetchone()
            self.m.require(row is not None, "Unknown original source: " + ident)
            sources["en" if ident[0] == "E" else "ko"]["cues"].append(
                json.loads(row[0])
            )
        result["sources"] = sources
        result["outputs"] = {}
        for anchor in anchors:
            row = self.db.execute(
                "SELECT body FROM output_unit WHERE work=? AND id=?",
                (state["work"], anchor),
            ).fetchone()
            if row:
                result["outputs"][anchor] = json.loads(row[0])
        result["supplements"] = {}
        for sid in supplements:
            row = self.db.execute(
                "SELECT body FROM supplement WHERE work=? AND id=?",
                (state["work"], sid),
            ).fetchone()
            if row:
                result["supplements"][sid] = json.loads(row[0])
        return result

    def compile_current(self, state):
        m = self.m
        work = state["work"]
        count = self.db.execute(
            "SELECT expected_outputs,completed_outputs FROM episode_index WHERE work=?",
            (work,),
        ).fetchone()
        if count[0] != count[1]:
            self.db.execute(
                "UPDATE episode_index SET output_hash=NULL WHERE work=?", (work,)
            )
            return
        final = [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT body FROM comparison WHERE work=? AND substr(id,1,1) IN ('O','S') ORDER BY start,CASE substr(id,1,1) WHEN 'S' THEN 1 ELSE 0 END,number",
                (work,),
            )
        ]
        if state.get("patch"):
            # Full baseline bytes must be inspected to enforce outside-byte preservation.
            raw = m.output_raw(m.load(work)[0], final)
        else:
            raw = m.render(final, state["duration"]) if final else None
        self.db.execute(
            "UPDATE episode_index SET output_hash=? WHERE work=?",
            (m.digest(raw) if raw else None, work),
        )

    def submit_rows(self, state, response):
        """Apply only declared rows and real correspondence/semantic consumers."""
        import time

        m = self.m
        work = state["work"]
        key = response.get("packet")
        receipt = self.db.execute(
            "SELECT body FROM receipt WHERE work=? AND id=?", (work, key)
        ).fetchone()
        m.require(receipt is not None, "Use a current generated packet")
        rec = json.loads(receipt[0])
        kind, worker, pins = rec["kind"], rec["worker"], rec["pins"]
        m.require(
            kind in {"author", "reviewer"}, "Use the metadata acknowledgement operation"
        )
        m.require(
            set(response)
            <= {
                "packet",
                "default_kind",
                "rows",
                "corrections",
                "supplements",
                "remove_supplements",
                "findings",
                "resolutions",
                "impact",
            },
            "Only language judgments/results belong in packet submission",
        )
        ctx = self.context_header(state)
        m.verify_inputs(state, evidence=self.guard_evidence(state, ctx))
        m.study_ready(state, ctx)
        role = "author" if kind == "author" else "reviewer"
        epoch = self.db.execute(
            "SELECT source_epoch,"
            + role
            + "_metadata_epoch FROM episode_index WHERE work=?",
            (work,),
        ).fetchone()
        m.require(
            pins["_epochs"]["source"] == epoch[0],
            "Stale packet context",
        )
        ctx = self.context_header(state)
        if kind == "author":
            m.require(
                worker == state["author"] and pins["global"] == ctx["global_revision"],
                "Assigned author and current source context required",
            )
            m.require(
                not any(
                    k in response for k in ("corrections", "findings", "resolutions")
                ),
                "Author submits correction rows, not reviewer judgments",
            )
            for ident, sig in pins["sources"].items():
                row = self.db.execute(
                    "SELECT source_signature FROM comparison WHERE work=? AND id=?",
                    (work, ident),
                ).fetchone()
                m.require(row and row[0] == sig, "Stale original/supplement")
            for ident, sig in pins["versions"].items():
                row = self.db.execute(
                    "SELECT signature FROM comparison WHERE work=? AND id=?",
                    (work, ident),
                ).fetchone()
                m.require(
                    row and row[0] == sig, "Stale source/correspondence interpretation"
                )
            for ident, sig in pins["outputs"].items():
                row = self.db.execute(
                    "SELECT body FROM output_unit WHERE work=? AND id=?", (work, ident)
                ).fetchone()
                m.require(
                    m.digest(json.loads(row[0]) if row else None) == sig,
                    "Stale output row",
                )
            rows = response.get("rows", [])
            allowed = pins["outputs"]
            displayed = pins["sources"]
        else:
            m.independent(state, worker)
            m.require(
                worker == state["reviewer"]
                and m.sealed(state, self.context_header(state)),
                "Assigned reviewer and completed author correction required",
            )
            m.require(
                "rows" not in response and "default_kind" not in response,
                "Reviewer submits corrections",
            )
            for ident, sig in pins["cues"].items():
                row = self.db.execute(
                    "SELECT signature FROM comparison WHERE work=? AND id=?",
                    (work, ident),
                ).fetchone()
                m.require(row and row[0] == sig, "Stale comparison packet")
            rows = response.get("corrections", [])
            allowed = pins["anchors"]
            displayed = pins["cues"]
        anchors = {r.get("id") for r in rows}
        m.require(None not in anchors, "Rows need English IDs")
        source_ids = set(displayed) | anchors
        for row in rows:
            old = self.db.execute(
                "SELECT body FROM output_unit WHERE work=? AND id=?", (work, row["id"])
            ).fetchone()
            previous = json.loads(old[0]) if old else {}
            source_ids.update(m.expand(row.get("ko", previous.get("ko", []))))
        suppids = {r["id"] for r in response.get("supplements", [])} | {
            r["id"] for r in response.get("remove_supplements", [])
        }
        for supp in response.get("supplements", []):
            source_ids.update(m.expand(supp.get("links", [])))
        scoped = self.scoped_state(state, source_ids, anchors, suppids)
        before_units = dict(scoped["outputs"])
        before_supp = dict(scoped["supplements"])
        seen = dict(
            self.db.execute(
                "SELECT id,signature FROM metadata_ack WHERE root=? AND worker=?",
                (str(Path(state["context_path"]).parent), worker),
            )
        )
        seen.update(self.current_metadata_pins(state, pins["metadata"]))
        m.apply_rows(
            scoped,
            rows,
            allowed,
            seen,
            response.get("default_kind"),
            partial=kind == "reviewer",
            ctx=ctx,
        )
        m.apply_supplements(scoped, response, displayed)
        changed = {
            en for en, unit in scoped["outputs"].items() if before_units.get(en) != unit
        }
        changed_s = {
            sid
            for sid in set(before_supp) | set(scoped["supplements"])
            if before_supp.get(sid) != scoped["supplements"].get(sid)
        }
        revised = any(en in before_units for en in changed) or bool(changed_s)
        valid = m.validate_output(
            scoped,
            units={en: scoped["outputs"][en] for en in changed},
            supplements={
                sid: scoped["supplements"][sid]
                for sid in changed_s
                if sid in scoped["supplements"]
            },
            ctx=ctx,
        )
        projected = {cue["id"]: cue for cue in valid}
        # Supplements must not duplicate any other surviving output, including one
        # outside this packet. This is an indexed-time SQL check, not a full export.
        for sid in changed_s & scoped["supplements"].keys():
            cue = projected[sid]
            duplicates = self.db.execute(
                "SELECT id FROM comparison WHERE work=? AND start=? AND kind IN (2,3) AND id!=? AND json_extract(body,'$.end')=? AND json_extract(body,'$.text')=?",
                (work, cue["start"], sid, cue["end"], cue["text"]),
            ).fetchall()
            m.require(
                not any(
                    ident not in {"O" + en[1:] for en in changed} | changed_s
                    for (ident,) in duplicates
                ),
                "Duplicate supplement",
            )
        affected = set()
        byte_change = False
        for en in changed:
            affected.update(
                r[0]
                for r in self.db.execute(
                    "SELECT target FROM membership WHERE work=? AND anchor=?",
                    (work, en),
                )
            )
            unit = scoped["outputs"][en]
            self.db.execute(
                "INSERT INTO output_unit VALUES(?,?,?) ON CONFLICT(work,id) DO UPDATE SET body=excluded.body",
                (work, en, m.packed(unit)),
            )
            self.unit_references(work, en, unit)
            if en not in before_units:
                self.db.execute(
                    "UPDATE episode_index SET completed_outputs=completed_outputs+1 WHERE work=?",
                    (work,),
                )
            self.db.execute(
                "DELETE FROM membership WHERE work=? AND anchor=?", (work, en)
            )
            members = m.unit_members(unit)
            affected.update(members)
            self.db.executemany(
                "INSERT INTO membership VALUES(?,?,?)",
                [(work, en, ident) for ident in members],
            )
            oid = "O" + en[1:]
            old = self.db.execute(
                "SELECT body FROM comparison WHERE work=? AND id=?", (work, oid)
            ).fetchone()
            new = projected.get(oid)
            if (json.loads(old[0]) if old else None) != new:
                byte_change = True
                if new:
                    self.put_comparison(work, oid, new)
                else:
                    self.remove_comparison(work, oid)
        for sid in changed_s:
            previous = before_supp.get(sid, {})
            affected.update({sid} | set(m.expand(previous.get("links", []))))
            self.db.execute(
                "DELETE FROM supplement_link WHERE work=? AND supplement=?", (work, sid)
            )
            current = scoped["supplements"].get(sid)
            old = self.db.execute(
                "SELECT body FROM comparison WHERE work=? AND id=?", (work, sid)
            ).fetchone()
            new = projected.get(sid)
            byte_change |= (json.loads(old[0]) if old else None) != new
            if current:
                self.db.execute(
                    "INSERT INTO supplement VALUES(?,?,?) ON CONFLICT(work,id) DO UPDATE SET body=excluded.body",
                    (work, sid, m.packed(current)),
                )
                self.db.executemany(
                    "INSERT INTO supplement_link VALUES(?,?,?)",
                    [(work, sid, ident) for ident in m.expand(current["links"])],
                )
                affected.update(m.expand(current["links"]))
                self.put_comparison(work, sid, new, current)
            else:
                self.db.execute(
                    "DELETE FROM supplement WHERE work=? AND id=?", (work, sid)
                )
                self.remove_comparison(work, sid)
        if revised:
            impact = response.get("impact")
            m.require(
                isinstance(impact, dict) and m.nonempty(impact.get("reason")),
                "Semantic revision needs actual impact and reason",
            )
            scope = impact.get("scope")
            m.require(
                scope in {"local", "episode", "global"},
                "Impact is local, episode or global",
            )
            extra = set(m.expand(impact.get("ids", [])))
            for ident in extra:
                m.require(
                    self.db.execute(
                        "SELECT 1 FROM comparison WHERE work=? AND id=?", (work, ident)
                    ).fetchone()
                    or ident in changed_s,
                    "Unknown impact ID",
                )
            m.require(scope != "local" or extra, "Local impact needs explicit IDs")
            if scope == "global":
                ctx["global_revision"] += 1
                # Whole-work invalidation is explicitly requested by this judgment.
                extra = {
                    r[0]
                    for r in self.db.execute(
                        "SELECT id FROM comparison WHERE work=?", (work,)
                    )
                }
            elif scope == "episode":
                extra = {
                    r[0]
                    for r in self.db.execute(
                        "SELECT id FROM comparison WHERE work=?", (work,)
                    )
                }
            if scope != "global":
                self.load_touches(state, extra, ctx)
                m.touch_context(ctx, state["media"], extra)
            ctx["revision"] += 1
            affected.update(extra)
            self.write_context_delta(state, ctx)
            if scope == "global":
                for (path,) in self.db.execute(
                    "SELECT path FROM records WHERE path LIKE '%/state.json'"
                ).fetchall():
                    if str(Path(path).parent) != work:
                        self.rebuild(m.load(Path(path).parent)[0])
        if kind == "author":
            self.db.executemany(
                "INSERT INTO coverage VALUES(?,'author','',?,?) ON CONFLICT(work,role,worker,id) DO UPDATE SET signature=excluded.signature",
                [(work, ident, sig) for ident, sig in pins["sources"].items()],
            )
            for ident in pins["sources"]:
                self.subtract_pending(work, "\x00author", ident[0], int(ident[1:]))
        else:
            self.db.executemany(
                "INSERT INTO coverage VALUES(?,?,?,?,?) ON CONFLICT(work,role,worker,id) DO UPDATE SET signature=excluded.signature",
                [
                    (work, role, who, ident, sig)
                    for ident, sig in pins["cues"].items()
                    for role, who in (("reviewer", worker), ("aggregate", ""))
                ],
            )
            for ident in pins["cues"]:
                self.subtract_pending(work, worker, ident[0], int(ident[1:]))
                self.subtract_pending(work, "", ident[0], int(ident[1:]))
        # Old and new direct correspondences plus declared semantic scope change;
        # unrelated comparisons retain their valid personal coverage.
        self.refresh_signatures(state, affected, ctx)
        for sid in changed_s:
            current = self.db.execute(
                "SELECT source_signature FROM comparison WHERE work=? AND id=?",
                (work, sid),
            ).fetchone()
            read = self.db.execute(
                "SELECT signature FROM coverage WHERE work=? AND role='author' AND worker='' AND id=?",
                (work, sid),
            ).fetchone()
            if current and (not read or read[0] != current[0]):
                self.add_pending(work, "\x00author", sid)
            else:
                self.subtract_pending(work, "\x00author", sid[0], int(sid[1:]))
        self.sync_metadata(state, ctx)
        if (
            byte_change
            or self.db.execute(
                "SELECT output_hash IS NULL FROM episode_index WHERE work=?", (work,)
            ).fetchone()[0]
        ):
            self.compile_current(state)
        if kind == "reviewer":
            findings = self.rows("finding", work)
            local = {**state, "findings": findings}
            m.add_findings(local, response.get("findings", []), worker, pins["cues"])
            for fid, answer in response.get("resolutions", {}).items():
                m.require(
                    fid in findings and m.nonempty(answer),
                    "Resolution needs an existing finding and actual reassessment",
                )
                for ident in findings[fid]["ids"]:
                    current = self.db.execute(
                        "SELECT signature FROM comparison WHERE work=? AND id=?",
                        (work, ident),
                    ).fetchone()
                    read = self.db.execute(
                        "SELECT signature FROM coverage WHERE work=? AND role='reviewer' AND worker=? AND id=?",
                        (work, worker, ident),
                    ).fetchone()
                    m.require(
                        current is None or (read and read[0] == current[0]),
                        "Read current affected material before resolving",
                    )
                findings[fid].update(
                    status="resolved", resolution=answer, resolved_by=worker
                )
            for fid, finding in findings.items():
                self.db.execute(
                    "INSERT INTO finding VALUES(?,?,?) ON CONFLICT(work,id) DO UPDATE SET body=excluded.body",
                    (work, fid, m.packed(finding)),
                )
        self.metadata_ack(state, worker, pins["metadata"])
        result = {
            "changed": sorted(changed),
            "supplements_added": sorted(changed_s - before_supp.keys()),
            "supplements_changed": sorted(
                changed_s & before_supp.keys() & scoped["supplements"].keys()
            ),
            "supplements_removed": sorted(changed_s - scoped["supplements"].keys()),
            "supplements_current": [
                r[0]
                for r in self.db.execute(
                    "SELECT id FROM supplement WHERE work=? ORDER BY id", (work,)
                )
            ],
            "pending": self.pending(work, worker if kind == "reviewer" else ""),
            "output_sha256": self.db.execute(
                "SELECT output_hash FROM episode_index WHERE work=?", (work,)
            ).fetchone()[0],
            "packet_elapsed_ms": round((time.time() - rec["issued"]) * 1000, 1),
        }
        state["revision"] += 1
        self.event(
            state,
            {
                "revision": state["revision"],
                "kind": kind,
                "worker": worker,
                "judgment": response,
                "result": result,
                "previous_outputs": {
                    en: before_units[en] for en in changed if en in before_units
                },
                "previous_supplements": {
                    sid: before_supp[sid] for sid in changed_s if sid in before_supp
                },
                "previous_sources": {},
            },
        )
        self.db.execute("DELETE FROM receipt WHERE work=? AND id=?", (work, key))
        self.save_header(state)
        return result

    def remove_comparison(self, work, key):
        if self.db.execute(
            "DELETE FROM comparison WHERE work=? AND id=?", (work, key)
        ).rowcount:
            self.db.execute(
                "UPDATE episode_index SET comparison_count=comparison_count-1,supplement_count=supplement_count-?,comparison_epoch=comparison_epoch+1 WHERE work=?",
                (int(key[0] == "S"), work),
            )

    def context_record(self, path, value):
        root = str(Path(path).parent)
        value = dict(value)
        touches = value.pop("touches", {})
        self.db.execute("DELETE FROM semantic_touch WHERE root=?", (root,))
        self.db.executemany(
            "INSERT INTO semantic_touch VALUES(?,?,?,?)",
            [
                (root, media, key, revision)
                for media, entries in touches.items()
                for key, revision in entries.items()
            ],
        )
        return value

    def export_context(self, path, value):
        value = dict(value)
        value["touches"] = {}
        for media, key, revision in self.db.execute(
            "SELECT media,id,revision FROM semantic_touch WHERE root=?",
            (str(Path(path).parent),),
        ):
            value["touches"].setdefault(media, {})[key] = revision
        return value

    def context_header(self, state):
        row = self.db.execute(
            "SELECT body FROM records WHERE path=?", (state["context_path"],)
        ).fetchone()
        value = (
            json.loads(row[0])
            if row
            else {"facts": {}, "evidence": {}, "revision": 0, "global_revision": 0}
        )
        return {**value, "touches": {}}

    def load_touches(self, state, ids, ctx):
        touches = ctx["touches"].setdefault(state["media"], {})
        root = str(Path(state["context_path"]).parent)
        for ident in ids:
            if ident in touches:
                continue
            row = self.db.execute(
                "SELECT revision FROM semantic_touch WHERE root=? AND media=? AND id=?",
                (root, state["media"], ident),
            ).fetchone()
            if row:
                touches[ident] = row[0]

    def load_signature_touches(self, state, ids, ctx):
        members = set(ids)
        for ident in ids:
            for (en,) in self.db.execute(
                "SELECT anchor FROM membership WHERE work=? AND target=?",
                (state["work"], ident),
            ):
                members.update(
                    row[0]
                    for row in self.db.execute(
                        "SELECT target FROM membership WHERE work=? AND anchor=?",
                        (state["work"], en),
                    )
                )
        self.load_touches(state, members, ctx)

    def write_context_delta(self, state, ctx):
        root = str(Path(state["context_path"]).parent)
        self.db.execute(
            "UPDATE root_version SET context_epoch=context_epoch+1 WHERE root=?",
            (root,),
        )
        self.db.executemany(
            "INSERT INTO semantic_touch VALUES(?,?,?,?) ON CONFLICT(root,media,id) DO UPDATE SET revision=excluded.revision",
            [
                (root, media, key, revision)
                for media, entries in ctx["touches"].items()
                for key, revision in entries.items()
            ],
        )
        header = {key: value for key, value in ctx.items() if key != "touches"}
        self.db.execute(
            "INSERT INTO records VALUES(?,?) ON CONFLICT(path) DO UPDATE SET body=excluded.body",
            (state["context_path"], self.m.packed(header)),
        )

    def guard_evidence(self, state, ctx):
        return {
            key: {"path": path, "hash": sha}
            for key, path, sha in self.db.execute(
                "SELECT id,path,sha256 FROM evidence_guard WHERE work=?",
                (state["work"],),
            )
        }

    def save_phase(self, state, kind):
        """Persist phase declarations without rewriting unchanged linguistic rows."""
        history = state["history"]
        offset = getattr(history, "count", 0)
        if callable(offset):
            offset = 0
        for revision, event in enumerate(list(history), offset):
            self.db.execute(
                "INSERT OR IGNORE INTO events VALUES(?,?,?)",
                (
                    str(Path(state["work"]) / "state.json"),
                    revision,
                    self.m.packed(event),
                ),
            )
        state["_history_count"] = len(history)
        if kind in {"seal", "resolve"}:
            self.replace_rows("finding", state["work"], state["findings"])
        self.save_header(state)

    def collection_metadata(self, state):
        key = ("native-collection", state["context_path"])
        operation = self.m._OPERATION
        if operation is not None and key in operation["collection"]:
            return operation["collection"][key]
        manifest = self.m.request_manifest(state)
        self.m.require(
            not any(row["decision"] == "held" for row in manifest.get("excluded", [])),
            "Complete requested source collection first: inspection-held items remain",
        )
        headers = [self.header(work) for work in manifest["items"]]
        self.m.require(
            all(set(header["sources"]) == {"en", "ko"} for header in headers),
            "Complete English/Korean collection for every requested item before study or correction",
        )
        for header in headers:
            self.m.verify_inputs(header, evidence={})
        result = self.m.digest(
            {
                "manifest": manifest,
                "sources": {
                    header["work"]: self.m.source_binding(header) for header in headers
                },
            }
        )
        if operation is not None:
            operation["collection"][key] = result
        return result

    def status(self, state):
        m = self.m
        work = state["work"]
        ctx = self.context_header(state)
        collection_ok = False
        try:
            m.verify_inputs(state, evidence=self.guard_evidence(state, ctx))
            collection_hash = None
            has_author = self.db.execute(
                "SELECT 1 FROM coverage WHERE work=? AND role='author' LIMIT 1", (work,)
            ).fetchone()
            if not has_author:
                collection_hash = self.collection_metadata(state)
            collection_ok = True
            study = m.study_record(state, ctx)
            try:
                study = m.study_ready(state, ctx)
            except m.Error as error:
                receipts = m.registry(state).get("study_receipts", {}).values()
                phase = (
                    "held"
                    if has_author
                    else "studying"
                    if any(
                        work in rec["items"]
                        and rec["collection"] == collection_hash
                        and rec["base"] == ctx["revision"]
                        for rec in receipts
                    )
                    else "study_required"
                )
                return self.status_result(
                    state, ctx, phase, False, [str(error)], study=study
                )
            draft = m.sealed(state, ctx)
            worker = state["reviewer"]
            todo = self.pending(work, worker or "")
            count = self.db.execute(
                "SELECT count FROM pending_total WHERE work=? AND worker=?",
                (work, worker or ""),
            ).fetchone()
            count = count[0] if count else 0
            index = self.db.execute(
                "SELECT expected_outputs,completed_outputs,output_hash,comparison_epoch FROM episode_index WHERE work=?",
                (work,),
            ).fetchone()
            findings = self.rows("finding", work)
            blockers = []
            if not draft:
                blockers = ["Complete source correction and author seal required"]
            elif index[0] != index[1] or not index[2]:
                blockers = [
                    "Classify the entire English scope and produce Korean output"
                ]
            elif not worker or count:
                blockers = ["Current assigned independent coverage incomplete"]
            elif any(f["status"] == "open" for f in findings.values()):
                blockers = ["Unresolved finding"]
            elif any(
                value.get("status") == "open" and value.get("material", True)
                for key, (value, _) in self.meta_entries(
                    state, "reviewer", worker
                ).items()
                if key.startswith("fact:")
            ):
                blockers = ["Unresolved material work judgment"]
            elif any(
                fid not in findings or findings[fid]["status"] != "resolved"
                for fid in state["sealed"]["unresolved"]
            ):
                blockers = ["Unresolved author question"]
            approval = state.get("approval")
            ok = bool(
                not blockers
                and approval
                and approval["worker"] == worker
                and approval["hash"] == index[2]
                and approval.get("comparison_epoch") == index[3]
                and approval["findings"] == m.digest(findings)
                and not self.meta_entries(state, "reviewer", worker, True)
            )
            phase = (
                "approved"
                if ok
                else "authoring"
                if not draft
                else "review_ready"
                if worker is None
                else "held"
                if not count and blockers
                else "reviewing"
            )
            return self.status_result(
                state,
                ctx,
                phase,
                ok,
                blockers or ([] if ok else ["Current exact-byte approval required"]),
                todo,
                draft,
                study,
            )
        except m.Error as error:
            phase = (
                "collecting"
                if not self.db.execute(
                    "SELECT 1 FROM coverage WHERE work=? AND role='author' LIMIT 1",
                    (work,),
                ).fetchone()
                and not collection_ok
                else "held"
            )
            return self.status_result(state, ctx, phase, False, [str(error)])

    def status_result(
        self,
        state,
        ctx,
        phase,
        approved,
        blocking,
        pending=None,
        draft=False,
        study=None,
    ):
        index = self.db.execute(
            "SELECT comparison_count-source_count FROM episode_index WHERE work=?",
            (state["work"],),
        ).fetchone()
        todo = (
            self.pending(state["work"], state["reviewer"] or "")
            if pending is None
            else pending
        )
        count = self.db.execute(
            "SELECT count FROM pending_total WHERE work=? AND worker=?",
            (state["work"], state["reviewer"] or ""),
        ).fetchone()
        return {
            "contract": self.m.CONTRACT,
            "media": state["media"],
            "author": state["author"],
            "reviewer": state["reviewer"],
            "draft_ready": draft,
            "study": {k: study[k] for k in ("id", "worker", "decision")}
            if study
            else None,
            "phase": phase,
            "context_revision": ctx["revision"],
            "pending": todo,
            "pending_count": count[0] if count else 0,
            "approved": approved,
            "approved_sha256": state["approval"]["hash"] if approved else None,
            "blocking": blocking,
            "output_cues": index[0] if index else 0,
            "metrics": state["metrics"],
            "tokens": "not_measured",
        }

    def study_metrics(self, state):
        row = self.db.execute(
            "SELECT body FROM records WHERE path=?",
            (str(Path(state["context_path"]).with_name("roles.json")),),
        ).fetchone()
        return json.loads(row[0]).get("study_metrics", {}) if row else {}

    def refresh_prerequisites(self, state, ctx):
        root = str(Path(state["context_path"]).parent)
        version = self.db.execute(
            "SELECT context_epoch,request_epoch FROM root_version WHERE root=?", (root,)
        ).fetchone()
        source = self.db.execute(
            "SELECT source_epoch FROM episode_index WHERE work=?", (state["work"],)
        ).fetchone()[0]
        try:
            self.m.study_ready(state, ctx)
            studied, block = 1, None
        except self.m.Error as error:
            studied, block = 0, str(error)
        binding = self.m.seal_binding(state, ctx)
        self.db.execute(
            "UPDATE episode_index SET prerequisite_context=?,prerequisite_request=?,prerequisite_source=?,studied=?,study_block=?,study_binding=?,global_epoch=? WHERE work=?",
            (
                version[0],
                version[1],
                source,
                studied,
                block,
                binding,
                ctx["global_revision"],
                state["work"],
            ),
        )

    def require_study(self, state):
        root = str(Path(state["context_path"]).parent)
        version = self.db.execute(
            "SELECT context_epoch,request_epoch FROM root_version WHERE root=?", (root,)
        ).fetchone()
        columns = (
            "prerequisite_context",
            "prerequisite_request",
            "prerequisite_source",
            "source_epoch",
            "studied",
            "study_block",
            "study_binding",
            "global_epoch",
        )
        row = dict(
            zip(
                columns,
                self.db.execute(
                    "SELECT " + ",".join(columns) + " FROM episode_index WHERE work=?",
                    (state["work"],),
                ).fetchone(),
            )
        )
        if (
            row["prerequisite_context"],
            row["prerequisite_request"],
            row["prerequisite_source"],
        ) != (version[0], version[1], row["source_epoch"]):
            self.refresh_prerequisites(state, self.context_header(state))
            row = dict(
                zip(
                    columns,
                    self.db.execute(
                        "SELECT "
                        + ",".join(columns)
                        + " FROM episode_index WHERE work=?",
                        (state["work"],),
                    ).fetchone(),
                )
            )
        self.m.require(
            row["studied"],
            row["study_block"] or "Complete shared work study before correction",
        )
        return row

    def sealed(self, state):
        row = self.require_study(state)
        return bool(
            state["sealed"] and state["sealed"]["binding"] == row["study_binding"]
        )

    def context_consumers(self, state, facts, evidence, study_consumers=()):
        """Actual indexed reference edges, never all sibling subtitle bodies."""
        result = {}
        has_study = False
        for work in self.m.request_manifest(state)["items"]:
            header = self.header(work)
            used = set()
            if (
                work in study_consumers
                and self.db.execute(
                    "SELECT 1 FROM output_unit WHERE work=? LIMIT 1", (work,)
                ).fetchone()
            ):
                has_study = True
            for kind, keys in (("fact", facts), ("lookup", evidence)):
                for key in keys:
                    used.update(
                        row[0] for row in self.reference_members(work, kind, key)
                    )
            for key in evidence:
                used.update(
                    row[0]
                    for row in self.db.execute(
                        "SELECT id FROM supplement WHERE work=? AND json_extract(body,'$.event')=?",
                        (work, key),
                    )
                )
            if used:
                result[header["media"]] = used
        return result, has_study

    def impact_scope(self, state, impact):
        if not isinstance(impact, dict):
            return set()
        if impact.get("scope") in {"global", "episode"}:
            return {
                row[0]
                for row in self.db.execute(
                    "SELECT id FROM comparison WHERE work=?", (state["work"],)
                )
            }
        known = set()
        for key in self.m.expand(impact.get("ids", [])):
            if self.db.execute(
                "SELECT 1 FROM comparison WHERE work=? AND id=?", (state["work"], key)
            ).fetchone():
                known.add(key)
        return known

    def refresh_context(self, state, old, current):
        facts = {
            key
            for key in old["facts"].keys() | current["facts"].keys()
            if old["facts"].get(key) != current["facts"].get(key)
        }
        evidence = {
            key
            for key in old["evidence"].keys() | current["evidence"].keys()
            if old["evidence"].get(key) != current["evidence"].get(key)
        }
        facts.update(
            key
            for ctx in (old, current)
            for key, value in ctx["facts"].items()
            if set(value["evidence"]) & evidence
        )
        consumers, _ = self.context_consumers(state, facts, evidence)
        global_change = old["global_revision"] != current["global_revision"]
        for work in self.m.request_manifest(state)["items"]:
            header = self.header(work)
            affected = set(consumers.get(header["media"], ())) | set(
                current["touches"].get(header["media"], {})
            )
            if global_change:
                affected = {
                    row[0]
                    for row in self.db.execute(
                        "SELECT id FROM comparison WHERE work=?", (work,)
                    )
                }
            if affected:
                self.refresh_signatures(header, affected, current)
            self.sync_metadata(header, current)

    def has_correction(self, work):
        return bool(
            self.db.execute(
                "SELECT 1 FROM output_unit WHERE work=? LIMIT 1", (work,)
            ).fetchone()
            or self.db.execute(
                "SELECT 1 FROM coverage WHERE work=? AND role='author' LIMIT 1", (work,)
            ).fetchone()
        )

    def referenced_evidence(self):
        refs = {
            row[0]
            for row in self.db.execute(
                "SELECT DISTINCT ref FROM unit_reference WHERE kind='lookup'"
            )
        }
        refs.update(
            row[0]
            for row in self.db.execute(
                "SELECT DISTINCT json_extract(body,'$.event') FROM supplement WHERE json_extract(body,'$.event') IS NOT NULL"
            )
        )
        return refs
