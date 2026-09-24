#!/usr/bin/env python3
"""One-shot real speech inference for recording_corpus; never sees a reference.

Each invocation cold-loads the model. Use inference_seconds separately from
model_load_seconds; the corpus's wall time is not a warmed production benchmark.
"""
from __future__ import annotations
import argparse
import contextlib
import json
import sys
import time
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('wav', type=Path)
    p.add_argument('--model', default='small.en')
    p.add_argument('--device', default='cpu')
    p.add_argument('--compute-type', default='int8')
    a = p.parse_args()
    result = {'status': 'blocked', 'real_inference_run': False}
    code = 2
    try:
        # Logs must not corrupt the JSON protocol on stdout.
        with contextlib.redirect_stdout(sys.stderr):
            from transcribe_worker import transcribe_file
            from faster_whisper import WhisperModel
            started = time.monotonic()
            model = WhisperModel(a.model, device=a.device, compute_type=a.compute_type)
            loaded = time.monotonic()
            text, segments, info = transcribe_file(model, a.wav.resolve())
            finished = time.monotonic()
        result.update(status='measured', real_inference_run=True, text=text,
                      segments=segments, model=a.model, device=a.device,
                      compute_type=a.compute_type, model_load_seconds=loaded-started,
                      inference_seconds=finished-loaded)
        code = 0
    except ImportError as exc:
        result['error'] = str(exc)
    except Exception as exc:
        result.update(status='error', error=type(exc).__name__ + ': ' + str(exc))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
