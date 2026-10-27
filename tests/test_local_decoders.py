"""Parser and adapter plumbing tests; fake programs are not decoder evidence."""
from __future__ import annotations

import json
import importlib.util
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from callsign_parser import parse_callsigns
from deepcw_adapter import resample_linear, translate_frequency, windows

HAS_NUMPY = importlib.util.find_spec("numpy") is not None


class CallsignParserTests(unittest.TestCase):
    def values(self, text, words=None):
        return [item["candidate"] for item in parse_callsigns(text, words)]

    def test_illustrative_phonetic_sequences(self):
        self.assertEqual(self.values("whiskey alpha six zulu tango yankee"), ["WA6ZTY"])
        self.assertEqual(self.values("kilo juliet six delta zulu bravo"), ["KJ6DZB"])

    def test_unrelated_and_compact_callsigns(self):
        self.assertEqual(self.values("alpha one bravo checking in"), ["A1B"])
        self.assertEqual(self.values("heard N7XYZ/P mobile"), ["N7XYZ/P"])
        self.assertEqual(self.values("kilo oscar six india gulf quebec"), ["KO6IGQ"])

    def test_partial_and_ordinary_words_are_not_completed(self):
        self.assertEqual(self.values("kilo juliet six delta"), ["KJ6D"])
        partial = parse_callsigns("whiskey alpha zulu tango yankee")
        self.assertEqual(partial[0]["candidate"], "WAZTY")
        self.assertFalse(partial[0]["complete"])
        self.assertEqual(partial[0]["uncertainty"], ["incomplete_structure"])
        self.assertEqual(self.values("I had one alpha release in uniform"), [])
        self.assertEqual(self.values("ordinary conversation with no identifier"), [])

    def test_uppercase_spelled_prefix_can_join_number_and_suffix(self):
        self.assertEqual(self.values("KJ six D"), ["KJ6D"])

    def test_ambiguous_token_is_not_silently_repaired(self):
        result = parse_callsigns("kilo oh alpha")
        self.assertEqual(result[0]["candidate"], "K0A")
        self.assertEqual(result[0]["uncertainty"], ["ambiguous_token"])

    def test_timestamps_and_raw_span_are_preserved(self):
        words = [{"word": "alpha", "start": 1.0, "end": 1.2},
                 {"word": "one", "start": 1.3, "end": 1.5},
                 {"word": "bravo", "start": 1.6, "end": 2.0}]
        item = parse_callsigns("alpha one bravo", words)[0]
        self.assertEqual((item["start"], item["end"]), (1.0, 2.0))
        self.assertEqual(item["raw_span"], "alpha one bravo")
        self.assertEqual((item["char_start"], item["char_end"]), (0, 15))


class DeepCWPreprocessingTests(unittest.TestCase):
    def test_windows_meet_upstream_duration_contract(self):
        self.assertEqual(windows(4.9), [])
        self.assertEqual(windows(12), [(0.0, 12)])
        result = windows(60)
        self.assertEqual(result[0], (0.0, 20.0))
        self.assertEqual(result[-1], (40.0, 60.0))
        self.assertTrue(all(5 <= end-start <= 20 for start, end in result))

    @unittest.skipUnless(HAS_NUMPY, "actual DeepCW environment supplies numpy")
    def test_frequency_translation_preserves_sample_count(self):
        import numpy as np
        rate = 3200
        audio = np.sin(2*math.pi*300*np.arange(rate)/rate).astype(np.float32)
        shifted = translate_frequency(audio, rate, 500)
        self.assertEqual(len(shifted), len(audio))
        peak = int(np.argmax(np.abs(np.fft.rfft(shifted))))
        self.assertAlmostEqual(peak, 800, delta=2)

    @unittest.skipUnless(HAS_NUMPY, "actual DeepCW environment supplies numpy")
    def test_linear_resampling_changes_samples_not_header_only(self):
        import numpy as np
        audio = np.arange(24000, dtype=np.float32)
        result = resample_linear(audio, 24000, 3200)
        self.assertEqual(len(result), 3200)
        self.assertNotEqual(result[-1], audio[3199])


if __name__ == "__main__":
    unittest.main()
