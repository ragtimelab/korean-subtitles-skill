# First correction

Read with [common packet operation](records.md) and [language/evidence principles](workflow.md).

## Author packet: Korean and necessary decisions

`view --role author --worker ID --pending` emits chronological E/K, context changes, current rows and a response template. At about 200 source rows, a page usually contains 80–120 English cues; adjust `--limit`/`--ids E1-E3,K1` for complete utterances/replies. Timing only orders candidates; author confirms actual K correspondences. Every selected E and K must actually be read. Do not omit unmatched Korean leads from the audit. Source/model history is not automatically supplied to new sessions.

Edit the generated template, retain `packet`, and submit:

```json
{"packet":"ENGINE_GENERATED","default_kind":"dialogue",
 "rows":[{"id":"E1","text":"내 친구들이 기다려.","ko":["K1"]}]}
```

Existing supplements can be requested with `--ids S1` or shown in an ordinary author page. Editing/removal requires current displayed S; additions require displayed linked sources and actual evidence. S body/version is pinned without counting it as original E/K reading.

`default_kind` is the author's classification of this packet, not an engine heuristic. Row `kind` overrides it: `dialogue|lyrics|track_info|nonverbal`. Excluded full nonverbal cues have empty text. Mixed `parts` identify exact original substrings with `text` plus `kind` (`speaker` also allowed); code locates unique literal spans. Repeated substrings use one-based `occurrence` to identify which instance; code computes character spans. Revision templates may already contain engine-generated spans. Classify every non-whitespace source span. Output text retains surviving SSOT lines/speakers/emphasis/expressive punctuation. Optional row `facts:["decision-id"]` references material shared judgments. No per-row assurance or manual hashes. Revision templates preserve previous classifications, parts, correspondences and fact references.

For source song identity or an absent artist, follow the [song identity/lookup record](evidence.md#song-identity-and-artist-lookup); use the generated template and reuse one actual inquiry per version.

Revisions additionally declare semantic impact:

```json
{"scope":"local","ids":["E1","O1"],"reason":"Actual meaning/dependency reassessment."}
```

Set this as `impact` in the response. `scope` is `local|episode|global`: local IDs, the current media item, or all requested items respectively. Multiple corrected lines within one item do not by themselves justify global scope. Code automatically dirties changed units/correspondences; this judgment declares **additional semantic impact**, not a forced graph for every cue. Local IDs must be real; unclear → episode, core relationship/causal/reveal → global. The reviewer checks the scope's adequacy. Large pages do not create dependency cliques.

After full source reading, correction and classification, updating actual study discrepancies/questions:

```json
{"kind":"seal","worker":"AUTHOR_SESSION","unresolved":[]}
```

Use `unresolved:[{ids:["E1"],question:"Actual material question"}]` for remaining questions. They become findings for the reviewer. Seal records completed source correction and binds current originals/prior study; it cannot substitute for pre-correction learning or prove comprehension. No repeated synopsis/understanding essay. Local edits preserve the completion record while invalidating actual review scope. Only current factual evidence/questions accompany independent review, not an author verdict.

