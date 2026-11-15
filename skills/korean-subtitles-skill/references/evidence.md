# Context and supplemental evidence records

Read for initial study facts, actual context updates, or evidential supplements. Language/evidence decisions remain in [workflow.md](workflow.md).

## Shared context: only consequential facts and reusable evidence

Partial updates use `kind:"context"`, actual `worker`, current integer `base` from `status.context_revision`, and `reason`. Supply changed entries only; code timestamps evidence with `date`, hashes local evidence files, archives previous context and computes consumers.

```json
{"kind":"context","worker":"AUTHOR_SESSION","base":0,"reason":"Actual original/independent form evidence inspected.",
 "evidence":{"script":{"kind":"selected_text","ref":"Original cue locator","path":"/actual/en.selected.srt","access":"body","observation":"Relevant actual source content."}},
 "facts":{"role":{"text":"Supported interpretation and Korean form; preserve specified uncertainty/reveal limits.","status":"established","global":false,"evidence":["script"]}}}
```

Evidence kinds: selected_text, external_subtitle, official, official_ost, official_film_credits, encyclopedia, dictionary, audiovisual, user. `external_subtitle` records an actually inspected non-selected subtitle and its source/ref, access, observation, claimed release/provenance and verification limitations without claiming independent or official origin. Actual access is `body|unavailable`; observation distinguishes inspection/inference. A source's field labels do not prove its truth. Facts use `status:established|open|intentional`, `global` for core work context, optional `material:false` only for nonconsequential open matters. Record identities, Korean form support, relations/register, chronology and uncertainty as needed, not every incidental word. Raw English spelling alone cannot establish Korean form. Full understanding is still required when no separate fact is needed.

For an established selected-English error, add its actual supporting evidence and a factual judgment in the existing context, attach the fact ID to the affected English-anchored output, and preserve the original cue and structure. A differing external subtitle alone need not establish an error. Corrections use the existing impact, independent comparison and exact-byte approval flow; unresolved material judgments block approval. Unsupported structural or song-identity changes hold under existing validation, not a silent override.

Change/retire current judgments with the same command: `remove_facts:["id"]` / `remove_evidence:["id"]`, actual reason and `impact` if existing use/core context is affected. Code unions old/current consumers and presents retirement notices; no field-specific correction schema. The `global` fact flag means broad context availability, not automatic invalidation. Adding information does not by itself invalidate existing comparisons. Revising an existing broadly shared premise requires explicit impact; actual causal/relationship/reveal changes use global scope. Code does not infer semantic scope from changed prose. Never delete evidence still referenced by a current fact/output. Reassess/remove retired fact references in all affected output rows. History remains available without treating old claims as active truth.

## Supplemental evidence events

Collected-original additions use `supplements:[{id:"S1",origin:"collected",kind:"dialogue|screen_text|lyrics",start,end,text,links:["K1"],reason}]`. Links refer to actual selected Korean original cues, at least one of whose intervals overlaps the output; do not attach audiovisual `event` or `fields`. The worker actually reads and judges the source and the reviewer independently compares the current output, original and affected English. Preserve original wording/timing when appropriate; an edited rendering or minimal overlap adjustment requires a real reason, never a claim of audiovisual observation. English absence alone does not require a finding. Avoid duplicating dialogue already rendered under an English anchor. These additions are unavailable for bounded patches. Use the ordinary supplement impact and review confirmation flow below.

One context evidence entry can support multiple supplements for the same observed document/chat/title. For audiovisual evidence also supply this exact `media`, `observed:true`, `verified:true`, `modality:video|audio_video`, `interval:[start,end]`, actual `observation`, optional exact artifact `path`, and `fields:{field-id:{text:original field content,available:first verified readable ms,until:observed hold end ms}}`. A union of later frames is not proof of first-frame readability. Engine only checks declared intervals/artifact identity.

Author response optional `supplements:[{id:"S1",start,end,text,links:["K1"],event:"evidence-id",fields:["field-id"],kind:"screen_text",reason}]`. A supplement stays within every referenced field interval. Official inspected `https://` evidence may support track_info, never unobserved screen text. Retractions use `remove_supplements:[{id,reason}]`. Omitting a supplement does **not** remove it; submission returns actual added/changed/removed/current IDs. All supplement additions/revisions/removals require impact judgment. Bounded text patches cannot insert supplements.


## Song identity and artist lookup

For `track_info`, `identity:{"title":"literal source title","artist":"literal source artist"}` preserves source identity. Missing artist requires `artist_lookup` with `outcome:verified|not_found|unavailable`, `evidence`, and `sources:[{kind:official_ost|official_film_credits,url,access:body|unavailable,checked_at,evidence}]`. OST is first. Verified result also needs `artist` and `version_evidence` for the actual used version; unavailable/absence cannot add a performer. Reuse one lookup per song/version: store the actual lookup under an evidence entry as `artist_lookup`, then use that entry ID as the row/part `artist_lookup` string. The engine resolves it and binds its current version; inline lookup dictionaries remain suitable for single use. Evidence submission fills missing source observation dates with `date`. Artist additions are separate verified supplements, not fabricated source fields.


Context entries stay inside the identified work profile that owns them, including facts added after initial study. A global fact is broadly available within that work; an explicit global impact still reassesses all requested items. Updating another work's entry requires its item and source-grounded scope. Replacing an incomplete, unread study retires its superseded facts and unreferenced evidence into history; it does not expose them as current guidance.
