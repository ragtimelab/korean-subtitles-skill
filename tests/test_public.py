"""Verify the public-release gate rejects realistic bad candidates."""
import hashlib,json,subprocess,sys,tempfile,unittest,uuid
from pathlib import Path

class PublicGate(unittest.TestCase):
    def test_clean_and_rejected_candidates(self):
        gate=Path(__file__).resolve().parents[1]/'tools/check_public.py'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);skill=root/'skills/korean-subtitles-skill';skill.mkdir(parents=True)
            text='---\nname: korean-subtitles-skill\ndescription: Synthetic fixture\n---\n'
            (skill/'SKILL.md').write_text(text)
            (root/'skill-files.json').write_text(json.dumps({'SKILL.md':hashlib.sha256(text.encode()).hexdigest()}))
            names=['public-files.json','skill-files.json','skills/korean-subtitles-skill/SKILL.md']
            (root/'public-files.json').write_text(json.dumps(sorted(names)))
            def git(*args):return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True)
            git('init');git('add','.')
            git('-c','user.name=fixture','-c','user.email=fixture@users.noreply.github.com','commit','-m','synthetic')
            def check():return subprocess.run([sys.executable,str(gate),str(root)],capture_output=True)
            self.assertEqual(check().returncode,0)
            # The filename is approved, but a real-shaped session ID is not.
            (root/'notes.md').write_text(str(uuid.uuid4()))
            names.append('notes.md');(root/'public-files.json').write_text(json.dumps(sorted(names)))
            git('add','.');git('-c','user.name=fixture','-c','user.email=fixture@users.noreply.github.com','commit','-m','synthetic bad content')
            self.assertNotEqual(check().returncode,0)
            (root/'notes.md').write_text('safe')
            git('add','.');git('-c','user.name=fixture','-c','user.email=fixture@users.noreply.github.com','commit','-m','remove from tip')
            self.assertNotEqual(check().returncode,0) # earlier tree is still unsafe
