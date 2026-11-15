#!/usr/bin/env python3
"""Cross-boundary invariants. Supplied judgments are synthetic, not linguistic discovery."""
import copy
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from test_subtitles import Fixture, w, EN, KO


class Decisions(Fixture):
    def use_fact(self):
        self.fact()
        p = self.view('author', ids='E4')['submit']; p['rows'][0]['facts']=['name']
        p['impact']={'scope':'local','ids':['E4'],'reason':'Synthetic fact is used only here.'}
        self.do(p); self.seal()
    def test_fact_ids_are_not_automatic_semantic_proof(self):
        self.use_fact(); self.assertIn('name',self.s['outputs']['E4']['facts'])
        # Fidelity of the supplied interpretation is a real reviewer responsibility.
    def test_context_updates_are_partial_and_engine_hashes_evidence(self):
        self.fact(); c=w.context(self.s)
        self.assertEqual(c['evidence']['script']['hash'], w.filehash(self.en))
        self.assertIn('checked_at',c['evidence']['script'])
        self.assertNotIn('synopsis',c)
    def test_context_base_conflict_preserves_current_state(self):
        self.fact(); before=w.context(self.s)
        with self.assertRaises(w.Error):self.fact(base=0)
        self.assertEqual(before,w.context(self.s))
    def test_context_noop_preserves_evidence_generated_fields(self):
        self.fact(); before=w.context(self.s); self.fact()
        self.assertEqual(before,w.context(self.s))
    def test_local_fact_update_dirties_only_actual_consumers(self):
        self.use_fact(); self.reviewed(); before=w.Analysis(self.s).sig
        self.fact(facts={'name':{'text':'Revised supported synthetic local interpretation.','status':'established','evidence':['script'],'global':False}},impact={'scope':'local','ids':['E4'],'reason':'Only declared local use.'})
        after=w.Analysis(self.s).sig
        self.assertEqual(before['O1'],after['O1']);self.assertNotEqual(before['O4'],after['O4'])
        self.assertEqual(w.pending(self.s),{'E4','K3','O4'})
    def test_new_shared_fact_does_not_automatically_invalidate_previous_work(self):
        self.reviewed();before=w.Analysis(self.s).sig
        self.fact(facts={'new':{'text':'New later information, not a correction to earlier assumptions.','status':'established','evidence':['script'],'global':True}})
        self.assertEqual(before,w.Analysis(self.s).sig);self.assertTrue(w.sealed(self.s))
        self.read(pending=True);self.approve()
    def test_existing_core_revision_requires_explicit_impact_judgment(self):
        self.fact(facts={'name':{'text':'Original shared background.','status':'established','evidence':['script'],'global':True}})
        with self.assertRaises(w.Error):self.fact(facts={'name':{'text':'Corrected core premise.','status':'established','evidence':['script'],'global':True}})
        self.fact(facts={'name':{'text':'Corrected core premise.','status':'established','evidence':['script'],'global':True}},impact={'scope':'global','reason':'Actual correction changes the whole causal interpretation.'})
        self.assertTrue(w.sealed(self.s));self.assertEqual(w.pending(self.s),set(w.Analysis(self.s).u))
    def test_retirement_is_generic_and_explicit_to_current_reader(self):
        self.use_fact(); self.reviewed(); before=w.context(self.s)
        self.do({'kind':'context','worker':'author','base':before['revision'],'reason':'Original reinspection disproves this application.',
                 'remove_facts':['name'],'impact':{'scope':'local','ids':['E4'],'reason':'This was the only actual consumer.'}})
        p=self.view('author',ids='E4')['submit'];p['rows'][0]['facts']=[]
        p['impact']={'scope':'local','ids':['E4'],'reason':'Remove retired dependency after original reassessment.'}
        self.do(p);self.seal();v=self.metadata()
        self.assertTrue(v['entries']['fact:name']['retired'])
        self.assertTrue(w.record_exists(self.work/'context-history'/'000001.json'));self.assertNotIn('name',w.context(self.s)['facts'])
        self.assertIn('E4',w.pending(self.s));self.read(pending=True);self.approve()
    def test_unknown_retirement_or_dangling_evidence_rejected(self):
        self.fact();before=w.context(self.s)
        for fields in [{'remove_facts':['missing']},{'remove_evidence':['script']}]:
            with self.subTest(fields=fields),self.assertRaises(w.Error):self.do({'kind':'context','worker':'author','base':before['revision'],'reason':'Synthetic invalid retraction.',**fields})
            self.assertEqual(before,w.context(self.s))
    def test_evidence_file_guard_is_real(self):
        self.fact();self.en.write_bytes(EN+b'\n')
        with self.assertRaises(w.Error):w.verify_inputs(self.s)
    def test_unread_new_fact_cannot_be_applied_to_old_packet(self):
        p=self.view('author',ids='E4')['submit'];self.fact();p['rows'][0]['facts']=['name']
        with self.assertRaises(w.Error):self.do(p)
    def test_unresolved_material_fact_blocks_approval(self):
        self.use_fact();self.fact(facts={'name':{'text':'Consequential unknown.','status':'open','evidence':['script'],'global':False}},impact={'scope':'local','ids':['E4'],'reason':'Actual unresolved local question.'})
        self.reviewed()
        with self.assertRaises(w.Error):self.approve()
    def test_unchanged_metadata_is_not_retransmitted(self):
        self.use_fact();first=self.metadata();self.assertIn('fact:name',first['entries'])
        self.assertEqual(self.view('metadata')['entries'],{})
        self.fact(facts={'name':{'text':'Changed local judgment.','status':'established','evidence':['script'],'global':False}},impact={'scope':'local','ids':['E4'],'reason':'Synthetic local reassessment.'})
        self.assertEqual(set(self.view('metadata')['entries']),{'fact:name'})
    def test_metadata_ack_is_per_entry_not_whole_bundle(self):
        self.use_fact();p=self.view('metadata')['submit']
        self.fact(facts={'other':{'text':'Independent unused fact.','status':'established','evidence':['script'],'global':False}})
        self.do(p)
        self.assertFalse(w.metadata_pending(self.s,'reader'))
    def test_stale_metadata_entry_cannot_be_acknowledged(self):
        self.use_fact();p=self.view('metadata')['submit']
        self.fact(facts={'name':{'text':'Changed fact.','status':'established','evidence':['script'],'global':False}},impact={'scope':'local','ids':['E4'],'reason':'Same fact changed.'})
        with self.assertRaises(w.Error):self.do(p)
    def test_metadata_only_worker_cannot_borrow_approval(self):
        self.reviewed();self.metadata('new_reader')
        with self.assertRaises(w.Error):self.approve('new_reader')
    def test_source_explanation_can_be_corrected_without_changing_original(self):
        self.reviewed();old=copy.deepcopy(self.s['sources']['ko']);p=self.source_record('ko',self.ko)
        p['presentation_evidence']['checked']='Actually external Korean SRT, not a native English packet.'
        p['impact']={'scope':'episode','reason':'Correct original provenance explanation and reassess current episode.'}
        self.do(p);self.assertEqual(old['hash'],self.s['sources']['ko']['hash'])
        self.assertEqual(KO,Path(self.s['sources']['ko']['path']).read_bytes());self.assertTrue(w.sealed(self.s))
        self.assertTrue(self.s['history'][-1]['previous_sources'])
    def test_different_original_after_reading_requires_fresh_project(self):
        p=self.source_record('ko',self.ko);other=self.root/'other.srt';other.write_bytes(KO);p['path']=str(other)
        with self.assertRaises(w.Error):self.do(p)


    def test_operational_date_does_not_force_semantic_context_rewrite(self):
        self.fact(); c=w.context(self.s); old=c['revision']
        e=copy.deepcopy(c['evidence']['script']); e.pop('checked_at'); e.pop('hash')
        self.do({'kind':'context','worker':'author','base':old,'reason':'Same inspected evidence, no semantic change.','evidence':{'script':e}})
        self.assertEqual(w.context(self.s)['revision'],old)
    def test_reviewer_receives_classification_and_source_parts(self):
        self.reviewed(); v=self.view('reviewer')
        self.assertEqual(v['comparisons'][0]['kind'],'dialogue')
        self.assertNotIn('texts',v['comparisons'][0])
    def test_generic_retirement_preserves_old_actual_application_in_notice(self):
        self.use_fact(); self.do({'kind':'context','worker':'author','base':w.context(self.s)['revision'],
            'reason':'Supplied source disproves prior interpretation.','remove_facts':['name'],
            'impact':{'scope':'local','ids':['E4'],'reason':'Only actual consumer.'}})
        notice=w.context(self.s)['notices']['fact:name']
        self.assertEqual(notice['media'],[self.s['media']]);self.assertIn('previous',notice)


    def test_disposable_index_cannot_hide_output_or_context_changes(self):
        first=w.Analysis(self.s);self.reviewed();self.revise();self.seal();after=w.Analysis(self.s)
        self.assertNotEqual(first.sig['O4'],after.sig['O4']);self.assertEqual(first.sig['O1'],after.sig['O1'])
        self.s['_analysis']['payload']['final'][0]['text']='Corrupted derivative'
        repaired=w.Analysis(self.s);self.assertEqual(repaired.final[0]['text'],'내 친구들이 기다려.')
    def test_cached_derivative_does_not_replace_original_guards(self):
        self.reviewed();self.approve();w.Analysis(self.s);self.ko.write_bytes(KO+b'\n')
        with self.assertRaises(w.Error):w.finalize(self.s)
    def test_empty_index_rebuild_preserves_exact_approval(self):
        self.reviewed();self.approve();self.s.pop('_analysis',None)
        self.assertTrue(w.approval_valid(self.s));self.assertEqual(w.status(self.s)['pending_count'],0)


    def test_reviewer_can_repair_retired_support_without_author_roundtrip(self):
        self.use_fact();self.reviewed();self.approve();c=w.context(self.s)
        self.do({'kind':'context','worker':'reader','base':c['revision'],'reason':'Supplied evidence disproves this application.',
            'remove_facts':['name'],'impact':{'scope':'local','ids':['E4'],'reason':'Only current consumer needs reassessment.'}})
        v=self.view(pending=True);p=v['submit'];p['corrections']=[{'id':'E4','facts':[]}]
        p['impact']={'scope':'local','ids':['E4'],'reason':'Remove retired support after source comparison.'};self.do(p)
        self.assertTrue(w.sealed(self.s));self.read(pending=True);self.approve()
    def test_retired_support_cannot_authorize_final_even_if_read(self):
        self.use_fact();self.reviewed();c=w.context(self.s)
        self.do({'kind':'context','worker':'reader','base':c['revision'],'reason':'Withdraw wrong interpretation.',
            'remove_facts':['name'],'impact':{'scope':'local','ids':['E4'],'reason':'Actual affected consumer.'}})
        self.read(pending=True)
        with self.assertRaises(w.Error):self.approve()
    def test_metadata_only_page_is_not_a_complete_comparison(self):
        v=self.metadata();self.assertTrue(v['entries'])
        with self.assertRaises(w.Error):self.approve()


