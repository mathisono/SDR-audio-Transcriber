from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from helpers import FakeModel, morse, tone, wav
import clip_classifier as classifier
import clip_writer
import enrichment_worker as enrichment
import morseangel_adapter as adapter
import pipeline_store as store
import safe_runtime as safe
import transcribe_worker as worker


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.wav = wav(self.root / 'radio clips' / 'KJ6DZB.wav')

    def command(self, code):
        return shlex.join([sys.executable, '-c', code]) + ' {wav}'


class RuntimeCase(BaseCase):
    def test_paths_are_substituted_after_tokenizing(self):
        self.assertEqual(adapter.build_argv('decode --input {wav}', self.wav), ['decode', '--input', str(self.wav)])

    def test_nonzero_exit_is_not_cw_evidence(self):
        result = classifier.run_external_cw_decoder(self.wav, self.command("print('error KJ6DZB');exit(2)"), 2)
        self.assertFalse(result['decoded'])
        self.assertFalse(result['callsigns'])
        self.assertTrue(result['error'])

    def test_successful_text_is_unverified_not_promoted(self):
        result = classifier.run_external_cw_decoder(self.wav, self.command("print('DE KJ6DZB')"), 2)
        self.assertEqual(result['callsigns'], ['KJ6DZB'])
        self.assertIsNone(result['confidence'])
        self.assertFalse(result['label_candidates'])
        self.assertFalse(result['verified'])

    def test_unconfigured_adapter_json_cannot_mine_filename(self):
        command = shlex.join([sys.executable, str(ROOT / 'scripts/morseangel_adapter.py'), '--json', '--input']) + ' {wav}'
        with patch.dict(os.environ, {'MORSEANGEL_COMMAND': ''}):
            result = classifier.run_external_cw_decoder(self.wav, command, 3)
        self.assertFalse(result['callsigns'])
        self.assertFalse(result['decoded'])

    def test_json_failure_and_diagnostic_message_are_not_text(self):
        for value in ({'decoded': False, 'text': 'KJ6DZB'}, {'error': 'bad', 'text': 'KJ6DZB'},
                      {'message': 'KJ6DZB failed'}, {'input': '/KJ6DZB.wav'}, [], {'text': ['KJ6DZB']}):
            with self.subTest(value=value):
                result = safe.decoded_output(json.dumps(value))
                self.assertFalse(result['decoded'])
                self.assertEqual(result['text'], '')

    def test_success_json_parses_only_text(self):
        value = safe.decoded_output('{"text":"DE KJ6DZB","input":"W1AW.wav","confidence":0.8,"wpm":18}')
        self.assertEqual(value['text'], 'DE KJ6DZB')
        self.assertEqual(value['confidence'], 0.8)
        self.assertEqual(value['wpm'], 18)

    def test_malformed_json_is_not_fallback_text(self):
        result = safe.decoded_output('{"text":"KJ6DZB"')
        self.assertFalse(result['decoded'])
        self.assertTrue(result['error'])

    def test_nonfinite_timeout_rejected(self):
        for value in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                safe.run_command([sys.executable, '-c', 'pass'], value)

    def test_output_limit(self):
        result = safe.run_command([sys.executable, '-c', "print('KJ6DZB'*100000)"], 3)
        self.assertIn('256 KiB', result['error'])
        self.assertFalse(safe.decoded_output(result['stdout'], result['error'])['decoded'])

    def test_timeout_reaps_grandchild(self):
        marker = self.root / 'orphan'
        code = f'import time;from pathlib import Path;time.sleep(1);Path({str(marker)!r}).write_text("bad")'
        parent = f'import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",{code!r}]);time.sleep(10)'
        result = safe.run_command([sys.executable, '-c', parent], 0.25)
        self.assertIn('timed out', result['error'])
        time.sleep(1.1)
        self.assertFalse(marker.exists())

    def test_nested_adapter_timeout_reaps_decoder(self):
        marker = self.root / 'nested-orphan'
        command = self.command(f'import time;from pathlib import Path;time.sleep(1);Path({str(marker)!r}).write_text("bad")')
        result = safe.run_command([sys.executable, str(ROOT / 'scripts/morseangel_adapter.py'), '--input', str(self.wav),
                                   '--command', command, '--timeout', '5'], 0.3)
        self.assertTrue(result['error'])
        time.sleep(1.1)
        self.assertFalse(marker.exists())

    def test_decoders_start_in_parallel(self):
        barrier = threading.Barrier(2)
        def branch(*args, **kwargs):
            barrier.wait(timeout=2)
            return {'decoded': False}
        with patch.object(classifier, 'run_internal_cw_decoder', branch), patch.object(classifier, 'run_external_cw_decoder', branch):
            result = classifier.classify_wav(self.wav)
        self.assertNotIn('error', result['cw_id'])
        self.assertNotIn('error', result['external_cw_decoder'])

    def test_internal_crash_does_not_skip_external(self):
        with patch.object(classifier, 'run_internal_cw_decoder', side_effect=ValueError('broken DSP')):
            result = classifier.classify_wav(self.wav, external_command=self.command("print('DE KJ6DZB')"))
        self.assertIn('broken DSP', result['cw_id']['error'])
        self.assertEqual(result['external_cw_decoder']['callsigns'], ['KJ6DZB'])

    def test_atomic_archive_never_overwrites_different_file(self):
        source, target = self.root / 'source', self.root / 'target'
        source.write_text('new'); target.write_text('old')
        with self.assertRaises(FileExistsError):
            safe.archive_file(source, target)
        self.assertEqual(source.read_text(), 'new')
        self.assertEqual(target.read_text(), 'old')

    def test_second_worker_lock_rejected(self):
        with safe.exclusive_lock(self.root / 'lock'):
            with self.assertRaises(RuntimeError):
                with safe.exclusive_lock(self.root / 'lock'):
                    pass


