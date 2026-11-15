# Coordinator operations and maintenance

Assigned language workers use [records.md](records.md). These operations apply only to the coordinator, an explicitly bounded patch or a storage/usage diagnosis.

## Authorized bounded text patch

`prepare MEDIA --work NEW --base DEPLOYED.srt --scope SCOPE.json` snapshots deployed bytes. Scope has `reason`, exact user `authorization`, E/K `ids`, and `changes:[{id:K_ID,before:EXACT_TEXT,after:EXACT_TEXT}]`. Select English; do not replace/relabel the fixed Korean baseline. Source coverage/output/review includes all declared scope and necessary context. Only authorized exact text changes may differ; cue structure must match the English or hold. Bytes outside changed text, BOM, cue numbers and newline styles survive exactly.

## Optional usage reporting

The current parser supports Codex-native logs, not every provider format. It is never a prerequisite for subtitle work on any host. Unsupported logs remain `not_measured`; host measurements may be reported separately with their actual scope.

`telemetry --session-log LOG [--session-log LOG ...] --root-turn-id ROOT_TURN --start UTC_ISO --end UTC_ISO` is read-only and needs no media, work directory or project database. Supply the coordinator and all descendant logs; audit their identities against the session registry. It accepts JSONL and JSONL.zst, streaming the latter through installed `zstd` without extraction files. Unknown/missing logs are not a pass. Do not load raw session image payloads into model context.

Native records use `(thread_id,response_id)` identities and explicit root-turn attribution, ignore inherited parent records, deduplicate aliases, and reconcile response sums against thread cumulative counters. Complete counter-only logs can provide interval deltas without a root-turn filter; resets reject. Counter-only logs cannot certify root-turn attribution. A native reconciliation mismatch or absent requested root returns `complete:false` and aggregate `usage:not_measured`. Descendant completeness remains the caller's responsibility.

`status WORK --session-log ... --root-turn-id ... --start ... --end ...` delegates to the same reader. Record UTC intervals with `date -u +%Y-%m-%dT%H:%M:%SZ`. Input includes cached input; output includes reasoning, so neither subcounter is added again. Usage measures processed tokens, not billing or account quota. Observed command interval unions are host observations, not all tool time, model-only duration or CPU time. Keep initial delivery, later corrections, maintenance and user idle intervals distinct.

## v7 storage and interrupted placement

`state.sqlite3` at the single/batch root is the operational SSOT. `state.json`, `context.json`, `roles.json`, `batch.json`, context history and placement-result names in engine records are logical database keys, not editable files. `records` stores only episode headers and small shared records. Selected sources/cues, output units, supplements, receipts, findings, per-worker coverage and metadata acknowledgement have dedicated tables. `comparison`, `membership`, `metadata`, `pending_cue`, `pending_interval` and `pending_total` are transactionally maintained indexes; they cannot replace real reading or declarations. `events` stores indexed immutable history. Child `item.json` contains the immutable root/item locator. Do not edit these records or move an active work root. Use source/context submissions and generated packets. Old projects are rejected; prepare fresh.

`view` and `submit` commit active state, shared metadata, coverage, receipts and history together. Source artifact files remain outside the database and immutable; a failed transaction may leave an unreferenced artifact, never an approved source. Source selection and meaningful judgments remain model responsibilities.

With `--template`, the response contains `response_template` instead of echoing `submit`; edit and submit that generated file. Without it, `submit` remains inline. `status` reports `context_revision` for context update base.

Finalize commits each placement intent before file replacement, rehashes the actual target and records completion. After interruption, matching approved target bytes reconcile completion; unchanged previous target bytes may proceed under all current guards; any other bytes hold. Batch placement remains per-file atomic with whole-request preflight and partial results, never a whole-batch transaction.

Empty optional per-unit metadata (for example `facts:[]`) is omitted from comparison display; absence retains the documented empty default. Classification, sources, output, complete pending ranges and current output hash remain available.

Default CLI usage reports omit session identifiers, paths and account limits. Use `--include-session-details` only for local private diagnostics; never publish that output.
