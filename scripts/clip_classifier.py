#!/usr/bin/env python3
"""Run independent, deadline-bounded CW decoders. Output is evidence, not identity."""
from __future__ import annotations

import argparse
import json
import re
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from safe_runtime import command_argv, decoded_output, positive_timeout, run_command

CALLSIGN_RE = re.compile(r'\b(?:[AKNW][A-Z]?\d[A-Z]{1,3}|[A-Z]{1,2}\d[A-Z]{1,4})\b', re.IGNORECASE)


def extract_callsigns(text: str) -> list[str]:
    return sorted({m.group(0).upper() for m in CALLSIGN_RE.finditer(text)})


def run_external_cw_decoder(path: Path, command: str, timeout: float) -> dict[str, Any]:
    if not command:
        return {'enabled': False, 'decoded': False, 'text': '', 'callsigns': [], 'label_candidates': []}
    try:
        proc = run_command(command_argv(command, path), timeout)
        output = decoded_output(proc['stdout'], proc['error'])
        output.update(enabled=True, engine='external-command', command=command,
                      returncode=proc['returncode'], stderr=proc['stderr'][-4000:])
    except Exception as exc:
        output = {'enabled': True, 'decoded': False, 'text': '', 'confidence': None, 'error': str(exc)}
    output['callsigns'] = extract_callsigns(output['text']) if output.get('decoded') else []
    output['decoder_confidence'] = output.pop('confidence', None)
    output['confidence'] = None  # Not calibrated, including a backend's own score.
    output['verified'] = False
    output['label_candidates'] = []
    return output


def run_internal_cw_decoder(path: Path, low_hz: int, high_hz: int, frame_ms: int,
                            wpm_min: float, wpm_max: float, timeout: float) -> dict[str, Any]:
    argv = [sys.executable, str(Path(__file__).with_name('cw_decode.py')), str(path),
            '--profile', 'repeater-id', '--low-hz', str(low_hz), '--high-hz', str(high_hz),
            '--frame-ms', str(frame_ms), '--expected-wpm-min', str(wpm_min), '--expected-wpm-max', str(wpm_max)]
    proc = run_command(argv, timeout)
    if proc['error']:
        return {'decoded': False, 'text': '', 'callsigns': [], 'error': proc['error'], 'stderr': proc['stderr'][-4000:]}
    value = json.loads(proc['stdout'])
    if not isinstance(value, dict):
        raise ValueError('internal CW decoder returned non-object JSON')
    if not value.get('decoded'):
        value['raw_decode_text'] = value.get('text', '')
        value['text'], value['callsigns'] = '', []
    value['heuristic_confidence'] = value.pop('confidence', None)
    value['confidence'] = None
    value['confidence_kind'] = 'uncalibrated heuristic; not a probability'
    value['verified'] = False
    value['label_candidates'] = []
    return value


def classify_wav(path: Path, low_hz: int = 300, high_hz: int = 2000, frame_ms: int = 20,
                 external_command: str = '', external_timeout: float = 20,
                 expected_wpm_min: float = 8, expected_wpm_max: float = 30,
                 internal_timeout: float = 20) -> dict[str, Any]:
    positive_timeout(internal_timeout)
    positive_timeout(external_timeout)
    result: dict[str, Any] = {'enabled': True, 'engine': 'clip_classifier_v4', 'file': path.name,
                              'label_candidates': [], 'automatic_label_promotion': False}
    try:
        with wave.open(str(path), 'rb') as stream:
            result['sample_rate'] = stream.getframerate()
            result['duration_sec'] = stream.getnframes() / stream.getframerate()
    except Exception as exc:
        result['audio_error'] = str(exc)
    # Both commands start before either is awaited. Each has its own supervisor.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            'cw_id': pool.submit(run_internal_cw_decoder, path, low_hz, high_hz, frame_ms,
                                 expected_wpm_min, expected_wpm_max, internal_timeout),
            'external_cw_decoder': pool.submit(run_external_cw_decoder, path, external_command, external_timeout),
        }
        for name, future in futures.items():
            try:
                value = future.result()
                if not isinstance(value, dict):
                    raise ValueError(f'{name} must return a JSON object')
                result[name] = value
            except Exception as exc:
                result[name] = {'decoded': False, 'text': '', 'callsigns': [], 'error': str(exc)}
    cw = result['cw_id']
    tone = cw.get('tone') or {}
    result['tone_id'] = {'detected': bool(tone.get('detected')), 'frequency_hz': tone.get('frequency_hz'),
                         'heuristic_confidence': tone.get('confidence'), 'confidence': None,
                         'keyed_candidate': cw.get('keyed_candidate', False)}
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('wav', type=Path)
    p.add_argument('--low-hz', type=int, default=300)
    p.add_argument('--high-hz', type=int, default=2000)
    p.add_argument('--frame-ms', type=int, default=20)
    p.add_argument('--expected-wpm-min', type=float, default=8)
    p.add_argument('--expected-wpm-max', type=float, default=30)
    p.add_argument('--cw-external-command', default='')
    p.add_argument('--cw-external-timeout', type=float, default=20)
    p.add_argument('--cw-internal-timeout', type=float, default=20)
    p.add_argument('--pretty', action='store_true')
    a = p.parse_args()
    result = classify_wav(a.wav, a.low_hz, a.high_hz, a.frame_ms, a.cw_external_command,
                          a.cw_external_timeout, a.expected_wpm_min, a.expected_wpm_max, a.cw_internal_timeout)
    print(json.dumps(result, indent=2 if a.pretty else None, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
