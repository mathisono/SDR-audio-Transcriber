#!/usr/bin/env python3
"""Classify SDR audio clips for repeater IDs and tone evidence.

This optional classifier estimates a dominant audio tone, runs the internal
one-shot DSP CW decoder in repeater-id profile, can run an optional external
command-line CW decoder, and returns JSON evidence used to populate label
candidates.

The output is evidence, not final truth. Repeated evidence over time is handled
by transcribe_worker.py and runtime/classification_state.json.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from cw_decode import decode_wav  # noqa: E402

CALLSIGN_RE = re.compile(r"\b(?:[AKNW][A-Z]?\d[A-Z]{1,3}|[A-Z]{1,2}\d[A-Z]{1,4})\b", re.IGNORECASE)


def wav_info(path: Path) -> tuple[int, float]:
    with wave.open(str(path), "rb") as wf:
        sample_rate = wf.getframerate()
        frames = wf.getnframes()
    return sample_rate, frames / sample_rate if sample_rate else 0.0


def extract_callsigns(text: str) -> list[str]:
    return sorted(set(match.group(0).upper() for match in CALLSIGN_RE.finditer(text or "")))


def run_external_cw_decoder(path: Path, command: str, timeout: int) -> dict[str, Any]:
    if not command:
        return {"enabled": False}

    if "{wav}" in command:
        args = shlex.split(command.replace("{wav}", str(path)))
    else:
        args = shlex.split(command) + [str(path)]

    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as exc:
        return {"enabled": True, "command": command, "error": str(exc), "label_candidates": []}

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    callsigns = extract_callsigns(stdout)
    confidence = 0.72 if callsigns else 0.25 if stdout else 0.0
    return {
        "enabled": True,
        "command": command,
        "argv": args,
        "returncode": proc.returncode,
        "stdout": stdout[-4000:],
        "stderr": stderr[-1000:],
        "callsigns": callsigns,
        "confidence": confidence,
        "label_candidates": [
            {
                "type": "cw_callsign",
                "label": callsign,
                "value": callsign,
                "confidence": confidence,
                "source": "external_cw_decoder",
            }
            for callsign in callsigns
        ],
    }


def classify_wav(
    path: Path,
    low_hz: int,
    high_hz: int,
    frame_ms: int,
    external_command: str = "",
    external_timeout: int = 20,
    expected_wpm_min: float = 8.0,
    expected_wpm_max: float = 30.0,
) -> dict[str, Any]:
    sample_rate, duration = wav_info(path)
    cw = decode_wav(
        path,
        low_hz=low_hz,
        high_hz=high_hz,
        frame_ms=frame_ms,
        expected_wpm_min=expected_wpm_min,
        expected_wpm_max=expected_wpm_max,
        profile="repeater-id",
    )
    external = run_external_cw_decoder(path, external_command, external_timeout)

    tone = cw.get("tone") or {"detected": False}
    result: dict[str, Any] = {
        "enabled": True,
        "engine": "clip_classifier_v3",
        "file": path.name,
        "sample_rate": sample_rate,
        "duration_sec": round(duration, 3),
        "tone_id": {
            "detected": bool(tone.get("detected")),
            "frequency_hz": tone.get("frequency_hz"),
            "power_ratio": tone.get("power_ratio"),
            "confidence": tone.get("confidence", 0.0),
            "duty_cycle": cw.get("duty_cycle"),
            "keyed_candidate": cw.get("keyed_candidate", False),
        },
        "cw_id": {
            "decoded": bool(cw.get("decoded")),
            "engine": cw.get("engine"),
            "profile": cw.get("profile"),
            "text": cw.get("text", ""),
            "confidence": cw.get("confidence", 0.0),
            "callsigns": cw.get("callsigns", []),
            "timing": cw.get("timing"),
            "symbols": cw.get("symbols"),
            "reason": cw.get("reason"),
        },
        "external_cw_decoder": external,
        "label_candidates": [],
    }

    if tone.get("detected") and tone.get("frequency_hz"):
        freq = int(tone["frequency_hz"])
        result["label_candidates"].append({
            "type": "tone_id_frequency",
            "label": f"TONE_{freq}Hz",
            "value": freq,
            "confidence": tone.get("confidence", 0.0),
            "source": "audio_tone_detection",
        })

    for candidate in cw.get("label_candidates", []):
        result["label_candidates"].append(candidate)

    for candidate in external.get("label_candidates", []):
        result["label_candidates"].append(candidate)

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify a WAV clip for CW ID and tone evidence")
    parser.add_argument("wav", type=Path)
    parser.add_argument("--low-hz", type=int, default=300)
    parser.add_argument("--high-hz", type=int, default=2000)
    parser.add_argument("--frame-ms", type=int, default=20)
    parser.add_argument("--expected-wpm-min", type=float, default=8.0)
    parser.add_argument("--expected-wpm-max", type=float, default=30.0)
    parser.add_argument("--cw-external-command", default="", help="Optional external CW decoder command. Use {wav} placeholder or WAV path is appended.")
    parser.add_argument("--cw-external-timeout", type=int, default=20)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = classify_wav(
        args.wav,
        args.low_hz,
        args.high_hz,
        args.frame_ms,
        external_command=args.cw_external_command,
        external_timeout=args.cw_external_timeout,
        expected_wpm_min=args.expected_wpm_min,
        expected_wpm_max=args.expected_wpm_max,
    )
    print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
