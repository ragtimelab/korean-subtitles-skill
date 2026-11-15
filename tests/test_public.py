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

    def test_working_content_and_historical_modes_metadata_and_artifacts(self):
        gate=Path(__file__).resolve().parents[1]/'tools/check_public.py'
        for case in ('working-contact', 'commit-contact', 'message', 'tag', 'symlink', 'submodule', 'artifact'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root=Path(directory);skill=root/'skills/korean-subtitles-skill';skill.mkdir(parents=True)
                text='---\nname: korean-subtitles-skill\ndescription: Synthetic fixture\n---\n'
                (skill/'SKILL.md').write_text(text)
                (root/'skill-files.json').write_text(json.dumps({'SKILL.md':hashlib.sha256(text.encode()).hexdigest()}))
                names=['public-files.json','skill-files.json','skills/korean-subtitles-skill/SKILL.md']
                (root/'public-files.json').write_text(json.dumps(sorted(names)))
                def git(*args):return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True)
                def commit(message='fixture', address='fixture@users.noreply.github.com'):
                    git('-c','user.name=fixture','-c','user.email='+address,'commit','--allow-empty','-m',message)
                def check():return subprocess.run([sys.executable,str(gate),str(root)],capture_output=True)
                git('init');git('add','.');commit()
                self.assertEqual(check().returncode,0)
                contact='person'+'@'+'example'+'.com'
                if case=='working-contact':
                    (skill/'SKILL.md').write_text(text+contact)
                elif case=='commit-contact': commit(address=contact)
                elif case=='message': commit(str(uuid.uuid4()))
                elif case=='tag':
                    git('-c','user.name=fixture','-c','user.email=fixture@users.noreply.github.com',
                        'tag','-a','fixture','-m',str(uuid.uuid4()))
                else:
                    name='fixture.srt' if case=='artifact' else 'fixture'
                    names.append(name);(root/'public-files.json').write_text(json.dumps(sorted(names)))
                    if case=='symlink': (root/name).symlink_to('public-files.json')
                    elif case=='submodule':
                        oid=git('rev-parse','HEAD').stdout.decode().strip()
                        git('update-index','--add','--cacheinfo','160000,'+oid+','+name)
                    else:(root/name).write_text('supplied synthetic content')
                    git('add','public-files.json')
                    if case!='submodule':git('add',name)
                    commit()
                    # Remove it from the current tree. Its historical presence must still fail.
                    git('rm','--cached',name)
                    if (root/name).is_symlink() or (root/name).exists():(root/name).unlink()
                    names.remove(name);(root/'public-files.json').write_text(json.dumps(sorted(names)))
                    git('add','public-files.json');commit()
                self.assertNotEqual(check().returncode,0)
