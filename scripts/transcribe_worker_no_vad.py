#!/usr/bin/env python3
"""Run transcribe_worker.py with faster-whisper VAD disabled.

This is a debugging wrapper for cases where the Raw Whisper Log shows empty
`raw_text` and zero segments even though the WAV clips contain audible audio.

It imports the normal worker, replaces only transcribe_file(), and then calls the
normal main() function. All normal transcribe_worker.py command-line arguments
still work.

Example:

  .venv/bin/python3 scripts/transcribe_worker_no_vad.py \
    --whisper-model small.en \
    --device cpu \
    --compute-type int8 \
    --no-cleanup \
    --enable-classifier \
    --classify-modes nfm
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import transcribe_worker as worker  # noqa: E402


def transcribe_file_no_vad(model: Any, wav_path: Path) -> tuple[str, list[dict[str, Any]], Any]:
    segments, info = model.transcribe(
        str(wav_path),
        language="en",
        beam_size=5,
        vad_filter=False,
    )
    parts: list[str] = []
    segment_data: list[dict[str, Any]] = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            parts.append(text)
        segment_data.append({"start": round(float(segment.start), 3), "end": round(float(segment.end), 3), "text": text})
    return " ".join(parts).strip(), segment_data, info


worker.transcribe_file = transcribe_file_no_vad

if __name__ == "__main__":
    raise SystemExit(worker.main())