class CaptureCase(BaseCase):
    def args(self, *extra):
        with patch.object(sys, 'argv', ['writer', '--queue', str(self.root / 'queue'), '--tmp', str(self.root / 'tmp'),
                                      '--sample-rate', '8000', '--threshold', '500', '--min-sec', '0',
                                      '--pre-roll-ms', '0', *extra]):
            return clip_writer.parse_args()

    def capture(self, pcm, *extra):
        writer = clip_writer.ClipWriter(self.args(*extra))
        writer.feed(pcm, final=True)
        writer.close('eof')
        files = sorted((self.root / 'queue').glob('*.wav'))
        durations = []
        for path in files:
            with wave.open(str(path), 'rb') as stream:
                self.assertEqual(stream.getframerate(), 8000)
                self.assertEqual(stream.getsampwidth(), 2)
                durations.append(stream.getnframes() / 8000)
            self.assertTrue(path.with_suffix('.json').exists())
        return files, durations

    def test_eof_publishes(self):
        files, durations = self.capture(tone())
        self.assertEqual(durations, [0.6])
        self.assertEqual(safe.read_json(files[0].with_suffix('.json'))['close_reason'], 'eof')

    def test_fast_replay_hang_is_audio_clocked(self):
        _, durations = self.capture(tone(0.3) + bytes(8000), '--hang-ms', '200')
        self.assertEqual(durations, [0.5])

    def test_max_length_preserves_all_samples(self):
        _, durations = self.capture(tone(1.2), '--max-sec', '0.5')
        self.assertEqual(durations, [0.5, 0.5, 0.2])

    def test_partial_reads_and_odd_final_byte(self):
        writer = clip_writer.ClipWriter(self.args())
        pcm = tone(0.3)
        for i in range(0, len(pcm), 3):
            writer.feed(pcm[i:i + 3])
        writer.feed(b'\x01', final=True)
        writer.close('eof')
        path = next((self.root / 'queue').glob('*.wav'))
        with wave.open(str(path), 'rb') as stream:
            self.assertEqual(stream.readframes(stream.getnframes()), pcm)

    def test_pre_roll_keeps_speech_onset(self):
        _, durations = self.capture(tone(0.2, amplitude=100) + tone(0.3), '--pre-roll-ms', '200')
        self.assertEqual(durations, [0.5])

    def test_invalid_live_settings_ignored(self):
        control = self.root / 'control.json'
        writer = clip_writer.ClipWriter(self.args('--threshold-control', str(control)))
        control.write_text('{"threshold": -5}')
        writer.update_control()
        self.assertEqual(writer.threshold, 500)

    def test_sigterm_finalizes_without_eof(self):
        args = self.args()
        cmd = [sys.executable, str(ROOT / 'scripts/clip_writer.py'), '--queue', args.queue, '--tmp', args.tmp,
               '--sample-rate', '8000', '--threshold', '500', '--min-sec', '0.1']
        with subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            proc.stdin.write(tone(0.3)); proc.stdin.flush()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not list(Path(args.tmp).glob('*.part')):
                time.sleep(0.02)
            proc.send_signal(signal.SIGTERM)
            stdout, stderr = proc.communicate(timeout=3)
            self.assertEqual(proc.returncode, 0, stderr.decode())
        self.assertTrue(list(Path(args.queue).glob('*.wav')))


