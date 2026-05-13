#!/usr/bin/env python3
"""Classify SDR audio clips for repeater IDs and tone evidence.

This is intentionally lightweight and optional. It reads a WAV file, estimates a
prominent audio tone frequency, looks for keyed CW-like on/off timing around that
tone, attempts a basic Morse decode, and returns JSON evidence that can be used
to populate label candidates.

The output is evidence, not a final truth. Repeated evidence over time should be
used by the UI/refinement loop to promote stable labels.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import wave
from pathlib import Path
from typing import Any

MORSE_TABLE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E",
    "..-.": "F", "--.": "G", "....": "H", "..": "I", ".---": "J",
    "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O",
    ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T",
    "..-": "U", "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y",
    "--..": "Z", ".----": "1", "..---": "2", "...--": "3", "....-": "4",
    ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
    "-----": "0", "/": " ",
}

CALLSIGN_RE = re.compile(r"\b(?:[AKNW][A-Z]?\d[A-Z]{1,3}|[A-Z]{1,2}\d[A-Z]{1,4})\b", re.IGNORECASE)


def read_wav_mono(path: Path) -> tuple[int, list[float]]:
    with wave.open(str(path), "rb") as wf:
        sample_rate = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())

    if width != 2:
        raise ValueError(f"only 16-bit PCM WAV is supported, got sample width {width}")

    samples: list[float] = []
    step = 2 * channels
    for i in range(0, len(frames), step):
        vals = []
        for ch in range(channels):
            j = i + ch * 2
            if j + 1 >= len(frames):
                continue
            value = int.from_bytes(frames[j:j + 2], byteorder="little", signed=True) / 32768.0
            vals.append(value)
        if vals:
            samples.append(sum(vals) / len(vals))
    return sample_rate, samples


def goertzel_power(samples: list[float], sample_rate: int, freq_hz: float) -> float:
    if not samples:
        return 0.0
    n = len(samples)
    k = int(0.5 + ((n * freq_hz) / sample_rate))
    omega = (2.0 * math.pi * k) / n
    coeff = 2.0 * math.cos(omega)
    q0 = q1 = q2 = 0.0
    for sample in samples:
        q0 = coeff * q1 - q2 + sample
        q2 = q1
        q1 = q0
    return q1 * q1 + q2 * q2 - q1 * q2 * coeff


def estimate_dominant_tone(sample_rate: int, samples: list[float], low_hz: int, high_hz: int) -> dict[str, Any]:
    # Analyze up to the first 12 seconds for speed.
    max_samples = min(len(samples), sample_rate * 12)
    window = samples[:max_samples]
    if len(window) < sample_rate // 2:
        return {"detected": False, "reason": "clip too short"}

    # Coarse scan. Repeater CW IDs are commonly around the audio tone range.
    candidates = list(range(low_hz, high_hz + 1, 25))
    powers = [(freq, goertzel_power(window, sample_rate, freq)) for freq in candidates]
    powers.sort(key=lambda item: item[1], reverse=True)
    best_freq, best_power = powers[0]
    median_power = statistics.median([p for _, p in powers]) or 1.0

    # Refine around the best bin.
    refine = list(range(max(low_hz, best_freq - 30), min(high_hz, best_freq + 30) + 1, 5))
    refined = [(freq, goertzel_power(window, sample_rate, freq)) for freq in refine]
    refined.sort(key=lambda item: item[1], reverse=True)
    best_freq, best_power = refined[0]

    ratio = best_power / median_power
    detected = ratio >= 8.0
    confidence = max(0.0, min(1.0, (math.log10(max(ratio, 1.0)) / 2.0)))

    return {
        "detected": detected,
        "frequency_hz": int(best_freq),
        "power_ratio": round(ratio, 3),
        "confidence": round(confidence, 3),
    }


def frame_tone_activity(sample_rate: int, samples: list[float], freq_hz: float, frame_ms: int) -> list[tuple[float, bool, float]]:
    frame_len = max(1, int(sample_rate * frame_ms / 1000.0))
    powers: list[float] = []
    frames: list[tuple[float, float]] = []
    for start in range(0, len(samples) - frame_len + 1, frame_len):
        frame = samples[start:start + frame_len]
        power = goertzel_power(frame, sample_rate, freq_hz)
        powers.append(power)
        frames.append((start / sample_rate, power))

    if not powers:
        return []

    median_power = statistics.median(powers)
    high_power = sorted(powers)[int(len(powers) * 0.90)] if len(powers) > 10 else max(powers)
    threshold = max(median_power * 4.0, high_power * 0.30)

    return [(timestamp, power >= threshold, power) for timestamp, power in frames]


def runs_from_activity(activity: list[tuple[float, bool, float]], frame_ms: int) -> list[tuple[bool, float]]:
    if not activity:
        return []
    runs: list[tuple[bool, float]] = []
    current = activity[0][1]
    count = 0
    for _, active, _ in activity:
        if active == current:
            count += 1
        else:
            runs.append((current, count * frame_ms / 1000.0))
            current = active
            count = 1
    runs.append((current, count * frame_ms / 1000.0))
    return [(state, duration) for state, duration in runs if duration >= frame_ms / 1000.0]


def estimate_dit(on_durations: list[float]) -> float | None:
    short = [d for d in on_durations if 0.03 <= d <= 0.40]
    if not short:
        return None
    short.sort()
    # Use a low percentile so dashes do not dominate.
    return short[max(0, min(len(short) - 1, int(len(short) * 0.25)))]


def decode_morse_from_runs(runs: list[tuple[bool, float]]) -> dict[str, Any]:
    on_durations = [duration for active, duration in runs if active]
    dit = estimate_dit(on_durations)
    if not dit:
        return {"decoded": False, "reason": "could not estimate dit length"}

    symbols: list[str] = []
    current = ""
    for active, duration in runs:
        units = duration / dit
        if active:
            current += "." if units < 2.2 else "-"
        else:
            if units >= 6.0:
                if current:
                    symbols.append(current)
                    current = ""
                symbols.append("/")
            elif units >= 2.4:
                if current:
                    symbols.append(current)
                    current = ""
            else:
                # Intra-character gap.
                pass
    if current:
        symbols.append(current)

    decoded_chars = [MORSE_TABLE.get(sym, "?") for sym in symbols]
    decoded = "".join(decoded_chars).replace("  ", " ").strip()
    good = sum(1 for c in decoded_chars if c != "?")
    total = max(1, len(decoded_chars))
    confidence = good / total

    callsigns = sorted(set(match.group(0).upper() for match in CALLSIGN_RE.finditer(decoded)))
    return {
        "decoded": bool(decoded),
        "dit_seconds": round(dit, 3),
        "symbols": " ".join(symbols),
        "text": decoded,
        "confidence": round(confidence, 3),
        "callsigns": callsigns,
    }


def classify_wav(path: Path, low_hz: int, high_hz: int, frame_ms: int) -> dict[str, Any]:
    sample_rate, samples = read_wav_mono(path)
    tone = estimate_dominant_tone(sample_rate, samples, low_hz, high_hz)
    result: dict[str, Any] = {
        "enabled": True,
        "engine": "clip_classifier_v1",
        "file": path.name,
        "sample_rate": sample_rate,
        "duration_sec": round(len(samples) / sample_rate, 3) if sample_rate else 0,
        "tone_id": tone,
        "cw_id": {"decoded": False, "reason": "tone not detected"},
        "label_candidates": [],
    }

    if tone.get("detected") and tone.get("frequency_hz"):
        freq = int(tone["frequency_hz"])
        activity = frame_tone_activity(sample_rate, samples, freq, frame_ms)
        runs = runs_from_activity(activity, frame_ms)
        active_time = sum(duration for active, duration in runs if active)
        total_time = sum(duration for _, duration in runs) or 1.0
        duty_cycle = active_time / total_time
        keyed = 0.03 < duty_cycle < 0.85 and len(runs) >= 6
        result["tone_id"]["duty_cycle"] = round(duty_cycle, 3)
        result["tone_id"]["keyed_candidate"] = keyed
        result["label_candidates"].append({
            "type": "tone_id_frequency",
            "label": f"TONE_{freq}Hz",
            "value": freq,
            "confidence": tone.get("confidence", 0.0),
            "source": "audio_tone_detection",
        })

        if keyed:
            cw = decode_morse_from_runs(runs)
            result["cw_id"] = cw
            for callsign in cw.get("callsigns", []):
                result["label_candidates"].append({
                    "type": "cw_callsign",
                    "label": callsign,
                    "value": callsign,
                    "confidence": cw.get("confidence", 0.0),
                    "source": "cw_audio_decode",
                })

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify a WAV clip for CW ID and tone evidence")
    parser.add_argument("wav", type=Path)
    parser.add_argument("--low-hz", type=int, default=300)
    parser.add_argument("--high-hz", type=int, default=2000)
    parser.add_argument("--frame-ms", type=int, default=20)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = classify_wav(args.wav, args.low_hz, args.high_hz, args.frame_ms)
    print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
