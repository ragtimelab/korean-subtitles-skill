"""SQLite failure boundaries and packet contracts on isolated synthetic sources."""
import copy
from pathlib import Path
from unittest.mock import patch
from test_subtitles import Fixture, w

class Storage(Fixture):
    def test_context_and_receipt_rollback_together(self):
        v = self.view('author', ids='E4')
        before = w.context(self.s)
        p = copy.deepcopy(v['submit'])
        p['rows'][0]['text'] = '문 좀 닫아.'
        p['impact'] = {'scope':'local','ids':['E4'],'reason':'Supplied local correction.'}
        real = w.write_record
        def fail(path, content):
            if Path(path).name == 'roles.json':
                raise OSError('injected commit failure')
            return real(path, content)
        with patch.object(w, 'write_record', side_effect=fail), self.assertRaises(OSError):
            self.do(p)
        disk, _ = w.load(self.work)
        self.assertEqual(w.context(disk), before)
        self.assertIn(v['packet'], disk['receipts'])
        self.assertEqual(disk['outputs']['E4']['texts']['E4'], '문을 닫아.')

    def test_state_write_failure_does_not_consume_packet(self):
        v = self.view()
        real = w.write_record
        def fail(path, content):
            if Path(path).name == 'state.json':
                raise OSError('injected final write failure')
            return real(path, content)
        with patch.object(w, 'write_record', side_effect=fail), self.assertRaises(OSError):
            self.do(v['submit'])
        disk, _ = w.load(self.work)
        self.assertIn(v['packet'], disk['receipts'])
        self.assertFalse(disk['reading_workers'].get('reader'))

    def test_active_records_have_one_database(self):
        self.assertTrue((self.work/'state.sqlite3').is_file())
        self.assertFalse((self.work/'state.json').exists())
        self.assertFalse((self.work/'context.json').exists())
        disk, _ = w.load(self.work)
        self.assertEqual(disk['revision'], self.s['revision'])
        self.assertEqual(disk['history'][-1], self.s['history'][-1])
        self.assertEqual(len(disk['history']), len(self.s['history']))

    def test_template_file_replaces_stdout_echo(self):
        v = self.view()
        dest = self.work/'responses'/'review.json'
        w.save_template(self.work, dest, v, [self.s])
        self.assertNotIn('submit', v)
        self.assertEqual(v['response_template'], str(dest.resolve()))
        self.assertIn('packet', w.json.loads(dest.read_text()))

    def test_post_replace_failure_recovers_approved_bytes(self):
        self.reviewed(); self.approve()
        real = w.write_record
        def fail(path, content):
            if Path(path).name == 'placement-results.json' and w.json.loads(content).get('complete'):
                raise OSError('injected completion failure')
            return real(path, content)
        with patch.object(w, 'write_record', side_effect=fail), self.assertRaises(OSError):
            w.finalize(self.s)
        disk, _ = w.load(self.work)
        result = w.finalize(disk)
        self.assertEqual(w.filehash(disk['target']), result['sha256'])

    def test_template_reserved_record_name_is_an_artifact(self):
        v=self.view('author',ids='E4');dest=self.work/'responses'/'state.json'
        w.save_template(self.work,dest,v,[self.s])
        self.assertEqual(w.json.loads(dest.read_text())['packet'],v['packet'])
        self.assertNotIn('submit',v)

    def test_template_metrics_measure_returned_packet(self):
        before=self.s['metrics']['presented_bytes'];v=self.view('author',ids='E4')
        w.save_template(self.work,self.work/'responses'/'packet.json',v,[self.s])
        self.assertEqual(self.s['metrics']['presented_bytes']-before,len(w.packed(v)))
        self.assertEqual(w.load(self.work)[0]['metrics']['presented_bytes'],self.s['metrics']['presented_bytes'])

    def addition(self):
        p=self.view(ids='K2')['submit']
        p['supplements']=[{'id':'S1','origin':'collected','kind':'dialogue','start':6300,'end':8500,
                           'text':'별도 대사.','links':['K2'],'reason':'Supplied collected-only dialogue.'}]
        p['impact']={'scope':'local','ids':['K2'],'reason':'Supplied local addition.'};self.do(p)

    def test_author_s_read_clears_pending(self):
        self.addition();v=self.view('author',ids='S1',pending=True);self.do(v['submit'])
        self.assertNotIn('S1',[x['id'] for x in self.view('author',pending=True)['cues']])

    def test_existing_s_edit_requires_displayed_version(self):
        self.addition();p=self.view('author',ids='K2')['submit']
        p['supplements']=[{**self.s['supplements']['S1'],'text':'고친 별도 대사.'}]
        p['impact']={'scope':'local','ids':['K2'],'reason':'Supplied edit.'}
        with self.assertRaises(w.Error):self.do(p)

    def test_cli_acquires_file_lock_before_database_write_lock(self):
        import fcntl,subprocess,sys,select,sqlite3
        code="""import sys
sys.path.insert(0,sys.argv[1])
import subtitles as w
real=w.fcntl.flock
def mark(fd,mode):
 print('locking',flush=True)
 return real(fd,mode)
w.fcntl.flock=mark
sys.argv=['subtitles.py','status',sys.argv[2]]
raise SystemExit(w.main())
"""
        with (self.work/'.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            child=subprocess.Popen([sys.executable,'-c',code,str(Path(w.__file__).parent),str(self.work)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                self.assertTrue(select.select([child.stdout],[],[],5)[0]);self.assertEqual(child.stdout.readline().strip(),'locking')
                db=sqlite3.connect(self.work/'state.sqlite3',timeout=.2)
                try:db.execute('BEGIN IMMEDIATE');db.rollback()
                finally:db.close()
                fcntl.flock(lock,fcntl.LOCK_UN)
                out,err=child.communicate(timeout=5);self.assertEqual(child.returncode,0,err)
            finally:
                if child.poll() is None:child.kill();child.communicate()

    def test_study_template_bytes_update_shared_study_metrics(self):
        c=w.context(self.s);c['studies']={};w.write_record(self.s['context_path'],w.packed(c))
        before=w.registry(self.s).get('study_metrics',{}).get('presented_bytes',0)
        v=self.view('study','coordinator')
        w.save_template(self.work,self.work/'responses'/'study.json',v,[self.s])
        self.assertEqual(w.registry(self.s)['study_metrics']['presented_bytes']-before,len(w.packed(v)))