class SpeechCase(BaseCase):
    def args(self, *extra):
        argv = ['worker', '--once', *extra]
        for name in ('queue', 'processing', 'done', 'failed', 'transcripts'):
            path = self.root / name; path.mkdir(exist_ok=True)
            argv += ['--' + name, str(path)]
        with patch.object(sys, 'argv', argv):
            return worker.parse_args()

    def queued(self, name='test.wav', directory='queue'):
        path = wav(self.root / directory / name)
        path.with_suffix('.json').write_text('{"mode":"nfm","source":"TEST"}')
        return path

    def record(self, name='test.wav'):
        return safe.read_json(self.root / 'done' / (Path(name).stem + '.transcript.json'))

    def test_baseline_api_consumes_generator(self):
        model = FakeModel()
        with patch.object(model, 'transcribe', wraps=model.transcribe) as call:
            text, segments, info = worker.transcribe_file(model, self.wav)
        self.assertEqual(text, 'Test speech was preserved.')
        self.assertEqual(call.call_args.kwargs, {'language': 'en', 'beam_size': 5, 'vad_filter': True})
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]['avg_logprob'], -0.2)

    def test_speech_published_before_any_optional_work(self):
        args = self.args('--enable-classifier', '--enable-cleanup')
        with patch.object(worker, 'run_clip_classifier', side_effect=AssertionError('must not run inline')):
            worker.process_clip(self.queued(), FakeModel(), args)
        record = self.record()
        self.assertEqual(record['raw_text'], 'Test speech was preserved.')
        self.assertEqual(record['enrichment']['status'], 'pending')
        self.assertTrue((self.root / 'done/test.wav').exists())
        self.assertIn('Test speech was preserved.', (self.root / 'transcripts/raw.html').read_text())

    def test_renderer_failure_does_not_lose_raw_speech(self):
        args = self.args()
        with patch.object(store, 'rebuild_views', side_effect=ValueError('broken HTML')):
            worker.process_clip(self.queued(), FakeModel(), args)
        self.assertEqual(self.record()['speech_status'], 'complete')
        self.assertEqual(self.record()['raw_text'], 'Test speech was preserved.')

    def test_checkpoint_survives_archive_failure_and_avoids_repeat_asr(self):
        args = self.args()
        with patch.object(worker, 'finish_checkpoint', side_effect=OSError('injected archive error')):
            with self.assertRaises(OSError):
                worker.process_clip(self.queued(), FakeModel(), args)
        checkpoint = self.root / 'processing/test.transcript.json'
        self.assertEqual(safe.read_json(checkpoint)['raw_text'], 'Test speech was preserved.')
        with patch.object(worker, 'transcribe_file', side_effect=AssertionError('must not repeat ASR')):
            worker.process_clip(self.root / 'processing/test.wav', FakeModel(), args)
        self.assertFalse(checkpoint.exists())
        self.assertEqual(self.record()['speech_status'], 'complete')

    def test_checkpoint_recovery_after_audio_already_archived(self):
        args = self.args()
        with patch.object(worker, 'finish_checkpoint', side_effect=OSError('crash')):
            with self.assertRaises(OSError):
                worker.process_clip(self.queued(), FakeModel(), args)
        safe.archive_file(self.root / 'processing/test.wav', self.root / 'done/test.wav')
        worker.finish_checkpoint(self.root / 'processing/test.transcript.json', args)
        self.assertTrue((self.root / 'done/test.json').exists())
        self.assertEqual(self.record()['raw_text'], 'Test speech was preserved.')

    def test_recovered_legacy_processing_job(self):
        args = self.args()
        path = self.queued(directory='processing')
        worker.process_clip(path, FakeModel(), args)
        self.assertFalse(path.exists())
        self.assertEqual(self.record()['speech_status'], 'complete')

    def test_duplicate_delivery_does_not_replace_enriched_record(self):
        args = self.args()
        original = self.queued()
        pcm = original.read_bytes()
        worker.process_clip(original, FakeModel(), args)
        record = self.record(); record['text'] = 'Already cleaned.'
        store.save_record(Path(args.done), record)
        original.write_bytes(pcm)
        with patch.object(worker, 'transcribe_file', side_effect=AssertionError('duplicate ASR')):
            worker.process_clip(original, FakeModel(), args)
        self.assertEqual(self.record()['text'], 'Already cleaned.')

    def test_post_publication_checkpoint_preserves_enrichment(self):
        args = self.args()
        worker.process_clip(self.queued(), FakeModel(), args)
        raw = self.record()
        checkpoint = self.root / 'processing/test.transcript.json'
        safe.atomic_json(checkpoint, raw)
        updated = dict(raw, text='Already cleaned.')
        store.save_record(Path(args.done), updated)
        worker.finish_checkpoint(checkpoint, args)
        self.assertEqual(self.record()['text'], 'Already cleaned.')

    def test_actual_asr_failure_archives_audio_without_cw_request(self):
        args = self.args('--enable-classifier')
        with patch.object(worker, 'transcribe_file', side_effect=ValueError('model failed')):
            worker.process_clip(self.queued(), FakeModel(), args)
        self.assertEqual(self.record()['speech_status'], 'failed')
        self.assertTrue((self.root / 'failed/test.wav').exists())
        self.assertEqual(self.record()['enrichment']['status'], 'disabled')

    def test_auxiliary_exceptions_and_bad_shapes_keep_speech(self):
        args = self.args('--enable-classifier', '--enable-cleanup')
        for i, failure in enumerate((ValueError('launch failed'), ['bad shape'])):
            name = f'case{i}.wav'
            worker.process_clip(self.queued(name), FakeModel(), args)
            options = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
            with patch.object(enrichment, 'classify_wav', **options), patch.object(enrichment, 'cleanup', side_effect=TimeoutError('cleanup timeout')):
                enrichment.enrich_record(store.record_path(Path(args.done), name), Path(args.done), Path(args.transcripts))
            result = self.record(name)
            self.assertEqual(result['raw_text'], 'Test speech was preserved.')
            self.assertEqual(result['text'], result['raw_text'])
            self.assertEqual(result['enrichment']['status'], 'complete_with_errors')

    def test_cleanup_changes_only_display_text(self):
        args = self.args('--enable-cleanup')
        worker.process_clip(self.queued(), FakeModel(), args)
        with patch.object(enrichment, 'cleanup', return_value={'text': 'Cleaned copy.'}):
            enrichment.enrich_record(store.record_path(Path(args.done), 'test.wav'), Path(args.done), Path(args.transcripts))
        result = self.record()
        self.assertEqual(result['text'], 'Cleaned copy.')
        self.assertEqual(result['raw_text'], 'Test speech was preserved.')
        self.assertIn('Cleaned copy.', (self.root / 'transcripts/processed.html').read_text())

    def test_altered_audio_is_not_enriched(self):
        args = self.args('--enable-classifier')
        worker.process_clip(self.queued(), FakeModel(), args)
        (self.root / 'done/test.wav').write_bytes(b'changed')
        with patch.object(enrichment, 'classify_wav', side_effect=AssertionError('must not decode altered audio')):
            enrichment.enrich_record(store.record_path(Path(args.done), 'test.wav'), Path(args.done), Path(args.transcripts))
        self.assertEqual(self.record()['raw_text'], 'Test speech was preserved.')
        self.assertIn('checksum changed', self.record()['enrichment']['errors']['job'])

    def test_legacy_index_entries_survive_rebuild(self):
        args = self.args()
        index = self.root / 'transcripts/index.jsonl'
        index.write_text('{"file":"legacy.wav","raw_text":"old recording"}\n')
        worker.process_clip(self.queued(), FakeModel(), args)
        records = [json.loads(line) for line in index.read_text().splitlines()]
        self.assertEqual({r['file'] for r in records}, {'legacy.wav', 'test.wav'})
        self.assertTrue((self.root / 'transcripts/index.pre-durable.jsonl').exists())

    def test_html_escapes_raw_and_external_evidence(self):
        html = store.evidence_page([{'file': 'test.wav', 'raw_text': '<script>alert(1)</script>',
                                    'classification': {'external_cw_decoder': {'text': '<img src=x>'}}}])
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('&lt;img src=x&gt;', html)

    def test_rebuild_does_not_duplicate_same_clip(self):
        args = self.args()
        worker.process_clip(self.queued(), FakeModel(), args)
        store.rebuild_views(Path(args.done), Path(args.transcripts))
        self.assertEqual(len((self.root / 'transcripts/index.jsonl').read_text().splitlines()), 1)


if __name__ == '__main__':
    unittest.main()