class Supplement(Fixture):
    def event(self):
        return self.do({'kind':'context','worker':'author','base':w.context(self.s)['revision'],'reason':'Synthetic supplied document visibility observation.',
            'evidence':{'paper':{'kind':'audiovisual','ref':'synthetic supplied frames','access':'body','verified':True,'observed':True,
              'observation':'Synthetic field becomes readable only in later frame.','media':self.s['media'],'modality':'video','interval':[10000,14000],
              'fields':{'name':{'text':'Nora','available':11000,'until':14000}}}}})
    def supplement(self,**extra):
        return {'id':'S1','start':11000,'end':13000,'text':'노라','links':['K3'],'event':'paper','fields':['name'],'kind':'screen_text','reason':'Synthetic actual observed separate field.',**extra}
    def add(self,**extra):
        p=self.view('author',ids='E4,K3')['submit'];p['supplements']=[self.supplement(**extra)]
        p['impact']={'scope':'local','ids':['K3'],'reason':'Actual separate document field.'};return self.do(p)
    def test_field_availability_and_event_reuse(self):
        self.event();self.add();self.seal();self.assertIn('S1',[x['id'] for x in w.compiled(self.s)])
        self.assertEqual(sum(k == 'paper' for k in w.context(self.s)['evidence']),1)
    def test_later_frames_do_not_prove_early_visibility(self):
        self.event()
        with self.assertRaisesRegex(w.Error, 'precedes field'):self.add(start=10000)
        self.assertFalse(self.s['supplements'])
    def test_supplement_must_end_inside_observed_hold(self):
        self.event()
        with self.assertRaisesRegex(w.Error, 'observed hold'):self.add(end=15000)
    def test_missing_event_and_unobserved_video_rejected(self):
        with self.assertRaises(w.Error):self.add()
        self.event();c=w.context(self.s);c['evidence']['paper']['observed']=False;w.write_record(self.s['context_path'],w.packed(c))
        with self.assertRaises(w.Error):self.add()
    def test_omission_is_not_deletion_and_response_reports_actual_state(self):
        self.event();self.add();self.seal();p=self.view('author',ids='E4')['submit'];self.do(p)
        self.assertIn('S1',self.s['supplements'])
        p=self.view('author',ids='E4,K3,S1')['submit'];p['remove_supplements']=[{'id':'S1','reason':'Reinspection disproves applicability.'}]
        p['impact']={'scope':'local','ids':['K3'],'reason':'Remove this supplemental field.'};result=self.do(p)
        self.assertEqual(result['supplements_removed'],['S1']);self.assertEqual(result['supplements_current'],[])
        self.assertTrue(self.s['history'][-1]['previous_supplements'])
    def test_duplicate_supplement_and_invalid_field_rejected(self):
        self.event()
        with self.assertRaises(w.Error):self.add(fields=['absent'])
        p=self.view('author',ids='E4,K3')['submit'];p['supplements']=[self.supplement(),self.supplement()]
        with self.assertRaises(w.Error):self.do(p)
    def test_literal_source_parts_generate_indices(self):
        text='[wind]\nHello.';parts=w.exact_parts({'text':text},[{'text':'[wind]','kind':'nonverbal'},{'text':'Hello.','kind':'dialogue'}])
        self.assertEqual(w.projection({'id':'E1','text':text},{'kind':'dialogue','parts':{'E1':parts}})[0],'Hello.')
    def test_ambiguous_substring_is_not_guessed(self):
        with self.assertRaises(w.Error):w.exact_parts({'text':'Go go Go'},[{'text':'Go','kind':'dialogue'}])


    def test_repeated_literal_occurrence_is_a_judgment_not_offset_math(self):
        parts=w.exact_parts({'text':'Go go Go'},[{'text':'Go','occurrence':2,'kind':'dialogue'}])
        self.assertEqual(parts[0]['span'],[6,8]);self.assertNotIn('occurrence',parts[0])
    def test_one_shared_song_lookup_resolves_without_duplicate_model_records(self):
        lookup={'outcome':'not_found','evidence':'Synthetic official OST inspected; artist absent.',
                'sources':[{'kind':'official_ost','url':'https://synthetic.invalid/ost','access':'body','checked_at':'supplied synthetic fixture','evidence':'Actual supplied fixture body.'}]}
        ctx={'evidence':{'song-version':{'artist_lookup':lookup}}}
        unit={'kind':'track_info','identity':{'title':'Home'},'artist_lookup':'song-version'}
        resolved=w.resolve_lookups(unit,ctx)
        text,_=w.projection({'id':'E1','text':'"Home"'},resolved)
        self.assertEqual(text,'"Home"');self.assertEqual(unit['artist_lookup'],'song-version')
        with self.assertRaises(w.Error):w.resolve_lookups(unit,{'evidence':{}})


