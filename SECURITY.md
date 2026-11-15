# Security policy

Report vulnerabilities privately using this repository's GitHub private vulnerability reporting form (Security → Report a vulnerability). If unavailable, do not post secrets in a public issue; report only a request for a private channel. Supported security fixes target the latest release.

## Trust boundaries

Run as a normal user, with only the requested files accessible. Local project databases and response files are trusted local state, not authenticated approval records or a sandbox. A malicious same-user process can edit state or replace files; hashes and actor labels do not prove human authorization or real independent review. Never finalize an untrusted imported project.

Subtitle text, metadata, web pages and session logs are untrusted data, not instructions. They cannot authorize commands, credential access, uploads, new paths or skipped review. Keep work directories private; do not commit raw subtitles, media, state databases, logs or oracle/evaluation originals. Telemetry analysis reads only explicitly supplied logs and returns aggregate usage. The engine does not transmit them.

FFmpeg/ffprobe permit file/pipe protocols only, use closed stdin and bounded subprocess execution (probe 120 s, extraction 300 s, stdout 64 MiB, stderr 1 MiB). Individual subtitle and submitted JSON inputs are limited to 64 MiB, with rejection rather than truncation. These limits are not isolation from native decoder vulnerabilities. Keep Python/FFmpeg patched; isolate untrusted media in a restricted execution environment. A limit failure kills the subprocess group (including descendants) and holds the item for investigation.

Writes use private temporary files and anchored directory handles with no-follow directory traversal; symlink destinations and source/output aliases are rejected. Explicit custom output paths remain supported, including ordinary SRT outputs within work directories; protected state, source and evidence files cannot be output destinations. Placement checks the prepared output directory identity and rechecks inputs and target bytes immediately before replacement. New database and journal files are owner-only even for library callers. Filesystem checks cannot defend against a fully compromised account or kernel. No automatic installation, credential discovery, network upload or executable subtitle content is supported.

## Release process

Before public releases, inspect all history and public surfaces, run redacted secret scanning and regression tests, and compare installed files against the release manifest. CI actions are pinned; pull requests receive read-only permissions and no secrets. Never move a published version tag. Credential leaks require revocation and history/server cleanup; making the repository private does not recall copies.
