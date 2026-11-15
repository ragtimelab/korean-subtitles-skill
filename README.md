# korean-subtitles-skill

Collect and audit ordinary Korean subtitles against a verified English source of truth, then deliver independently reviewed UTF-8 Korean SRT. Source selection, shared work study, actual separate-worker review and exact-byte placement are mandatory. Code verifies records and consistency; it cannot certify translation quality or worker independence.

## Requirements and installation

Codex with independent worker support, Python 3.10+ on POSIX, and security-patched ffmpeg/ffprobe on PATH. Compressed optional usage logs also need zstd. macOS is locally tested; Linux is checked by CI. Windows is unsupported (fcntl and POSIX file APIs). No model API keys or Python runtime packages are required.

Install only the selected skill to this project's Codex environment:

```sh
npx skills@1.8.1 add ragtimelab/korean-subtitles-skill@v0.1.0 --skill korean-subtitles-skill -a codex
```

Inspect the source and release before installing. Full commit references can replace the release tag. Installation does not authorize processing media. Invoke `$korean-subtitles-skill` with an explicit media path and requested operation; the agent follows skills/korean-subtitles-skill/SKILL.md. All generated work belongs outside the skill source directory. Existing work from a different engine fingerprint requires fresh preparation.

```sh
npx skills@1.8.1 remove korean-subtitles-skill -a codex
```

Updates should select a reviewed release explicitly with the install command. `skills update` can advance source revisions; inspect the result before use. Do not overwrite an existing global/local installation without checking its source and modifications.

## Privacy, security and rights

The engine runs locally and has no upload client. The Skills installer is a separate network tool: `DISABLE_TELEMETRY=1` or `DO_NOT_TRACK=1` disables its telemetry and audit requests. Review SECURITY.md for trust boundaries. Supply only media/subtitles you are authorized to process; no media, subtitle collections, lyrics, private logs or evaluation originals are distributed. MIT covers the repository's own code and instructions, not third-party inputs.

## Development

```sh
python3 tools/test.py
python3 tools/check_public.py
```

Tests use synthetic supplied judgments. They verify mechanics, not autonomous linguistic quality. Private development records and evaluation originals are excluded.
