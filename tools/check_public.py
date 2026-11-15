"""Fail closed on unexpected files and private content in every Git tree."""
from pathlib import Path
import json,re,subprocess,sys
root=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
def fail(message):raise SystemExit(message)
files=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')[:-1]
expected=json.loads((root/'public-files.json').read_text())
if sorted(files)!=expected:fail('Unexpected public inventory')
rules=[rb'/(?:Users|Volumes|home)/[A-Za-z0-9_]',rb'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b',rb'\b(?:'+b'Si'+b'lo|'+b'Dex'+b'ter'+rb')\b',rb'(?i)(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{20,}',rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----']
for row in subprocess.check_output(['git','rev-list','--objects','--all'],cwd=root,text=True).splitlines():
 oid,_,name=row.partition(' ')
 if subprocess.check_output(['git','cat-file','-t',oid],cwd=root,text=True).strip()!='blob':continue
 raw=subprocess.check_output(['git','cat-file','blob',oid],cwd=root)
 if any(re.search(rule,raw) for rule in rules):fail('Private content: '+name)
for name in files:
 p=root/name
 if p.is_symlink() or not p.is_file():fail('Nonregular public file: '+name)
 if re.search(r'\.(?:mkv|mp4|srt|sqlite3|jsonl|zst|zip|pem|key)$',name):fail('Private artifact: '+name)
for doc in root.rglob('*.md'):
 if '.git' in doc.parts:continue
 for link in re.findall(r'\]\(([^)]+)\)',doc.read_text()):
  if '://' not in link and not link.startswith('#') and not (doc.parent/link.split('#')[0]).exists():fail('Broken link: '+str(doc.relative_to(root)))
skill=root/'skills/korean-subtitles-skill'
if not (skill/'SKILL.md').read_text().startswith('---\nname: korean-subtitles-skill\ndescription: '):fail('Invalid skill identity')
manifest=json.loads((root/'skill-files.json').read_text())
actual={str(p.relative_to(skill)):__import__('hashlib').sha256(p.read_bytes()).hexdigest() for p in skill.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
if actual!=manifest:fail('Installed skill manifest mismatch')
print('Public inventory, privacy, links, identity and package hashes passed')
