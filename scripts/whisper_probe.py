#!/usr/bin/env python3
"""Probe one WAV file with faster-whisper for debugging raw transcription.

Use this when raw.html looks wrong. It runs the same faster-whisper model as the
live worker, with optional VAD on/off comparison, and prints segment-level output.

Examples:

  .venv/bin/python3 scripts/whisper_probe.py runtime/done/example.wav

  .venv/bin/python3 scripts/whisper_probe.py runtime/done/example.wav --compare-vad

  .venv/bin/python3 scripts/whisper_probe.py runtime/done/example.wav --no-vad
"""
from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel


def wav_info(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        frames = wf.getnframes()
    duration = frames / sample_rate if sample_rate else 0
    return {
        "channels": channels,
        "sample_width_bytes": sample_width,
        "sample_rate": sample_rate,
        "frames": frames,
        "duration_sec": round(duration, 3),
    }


def run_probe(model: WhisperModel, wav_path: Path, vad_filter: bool, beam_size: int) -> dict[str, Any]:
    segments_iter, info = model.transcribe(
        str(wav_path),
        language="en",
        beam_size=beam_size,
        vad_filter=vad_filter,
    )
    parts: list[str] = []
    segments: list[dict[str, Any]] = []
    for segment in segments_iter:
        text = segment.text.strip()
        if text:
            parts.append(text)
        segments.append({
            "start": round(float(segment.start), 3),
            "end": round(float(segment.end), 3),
            "text": text,
        })
    return {
        "vad_filter": vad_filter,
        "language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "duration": getattr(info, "duration", None),
        "text": " ".join(parts).strip(),
        "segments": segments,
    }


def print_result(result: dict[str, Any]) -> None:
    print(f"vad_filter={result['vad_filter']} language={result.get('language')} prob={result.get('language_probability')} duration={result.get('duration')}")
    print(f"text: {result.get('text') or '[empty]'}")
    print(f"segments: {len(result.get('segments') or [])}")
    for segment in result.get("segments") or []:
        print(f"  [{segment['start']:>7} .. {segment['end']:>7}] {segment['text']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe faster-whisper on a single WAV file")
    parser.add_argument("wav", type=Path)
    parser.add_argument("--whisper-model", default="small.en")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--no-vad", action="store_true", help="Disable VAD for this probe")
    parser.add_argument("--compare-vad", action="store_true", help="Run once with VAD on and once with VAD off")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of human-readable text")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.wav.exists():
        raise SystemExit(f"WAV not found: {args.wav}")

    model = WhisperModel(args.whisper_model, device=args.device, compute_type=args.compute_type)
    base = {"file": str(args.wav), "wav_info": wav_info(args.wav)}

    if args.compare_vad:
        results = [
            run_probe(model, args.wav, vad_filter=True, beam_size=args.beam_size),
            run_probe(model, args.wav, vad_filter=False, beam_size=args.beam_size),
        ]
    else:
        results = [run_probe(model, args.wav, vad_filter=not args.no_vad, beam_size=args.beam_size)]

    if args.json:
        print(json.dumps({**base, "results": results}, indent=2, ensure_ascii=False))
        return 0

    print(json.dumps(base["wav_info"], indent=2))
    print("-" * 80)
    for result in results:
        print_result(result)
        print("-" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