class Season(Fixture):
    def make_group(self):
        shared=self.root/'shared'/'context.json';shared.parent.mkdir()
        first_work=shared.parent/'first';second_work=shared.parent/'second'
        second_media=self.root/'second.mkv';second_media.write_bytes(b'synthetic2')
        for media,work in ((self.media,first_work),(second_media,second_work)):
            with patch.object(w,'command',return_value=json.dumps({'format':{'duration':'30'},'streams':[]}).encode()):w.prepare_one(media,work,shared=shared)
            self.s,_=w.load(work);self.work=work;self.select('en',self.en);self.select('ko',self.ko);self.save()
        w.write_record(shared.with_name('batch.json'),w.packed({'contract':w.CONTRACT,'items':[str(first_work),str(second_work)]}))
        self.s,_=w.load(first_work);self.work=first_work;self.study();self.author();self.seal();self.save();first=self.s
        self.s,_=w.load(second_work);self.work=second_work;self.author();self.seal();self.save();second=self.s
        self.s=first;self.work=first_work
        return first,second,first_work,second_work,shared.parent
    def test_review_one_episode_while_another_is_not_ready(self):
        first,second,fw,sw,root=self.make_group();second['sealed']=None;w.write_record(sw/'state.json',w.packed(second))
        self.read();self.approve();self.save();self.assertTrue(w.status(self.s)['approved'])
        with self.assertRaises(w.Error):w.finalize_batch(root)
    def test_episode_order_is_not_a_review_gate(self):
        first,second,fw,sw,root=self.make_group()
        self.s=second;self.work=sw;self.read();self.approve();self.save()
        self.s=first;self.work=fw;self.read();self.approve();self.save()
        self.assertTrue(w.finalize_batch(root)['complete'])
    def test_child_cannot_bypass_requested_batch_preflight(self):
        first,second,fw,sw,root=self.make_group();self.read();self.approve();self.save()
        with self.assertRaises(w.Error):w.finalize(self.s)
        self.assertFalse(Path(first['target']).exists())
    def test_batch_preflight_failure_places_nothing(self):
        first,second,fw,sw,root=self.make_group()
        with self.assertRaises(w.Error):w.finalize_batch(root)
        self.assertFalse(Path(first['target']).exists());self.assertFalse(Path(second['target']).exists())
    def test_batch_preflight_rejects_duplicate_targets(self):
        first,second,fw,sw,root=self.make_group();second['target']=first['target'];w.write_record(sw/'state.json',w.packed(second))
        with self.assertRaises(w.Error):w.finalize_batch(root)

    def test_batch_cross_item_source_rejected_before_any_replace(self):
        first,second,fw,sw,root=self.make_group()
        target=Path(first['target']); target.write_bytes(self.ko.read_bytes())
        first['target_hash']=w.filehash(target)
        second['sources']['ko']['origin_path']=str(target)
        w.write_record(fw/'state.json',w.packed(first));w.write_record(sw/'state.json',w.packed(second))
        before=target.read_bytes()
        with patch.object(w.os,'replace',side_effect=AssertionError('must preflight first')):
            with self.assertRaisesRegex(w.Error,'Batch destination aliases'):w.finalize_batch(root)
        self.assertEqual(target.read_bytes(),before)
        self.assertFalse(Path(second['target']).exists())

    def test_batch_resource_roles_alias_refresh_and_baseline_exception(self):
        import os
        first,second,fw,sw,root=self.make_group()
        a=Path(first['target']); b=Path(second['target'])
        # Only the exact own deployed baseline role is replaceable.
        with patch.object(w,'protected_resources',side_effect=lambda s:[(a,'baseline')] if s is first else []):
            guard=w.batch_destination_guard([first,second])
            guard(first)
        with patch.object(w,'protected_resources',side_effect=lambda s:[(a,'baseline'),(a,'protected')] if s is first else []):
            with self.assertRaises(w.Error):w.batch_destination_guard([first,second])
        victim=root/'source.srt';victim.write_bytes(b'keep')
        with patch.object(w,'protected_resources',side_effect=lambda s:[] if s is first else [(victim,'protected')]):
            guard=w.batch_destination_guard([first,second])
            os.link(victim,a)
            with self.assertRaises(w.Error):guard(first)
            self.assertEqual(victim.read_bytes(),b'keep')
            a.unlink()
        a.write_bytes(b'keep');os.link(a,b)
        with self.assertRaises(w.Error):w.batch_destination_guard([first,second])

    def test_skipped_output_path_is_protected(self):
        first,second,fw,sw,root=self.make_group()
        skipped=Path(first['target']).with_suffix('').with_suffix('.mp4')
        with self.assertRaises(w.Error):
            w.batch_destination_guard([first,second],[{'media':str(skipped),'decision':'skipped'}])

    def test_batch_final_write_guard_rejects_late_cross_item_alias(self):
        import os
        first,second,fw,sw,root=self.make_group()
        unique=root/'unique-original.srt'
        unique.write_bytes(Path(second['sources']['ko']['origin_path']).read_bytes())
        second['sources']['ko']['origin_path']=str(unique)
        w.write_record(sw/'state.json',w.packed(second))
        for s,work in ((first,fw),(second,sw)):
            self.s=s;self.work=work;self.read();self.approve();self.save()
        a=Path(first['target']); original=Path(second['sources']['ko']['origin_path'])
        before=original.read_bytes();real=w.os.fsync
        def alias(fd):
            real(fd)
            if not a.exists():os.link(original,a)
        with patch.object(w.os,'fsync',side_effect=alias):
            with self.assertRaisesRegex(w.Error,'Batch destination aliases'):w.finalize_batch(root)
        self.assertEqual(original.read_bytes(),before)
        self.assertFalse(Path(second['target']).exists())


