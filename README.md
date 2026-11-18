# korean-subtitles-skill

Collect and audit ordinary Korean subtitles against a verified English source of truth, then deliver independently reviewed UTF-8 Korean SRT. Source selection, shared work study, actual separate-worker review and exact-byte placement are mandatory. Code verifies records and consistency; it cannot certify translation quality or worker independence.

## Requirements and installation

An LLM agent host with file/command tools and actual independent worker execution or connection, Python 3.10+ on POSIX, and security-patched ffmpeg/ffprobe on PATH. Compressed optional usage logs also need zstd. macOS is locally tested; Linux is checked by CI. Windows is unsupported (fcntl and POSIX file APIs). No model API keys or Python runtime packages are required.

Install the selected skill and choose the target agent environment:

```sh
npx skills@1.8.1 add ragtimelab/korean-subtitles-skill@v0.3.0 --skill korean-subtitles
```

Inspect the source and release before installing. Full commit references can replace the release tag. Installation does not authorize processing media. Invoke the skill through the host’s skill mechanism (for example `$korean-subtitles`) with an explicit media path and requested operation; the agent follows skills/korean-subtitles/SKILL.md. All generated work belongs outside the skill source directory. Existing work from a different engine fingerprint requires fresh preparation.

```sh
npx skills@1.8.1 remove korean-subtitles
```

If you installed `korean-subtitles-skill` from v0.2.1 or earlier, install `korean-subtitles` with the command above. Verify the new skill in your agent, then remove the old skill with `npx skills@1.8.1 remove korean-subtitles-skill` in its installation scope. Update saved prompts to use `$korean-subtitles`.

Updates should select a reviewed release explicitly with the install command. `skills update` can advance source revisions; inspect the result before use. Do not overwrite an existing global/local installation without checking its source and modifications.

## Privacy, security and rights

The engine runs locally and has no upload client. The Skills installer is a separate network tool: `DISABLE_TELEMETRY=1` or `DO_NOT_TRACK=1` disables its telemetry and audit requests. Review SECURITY.md for trust boundaries. Supply only media/subtitles you are authorized to process; no media, subtitle collections, lyrics, private logs or evaluation originals are distributed. MIT covers the repository's own code and instructions, not third-party inputs.

## Development

```sh
python3 tools/test.py
python3 tools/check_public.py
```

Tests use synthetic supplied judgments. They verify mechanics, not autonomous linguistic quality. Private development records and evaluation originals are excluded.

Run the supplied two-item integration fixture with:

```sh
python3 tools/bench_flow.py --engine local=skills/korean-subtitles/scripts/subtitles.py --size 8 --episodes 2 --runs 1 --no-warmup
```

It creates synthetic media outside the repository, supplies study/correction/review judgments, exercises batch placement, and checks exact output hashes. It does not run independent language agents or establish translation quality or a performance improvement.

## Agent compatibility

The common package has no vendor allowlist. Skills CLI targets include `codex`, `claude-code`, `gemini-cli`, `grok` (Grok Build) and `universal`; append `-a TARGET` to choose one. See [the pinned installer target reference](https://github.com/vercel-labs/skills/blob/v1.8.1/docs/agents.md). Other hosts may load the standard package using their own skill loader. A product's ordinary chat interface does not acquire local tools merely by installing instructions.

Read the package's [execution contract](skills/korean-subtitles/references/execution.md). File/command execution and real independent workers are capabilities, not product restrictions. No manual command/copy handoff is the default. Missing capability is reported without inventing approval. macOS/Linux are supported; Windows needs a POSIX execution environment. Installation, supplied-judgment tests and real agent execution are distinct evidence; no universal autonomous language-quality claim is made. Optional usage parsing currently supports Codex-native logs; other logs remain not_measured.

Release validation distinguishes five isolated installer targets (Codex, Claude Code, Gemini CLI, Grok Build and Universal) from actual execution: two independent workers on the available host corrected and cross-reviewed two synthetic episodes through approval and batch placement. This does not certify execution on every target or autonomous language quality.
