# Collection and initial study

One Python-stdlib engine plus installed ffprobe/ffmpeg. Keep generated bindings out of model-authored records. The contract is `english-ssot-output-v7`; old projects/approvals are rejected, not migrated. E/K identify original English/Korean; O uses its surviving E number; S identifies supplements. IDs/timing are not proof of semantic correspondence.

```sh
python3 scripts/subtitles.py prepare MEDIA --work NEW_WORK
# Select and guard English/Korean originals for every admitted item first.
python3 scripts/subtitles.py view PARENT_OR_ITEM --role study --worker COORDINATOR --template WORK/responses/study.json
python3 scripts/subtitles.py submit PARENT_OR_ITEM WORK/responses/study.json
python3 scripts/subtitles.py view WORK --role author --worker AUTHOR_SESSION --pending --template WORK/responses/author.json
python3 scripts/subtitles.py submit WORK WORK/responses/author.json
python3 scripts/subtitles.py status WORK
python3 scripts/subtitles.py finalize WORK
```

`prepare` defaults to `--intent collect`: embedded Korean skips the file before any work creation. For explicit embedded-Korean audit use `--intent audit --authorization "EXACT USER REQUEST"`; patch scope authorization supplies this exception. `inspect MEDIA` returns the same metadata decisions without writing. Do not routinely run both. A metadata conflict or probe error returns `held`. Tags identify declared language, not content quality.

Directory preparation inspects all immediate requested media before writing, reuses each probe and prepares only admitted items. It returns `items`, `excluded`, `prepared`, `skipped`, `held`; if none are admitted no parent is created. A mixed batch retains exclusions. `status PARENT` reports both; `finalize PARENT` preflights active outputs, preserves skipped targets and refuses held requested items. Source/correction/review use item directories; study view/submission also accept the parent. Brief filesystem commits use a shared root SQLite transaction and lock, not model work. Collection for the entire request and relevant shared study precede correction. Afterwards use available permitted workers for ready independent episodes; no later season-wide correction/review barrier, fixed worker count, runtime changes, dummy probes or Korean-only prereading. Coordinate completion events, not chat polling. Actor strings do not prove real independence.

## Select sources once

Submit `kind:"source"` with actual judgments below. Inspect local candidates before filling them. `prepare` supplies tracks/paths. Absolute ffprobe stream index is required, not subtitle ordinal. Keep source/provenance assertions accurate; booleans are declarations, not automated verification.

```json
{"kind":"source","language":"en","origin":"embedded","stream":2,
 "automatic":false,"verified_language":true,"complete":true,"release_match":true,
 "profile":"sdh","selection_reason":"Actual suitability finding.",
 "candidate_assessments":[{"origin":"embedded","stream":2,"profile":"sdh","usable":true,"evidence":"Concrete inspected anchors."}],
 "presentation_evidence":{"checked":"Original content and meaningful native presentation inspected.","preserved":"What extraction retains.","limitations":[],"resolved":true},
 "provenance":{"track_title":"Actual track title"},"evidence":"Actual language/cut/completeness findings."}
```

Assess every apparent embedded English text track. Priority among usable candidates: embedded SDH → embedded ordinary → external SDH → external ordinary. For external use `origin:"external"`, `path`, `fallback_reason` and all embedded rejection assessments. Conversion fidelity needs actual inspection; consequential unsupported native presentation holds before extraction. Embedded extraction preserves native PTS, not inferred zero-origin time. Image/forced tracks cannot be selected.

Korean uses `language:"ko"`, `origin:"sidecar"`/`embedded`, `path`/absolute `stream`, `ordinary:true`, `generated:false`, `translator_status:"credited"|"uncredited"|"unknown"`, provenance/evidence/presentation fields and the same language/completeness/cut declarations. No English candidate fields are needed. Embedded Korean must be SubRip; otherwise select a verified faithful external SRT or hold. Unknown translator status does not prove human authorship. Preserve a separate original via `original_path` when converting an external original. Engine hashes all originals/work copies. Selected snapshots have content-addressed immutable filenames.

Once study is submitted or correction reading begins, original candidate/bytes are fixed. Correct a mistaken source explanation with the same source command, exact original path/stream and unchanged bytes, plus semantic `impact` after correction has begun. Old explanations remain in history. A different original/request scope requires fresh preparation. Post-correction explanation updates preserve completed reading/correction of the unchanged originals. They invalidate the declared comparison scope and require current independent metadata/comparison checking. They do not create a routine author reseal or raw-source reread.

## Shared study before correction

`view PARENT_OR_ITEM --role study --worker COORDINATOR` succeeds only after every requested admitted item has English/Korean originals and no inspection-held item remains. It exposes source identity/provenance and a generated template, not editable correction rows. Actually study the external work references following workflow.md before filling it. Group items belonging to the same work/season; split unrelated works. A response can cover a subset of displayed items, but each item occurs once. Each group's `identity` names the actual work; `items` retains generated paths; `facts`/`evidence` use the context fields in [evidence.md](evidence.md) with local IDs. Code namespaces IDs and binds sources/scope/time automatically.

Return `kind:"study"`, generated `packet`, and `works:[{identity,items,facts,evidence,decision,unresolved}]`. `decision` is the LLM's explicit `ready|needs_evidence` judgment, never an engine inference. Facts/evidence must reflect real inspection; at least one supplied/inspected body is required. An unavailable Wikipedia inquiry can coexist with an actually inspected alternative, never masquerade as a successful body read. Record real questions as `unresolved:[{question:"...",material:true}]`; immaterial questions may use `material:false`. `ready` cannot coexist with declared material gaps/open facts. Such consistency checks do not prove learning or factual truth.

One study per work is stored in the existing context, with target/source bindings. Before correction the coordinator hands its factual material to the corrector; the first author packet presents it before cues. Study facts are also required reviewer context, not an answer key or protected interpretation. Subsequent packets only carry changed entries. Unrelated work profiles are filtered out. `status` distinguishes `collecting`, `study_required`, `studying`, then `authoring` and existing review states. A valid initial study is needed at author view/submission, seal, approval and placement; post-correction fact refinements use `context` and actual impact, not repeated study submission.

