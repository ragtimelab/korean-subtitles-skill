#!/usr/bin/env python3
"""Synthetic mechanical regressions, never a claim of automatic linguistic judgment."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('subtitle_engine_tests', Path(__file__).resolve().parents[1] / 'skills/korean-subtitles-skill/scripts/subtitles.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
PRESENTATION = {'checked': 'Synthetic original supplied text/layout inspected.', 'preserved': 'Supplied SRT layout.', 'limitations': [], 'resolved': True}
EN = ('1\n00:00:01,000 --> 00:00:03,000\nMy friends are waiting.\n\n'
      '2\n00:00:03,000 --> 00:00:05,000\n- <i>I</i> saved you.\n- I know.\n\n'
      '3\n00:00:06,000 --> 00:00:08,000\nUnless you come, I cannot go.\n\n'
      '4\n00:00:08,000 --> 00:00:10,000\nClose the door.\n').encode()
KO = ('1\n00:00:01,200 --> 00:00:05,100\n친구들이 기다려. 네가 날 구했잖아.\n\n'
      '2\n00:00:06,300 --> 00:00:08,500\n네가 오면 난 못 가.\n\n'
      '3\n00:00:08,500 --> 00:00:10,500\n문을 닫아.\n').encode()
TEXTS = ['내 친구들이 기다려.', '- <i>내가</i> 널 구했잖아.\n- 알고 있어.', '네가 오지 않으면 난 못 가.', '문을 닫아.']


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.media = self.root / 'synthetic.mkv'; self.media.write_bytes(b'synthetic-media')
        self.work = self.root / 'work'
        with patch.object(w, 'command', return_value=json.dumps({'format': {'duration': '30'}, 'streams': []}).encode()):
            w.prepare_one(self.media, self.work)
        self.s, _ = w.load(self.work)
        self.en = self.root / 'en.srt'; self.en.write_bytes(getattr(self, 'EN', EN))
        self.ko = self.root / 'ko.srt'; self.ko.write_bytes(getattr(self, 'KO', KO))
        self.select('en', self.en); self.select('ko', self.ko)
        self.study()
        self.author(); self.seal()

    def tearDown(self): self.tmp.cleanup()
    def do(self, p): return w.submit(self.s, p, self.work)
    def source_record(self, lang, path):
        return {'kind': 'source', 'language': lang, 'origin': 'external' if lang == 'en' else 'sidecar', 'path': str(path),
                'automatic': False, 'verified_language': True, 'complete': True, 'release_match': True,
                'ordinary': True, 'generated': False, 'translator_status': 'unknown', 'provenance': {'file': str(path)},
                'evidence': 'Supplied synthetic source inspected.', 'presentation_evidence': copy.deepcopy(PRESENTATION),
                'profile': 'ordinary', 'selection_reason': 'Only supplied English.', 'fallback_reason': 'No embedded tracks.',
                'candidate_assessments': [{'origin': 'external', 'path': str(path), 'profile': 'ordinary', 'usable': True, 'evidence': 'Synthetic inspected source.'}]}
    def select(self, lang, path): return self.do(self.source_record(lang, path))
    def study(self):
        return self.do(self.study_response(self.view('study', 'coordinator')))
    def study_response(self, v):
        doc = self.root / 'work-reference.txt'
        doc.write_text('Supplied synthetic complete work reference: participants, causal sequence and uncertainty. Not a web observation.')
        p = copy.deepcopy(v['submit']); group = p['works'][0]
        group['identity'] = {'title': 'Supplied synthetic work'}
        group['evidence'] = {'reference': {'kind': 'selected_text', 'ref': 'supplied synthetic external work reference',
            'path': str(doc), 'access': 'body', 'observation': 'Synthetic source supplied for mechanical study gating.'}}
        group['facts'] = {'overview': {'text': 'Supplied complete synthetic causal sequence, participant relations and intentional uncertainty.',
            'status': 'established', 'evidence': ['reference'], 'global': False}}
        group['decision'] = 'ready'
        return p
    def view(self, role='reviewer', worker=None, **extra):
        return w.view(self.s, argparse.Namespace(**{'role':role, 'worker':worker or ('author' if role == 'author' else 'reader'), 'ids':None, 'offset':0, 'limit':200, 'pending':False, **extra}))
    def author(self):
        v = self.view('author')
        p = v['submit']; p['default_kind'] = 'dialogue'
        for row, text, links in zip(p['rows'], getattr(self, 'TEXTS', TEXTS), [['K1'], ['K1'], ['K2'], ['K3']]):
            row.update(text=text, ko=links)
        return self.do(p)
    def seal(self): return self.do({'kind': 'seal', 'worker': 'author', 'unresolved': []})
    def metadata(self, worker='reader'):
        v = self.view('metadata', worker); self.do(v['submit']); return v
    def read(self, worker='reader', **extra):
        v = self.view('reviewer', worker, **extra); self.do(v['submit']); return v
    def reviewed(self): self.read()
    def approve(self, worker='reader'): return self.do({'kind': 'approval', 'worker': worker})
    def revise(self, en='E4', text='문 좀 닫아.', scope='local', ids=None):
        v = self.view('author', ids=en)
        p = v['submit']; p['default_kind'] = 'dialogue'; p['rows'][0]['text'] = text
        p['impact'] = {'scope': scope, 'ids': ids or [en], 'reason': 'Synthetic actual correction impact judgment.'}
        return self.do(p)
    def fact(self, **extra):
        c = w.context(self.s)
        return self.do({'kind': 'context', 'worker': 'author', 'base': c['revision'],
            'reason': 'Synthetic supplied supporting observation.',
            'evidence': {'script': {'kind': 'selected_text', 'ref': 'supplied synthetic script', 'path': str(self.en), 'access': 'body', 'observation': 'Synthetic original identity.'}},
            'facts': {'name': {'text': 'Synthetic principal identity with Korean form evidence.', 'status': 'established', 'evidence': ['script'], 'global': False}}, **extra})
    def save(self): w.write_record(self.work / 'state.json', w.packed(self.s))


class Core(Fixture):
    def test_ssot_layout_and_raw_korean_preserved(self):
        final = w.compiled(self.s)
        self.assertEqual([x['text'] for x in final], TEXTS)
        self.assertEqual([(x['start'], x['end']) for x in final], [(x['start'], x['end']) for x in self.s['sources']['en']['cues']])
        self.assertEqual(Path(self.s['sources']['ko']['path']).read_bytes(), KO)
    def test_no_minimum_edit_limit(self):
        self.revise('E1', '내 친구들이 여기서 우리를 기다리고 있어.')
        self.assertIn('우리를', w.compiled(self.s)[0]['text'])
    def test_structure_override_is_rejected_atomically(self):
        before = w.packed(self.s['outputs'])
        for text in ['문을\n닫아.', '<i>문을 닫아.</i>', '문을 닫아?']:
            with self.subTest(text=text), self.assertRaises(w.Error): self.revise(text=text)
        self.assertEqual(before, w.packed(self.s['outputs']))
    def test_no_model_hash_or_management_fields(self):
        p = self.view('author', ids='E4')['submit']; p['hash'] = 'made-up'
        with self.assertRaises(w.Error): self.do(p)
    def test_default_classification_is_an_author_decision(self):
        p = self.view('author', ids='E4')['submit']; p['rows'][0]['kind'] = 'dialogue'
        self.do(p)
        p = self.view('author', ids='E4')['submit']; p['rows'][0].pop('kind')
        with self.assertRaises(w.Error): self.do(p)
    def test_packet_must_return_all_and_only_english_rows(self):
        p = self.view('author')['submit']; p['rows'].pop()
        with self.assertRaises(w.Error): self.do(p)
    def test_unknown_collected_correspondence_rejected(self):
        p = self.view('author', ids='E4')['submit']; p['default_kind'] = 'dialogue'; p['rows'][0]['ko'] = ['K999']
        with self.assertRaises(w.Error): self.do(p)
    def test_old_contract_never_migrates(self):
        self.s['contract'] = 'english-ssot-output-v2'; self.save()
        with self.assertRaises(w.Error): w.load(self.work)
    def test_missing_output_or_source_coverage_blocks_seal(self):
        self.s['outputs'].pop('E4')
        with self.assertRaises(w.Error): self.seal()
        self.s['author_readings'].pop('K3')
        with self.assertRaises(w.Error): self.seal()
    def test_material_author_question_blocks(self):
        self.reviewed(); self.s['sealed']['unresolved'] = ['Unknown consequential referent.']
        with self.assertRaises(w.Error): self.approve()
    def test_same_packet_cannot_be_replayed(self):
        v = self.view('author', ids='E4'); p = v['submit']; p['default_kind'] = 'dialogue'; self.do(p)
        with self.assertRaises(w.Error): self.do(p)
    def test_stale_author_packet_rejected(self):
        p = self.view('author', ids='E4')['submit']; p['default_kind'] = 'dialogue'
        self.revise()
        with self.assertRaises(w.Error): self.do(p)
    def test_author_cannot_change(self):
        with self.assertRaises(w.Error): self.view('author', 'other')
    def test_wrong_korean_semantics_is_not_falsely_machine_rejected(self):
        # Semantic behavior belongs in real independent evaluation, not a regex oracle.
        self.revise('E1', '친구들이 기다려.')
        self.assertEqual(w.compiled(self.s)[0]['text'], '친구들이 기다려.')
    def test_parser_rejects_bad_encoding_blocks_and_times(self):
        for raw in [b'\xff', b'not srt', b'1\n00:00:03,000 --> 00:00:02,000\nX\n', b'1\n00:00:01,000 --> 00:00:02,000\nX\n\nbad']:
            with self.subTest(raw=raw), self.assertRaises((w.Error, UnicodeError)): w.parse(raw, 'E')


class Review(Fixture):
    def external_evidence(self, status='established', material=None):
        c=w.context(self.s)
        fact={'text':'Synthetic source-error judgment, supplied by the test worker.',
              'status':status,'global':False,'evidence':['alternate']}
        if material is not None: fact['material']=material
        return self.do({'kind':'context','worker':'author','base':c['revision'],
            'reason':'Record the inspected synthetic comparison.',
            'evidence':{'alternate':{'kind':'external_subtitle','ref':'https://example.invalid/fixture',
                        'access':'body','observation':'Synthetic alternate; release origin unverified.'}},
            'facts':{'source_error':fact}})
    def test_external_source_judgment_is_reviewed_without_changing_original(self):
        original=self.en.read_bytes();self.reviewed();self.approve();self.external_evidence()
        self.metadata();p=self.view(ids='E1')['submit']
        p['corrections']=[{'id':'E1','text':'내 친구들이 여기서 기다려.','facts':['source_error']}]
        p['impact']={'scope':'local','ids':['E1'],'reason':'Synthetic evidence-grounded correction.'}
        self.do(p);self.assertFalse(w.approval_valid(self.s))
        self.assertEqual(self.en.read_bytes(),original)
        v=self.view(ids='E1');self.assertEqual(v['comparisons'][0]['english']['text'],'My friends are waiting.')
        self.assertEqual(v['comparisons'][0]['facts'],['source_error'])
        self.read(pending=True);self.approve();self.assertTrue(w.approval_valid(self.s))
    def test_material_open_source_judgment_blocks_until_resolved(self):
        self.external_evidence(status='open');self.metadata();p=self.view(ids='E1')['submit']
        p['corrections']=[{'id':'E1','facts':['source_error']}]
        p['impact']={'scope':'local','ids':['E1'],'reason':'Unresolved synthetic source discrepancy.'}
        self.do(p);self.read(pending=True)
        with self.assertRaises(w.Error): self.approve()
        self.assertEqual(self.s['outputs']['E1']['texts']['E1'],TEXTS[0])
    def test_changed_external_evidence_invalidates_consumers(self):
        self.external_evidence();self.metadata();p=self.view(ids='E1')['submit']
        p['corrections']=[{'id':'E1','facts':['source_error']}]
        p['impact']={'scope':'local','ids':['E1'],'reason':'Attach inspected synthetic evidence.'}
        self.do(p);self.read(pending=True);self.approve()
        c=w.context(self.s);e=copy.deepcopy(c['evidence']['alternate'])
        e['observation']='The synthetic alternate is related to the selected source, not independent.'
        self.do({'kind':'context','worker':'author','base':c['revision'],'reason':'Correct provenance assessment.',
                 'evidence':{'alternate':e},'impact':{'scope':'local','ids':['E1'],'reason':'Reassess the affected claim.'}})
        self.assertFalse(w.approval_valid(self.s));self.assertIn('E1',w.pending(self.s))
        with self.assertRaises(w.Error):self.approve()
        self.read(pending=True);self.approve()
    def collected_addition(self, **change):
        p=self.view(ids='K2')['submit']
        p['supplements']=[{'id':'S1','origin':'collected','kind':'dialogue','start':6300,'end':8500,
                           'text':'별도 대사.','links':['K2'],'reason':'Synthetic collected-only dialogue.',**change}]
        p['impact']={'scope':'local','ids':['K2'],'reason':'Synthetic local addition.'}
        return self.do(p)
    def test_collected_addition_is_source_linked_reviewed_and_approved(self):
        self.reviewed();self.approve();self.collected_addition()
        self.assertFalse(w.approval_valid(self.s))
        self.assertIn('S1',w.pending(self.s))
        v=self.view(ids='S1');self.assertEqual(v['supplements'][0]['origin'],'collected')
        self.assertTrue(any(c['id']=='K2' for c in v['korean_sources']) or
                        any(isinstance(c,dict) and c['id']=='K2' for row in v['comparisons'] for c in row['collected']))
        self.read(pending=True);self.approve()
        self.assertIn('별도 대사.',w.output_raw(self.s).decode())
    def test_collected_addition_requires_real_korean_source_and_overlap(self):
        for change in [{'links':['E1']},{'links':['K999']},{'links':[]},
                       {'event':'fictional'},{'start':11000,'end':12000}, {'kind':'track_info'}]:
            with self.subTest(change=change),self.assertRaises(w.Error): self.collected_addition(**change)
        self.assertFalse(self.s['supplements'])
    def test_collected_addition_revision_invalidates_linked_coverage(self):
        self.reviewed();self.collected_addition();self.read(pending=True);self.approve()
        p=self.view(ids='S1')['submit'];p['supplements']=[{**self.s['supplements']['S1'],'text':'고친 별도 대사.'}]
        p['impact']={'scope':'local','ids':['K2'],'reason':'Synthetic wording correction.'};self.do(p)
        self.assertIn('K2',w.pending(self.s));self.assertFalse(w.approval_valid(self.s))
    def test_direct_comparison_needs_no_other_reading_stage(self):
        v=self.view();self.assertTrue(v['comparisons']);self.assertTrue(v['context_changes'])
        self.assertEqual(w.displayed_cues(v).keys(),w.Analysis(self.s).u.keys())
        self.do(v['submit']);self.approve()
    def test_author_cannot_review_or_approve_own_episode(self):
        for role in ['reviewer','metadata']:
            with self.subTest(role=role),self.assertRaises(w.Error):self.view(role,'author')
        with self.assertRaises(w.Error):self.approve('author')
    def test_all_english_korean_and_outputs_are_required(self):
        self.reviewed();self.assertEqual(set(self.s['readings']),w.source_ids(self.s)|{'O1','O2','O3','O4'})
    def test_local_revision_preserves_unrelated_readings(self):
        self.reviewed();self.approve();before=w.Analysis(self.s).sig;self.revise();after=w.Analysis(self.s).sig
        self.assertTrue(w.sealed(self.s));self.assertEqual(before['O1'],after['O1']);self.assertNotEqual(before['O4'],after['O4'])
        self.assertEqual(w.pending(self.s),{'E4','K3','O4'});self.assertFalse(w.approval_valid(self.s))
    def test_packet_membership_is_not_a_dependency(self):
        self.reviewed();self.revise('E3','네가 안 오면 난 갈 수 없어.')
        self.assertEqual(w.pending(self.s),{'E3','K2','O3'})
    def test_changed_output_stales_previous_review_packet(self):
        p=self.view()['submit'];self.revise()
        with self.assertRaises(w.Error):self.do(p)
    def test_reviewer_can_correct_without_author_return_or_reseal(self):
        v=self.view();p=v['submit'];p['corrections']=[{'id':'E1','text':'내 친구들이 여기서 기다려.'}]
        p['impact']={'scope':'local','ids':['E1'],'reason':'Supplied source-grounded local correction.'}
        self.do(p);self.assertTrue(w.sealed(self.s));self.assertEqual(w.pending(self.s),{'E1','K1','O1'})
        self.read(pending=True);self.approve();self.assertTrue(w.approval_valid(self.s))
    def test_reviewer_cannot_change_unseen_anchor(self):
        p=self.view(ids='E4')['submit'];p['corrections']=[{'id':'E1','text':'다른 대사.'}]
        with self.assertRaises(w.Error):self.do(p)
    def test_correction_cannot_approve_changed_output_before_confirmation(self):
        p=self.view()['submit'];p['corrections']=[{'id':'E4','text':'문 좀 닫아.'}]
        p['impact']={'scope':'local','ids':['E4'],'reason':'Local correction.'};self.do(p)
        with self.assertRaises(w.Error):self.approve()
        self.read(pending=True);self.approve()
    def test_conflicting_reviewer_corrections_reject_stale_second_result(self):
        p=self.view()['submit'];q=self.view()['submit']
        for proposal,text in [(p,'문 좀 닫아.'),(q,'문을 닫아 줘.')]:
            proposal['corrections']=[{'id':'E4','text':text}]
            proposal['impact']={'scope':'local','ids':['E4'],'reason':'Actual current-source correction.'}
        self.do(p)
        with self.assertRaises(w.Error):self.do(q)
        self.assertEqual(self.s['outputs']['E4']['texts']['E4'],'문 좀 닫아.')
    def test_reviewer_cannot_override_ssot_structure(self):
        p=self.view()['submit'];p['corrections']=[{'id':'E4','text':'문을\n닫아.'}]
        p['impact']={'scope':'local','ids':['E4'],'reason':'Supplied invalid structure.'}
        before=w.packed(self.s['outputs'])
        with self.assertRaises(w.Error):self.do(p)
        self.assertEqual(before,w.packed(self.s['outputs']))
    def test_assigned_reviewer_handover_does_not_borrow_personal_coverage(self):
        self.reviewed();self.approve()
        with self.assertRaises(w.Error):self.view(worker='reader2')
        v=self.view(worker='reader2',reassign='Previous actual reader stopped; explicit handover.')
        self.assertFalse(w.approval_valid(self.s));self.do(v['submit']);self.approve('reader2')
    def test_actual_findings_block_until_current_resolution(self):
        p=self.view()['submit'];p['findings']=[{'ids':['O1'],'question':'Whose friends?'}];self.do(p)
        with self.assertRaises(w.Error):self.approve()
        self.do({'kind':'resolve','worker':'reader','resolutions':{'F1':'Current source and Korean identify the speaker ownership.'}});self.approve()
    def test_resolution_cannot_use_stale_affected_reading(self):
        p=self.view()['submit'];p['findings']=[{'ids':['O4'],'question':'Check source command.'}];self.do(p);self.revise()
        with self.assertRaises(w.Error):self.do({'kind':'resolve','worker':'reader','resolutions':{'F1':'Assurance only.'}})
    def test_declared_global_change_rechecks_all_without_author_reseal(self):
        self.reviewed();self.revise(scope='global');self.assertTrue(w.sealed(self.s))
        self.assertEqual(w.pending(self.s),set(w.Analysis(self.s).u));self.read(pending=True);self.approve()
    def test_author_question_can_be_resolved_by_reviewer(self):
        self.do({'kind':'seal','worker':'author','context':'Complete synthetic work understood with one remaining question.',
                 'unresolved':[{'ids':['E4'],'question':'Verify the imperative against supplied source.'}]})
        self.read();self.do({'kind':'resolve','worker':'reader','resolutions':{'F1':'Actual source is an imperative and the Korean preserves it.'}});self.approve()
    def test_metadata_acknowledgement_is_in_comparison_submission(self):
        v=self.view();self.assertTrue(v['context_changes']);self.do(v['submit'])
        self.assertEqual(self.view(pending=True)['context_changes'],{})
    def test_author_understanding_is_history_not_review_answer_key(self):
        self.s['sealed']['context']='UNIQUE_AUTHOR_CONCLUSION_NOT_IN_REVIEW'
        v=self.view()
        self.assertNotIn('UNIQUE_AUTHOR_CONCLUSION_NOT_IN_REVIEW',json.dumps(v))
        self.assertEqual(len(w.displayed_cues(v)),11)
    def test_compact_pairing_exposes_every_collected_cue_without_duplicates(self):
        v=self.view();ids=[x['id'] for x in v['korean_sources']]+[x['id'] for row in v['comparisons'] for x in row['collected'] if isinstance(x,dict)]
        self.assertEqual(len(ids),len(set(ids)));self.assertEqual(set(ids),{'K1','K2','K3'})
        self.assertEqual(v['comparisons'][0]['english']['text'],'My friends are waiting.')
        self.assertEqual(v['comparisons'][0]['output']['text'],'내 친구들이 기다려.')


class Placement(Fixture):
    def test_collected_original_cannot_be_target(self):
        self.s['target'] = str(self.ko.resolve())
        with self.assertRaises(w.Error): self.select('ko',self.ko)
        with self.assertRaises(w.Error): w.verify_inputs(self.s)
    def test_final_bytes_are_approved_exactly(self):
        self.reviewed(); self.approve(); raw = w.output_raw(self.s); w.finalize(self.s)
        self.assertEqual(Path(self.s['target']).read_bytes(), raw)
    def test_target_guard(self):
        self.reviewed(); self.approve(); Path(self.s['target']).write_text('changed')
        with self.assertRaises(w.Error): w.finalize(self.s)
    def test_source_media_and_original_guards(self):
        self.reviewed(); self.approve()
        for target in [self.media, self.en, Path(self.s['sources']['ko']['path'])]:
            raw = target.read_bytes(); target.write_bytes(raw+b'x')
            with self.subTest(target=target), self.assertRaises(w.Error): w.finalize(self.s)
            target.write_bytes(raw)
    def test_unapproved_output_not_placed(self):
        self.reviewed()
        with self.assertRaises(w.Error): w.finalize(self.s)
        self.assertFalse(Path(self.s['target']).exists())
    def test_atomic_failure_keeps_target(self):
        target = self.root/'write_record'; target.write_bytes(b'old')
        with patch.object(w.os,'replace',side_effect=OSError('injected')), self.assertRaises(OSError): w.atomic(target,b'new')
        self.assertEqual(target.read_bytes(),b'old')
    def test_non_srt_target_is_not_placed(self):
        self.reviewed(); self.approve(); self.s['target'] = str(self.root/'wrong.txt')
        with self.assertRaises(w.Error): w.finalize(self.s)
    def test_bounded_patch_preserves_unrelated_exact_bytes(self):
        self.ko.write_bytes(EN.replace(b'My friends are waiting.', '내 친구들이 기다려.'.encode()).replace(b'- <i>I</i> saved you.\n- I know.', '- <i>내가</i> 널 구했잖아.\n- 알고 있어.'.encode()).replace(b'Unless you come, I cannot go.', '네가 오지 않으면 난 못 가.'.encode()).replace(b'Close the door.', '문을 닫아.'.encode()))
        base = self.root/'base.srt'; raw = b'\xef\xbb\xbf'+self.ko.read_bytes().replace(b'\n',b'\r\n'); base.write_bytes(raw)
        scope = self.root/'scope.json'; scope.write_text(json.dumps({'reason':'Synthetic authorized correction','authorization':'Synthetic user request','ids':['E4','K4'],'changes':[{'id':'K4','before':'문을 닫아.','after':'문 좀 닫아.'}]}))
        work = self.root/'patch'
        with patch.object(w,'command',return_value=json.dumps({'format':{'duration':'30'},'streams':[]}).encode()):w.prepare_one(self.media,work,base=base,scope=scope,target=base)
        self.work=work; self.s,_=w.load(work); self.select('en',self.en); self.study()
        p=self.view('author')['submit'];p['default_kind']='dialogue';p['rows'][0].update(text='문 좀 닫아.',ko=['K4']);self.do(p);self.seal()
        self.reviewed();self.approve();w.finalize(self.s)
        self.assertEqual(base.read_bytes(),raw.replace('문을 닫아.'.encode(),'문 좀 닫아.'.encode()))


class MusicContract(unittest.TestCase):
    def lookup(self,**extra):
        return {'outcome':'not_found','evidence':'Synthetic official OST body inspected; performer unresolved.',
            'sources':[{'kind':'official_ost','url':'https://synthetic.invalid/ost','checked_at':'synthetic fixture',
                        'access':'body','evidence':'Synthetic supplied score credits do not identify performer.'}],**extra}
    def test_missing_artist_requires_official_ost_inquiry(self):
        with self.assertRaises(w.Error):w.artist_lookup({'identity':{'title':'Home'}})
        w.artist_lookup({'identity':{'title':'Home'},'artist_lookup':self.lookup()})
    def test_known_source_artist_needs_no_invented_lookup(self):
        self.assertIsNone(w.artist_lookup({'identity':{'title':'Home','artist':'Arlo Penn'}}))
    def test_search_excerpt_or_unofficial_source_cannot_verify_performer(self):
        for change in [{'access':'snippet'},{'kind':'fan_site'},{'url':'not-a-url'}]:
            lookup=self.lookup();lookup['sources'][0].update(change)
            with self.subTest(change=change),self.assertRaises(w.Error):w.artist_lookup({'identity':{'title':'Home'},'artist_lookup':lookup})
    def test_verified_artist_requires_actual_version_link(self):
        lookup=self.lookup(outcome='verified',artist='Arlo Penn')
        with self.assertRaises(w.Error):w.artist_lookup({'identity':{'title':'Home'},'artist_lookup':lookup})
        lookup['version_evidence']='Synthetic official credits identify this work-used vocal version.'
        w.artist_lookup({'identity':{'title':'Home'},'artist_lookup':lookup})
    def test_unavailable_lookup_cannot_add_performer(self):
        lookup=self.lookup(outcome='unavailable',artist='Unverified Singer')
        with self.assertRaises(w.Error):w.artist_lookup({'identity':{'title':'Home'},'artist_lookup':lookup})
    def test_not_found_is_not_body_access_failure(self):
        lookup=self.lookup();lookup['sources'][0]['access']='unavailable'
        with self.assertRaises(w.Error):w.artist_lookup({'identity':{'title':'Home'},'artist_lookup':lookup})
    def test_song_identity_must_match_literal_original_span(self):
        cue={'id':'E1','text':'"Home" by Arlo Penn','start':1000,'end':3000}
        with self.assertRaises(w.Error):w.projection(cue,{'kind':'track_info','identity':{'title':'Different','artist':'Arlo Penn'}})
        text,_=w.projection(cue,{'kind':'track_info','identity':{'title':'Home','artist':'Arlo Penn'}})
        self.assertEqual(text,cue['text'])
    def test_lyric_tail_must_be_classified_after_effect(self):
        text='[engine hums]\n♪ Go home ♪';cue={'id':'E1','text':text,'start':1000,'end':3000}
        with self.assertRaises(w.Error):w.projection(cue,{'kind':'lyrics','parts':{'E1':[{'span':[0,13],'kind':'nonverbal'}]}})
        result,_=w.projection(cue,{'kind':'lyrics','parts':{'E1':[{'span':[0,13],'kind':'nonverbal'},
            {'span':[14,len(text)],'kind':'lyrics'}]}})
        self.assertEqual(result,'♪ Go home ♪')


class MediaIntegration(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg/ffprobe required')
    def test_real_probe_embedded_extraction_directory_and_cli(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            sub = root / 'english.srt'; sub.write_bytes(EN)
            media = root / 'episode.mkv'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=16x16:r=1:d=20', '-i', str(sub), '-map', '0:v', '-map', '1:0', '-c:v', 'ffv1', '-c:s', 'srt', '-metadata:s:s:0', 'language=eng', str(media)], check=True)
            work = root / 'work'
            result = w.prepare_one(media, work)
            s, _ = w.load(work)
            self.assertEqual(len(result['tracks']), 1)
            w.submit(s, {'kind': 'source', 'language': 'en', 'origin': 'embedded', 'stream': 1, 'automatic': False,
                         'verified_language': True, 'complete': True, 'release_match': True, 'evidence': 'Synthetic embedded track',
                         'profile': 'ordinary', 'selection_reason': 'Only embedded full text.',
                         'candidate_assessments': [{'origin': 'embedded', 'stream': 1, 'profile': 'ordinary', 'usable': True, 'evidence': 'Read extracted text.'}],
                         'presentation_evidence': copy.deepcopy(PRESENTATION), 'provenance': {'file': str(media)}}, work)
            self.assertEqual(len(s['sources']['en']['cues']), 4)
            extras = root / 'Extras'; extras.mkdir(); (extras / 'extra.mkv').write_bytes(b'not probed')
            (root / '.korean-subtitles-skill-work').mkdir()
            prepared = w.prepare(argparse.Namespace(media=str(root), work=str(root / 'batch'), target=None))
            self.assertEqual(prepared['prepared'], 1)
            cli = subprocess.run(['python3', str(Path(w.__file__)), 'status', str(work)], capture_output=True, text=True)
            self.assertEqual(cli.returncode, 0, cli.stderr)
            self.assertIn('not_measured', cli.stdout)


    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg/ffprobe required')
    def test_embedded_extraction_preserves_native_pts_with_nonzero_container_start(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);sub=root/'timed.srt'
            sub.write_text('1\n00:00:01,000 --> 00:00:02,000\nFirst.\n\n2\n00:00:03,000 --> 00:00:04,000\nSecond.\n')
            media=root/'offset.mkv'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=black:s=16x16:r=1:d=8',
                '-i',str(sub),'-map','0:v','-map','1:0','-c:v','ffv1','-c:s','srt',
                '-metadata:s:s:0','language=eng','-output_ts_offset','1',str(media)],check=True)
            native=json.loads(subprocess.run(['ffprobe','-v','error','-show_format','-select_streams','s:0',
                '-show_packets','-show_entries','format=start_time:packet=pts_time,duration_time','-of','json',str(media)],
                check=True,capture_output=True,text=True).stdout)
            self.assertGreater(float(native['format']['start_time']),0)
            expected=[(round(float(x['pts_time'])*1000),round((float(x['pts_time'])+float(x['duration_time']))*1000))
                      for x in native['packets']]
            self.assertEqual(expected,[(2000,3000),(4000,5000)])
            work=root/'work';w.prepare_one(media,work);state,_=w.load(work)
            w.submit(state,{'kind':'source','language':'en','origin':'embedded','stream':1,'automatic':False,
                'verified_language':True,'complete':True,'release_match':True,'evidence':'Compared native packet timestamps and supplied synthetic text.',
                'profile':'ordinary','selection_reason':'Only synthetic embedded English.',
                'candidate_assessments':[{'origin':'embedded','stream':1,'profile':'ordinary','usable':True,'evidence':'Native subtitle packets inspected.'}],
                'presentation_evidence':copy.deepcopy(PRESENTATION),'provenance':{'file':str(media)}},work)
            self.assertEqual([(x['start'],x['end']) for x in state['sources']['en']['cues']],expected)

if __name__ == '__main__':
    unittest.main(verbosity=2)
