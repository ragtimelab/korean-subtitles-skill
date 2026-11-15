"""Admission and lossless compact-display behavior, not automatic language quality."""
import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_subtitles import w, Fixture


def probe(language=None, codec='subrip', title='', **disposition):
    tracks = [] if language is None else [{'index': 3, 'codec_type': 'subtitle', 'codec_name': codec,
        'tags': {'language': language, 'title': title}, 'disposition': disposition}]
    return json.dumps({'format': {'duration': '30'}, 'streams': tracks}).encode()


class Admission(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.media = self.root / 'episode.mkv'
        self.media.write_bytes(b'synthetic-media')
        self.work = self.root / 'work'

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_korean_formats_skip_without_creating_work_or_touching_target(self):
        target = self.media.with_suffix('.ko.srt'); target.write_bytes(b'keep')
        for language in ('ko', 'kor', 'KO-kr', 'ko_KR', 'kor-KR'):
            for codec in ('subrip', 'ass', 'hdmv_pgs_subtitle', 'dvd_subtitle'):
                with self.subTest(language=language, codec=codec), patch.object(w, 'command', return_value=probe(language, codec, 'SDH', forced=1, hearing_impaired=1)) as call:
                    result = w.prepare_one(self.media, self.work)
                    self.assertEqual(result['reason'], 'embedded_korean_present')
                    self.assertEqual(result['decision'], 'skipped')
                    self.assertEqual(call.call_count, 1)
                    self.assertFalse(self.work.exists())
                    self.assertEqual(target.read_bytes(), b'keep')

    def test_audit_requires_explicit_request_and_preserves_admission(self):
        with patch.object(w, 'command', return_value=probe('kor')):
            with self.assertRaises(w.Error): w.prepare_one(self.media, self.work, intent='audit')
            w.prepare_one(self.media, self.work, intent='audit', authorization='내장 한국어를 감사해라')
        state, _ = w.load(self.work)
        self.assertEqual(state['admission']['intent'], 'audit')
        self.assertEqual(state['admission']['authorization'], '내장 한국어를 감사해라')

    def test_conflict_and_probe_failure_hold_without_work(self):
        for raw in (probe('und', title='Korean'), probe('eng', title='한국어'), probe('kor', title='English')):
            with patch.object(w, 'command', return_value=raw):
                self.assertEqual(w.prepare_one(self.media, self.work)['decision'], 'held')
                self.assertFalse(self.work.exists())
        with patch.object(w, 'command', side_effect=w.Error('probe failed')):
            self.assertEqual(w.prepare_one(self.media, self.work)['reason'], 'probe_failed')
            self.assertFalse(self.work.exists())

    def test_inspect_is_read_only_and_non_korean_is_ready(self):
        with patch.object(w, 'command', return_value=probe('eng')):
            result = w.inspect_media(self.media)
        self.assertEqual(result['items'][0]['decision'], 'ready')
        self.assertEqual(list(self.root.iterdir()), [self.media])

    def test_mixed_directory_probes_all_before_first_write_and_reuses_probes(self):
        other = self.root / 'second.mkv'; other.write_bytes(b'synthetic')
        calls = []
        def command(args):
            calls.append(args[-1])
            self.assertFalse(self.work.exists())
            return probe('kor') if Path(args[-1]) == self.media.resolve() else probe('eng')
        args = argparse.Namespace(media=str(self.root), work=str(self.work), target=None)
        with patch.object(w, 'command', side_effect=command): result = w.prepare(args)
        self.assertEqual(len(calls), 2)
        self.assertEqual((result['prepared'], result['skipped'], result['held']), (1, 1, 0))
        manifest = w.read_record(self.work / 'batch.json')
        self.assertEqual(len(manifest['items']), 1)
        self.assertEqual(manifest['excluded'][0]['media'], str(self.media.resolve()))
        self.assertFalse((self.work / 'item-001').exists())

    def test_all_skipped_directory_creates_nothing(self):
        with patch.object(w, 'command', return_value=probe('kor')):
            result = w.prepare(argparse.Namespace(media=str(self.root), work=str(self.work), target=None))
        self.assertEqual(result['prepared'], 0)
        self.assertEqual(result['skipped'], 1)
        self.assertFalse(self.work.exists())

    def test_changed_probe_identity_rejected_before_work(self):
        with patch.object(w, 'command', return_value=probe('eng')): row = w.inspect_one(self.media)
        self.media.write_bytes(b'changed')
        with self.assertRaises(w.Error): w.prepare_one(self.media, self.work, inspection=row)
        self.assertFalse(self.work.exists())

    def test_held_batch_cannot_place(self):
        self.work.mkdir()
        (self.work / 'batch.json').write_text(json.dumps({'items': [], 'excluded': [{'decision': 'held'}]}))
        with self.assertRaises(w.Error): w.finalize_batch(self.work)
        self.assertFalse((self.work / 'placement-results.json').exists())


class CompactDisplay(Fixture):
    def test_equal_output_reference_keeps_distinct_coverage_and_exact_text(self):
        self.revise('E4', '문을 닫아.')
        v = self.view()
        row = next(x for x in v['comparisons'] if x['english']['id'] == 'E4')
        self.assertEqual(row['output'], {'id': 'O4', 'text_ref': 'K3'})
        shown = w.displayed_cues(v)
        self.assertEqual(shown['O4']['text'], shown['K3']['text'])
        self.do(v['submit']); self.approve()
        self.assertEqual(w.pending(self.s), set())

    def test_shared_collected_body_once_and_references_resolve(self):
        v = self.view()
        refs = [x for row in v['comparisons'] for x in row['collected']]
        self.assertEqual(sum(isinstance(x, dict) and x['id'] == 'K1' for x in refs), 1)
        self.assertIn('K1', refs)
        self.assertEqual(set(w.displayed_cues(v)), {'E1','E2','E3','E4','O1','O2','O3','O4','K1','K2','K3'})


if __name__ == '__main__':
    unittest.main()