class SourceAndTelemetry(Fixture):
    def test_usable_embedded_sdh_blocks_lower_priority(self):
        self.s['tracks']=[{'index':2,'codec_name':'subrip','tags':{'language':'eng'},'disposition':{}}]
        p=self.source_record('en',self.en);p['candidate_assessments'].append({'origin':'embedded','stream':2,'profile':'sdh','usable':True,'evidence':'Supplied inspected matching SDH.'})
        with self.assertRaises(w.Error):w.english_selection(self.s,p)
        p['candidate_assessments'][1].update(usable=False,evidence='Actual synthetic edit mismatch.');w.english_selection(self.s,p)
    def test_external_sdh_blocks_external_ordinary(self):
        p=self.source_record('en',self.en);p['candidate_assessments'].append({'origin':'external','path':str(self.root/'higher.srt'),'profile':'sdh','usable':True,'evidence':'Supplied matching SDH.'})
        with self.assertRaises(w.Error):w.english_selection(self.s,p)
    def test_unresolved_presentation_loss_is_rejected(self):
        p=self.source_record('ko',self.ko);p['presentation_evidence']['resolved']=False
        with self.assertRaises(w.Error):self.do(p)
    def test_quota_is_not_computed_from_tokens_and_reasoning_not_added(self):
        log=self.root/'session.jsonl'
        def row(t,n):return {'timestamp':t,'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':n,'cached_input_tokens':n//2,'output_tokens':10,'reasoning_output_tokens':4}},'rate_limits':{'observed':'unchanged raw'}}}
        log.write_text('\n'.join(json.dumps(x) for x in [row('2026-01-01T00:00:00Z',100),row('2026-01-01T00:01:00Z',200),row('2026-01-01T00:01:00Z',200)]))
        u=w.usage_logs([str(log)],'2026-01-01T00:00:30Z','2026-01-01T00:02:00Z')
        self.assertEqual(u['usage']['input_tokens'],100);self.assertEqual(u['usage']['uncached_input_tokens'],50)
        self.assertEqual(u['usage']['output_tokens'],0);self.assertNotIn('quota_cost',u)
    def test_telemetry_millisecond_boundaries_and_duplicate_paths(self):
        log=self.root/'session.jsonl'
        def event(t,n):return {'timestamp':t,'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':n,'cached_input_tokens':0,'output_tokens':n}}}}
        log.write_text('\n'.join(json.dumps(x) for x in [event('2026-01-01T00:00:29.999Z',100),event('2026-01-01T00:00:30.100Z',150),event('2026-01-01T00:00:31.100Z',200)]))
        u=w.usage_logs([str(log),str(log)],'2026-01-01T00:00:30Z','2026-01-01T00:00:31Z')
        self.assertEqual(u['usage']['input_tokens'],50);self.assertEqual(len(u['sessions']),1)
    def test_counter_reset_is_not_reported_as_negative_usage(self):
        log=self.root/'session.jsonl'
        rows=[{'timestamp':t,'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':n}}}} for t,n in [('2026-01-01T00:00:29Z',100),('2026-01-01T00:00:31Z',20)]]
        log.write_text('\n'.join(json.dumps(x) for x in rows))
        with self.assertRaises(w.Error):w.usage_logs([str(log)],'2026-01-01T00:00:30Z','2026-01-01T00:00:32Z')
    def test_concurrent_cli_submissions_cannot_lose_an_update(self):
        proposals=[]
        for index,text in enumerate(['문 좀 닫아.','문을 닫아 줘.']):
            p=self.view('author',ids='E4')['submit'];p['rows'][0]['text']=text
            p['impact']={'scope':'local','ids':['E4'],'reason':'Concurrent synthetic correction.'}
            path=self.root/f'result-{index}.json';path.write_text(json.dumps(p));proposals.append(path)
        self.save()
        processes=[subprocess.Popen(['python3',w.__file__,'submit',str(self.work),str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for path in proposals]
        results=[proc.communicate(timeout=30) for proc in processes]
        self.assertEqual(sorted(proc.returncode for proc in processes),[0,2])
        current,_=w.load(self.work)
        self.assertIn(current['outputs']['E4']['texts']['E4'],['문 좀 닫아.','문을 닫아 줘.'])
        self.assertTrue(any(b'Stale author packet' in err for out,err in results))
    def test_cli_rejects_templates_outside_responses(self):
        self.save();raw=Path(self.s['target']).read_bytes() if Path(self.s['target']).exists() else None
        result=subprocess.run(['python3',w.__file__,'view',str(self.work),'--role','author','--worker','author','--template',str(self.media)],capture_output=True)
        self.assertNotEqual(result.returncode,0);self.assertEqual(self.media.read_bytes(),b'synthetic-media')
        self.assertEqual(Path(self.s['target']).read_bytes() if Path(self.s['target']).exists() else None,raw)


if __name__ == '__main__':unittest.main(verbosity=2)
