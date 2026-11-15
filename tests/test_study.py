"""Study order and declarations/bindings only, never inferred comprehension."""
import argparse
import copy
import json
import subprocess
from pathlib import Path
from unittest.mock import patch
from test_subtitles import Fixture, w


class Study(Fixture):
    def fresh(self, selected=True):
        self.work = self.root / 'fresh'
        with patch.object(w, 'command', return_value=json.dumps({'format': {'duration': '30'}, 'streams': []}).encode()):
            w.prepare_one(self.media, self.work)
        self.s, _ = w.load(self.work)
        if selected:
            self.select('en', self.en); self.select('ko', self.ko)

    def group(self, complete=True):
        media = self.root / 'media'; media.mkdir()
        for name in ('one.mkv', 'two.mkv'): (media / name).write_bytes(b'synthetic')
        root = self.root / 'batch'
        with patch.object(w, 'command', return_value=json.dumps({'format': {'duration': '30'}, 'streams': []}).encode()):
            result = w.prepare(argparse.Namespace(media=str(media), work=str(root), target=None))
        paths = [Path(x['project']) for x in result['items']]
        for n, work in enumerate(paths):
            self.s, _ = w.load(work); self.work = work
            self.select('en', self.en)
            if n == 0 or complete: self.select('ko', self.ko)
            self.save()
        self.s, _ = w.load(paths[0]); self.work = paths[0]
        return root, paths

    def test_sources_alone_do_not_enable_correction(self):
        self.fresh()
        self.assertEqual(w.status(self.s)['phase'], 'study_required')
        with self.assertRaises(w.Error): self.view('author')
        self.assertIsNone(self.s['author']); self.assertFalse(self.s['outputs'])
        v = self.view('study', 'coordinator')
        self.assertNotIn('rows', v['submit']); self.assertNotIn('cues', v)
        self.assertEqual(w.status(self.s)['phase'], 'studying')
        self.do(self.study_response(v))
        self.assertEqual(w.status(self.s)['phase'], 'authoring')
        authored = self.view('author')
        self.assertEqual(next(iter(authored)), 'mode')
        self.assertLess(list(authored).index('context_changes'), list(authored).index('cues'))

    def test_whole_request_collection_precedes_study_and_correction(self):
        _, paths = self.group(complete=False)
        self.assertEqual(w.status(self.s)['phase'], 'collecting')
        with self.assertRaises(w.Error): self.view('study', 'coordinator')
        with self.assertRaises(w.Error): self.view('author')
        self.s, _ = w.load(paths[1]); self.work = paths[1]; self.select('ko', self.ko); self.save()
        self.s, _ = w.load(paths[0]); self.work = paths[0]
        self.study()
        first = w.study_record(self.s)
        self.assertEqual(len(first['items']), 2)
        second, _ = w.load(paths[1])
        self.assertEqual(w.study_ready(second)['id'], first['id'])
        self.assertEqual(len(w.context(self.s)['studies']), 1)

    def test_inspection_held_item_blocks_study(self):
        root, _ = self.group()
        p = root / 'batch.json'; manifest = w.read_record(p); manifest['excluded'] = [{'decision': 'held'}]
        w.write_record(p, w.packed(manifest))
        with self.assertRaises(w.Error): self.view('study', 'coordinator')
        self.assertEqual(w.status(self.s)['phase'], 'collecting')

    def test_stale_study_receipt_cannot_bind_replaced_original(self):
        self.fresh(); v = self.view('study', 'coordinator'); p = self.study_response(v)
        replacement = self.root / 'new-en.srt'; replacement.write_bytes(self.en.read_bytes().replace(b'Close', b'Open'))
        self.select('en', replacement)
        with self.assertRaises(w.Error): self.do(p)
        self.assertFalse(w.context(self.s).get('studies'))

    def test_study_locks_source_selection_before_any_author_reading(self):
        self.fresh(); self.study()
        replacement = self.root / 'new-en.srt'; replacement.write_bytes(self.en.read_bytes())
        with self.assertRaises(w.Error): self.select('en', replacement)
        self.assertFalse(self.s['author_readings'])

    def test_explicit_llm_decision_not_inferred_from_full_fields(self):
        self.fresh(); p = self.study_response(self.view('study', 'coordinator'))
        p['works'][0]['decision'] = None
        with self.assertRaises(w.Error): self.do(p)
        self.assertFalse(w.context(self.s).get('studies'))

    def test_material_gaps_cannot_be_declared_ready(self):
        self.fresh(); p = self.study_response(self.view('study', 'coordinator'))
        p['works'][0]['unresolved'] = [{'question': 'Actual necessary context missing.', 'material': True}]
        with self.assertRaises(w.Error): self.do(p)
        p['works'][0]['decision'] = 'needs_evidence'; self.do(p)
        with self.assertRaises(w.Error): self.view('author')
        self.assertEqual(w.status(self.s)['phase'], 'study_required')

    def test_intentional_uncertainty_and_immaterial_questions_allow_ready(self):
        self.fresh(); p = self.study_response(self.view('study', 'coordinator'))
        p['works'][0]['facts']['overview']['status'] = 'intentional'
        p['works'][0]['unresolved'] = [{'question': 'Unimportant detail is unknown.', 'material': False}]
        self.do(p); v = self.view('author')
        entries = [v for k, v in v['context_changes'].items() if k.startswith('study:')]
        self.assertEqual(entries[0]['unresolved'][0]['material'], False)

    def test_access_failure_alone_cannot_be_study(self):
        self.fresh(); p = self.study_response(self.view('study', 'coordinator'))
        p['works'][0]['evidence']['reference']['access'] = 'unavailable'
        with self.assertRaises(w.Error): self.do(p)

    def test_unrelated_studies_are_not_transmitted_to_each_other(self):
        _, paths = self.group(); p = self.study_response(self.view('study', 'coordinator'))
        a = p['works'][0]; b = copy.deepcopy(a)
        a['items'] = [str(paths[0])]; b['items'] = [str(paths[1])]
        a['facts']['overview']['text'] = 'ONLY_WORK_A'
        b['facts']['overview']['text'] = 'ONLY_WORK_B'
        b['identity']['title'] = 'Another supplied work'; p['works'].append(b)
        self.do(p)
        self.assertIn('ONLY_WORK_A', json.dumps(w.author_metadata(self.s)))
        self.assertNotIn('ONLY_WORK_B', json.dumps(w.author_metadata(self.s)))
        other, _ = w.load(paths[1])
        self.assertNotIn('ONLY_WORK_A', json.dumps(w.metadata(other)))
        self.assertIn('ONLY_WORK_B', json.dumps(w.metadata(other)))

    def test_duplicate_study_items_do_not_partially_commit(self):
        self.fresh(); p = self.study_response(self.view('study', 'coordinator'))
        p['works'].append(copy.deepcopy(p['works'][0]))
        before = w.context(self.s)
        with self.assertRaises(w.Error): self.do(p)
        self.assertEqual(w.context(self.s), before)

    def test_initial_study_cannot_be_submitted_after_correction(self):
        self.fresh(); v = self.view('study', 'coordinator'); self.do(self.study_response(v)); self.author()
        p = self.study_response(v)
        with self.assertRaises(w.Error): self.do(p)

    def test_study_changes_use_existing_impact_and_stale_packet_guards(self):
        v = self.view('author', ids='E4')
        c = w.context(self.s); key = w.study_record(self.s)['facts'][0]
        updated = copy.deepcopy(c['facts'][key]); updated['text'] = 'Revised actual supplied work premise.'
        p = {'kind': 'context', 'worker': 'coordinator', 'base': c['revision'], 'reason': 'Actual source reassessment.', 'facts': {key: updated}}
        with self.assertRaises(w.Error): self.do(p)
        p['impact'] = {'scope': 'local', 'ids': ['E4'], 'reason': 'Actual local effect judgment.'}; self.do(p)
        v['submit']['default_kind'] = 'dialogue'
        with self.assertRaises(w.Error): self.do(v['submit'])
        self.assertTrue(w.sealed(self.s))

    def test_seal_links_study_without_another_understanding_essay(self):
        self.assertNotIn('context', self.s['sealed'])
        self.assertEqual(self.s['sealed']['study'], w.study_record(self.s)['id'])
        self.reviewed(); self.approve()
        c = w.context(self.s); c['studies'] = {}; w.write_record(self.s['context_path'], w.packed(c))
        with self.assertRaises(w.Error): w.finalize(self.s)
        self.assertFalse(Path(self.s['target']).exists())

    def test_post_correction_material_gap_uses_context_not_initial_study_again(self):
        c = w.context(self.s); key = w.study_record(self.s)['facts'][0]
        fact = copy.deepcopy(c['facts'][key]); fact['status'] = 'open'
        self.do({'kind': 'context', 'worker': 'coordinator', 'base': c['revision'],
            'reason': 'A real material question arose during source correction.', 'facts': {key: fact},
            'impact': {'scope': 'episode', 'reason': 'Source question affects this episode.'}})
        self.assertEqual(w.status(self.s)['phase'], 'held')
        self.assertTrue(w.study_record(self.s)['complete'])
        with self.assertRaises(w.Error): self.view('study', 'coordinator')

    def test_parent_study_template_cannot_overwrite_collected_original(self):
        root, _ = self.group(); target = root / 'responses' / 'original.ko.srt'
        target.parent.mkdir(); target.write_bytes(self.ko.read_bytes())
        self.select('ko', target); self.save(); before = target.read_bytes()
        result = subprocess.run(['python3', '-B', w.__file__, 'view', str(root), '--role', 'study',
            '--worker', 'coordinator', '--template', str(target)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('protected', result.stderr)
        self.assertEqual(target.read_bytes(), before)

    def test_later_facts_stay_inside_their_work_profile(self):
        _,paths=self.group()
        response=self.study_response(self.view('study','coordinator'))
        first=copy.deepcopy(response['works'][0]);second=copy.deepcopy(first)
        first['items']=[str(paths[0])];first['identity']={'title':'Supplied work A'}
        second['items']=[str(paths[1])];second['identity']={'title':'Supplied unrelated work B'}
        response['works']=[first,second];self.do(response)
        self.fact(facts={'later':{'text':'Additional supplied work A fact.','status':'established','global':True,'evidence':['script']}})
        other,_=w.load(paths[1]);here,_=w.load(paths[0])
        self.assertIn('fact:later',w.author_metadata(here))
        self.assertNotIn('fact:later',w.author_metadata(other))
        self.assertNotIn('fact:later',w.metadata(other))
        self.assertNotIn('evidence:script',w.author_metadata(other))

    def test_replaced_incomplete_study_claims_are_inactive_and_retirable(self):
        self.fresh();first=self.study_response(self.view('study','coordinator'))
        first['works'][0]['decision']='needs_evidence'
        first['works'][0]['unresolved']=[{'question':'Supplied unresolved source question.','material':True}]
        self.do(first);old=w.study_record(self.s);old_fact=old['facts'][0]
        second=self.study_response(self.view('study','coordinator'));self.do(second)
        self.assertNotIn('fact:'+old_fact,w.author_metadata(self.s))
        self.assertNotIn(old_fact,w.context(self.s)['facts'])
        self.assertIn('fact:'+old_fact,w.context(self.s)['notices'])
