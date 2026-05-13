#!/usr/bin/env python3
"""One-shot command-line CW/Morse decoder for WAV audio.

This is intended for repeater ID clips and other single-tone CW IDs inside FM
audio. It is not a full contest-grade CW skimmer. It favors robust, explainable
DSP over a black-box model:

  WAV -> mono PCM -> tone search -> Goertzel envelope -> adaptive gate -> timing
  estimate -> Morse decode -> callsign-biased candidates

Examples:
  scripts/cw_decode.py clip.wav
  scripts/cw_decode.py clip.wav --pretty
  scripts/cw_decode.py clip.wav --text-only
  scripts/cw_decode.py clip.wav --expected-wpm-min 8 --expected-wpm-max 25
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
    "-----": "0", ".-.-.-": ".", "--..--": ",", "..--..": "?", "-..-.": "/",
    "-....-": "-", "-...-": "=", ".-.-.": "+", ".--.-.": "@", "-.-.--": "!",
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
        values = []
        for ch in range(channels):
            j = i + ch * 2
            if j + 1 >= len(frames):
                continue
            values.append(int.from_bytes(frames[j:j + 2], "little", signed=True) / 32768.0)
        if values:
            samples.append(sum(values) / len(values))
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
    return max(0.0, q1 * q1 + q2 * q2 - q1 * q2 * coeff)


def moving_average(values: list[float], radius: int) -> list[float]:
    if radius <= 0 or not values:
        return values[:]
    out: list[float] = []
    for i in range(len(values)):
        lo = max(0, i - radius)
        hi = min(len(values), i + radius + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * pct))
    return ordered[max(0, min(len(ordered) - 1, idx))]


def estimate_tone(sample_rate: int, samples: list[float], low_hz: int, high_hz: int) -> dict[str, Any]:
    window = samples[: min(len(samples), sample_rate * 15)]
    if len(window) < sample_rate // 2:
        return {"detected": False, "reason": "clip too short"}

    coarse = list(range(low_hz, high_hz + 1, 25))
    coarse_powers = [(freq, goertzel_power(window, sample_rate, freq)) for freq in coarse]
    coarse_powers.sort(key=lambda item: item[1], reverse=True)
    best_freq = coarse_powers[0][0]
    floor = statistics.median([p for _, p in coarse_powers]) or 1.0

    fine = list(range(max(low_hz, best_freq - 35), min(high_hz, best_freq + 35) + 1, 5))
    fine_powers = [(freq, goertzel_power(window, sample_rate, freq)) for freq in fine]
    fine_powers.sort(key=lambda item: item[1], reverse=True)
    freq, power = fine_powers[0]
    ratio = power / floor

    return {
        "detected": ratio >= 7.0,
        "frequency_hz": int(freq),
        "power_ratio": round(ratio, 3),
        "confidence": round(max(0.0, min(1.0, math.log10(max(ratio, 1.0)) / 2.0)), 3),
    }


def tone_envelope(sample_rate: int, samples: list[float], freq_hz: float, frame_ms: int, smooth_frames: int) -> list[tuple[float, float]]:
    frame_len = max(1, int(sample_rate * frame_ms / 1000.0))
    times: list[float] = []
    powers: list[float] = []
    for start in range(0, len(samples) - frame_len + 1, frame_len):
        frame = samples[start:start + frame_len]
        powers.append(math.sqrt(goertzel_power(frame, sample_rate, freq_hz)))
        times.append(start / sample_rate)
    smoothed = moving_average(powers, smooth_frames)
    return list(zip(times, smoothed))


def activity_from_envelope(envelope: list[tuple[float, float]], on_fraction: float, off_fraction: float) -> tuple[list[bool], dict[str, Any]]:
    values = [v for _, v in envelope]
    if not values:
        return [], {"threshold": 0.0}
    noise = percentile(values, 0.20)
    high = percentile(values, 0.90)
    on_threshold = noise + (high - noise) * on_fraction
    off_threshold = noise + (high - noise) * off_fraction
    active = False
    activity: list[bool] = []
    for value in values:
        if active:
            active = value >= off_threshold
        else:
            active = value >= on_threshold
        activity.append(active)
    return activity, {
        "noise_floor": round(noise, 6),
        "high_level": round(high, 6),
        "on_fraction": on_fraction,
        "off_fraction": off_fraction,
        "on_threshold": round(on_threshold, 6),
        "off_threshold": round(off_threshold, 6),
    }


def runs_from_activity(activity: list[bool], frame_ms: int) -> list[tuple[bool, float]]:
    if not activity:
        return []
    runs: list[tuple[bool, float]] = []
    current = activity[0]
    count = 0
    for state in activity:
        if state == current:
            count += 1
        else:
            duration = count * frame_ms / 1000.0
            if duration >= frame_ms / 1000.0:
                runs.append((current, duration))
            current = state
            count = 1
    duration = count * frame_ms / 1000.0
    if duration >= frame_ms / 1000.0:
        runs.append((current, duration))
    return merge_short_runs(runs, min_duration=frame_ms / 1000.0 * 1.1)


def merge_short_runs(runs: list[tuple[bool, float]], min_duration: float) -> list[tuple[bool, float]]:
    changed = True
    result = runs[:]
    while changed and len(result) >= 3:
        changed = False
        new: list[tuple[bool, float]] = []
        i = 0
        while i < len(result):
            if 0 < i < len(result) - 1 and result[i][1] < min_duration and result[i - 1][0] == result[i + 1][0]:
                state = result[i - 1][0]
                duration = result[i - 1][1] + result[i][1] + result[i + 1][1]
                if new:
                    new.pop()
                new.append((state, duration))
                i += 2
                changed = True
            else:
                new.append(result[i])
            i += 1
        result = new
    return result


def dit_from_wpm(wpm: float) -> float:
    return 1.2 / wpm


def estimate_dit(runs: list[tuple[bool, float]], wpm_min: float, wpm_max: float, use_wpm_prior: bool) -> dict[str, Any]:
    on = [duration for active, duration in runs if active]
    if not on:
        return {"ok": False, "reason": "no key-down runs"}

    min_dit = dit_from_wpm(wpm_max)
    max_dit = dit_from_wpm(wpm_min)
    prior_wpm = (wpm_min + wpm_max) / 2.0
    prior_dit = dit_from_wpm(prior_wpm)

    candidates = [d for d in on if min_dit * 0.50 <= d <= max_dit * 1.50]
    if not candidates:
        candidates = on
    candidates.sort()

    dot_cluster = candidates[: max(1, math.ceil(len(candidates) * 0.20))]
    measured_dit = percentile(dot_cluster, 0.10)
    measured_dit = max(min_dit * 0.50, min(max_dit * 1.50, measured_dit))

    if use_wpm_prior and measured_dit > prior_dit * 1.15:
        dit = prior_dit
        source = "wpm_prior"
    else:
        dit = measured_dit
        source = "measured"

    wpm = 1.2 / dit if dit else 0.0
    return {
        "ok": True,
        "dit_seconds": round(dit, 4),
        "estimated_wpm": round(wpm, 2),
        "source": source,
        "measured_dit_seconds": round(measured_dit, 4),
        "prior_dit_seconds": round(prior_dit, 4),
        "wpm_min": wpm_min,
        "wpm_max": wpm_max,
        "on_durations": [round(x, 4) for x in on[:80]],
    }


def decode_runs(runs: list[tuple[bool, float]], dit: float, char_gap_units: float, word_gap_units: float) -> dict[str, Any]:
    symbols: list[str] = []
    current = ""
    for active, duration in runs:
        units = duration / dit if dit else 0
        if active:
            current += "." if units < 2.20 else "-"
        else:
            if units >= word_gap_units:
                if current:
                    symbols.append(current)
                    current = ""
                symbols.append("/")
            elif units >= char_gap_units:
                if current:
                    symbols.append(current)
                    current = ""
            else:
                pass
    if current:
        symbols.append(current)

    decoded_chars = [" " if sym == "/" else MORSE_TABLE.get(sym, "?") for sym in symbols]
    text = re.sub(r"\s+", " ", "".join(decoded_chars)).strip()
    good = sum(1 for c in decoded_chars if c not in {"?"})
    non_space = sum(1 for c in decoded_chars if c != " ") or 1
    base_confidence = good / non_space
    callsigns = extract_callsigns(text)
    confidence = min(0.99, base_confidence + 0.08) if callsigns and base_confidence >= 0.70 else base_confidence
    return {
        "decoded": bool(text),
        "symbols": " ".join(symbols),
        "text": text,
        "confidence": round(confidence, 3),
        "base_confidence": round(base_confidence, 3),
        "callsigns": callsigns,
        "unknown_symbols": decoded_chars.count("?"),
        "char_gap_units": char_gap_units,
        "word_gap_units": word_gap_units,
    }


def extract_callsigns(text: str) -> list[str]:
    return sorted(set(match.group(0).upper() for match in CALLSIGN_RE.finditer(text or "")))


def decode_wav(
    path: Path,
    low_hz: int = 300,
    high_hz: int = 2000,
    frame_ms: int = 10,
    smooth_frames: int = 0,
    expected_wpm_min: float = 8.0,
    expected_wpm_max: float = 30.0,
    use_wpm_prior: bool = True,
    char_gap_units: float = 2.25,
    word_gap_units: float = 5.50,
    label_min_confidence: float = 0.72,
    on_fraction: float = 0.55,
    off_fraction: float = 0.45,
) -> dict[str, Any]:
    sample_rate, samples = read_wav_mono(path)
    duration = len(samples) / sample_rate if sample_rate else 0.0
    tone = estimate_tone(sample_rate, samples, low_hz, high_hz)
    result: dict[str, Any] = {
        "engine": "cw_decode_dsp_v4",
        "file": path.name,
        "sample_rate": sample_rate,
        "duration_sec": round(duration, 3),
        "tone": tone,
        "decoded": False,
        "text": "",
        "callsigns": [],
        "confidence": 0.0,
    }
    if not tone.get("detected") or not tone.get("frequency_hz"):
        result["reason"] = tone.get("reason", "tone not detected")
        return result

    envelope = tone_envelope(sample_rate, samples, float(tone["frequency_hz"]), frame_ms, smooth_frames)
    activity, threshold = activity_from_envelope(envelope, on_fraction, off_fraction)
    runs = runs_from_activity(activity, frame_ms)
    active_time = sum(duration for active, duration in runs if active)
    total_time = sum(duration for _, duration in runs) or 1.0
    duty_cycle = active_time / total_time

    result["threshold"] = threshold
    result["frame_ms"] = frame_ms
    result["smooth_frames"] = smooth_frames
    result["runs"] = [{"active": active, "duration": round(duration, 4)} for active, duration in runs[:180]]
    result["duty_cycle"] = round(duty_cycle, 3)
    result["keyed_candidate"] = 0.02 < duty_cycle < 0.88 and len(runs) >= 5
    if not result["keyed_candidate"]:
        result["reason"] = "tone is not keyed like CW"
        return result

    timing = estimate_dit(runs, expected_wpm_min, expected_wpm_max, use_wpm_prior)
    result["timing"] = timing
    if not timing.get("ok"):
        result["reason"] = timing.get("reason", "timing failed")
        return result

    decoded = decode_runs(runs, float(timing["dit_seconds"]), char_gap_units, word_gap_units)
    result.update(decoded)
    result["label_candidates"] = [
        {
            "type": "cw_callsign",
            "label": callsign,
            "value": callsign,
            "confidence": result.get("confidence", 0.0),
            "source": "cw_decode_dsp_v4",
        }
        for callsign in result.get("callsigns", [])
        if float(result.get("confidence", 0.0)) >= label_min_confidence
    ]
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Decode a WAV file containing CW/Morse audio")
    parser.add_argument("wav", type=Path)
    parser.add_argument("--low-hz", type=int, default=300)
    parser.add_argument("--high-hz", type=int, default=2000)
    parser.add_argument("--frame-ms", type=int, default=10)
    parser.add_argument("--smooth-frames", type=int, default=0)
    parser.add_argument("--expected-wpm-min", type=float, default=8.0)
    parser.add_argument("--expected-wpm-max", type=float, default=30.0)
    parser.add_argument("--no-wpm-prior", action="store_true", help="Use only measured timing; do not pull dit timing toward expected WPM")
    parser.add_argument("--char-gap-units", type=float, default=2.25)
    parser.add_argument("--word-gap-units", type=float, default=5.50)
    parser.add_argument("--on-fraction", type=float, default=0.55)
    parser.add_argument("--off-fraction", type=float, default=0.45)
    parser.add_argument("--label-min-confidence", type=float, default=0.72)
    parser.add_argument("--text-only", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = decode_wav(
        args.wav,
        low_hz=args.low_hz,
        high_hz=args.high_hz,
        frame_ms=args.frame_ms,
        smooth_frames=args.smooth_frames,
        expected_wpm_min=args.expected_wpm_min,
        expected_wpm_max=args.expected_wpm_max,
        use_wpm_prior=not args.no_wpm_prior,
        char_gap_units=args.char_gap_units,
        word_gap_units=args.word_gap_units,
        label_min_confidence=args.label_min_confidence,
        on_fraction=args.on_fraction,
        off_fraction=args.off_fraction,
    )
    if args.text_only:
        print(result.get("text", ""))
    else:
        print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
