"""Corpus plumbing tests. Fake decoder outputs here are NOT recognition tests."""
import contextlib
import io
import json
import math
from pathlib import Path
import shlex
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import recording_corpus as c


def write_wav(path, seconds=0.2, rate=8000, tone=600, amplitude=9000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b''.join(struct.pack('<h', round(amplitude * math.sin(2*math.pi*tone*i/rate)))
                              for i in range(int(rate*seconds))))
    return path


def reference(speech=None, cw='DE KJ6DZB', **kw):
    return dict(provenance='human', speech=speech, cw=cw, **kw)


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'corpus'
        self.wav = write_wav(self.base / 'archive' / 'one.wav')
        self.config = {'engine': 'cw', 'timeout': 1}

    def collect(self, path=None, session='session1', **kw):
        return c.collect_one(self.root, path or self.wav, session, settle=0, **kw)

    def labeled(self, ref=None, split='test'):
        asset = self.collect(split=split)['id']
        c.annotate(self.root, asset, 'reviewer', ref or reference())
        return asset

    def run_fake(self, text='DE KJ6DZB', **kw):
        # Explicit injected fake; subprocess/real-engine tests are separate.
        return c.evaluate(self.root, ROOT, self.config,
                          runner=lambda *a: {'status': 'measured', 'text': text}, **kw)

    def test_inventory_has_asset_session_and_review(self):
        asset = self.labeled()
        listing = c.inventory(self.root)
        self.assertEqual(listing['recordings'][0]['id'], asset)
        self.assertEqual(listing['recordings'][0]['observations'][0]['session'], 'session1')
        self.assertIsNotNone(listing['recordings'][0]['latest_review'])

    def test_negative_settle_rejected_for_empty_directory(self):
        directory = self.base / 'empty'; directory.mkdir()
        with contextlib.redirect_stderr(io.StringIO()):
            code = c.main(['--root', str(self.root), 'collect', str(directory),
                           '--session', 's', '--settle-seconds', '-1'])
        self.assertEqual(code, 2)

    def test_watch_skips_unchanged_successful_files(self):
        sleeps = [None, KeyboardInterrupt()]
        with mock.patch.object(c, 'collect_one', wraps=c.collect_one) as collect:
            with mock.patch.object(c.time, 'sleep', side_effect=sleeps):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = c.main(['--root', str(self.root), 'collect', str(self.wav.parent),
                                   '--session', 's', '--settle-seconds', '0', '--watch'])
        self.assertEqual(code, 130)
        self.assertEqual(collect.call_count, 1)

    def test_provenance_includes_actual_evaluator(self):
        data = c.provenance(self.base, self.config)
        self.assertEqual(data['evaluator_sha256']['recording_corpus.py'], c.file_hash(Path(c.__file__)))

    def test_failed_structured_status_not_evidence(self):
        for status in ('error', 'failed', 'blocked'):
            config = {'engine': 'external-cw', 'timeout': 2,
                      'command': shlex.join([sys.executable, '-c',
                          "print(" + repr(json.dumps({'status': status, 'text': 'KJ6DZB'})) + ")"])}
            result = c.execute_decoder(ROOT, self.wav, config)
            self.assertEqual(result['status'], 'error')
            self.assertEqual(result['text'], '')

    def test_copy_preserves_source_and_not_a_hardlink(self):
        before = self.wav.read_bytes()
        info = self.collect()
        dest = c.blob_path(self.root, info['id'])
        self.assertEqual(dest.read_bytes(), before)
        self.wav.write_bytes(b'changed original')
        self.assertEqual(dest.read_bytes(), before)

    def test_identical_import_idempotent(self):
        first = self.collect()
        self.assertEqual(self.collect()['status'], 'already_collected')
        self.assertEqual(len(list((self.root / 'audio').glob('*.wav'))), 1)
        self.assertEqual(first['id'], c.file_hash(self.wav))

    def test_independent_observations_share_audio(self):
        self.collect()
        self.collect(session='session2')
        with contextlib.closing(c.open_db(self.root)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM assets').fetchone()[0], 1)

    def test_capture_metadata_and_settings_preserved(self):
        self.wav.with_suffix('.json').write_text(json.dumps({'frequency_hz': 147660000, 'sample_rate': 8000}))
        self.collect(settings={'gain_db': 30})
        with contextlib.closing(c.open_db(self.root)) as db:
            self.assertEqual(json.loads(db.execute('SELECT metadata FROM observations').fetchone()[0])['frequency_hz'], 147660000)
            self.assertEqual(json.loads(db.execute('SELECT settings FROM sessions').fetchone()[0])['gain_db'], 30)

    def test_missing_metadata_explicit(self):
        self.collect()
        with contextlib.closing(c.open_db(self.root)) as db:
            meta = json.loads(db.execute('SELECT metadata FROM observations').fetchone()[0])
            self.assertIn('collection_warning', meta)

    def test_sample_rate_disagreement_rejected(self):
        self.wav.with_suffix('.json').write_text('{"sample_rate":48000}')
        with self.assertRaisesRegex(ValueError, 'sample rate'):
            self.collect()

    def test_partial_filename_rejected(self):
        with self.assertRaises(ValueError):
            self.collect(self.wav.with_suffix('.wav.part'))

    def test_symlink_rejected(self):
        link = self.wav.with_name('link.wav'); link.symlink_to(self.wav)
        with self.assertRaises(ValueError):
            self.collect(link)

    def test_truncated_wav_rejected(self):
        self.wav.write_bytes(self.wav.read_bytes()[:-10])
        with self.assertRaisesRegex(ValueError, 'truncated'):
            self.collect()

    def test_odd_sample_rejected(self):
        self.wav.write_bytes(self.wav.read_bytes()[:-1])
        with self.assertRaises(ValueError):
            self.collect()

    def test_settle_defers_recent_audio(self):
        r = c.collect_one(self.root, self.wav, 'one', settle=60)
        self.assertEqual(r['status'], 'not_settled')

    def test_negative_or_nan_settle_rejected(self):
        for value in (-1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                c.collect_one(self.root, self.wav, 'one', settle=value)

    def test_session_reassignment_rejected(self):
        self.collect(split='train')
        with self.assertRaisesRegex(ValueError, 'session settings'):
            self.collect(split='test')

    def test_settings_change_requires_new_session(self):
        self.collect(settings={'gain': 20})
        with self.assertRaises(ValueError):
            self.collect(settings={'gain': 40})

    def test_exact_duplicate_cannot_cross_splits(self):
        self.collect(split='train')
        with self.assertRaisesRegex(ValueError, 'different split'):
            self.collect(session='other', split='test')

    def test_quality_measured_not_snr(self):
        q = self.collect()['quality']
        self.assertEqual(q['sample_rate'], 8000)
        self.assertEqual(q['frames'], 1600)
        self.assertEqual(q['snr_db'], None)
        self.assertEqual(q['near_full_scale_fraction'], 0)
        self.assertTrue(-30 < q['rms_dbfs'] < -10)

    def test_silence_has_null_rms(self):
        write_wav(self.wav, amplitude=0)
        q = self.collect()['quality']
        self.assertIsNone(q['rms_dbfs'])
        self.assertEqual(q['zero_fraction'], 1)
        c.encode(q)

    def test_nan_settings_rejected(self):
        with self.assertRaises(ValueError):
            self.collect(settings={'gain': float('nan')})

    def test_future_schema_rejected(self):
        with contextlib.closing(c.open_db(self.root)) as db:
            db.execute('PRAGMA user_version=999')
        with self.assertRaises(ValueError):
            c.open_db(self.root)

    def test_reference_cannot_be_machine_truth(self):
        asset = self.collect()['id']
        with self.assertRaises(ValueError):
            c.annotate(self.root, asset, 'bot', dict(provenance='prediction', speech='hello', cw=None))

    def test_references_append_not_overwrite(self):
        asset = self.labeled()
        c.annotate(self.root, asset, 'reviewer2', reference(cw='DE W1AW'))
        with contextlib.closing(c.open_db(self.root)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 2)

    def test_reference_types_and_missing_targets(self):
        for ref in (reference(speech=7), reference(speech=None, cw=None),
                    reference(protected='yes'), reference(cw='', callsigns=['KJ6DZB']),
                    reference(required_tokens=['147.66']), reference(callsigns=[''])):
            with self.assertRaises(ValueError):
                c.validate_reference(ref)

    def test_null_vs_empty_reference(self):
        self.assertIsNone(c.score(reference(cw=None, speech='hi'), 'CQ', 'cw'))
        m = c.score(reference(cw=''), 'CQ', 'cw')
        self.assertIsNone(m['wer'])
        self.assertTrue(m['false_text'])

    def test_cw_unknown_marks_not_silently_discarded(self):
        self.assertFalse(c.score(reference(cw='KJ6DZB'), 'KJ6DZB?', 'cw')['exact'])
        self.assertTrue(c.score(reference(cw=''), '?', 'cw')['false_text'])

    def test_word_error_rate_not_clamped(self):
        m = c.score(reference(speech='hello', cw=None), 'hello many extra words', 'speech')
        self.assertEqual(m['wer'], 3)

    def test_callsign_and_numeric_details(self):
        m = c.score(reference(callsigns=['KJ6DZB']), 'DE W1AW', 'cw')
        self.assertEqual(m['callsigns']['missed'], ['KJ6DZB'])
        self.assertEqual(m['callsigns']['extra'], ['W1AW'])
        m = c.score(reference(speech='go to 147.66', required_tokens=['147.66']), 'go to 147.76', 'speech')
        self.assertFalse(m['required_tokens']['147.66'])

    def test_unreviewed_measured_is_unscored(self):
        self.collect(split='test')
        out = self.run_fake()
        self.assertEqual(out['summary']['scored'], 0)
        self.assertEqual(out['summary']['statuses'], {'measured': 1})

    def test_reference_never_sent_to_decoder(self):
        self.labeled()
        def fake(repo, wav, config):
            self.assertNotIn('reference', config)
            self.assertNotIn('KJ6DZB', c.encode(config))
            return {'status': 'measured', 'text': ''}
        out = c.evaluate(self.root, ROOT, self.config, runner=fake)
        self.assertEqual(out['summary']['word_error_rate'], 1)

    def test_blocked_not_a_pass_or_zero_error(self):
        self.labeled()
        out = c.evaluate(self.root, ROOT, self.config, runner=lambda *a: {'status': 'blocked', 'error': 'no weights'})
        self.assertEqual(out['summary']['statuses'], {'blocked': 1})
        self.assertIsNone(out['summary']['word_error_rate'])

    def test_error_is_preserved(self):
        self.labeled()
        def broken(*a):
            raise ValueError('fixture failure')
        out = c.evaluate(self.root, ROOT, self.config, runner=broken)
        self.assertEqual(out['summary']['statuses'], {'error': 1})

    def test_invalid_result_not_a_measurement(self):
        self.labeled()
        for value in ([], {'status': 'measured', 'text': 3}, {'status': 'pass', 'text': 'CQ'}):
            r = c.evaluate(self.root, ROOT, self.config, runner=lambda *a: value)
            self.assertEqual(r['summary']['statuses'], {'error': 1})

    def test_audio_integrity_checked(self):
        asset = self.labeled()
        c.blob_path(self.root, asset).write_bytes(b'corrupt')
        runner = mock.Mock()
        out = c.evaluate(self.root, ROOT, self.config, runner=runner)
        runner.assert_not_called()
        self.assertEqual(out['summary']['statuses'], {'error': 1})

    def test_empty_selection_is_not_success(self):
        with self.assertRaisesRegex(ValueError, 'no recordings'):
            self.run_fake()

    def test_timeout_validation(self):
        self.labeled()
        with self.assertRaises(ValueError):
            c.evaluate(self.root, ROOT, {'engine':'cw','timeout':float('nan')})

    def test_aborted_run_keeps_entire_denominator(self):
        self.labeled()
        second = write_wav(self.wav.with_name('two.wav'), tone=720)
        self.collect(second, split='test')
        def interrupted(*a):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            c.evaluate(self.root, ROOT, self.config, runner=interrupted)
        with contextlib.closing(c.open_db(self.root)) as db:
            run = db.execute('SELECT id FROM runs').fetchone()[0]
        rep = c.report(self.root, run)
        self.assertEqual(rep['summary']['attempted'], 2)
        self.assertEqual(rep['summary']['statuses'], {'not_completed': 2})

    def test_comparison_pairs_frozen_reference(self):
        self.labeled()
        a = self.run_fake('DE W1AW')['run']
        b = self.run_fake()['run']
        comp = c.compare(self.root, a, b)
        self.assertEqual(comp['paired'], 1)
        self.assertEqual(comp['pairs'][0]['word_error_delta'], -1)

    def test_reference_change_excluded_from_comparison(self):
        asset = self.labeled()
        a = self.run_fake()['run']
        c.annotate(self.root, asset, 'corrected', reference(cw='CQ'))
        b = self.run_fake()['run']
        self.assertEqual(c.compare(self.root, a, b)['paired'], 0)
        self.assertEqual(c.report(self.root, a)['summary']['exact_positive_clips'], 1)

    def test_split_and_origin_selection(self):
        self.collect(split='test', origin='synthetic')
        with self.assertRaises(ValueError):
            self.run_fake(origin='rf')
        out = self.run_fake(origin='synthetic')
        rep = c.report(self.root, out['run'])
        self.assertEqual(rep['results'][0]['captures'][0]['origin'], 'synthetic')

    def test_retention_never_deletes_high_score(self):
        asset = self.labeled()
        c.evaluate(self.root, ROOT, self.config, runner=lambda *a: {'status':'measured','text':'DE KJ6DZB','confidence':1.0})
        before = c.blob_path(self.root, asset).read_bytes()
        audit = c.retention_plan(self.root)
        self.assertFalse(audit['deletion_enabled'])
        self.assertEqual(audit['decisions'][0]['action'], 'keep')
        self.assertIn('protected_reference_or_holdout', audit['decisions'][0]['reasons'])
        self.assertEqual(c.blob_path(self.root, asset).read_bytes(), before)

    def test_retention_protects_any_historical_pin_and_failures(self):
        asset = self.labeled(reference(protected=True), split='train')
        self.run_fake('garbage', split='train')
        c.annotate(self.root, asset, 'next reviewer', reference(protected=False))
        reasons = c.retention_plan(self.root)['decisions'][0]['reasons']
        self.assertIn('protected_reference_or_holdout', reasons)
        self.assertIn('failure_or_disagreement', reasons)

    def test_cli_live_queue_guard(self):
        queue = self.base / 'queue'; queue.mkdir()
        with contextlib.redirect_stderr(io.StringIO()):
            code = c.main(['--root', str(self.root), 'collect', str(queue), '--session','s'])
        self.assertEqual(code, 2)

    def test_cli_local_collection(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = c.main(['--root',str(self.root),'collect',str(self.wav.parent),'--session','s','--settle-seconds','0'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'collected')
        self.assertEqual((self.root/'.gitignore').read_text(), '*\n')

    def test_invalid_id_rejected(self):
        with self.assertRaises(ValueError):
            c.blob_path(self.root, '../../elsewhere')


class DecoderProtocolTests(unittest.TestCase):
    """Real process supervision, fake decoder output. Not speech/CW accuracy."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.wav = write_wav(self.base/'a file with KJ6DZB spaces.wav')
        self.config = {'engine':'external-cw','timeout':2}

    def command(self, code):
        script = self.base/'decoder.py'; script.write_text(code)
        return ' '.join([shlex.quote(sys.executable), shlex.quote(str(script)), '{wav}'])

    def execute(self, code):
        return c.execute_decoder(ROOT, self.wav, dict(self.config, command=self.command(code)))

    def test_external_disabled_is_blocked(self):
        self.assertEqual(c.execute_decoder(ROOT, self.wav, self.config)['status'], 'blocked')

    def test_failed_process_cannot_add_text(self):
        result = self.execute("import sys\nprint('KJ6DZB error')\nsys.exit(2)\n")
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['text'], '')

    def test_diagnostic_json_ignored(self):
        result = self.execute("import json,sys\nprint(json.dumps({'decoded':False,'message':'KJ6DZB','input':sys.argv[1]}))\n")
        self.assertEqual(result['text'], '')

    def test_explicit_json_success(self):
        result = self.execute("import json\nprint(json.dumps({'decoded':True,'text':'DE KJ6DZB'}))\n")
        self.assertEqual(result['status'], 'measured')
        self.assertEqual(result['text'], 'DE KJ6DZB')

    def test_spaced_path_one_argument(self):
        result = self.execute("import sys\nassert len(sys.argv)==2\nassert ' ' in sys.argv[1]\nprint('CQ')\n")
        self.assertEqual(result['text'], 'CQ')

    def test_timeout_is_error(self):
        self.config['timeout'] = 0.1
        result = self.execute("import time\ntime.sleep(5)\nprint('KJ6DZB')\n")
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['text'], '')

    def test_missing_speech_dependency_reported_blocked(self):
        # Isolated replica of the entry point + deliberately missing dependency;
        # doesn't depend on whether the test host happens to have Whisper installed.
        repo = self.base/'isolated'; (repo/'scripts').mkdir(parents=True)
        shutil.copyfile(ROOT/'scripts/corpus_decode.py', repo/'scripts/corpus_decode.py')
        (repo/'scripts/transcribe_worker.py').write_text("raise ModuleNotFoundError('test dependency unavailable')\n")
        result = c.execute_decoder(repo, self.wav, dict(engine='speech', timeout=3, model='small.en', device='cpu', compute_type='int8'))
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(result['real_inference_run'])

    def test_cli_run_blocked_returns_nonzero_and_persists(self):
        corpus = self.base/'corpus'
        c.collect_one(corpus, self.wav, 'session', split='test', settle=0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = c.main(['--root',str(corpus),'run','--engine','external-cw'])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue())['summary']['statuses'], {'blocked':1})


if __name__ == '__main__':
    unittest.main()
