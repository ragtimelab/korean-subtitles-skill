# Independent comparison

Read with [common packet operation](records.md) and [language/evidence principles](workflow.md).

## Direct comparison, correction and approval

An episode is ready when its first corrector has read all E/K, supplied all classifications/current Korean and sealed completed correction under prior study. Other episodes may still be in first correction. Run:

```sh
python3 scripts/subtitles.py view ITEM --role reviewer --worker READER --pending --template
```

The first successful review view assigns the episode reviewer. A different worker cannot duplicate that assignment. If the prior actual reviewer has stopped, `--reassign "Actual handover reason"` assigns a new independent worker, invalidates approval and requires their own full current comparison; old personal coverage is not borrowed. A first corrector may review another episode.

`comparisons` presents original `english`, `collected` Korean and current `output` together, with classification/parts/identity. Each collected K body appears inline at its first use; later uses are its K ID. If output text is identical to a linked K body, `output.text_ref` references that literal K body instead of repeating it; this is byte equality, never quality approval. Ordinary output shares the English interval. `korean_sources` contains only unmatched displayed K bodies and `supplements` separate S bodies. All references resolve within this packet; excluded English and unmatched Korean remain required. `context_changes` accompanies comparison, not a separate acknowledgment stage. Coverage binds every displayed E/K/O/S, including referenced text. Use pending offset zero. Fetch necessary context via `--ids`, not fixed whole-work overlap.

For established errors submit only changed fields of English-anchored rows:

```json
{"packet":"ENGINE_GENERATED","corrections":[{"id":"E1","text":"내 친구들이 기다려."}],
 "findings":[],"resolutions":{},"impact":{"scope":"local","ids":["E1"],"reason":"Actual source-grounded correction and impact judgment."}}
```

Corrections reuse author row validation, fixed source structure and current bindings. Allowed fields are the same semantic row fields; omitted fields retain the current classification/correspondence/facts. Correct only displayed English anchors; retrieve necessary context before an outside-scope edit. `supplements` and explicit `remove_supplements` also work in review with actual displayed links and inspected evidence. New fact/lookup evidence can be inspected explicitly with `view --role metadata --ids fact:ID,evidence:ID --worker READER`; this is for actual new evidence, not a default stage.

Submission reports actual changed IDs/hash/pending scope. Changed outputs/correspondences and additional declared semantic impact need current confirmation; unaffected coverage survives. Inspect them with the same reviewer pending view and submit its template. This is a local confirmation of compiled corrections, not another full pass or writer reseal.

Only actual unresolved questions need `findings:[{ids:["E1","O1"],question:"Question requiring resolution"}]`; ordinary resolved corrections do not need a duplicate finding essay. On current comparison, `resolutions:{F1:"Actual source/current-output reassessment"}` resolves existing findings. Alternatively use `kind:"resolve",worker:READER,resolutions:...`. Stale affected scope cannot be resolved before current confirmation. Retired support can be displayed for repair but cannot authorize final approval.

Finally submit `{"kind":"approval","worker":"READER"}`. The assigned reviewer must personally cover all current E/K/O/S and required context, with all material findings resolved. Approval binds exact current compiled bytes. Only finalize writes sidecars; use the batch parent for a directory request, with source/media/evidence/target guards and all-item preflight. A hash/receipt is never proof of cognition, observation or real independence.

