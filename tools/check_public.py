"""Fail closed on private content, file types and manifests in every public tree."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import sys

root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]


def fail(message):
    raise SystemExit(message)


def git(*args):
    return subprocess.check_output(['git', *args], cwd=root)


rules = [
    rb'/(?:Users|Volumes|home)/[A-Za-z0-9_]',
    rb'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b',
    rb'(?i)(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{20,}',
    rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
]
email = re.compile(rb'[A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
private_suffix = re.compile(r'\.(?:mkv|mp4|m4v|mov|avi|ts|webm|srt|ass|vtt|sqlite3|db|jsonl|zst|zip|bundle|pem|key)$')
private_docs = {'performance.md', 'performance-history.md', 'development.md'}
skill_prefix = 'skills/korean-subtitles-skill/'


def content(raw, name):
    if any(re.search(rule, raw) for rule in rules):
        fail('Private content: ' + name)
    if any(not item.lower().endswith(b'@users.noreply.github.com') for item in email.findall(raw)):
        fail('Private contact: ' + name)


def validate(files, label):
    expected = json.loads(files['public-files.json'])
    if sorted(files) != expected:
        fail('Unexpected public inventory: ' + label)
    for name, raw in files.items():
        if private_suffix.search(name) or Path(name).name in private_docs:
            fail('Private artifact: ' + name)
        content(raw, name)
    skill = files[skill_prefix + 'SKILL.md']
    if not skill.startswith(b'---\nname: korean-subtitles-skill\ndescription: '):
        fail('Invalid skill identity: ' + label)
    manifest = json.loads(files['skill-files.json'])
    actual = {name[len(skill_prefix):]: hashlib.sha256(raw).hexdigest()
              for name, raw in files.items() if name.startswith(skill_prefix)}
    if actual != manifest:
        fail('Installed skill manifest mismatch: ' + label)
    for name, raw in files.items():
        if not name.endswith('.md'):
            continue
        for link in re.findall(r'\]\(([^)]+)\)', raw.decode()):
            if '://' in link or link.startswith('#'):
                continue
            target = (root / Path(name).parent / link.split('#')[0]).resolve()
            try:
                relative = str(target.relative_to(root))
            except ValueError:
                fail('Escaping documentation link: ' + name)
            if relative not in files:
                fail('Broken link: ' + name)


names = git('ls-files', '-z').decode().split('\0')[:-1]
working = {}
for name in names:
    path = root / name
    if path.is_symlink() or not path.is_file():
        fail('Nonregular public file: ' + name)
    working[name] = path.read_bytes()
validate(working, 'working tree')
skill_root = root / skill_prefix
for path in skill_root.rglob('*'):
    if path.is_symlink() or not (path.is_file() or path.is_dir()):
        fail('Nonregular package entry')
actual = {str(p.relative_to(skill_root)): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in skill_root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
if actual != json.loads(working['skill-files.json']):
    fail('Unexpected installed package file')

blobs = {}
commits = git('rev-list', '--all').decode().splitlines()
for commit in commits:
    # Include messages and author/committer metadata, not just file contents.
    content(git('cat-file', 'commit', commit), 'commit metadata')
    files = {}
    for entry in git('ls-tree', '-rz', '--full-tree', commit).split(b'\0'):
        if not entry:
            continue
        metadata, name = entry.split(b'\t', 1)
        mode, kind, oid = metadata.split()
        if mode not in (b'100644', b'100755') or kind != b'blob':
            fail('Nonregular historical file')
        if oid not in blobs:
            blobs[oid] = git('cat-file', 'blob', oid.decode())
        files[name.decode()] = blobs[oid]
    validate(files, commit[:12])
for ref in git('for-each-ref', '--format=%(objecttype) %(objectname)').decode().splitlines():
    kind, oid = ref.split()
    if kind == 'tag':
        content(git('cat-file', 'tag', oid), 'tag metadata')
print(f'Public inventory, privacy, links, identity and package hashes passed ({len(commits)} commits)')
