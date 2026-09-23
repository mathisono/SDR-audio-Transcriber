"""Actual synthetic-CW checks and fake-device rate tests (no live RF)."""
from __future__ import annotations

import json
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from helpers import morse, tone, wav
from cw_decode import decode_wav


class CWCase(unittest.TestCase):
    def test_known_morse_matrix(self):
        cases = [('8wpm', {'wpm': 8}), ('18wpm', {}), ('30wpm', {'wpm': 30}),
                 ('late_id', {'lead': 16}), ('off_grid', {'frequency': 613}),
                 ('noise6db', {'noise_db': 6}), ('48khz', {'rate': 48000})]
        with tempfile.TemporaryDirectory() as tmp:
            for name, options in cases:
                with self.subTest(case=name):
                    path = wav(Path(tmp) / (name + '.wav'), morse(**options), options.get('rate', 8000))
                    result = decode_wav(path)
                    self.assertEqual(result['text'], 'DE KJ6DZB')
                    self.assertEqual(result['callsigns'], ['KJ6DZB'])
                    self.assertIn('uncalibrated', result['confidence_kind'])

    def test_silence_and_white_noise_do_not_decode(self):
        rng = random.Random(42)
        noise = b''.join(struct.pack('<h', max(-32767, min(32767, round(rng.gauss(0, 2500))))) for _ in range(24000))
        with tempfile.TemporaryDirectory() as tmp:
            for name, pcm in [('silence', bytes(48000)), ('noise', noise)]:
                with self.subTest(case=name):
                    result = decode_wav(wav(Path(tmp) / (name + '.wav'), pcm))
                    self.assertFalse(result['decoded'])
                    self.assertFalse(result['callsigns'])

    def test_unknown_symbols_alone_are_not_a_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = decode_wav(wav(Path(tmp) / 'tone.wav', tone(3)))
            self.assertFalse(result['decoded'])
            self.assertFalse(result['callsigns'])

    def test_invalid_wpm_rejected(self):
        with self.assertRaises(ValueError):
            decode_wav(Path('does-not-need-to-exist.wav'), expected_wpm_min=0)


class ReceiverCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'project with spaces'
        (self.root / 'scripts').mkdir(parents=True)
        (self.root / 'bin').mkdir()
        for name in ('start_rtl_fm_receiver.sh', 'clip_writer.py', 'safe_runtime.py'):
            shutil.copyfile(ROOT / 'scripts' / name, self.root / 'scripts' / name)
        # Only the hardware/config helper is faked. Launcher and writer are real.
        (self.root / 'scripts/ppm_config.py').write_text("print('-p 0')\n")
        (self.root / 'config.json').write_text(json.dumps({
            'source': {'ppm_correction': 0, 'sample_rate': 240000, 'gain_db': 30},
            'clip_writer': {'min_clip_seconds': 0.1, 'max_clip_seconds': 2,
                            'queue_directory': str(self.root / 'queue'), 'tmp_directory': str(self.root / 'tmp')}
        }))
        device = self.root / 'bin/rtl_fm'
        device.write_text('#!' + sys.executable + '\n' + '''import json,sys,math,struct
from pathlib import Path
Path('argv.json').write_text(json.dumps(sys.argv[1:]))
rate=int(sys.argv[sys.argv.index('-r')+1])
sys.stdout.buffer.write(b''.join(struct.pack('<h',round(10000*math.sin(2*math.pi*700*i/rate))) for i in range(rate//5)))
''')
        device.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.root / 'bin') + os.pathsep + os.environ.get('PATH', ''))

    def launch(self, mode, *extra):
        return subprocess.run(['bash', str(self.root / 'scripts/start_rtl_fm_receiver.sh'),
                               '--config', str(self.root / 'config.json'), '--frequency', '162.4M',
                               '--mode', mode, '--no-calibrate', '--threshold', '500', *extra],
                              env=self.env, capture_output=True, text=True, timeout=30)

    def verify_rate(self, expected):
        argv = json.loads((self.root / 'argv.json').read_text())
        self.assertEqual(int(argv[argv.index('-r') + 1]), expected)
        path = next((self.root / 'queue').glob('*.wav'))
        with wave.open(str(path), 'rb') as stream:
            self.assertEqual(stream.getframerate(), expected)
            self.assertEqual(stream.getnframes(), expected // 5)
        metadata = json.loads(path.with_suffix('.json').read_text())
        self.assertEqual(metadata['sample_rate'], expected)

    def test_nfm_explicit_audio_resampling_and_header(self):
        result = self.launch('nfm', '--sample-rate', '48000', '--audio-rate', '16000')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.verify_rate(16000)

    def test_wbfm_default_audio_rate_is_explicit(self):
        result = self.launch('wbfm')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.verify_rate(48000)

    def test_nfm_default_matches_pcm(self):
        result = self.launch('nfm')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.verify_rate(24000)

    def test_unsupported_upsampling_is_rejected(self):
        result = self.launch('nfm', '--sample-rate', '16000', '--audio-rate', '48000')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'argv.json').exists())


if __name__ == '__main__':
    unittest.main()
