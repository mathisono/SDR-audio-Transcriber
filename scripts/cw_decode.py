#!/usr/bin/env python3
"""One-shot command-line CW/Morse decoder for WAV audio.

This is intended for repeater ID clips and other single-tone CW IDs inside FM
audio. It is not a full contest-grade CW skimmer. It favors robust, explainable
DSP over a black-box model:

  WAV -> mono PCM -> tone search -> Goertzel envelope -> adaptive gate -> timing
  estimate -> multi-attempt Morse decode -> callsign-biased candidates

The v5 path tries several gate/smoothing/gap combinations and chooses the best
scored decode. This is more reliable for short/noisy repeater IDs than a single
fixed threshold.

Important: clean Morse practice files made mostly of E/I/S/H/5 can decode into
callsign-looking strings such as EE5EHS. This script now blocks those as label
candidates unless the symbol stream has enough dah/dash content to look like a
real callsign-bearing ID.
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
LIKELY_ID_WORD_RE = re.compile(r"\b(?:DE|ID|RPT|REPEATER|TEST|QST)\b", re.IGNORECASE)


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


def median_filter(values: list[float], radius: int) -> list[float]:
    if radius <= 0 or not values:
        return values[:]
    out: list[float] = []
    for i in range(len(values)):
        lo = max(0, i - radius)
        hi = min(len(values), i + radius + 1)
        out.append(statistics.median(values[lo:hi]))
    return out


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * pct
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[int(pos)]
    return ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo)


def symbol_stats(symbols: str) -> dict[str, Any]:
    marks = symbols.replace(" ", "").replace("/", "")
    dot_count = marks.count(".")
    dash_count = marks.count("-")
    mark_count = dot_count + dash_count
    return {
        "dot_count": dot_count,
        "dash_count": dash_count,
        "mark_count": mark_count,
        "dash_ratio": round(dash_count / mark_count, 3) if mark_count else 0.0,
    }


def valid_callsign_candidate(callsign: str, symbols: str) -> bool:
    callsign = callsign.upper().strip()
    if not CALLSIGN_RE.fullmatch(callsign):
        return False
    stats = symbol_stats(symbols)
    # Block false callsigns from dot-heavy practice audio such as E/I/S/H/5 drills.
    if stats["mark_count"] >= 12 and stats["dash_count"] < 2:
        return False
    # Avoid highly repetitive fake labels like EE5EHS / EE5EHEE.
    letters = callsign.replace("0", "O")
    if len(set(letters)) <= 3 and not any(ch in letters for ch in "AKNW"):
        return False
    return True


def extract_callsigns(text: str, symbols: str = "") -> list[str]:
    compact = re.sub(r"[^A-Z0-9 ]", " ", (text or "").upper())
    direct = {match.group(0).upper() for match in CALLSIGN_RE.finditer(compact)}
    squashed = re.sub(r"\s+", "", compact)
    direct.update(match.group(0).upper() for match in CALLSIGN_RE.finditer(squashed))
    return sorted(callsign for callsign in direct if valid_callsign_candidate(callsign, symbols))


def estimate_tone(sample_rate: int, samples: list[float], low_hz: int, high_hz: int) -> dict[str, Any]:
    window = samples[: min(len(samples), sample_rate * 15)]
    if len(window) < sample_rate // 2:
        return {"detected": False, "reason": "clip too short"}
    coarse = list(range(low_hz, high_hz + 1, 25))
    coarse_powers = [(freq, goertzel_power(window, sample_rate, freq)) for freq in coarse]
    coarse_powers.sort(key=lambda item: item[1], reverse=True)
    best_freq = coarse_powers[0][0]
    floor = statistics.median([p for _, p in coarse_powers]) or 1.0
    fine = list(range(max(low_hz, best_freq - 45), min(high_hz, best_freq + 45) + 1, 5))
    fine_powers = [(freq, goertzel_power(window, sample_rate, freq)) for freq in fine]
    fine_powers.sort(key=lambda item: item[1], reverse=True)
    freq, power = fine_powers[0]
    ratio = power / floor
    return {
        "detected": ratio >= 5.0,
        "frequency_hz": int(freq),
        "power_ratio": round(ratio, 3),
        "confidence": round(max(0.0, min(1.0, math.log10(max(ratio, 1.0)) / 2.0)), 3),
        "top_frequencies": [{"frequency_hz": int(f), "relative_power": round(p / floor, 3)} for f, p in fine_powers[:5]],
    }


def tone_envelope(sample_rate: int, samples: list[float], freq_hz: float, frame_ms: int, smooth_frames: int, median_frames: int = 0) -> list[tuple[float, float]]:
    frame_len = max(1, int(sample_rate * frame_ms / 1000.0))
    times: list[float] = []
    powers: list[float] = []
    for start in range(0, len(samples) - frame_len + 1, frame_len):
        frame = samples[start:start + frame_len]
        powers.append(math.sqrt(goertzel_power(frame, sample_rate, freq_hz)))
        times.append(start / sample_rate)
    filtered = median_filter(powers, median_frames)
    smoothed = moving_average(filtered, smooth_frames)
    return list(zip(times, smoothed))


def activity_from_envelope(envelope: list[tuple[float, float]], on_fraction: float, off_fraction: float) -> tuple[list[bool], dict[str, Any]]:
    values = [v for _, v in envelope]
    if not values:
        return [], {"threshold": 0.0}
    noise = percentile(values, 0.20)
    mid = percentile(values, 0.50)
    high = percentile(values, 0.92)
    if high <= noise * 1.05:
        return [False for _ in values], {"noise_floor": round(noise, 6), "high_level": round(high, 6), "reason": "no envelope contrast"}
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
        "median_level": round(mid, 6),
        "high_level": round(high, 6),
        "on_fraction": on_fraction,
        "off_fraction": off_fraction,
        "on_threshold": round(on_threshold, 6),
        "off_threshold": round(off_threshold, 6),
    }


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


def runs_from_activity(activity: list[bool], frame_ms: int, glitch_frames: int = 1) -> list[tuple[bool, float]]:
    if not activity:
        return []
    runs: list[tuple[bool, float]] = []
    current = activity[0]
    count = 0
    for state in activity:
        if state == current:
            count += 1
        else:
            runs.append((current, count * frame_ms / 1000.0))
            current = state
            count = 1
    runs.append((current, count * frame_ms / 1000.0))
    return merge_short_runs(runs, min_duration=(frame_ms / 1000.0) * max(1.1, glitch_frames + 0.1))


def dit_from_wpm(wpm: float) -> float:
    return 1.2 / wpm


def cluster_dit_from_on_runs(on: list[float], min_dit: float, max_dit: float) -> float:
    if not on:
        return max_dit
    lo = max(min(on) * 0.60, min_dit * 0.60)
    hi = min(max_dit * 1.60, max(min(on) * 2.2, min(max(on), max_dit * 1.6)))
    if hi <= lo:
        return max(min_dit, min(max_dit, min(on)))
    best_dit = lo
    best_score = float("inf")
    for i in range(81):
        dit = lo + (hi - lo) * i / 80
        score = 0.0
        for duration in on:
            units = duration / dit
            target = 1.0 if units < 2.1 else 3.0
            score += min(abs(units - target), 2.0)
        score /= len(on)
        if min_dit <= dit <= max_dit:
            score *= 0.90
        if score < best_score:
            best_score = score
            best_dit = dit
    return max(min_dit * 0.60, min(max_dit * 1.60, best_dit))


def estimate_dit(runs: list[tuple[bool, float]], wpm_min: float, wpm_max: float, use_wpm_prior: bool) -> dict[str, Any]:
    on = [duration for active, duration in runs if active]
    if not on:
        return {"ok": False, "reason": "no key-down runs"}
    min_dit = dit_from_wpm(wpm_max)
    max_dit = dit_from_wpm(wpm_min)
    prior_wpm = (wpm_min + wpm_max) / 2.0
    prior_dit = dit_from_wpm(prior_wpm)
    measured_dit = cluster_dit_from_on_runs(on, min_dit, max_dit)
    if use_wpm_prior:
        if measured_dit < min_dit or measured_dit > max_dit:
            dit = prior_dit
            source = "wpm_prior_out_of_range"
        else:
            dit = (measured_dit * 0.70) + (prior_dit * 0.30)
            source = "measured_prior_blend"
    else:
        dit = measured_dit
        source = "measured_cluster"
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
            current += "." if units < 2.15 else "-"
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
    if current:
        symbols.append(current)

    symbols_text = " ".join(symbols)
    decoded_chars = [" " if sym == "/" else MORSE_TABLE.get(sym, "?") for sym in symbols]
    text = re.sub(r"\s+", " ", "".join(decoded_chars)).strip()
    non_space_chars = [c for c in decoded_chars if c != " "]
    known_non_space = [c for c in non_space_chars if c != "?"]
    base_confidence = min(1.0, len(known_non_space) / (len(non_space_chars) or 1))
    stats = symbol_stats(symbols_text)
    callsigns = extract_callsigns(text, symbols_text)
    confidence = min(0.99, base_confidence + 0.10) if callsigns and base_confidence >= 0.60 else base_confidence
    if stats["mark_count"] >= 12 and (stats["dash_ratio"] < 0.05 or stats["dash_ratio"] > 0.95):
        confidence = min(confidence, 0.55)
    return {
        "decoded": bool(text),
        "symbols": symbols_text,
        "text": text,
        "confidence": round(confidence, 3),
        "base_confidence": round(base_confidence, 3),
        "callsigns": callsigns,
        "symbol_stats": stats,
        "unknown_symbols": decoded_chars.count("?"),
        "char_gap_units": char_gap_units,
        "word_gap_units": word_gap_units,
    }


def score_decode(decoded: dict[str, Any], runs: list[tuple[bool, float]], duty_cycle: float, tone_confidence: float) -> float:
    text = decoded.get("text") or ""
    callsigns = decoded.get("callsigns") or []
    unknown = int(decoded.get("unknown_symbols", 0) or 0)
    confidence = float(decoded.get("confidence", 0.0) or 0.0)
    stats = decoded.get("symbol_stats") or {}
    mark_count = int(stats.get("mark_count", 0) or 0)
    dash_ratio = float(stats.get("dash_ratio", 0.0) or 0.0)
    non_space_len = len(re.sub(r"\s+", "", text))
    score = 0.0
    score += confidence * 4.0
    score += min(non_space_len, 20) * 0.08
    score += len(callsigns) * 2.5
    score += len(LIKELY_ID_WORD_RE.findall(text)) * 0.25
    score += min(len(runs), 80) * 0.01
    score += tone_confidence * 0.5
    score -= unknown * 0.35
    if mark_count >= 12 and dash_ratio < 0.05:
        score -= 3.0
    if mark_count >= 12 and dash_ratio > 0.82:
        score -= (dash_ratio - 0.82) * 2.0
    if duty_cycle < 0.02 or duty_cycle > 0.88:
        score -= 2.0
    if text and set(text.replace(" ", "")) <= {"T", "E"} and len(text.replace(" ", "")) > 8:
        score -= 3.0
    return round(score, 4)


def decode_attempt(sample_rate: int, samples: list[float], tone: dict[str, Any], frame_ms: int, smooth_frames: int, median_frames: int, on_fraction: float, off_fraction: float, expected_wpm_min: float, expected_wpm_max: float, use_wpm_prior: bool, char_gap_units: float, word_gap_units: float) -> dict[str, Any]:
    envelope = tone_envelope(sample_rate, samples, float(tone["frequency_hz"]), frame_ms, smooth_frames, median_frames)
    activity, threshold = activity_from_envelope(envelope, on_fraction, off_fraction)
    runs = runs_from_activity(activity, frame_ms, glitch_frames=max(1, smooth_frames))
    active_time = sum(duration for active, duration in runs if active)
    total_time = sum(duration for _, duration in runs) or 1.0
    duty_cycle = active_time / total_time
    keyed_candidate = 0.02 < duty_cycle < 0.88 and len(runs) >= 5
    attempt: dict[str, Any] = {
        "params": {"frame_ms": frame_ms, "smooth_frames": smooth_frames, "median_frames": median_frames, "on_fraction": on_fraction, "off_fraction": off_fraction, "char_gap_units": char_gap_units, "word_gap_units": word_gap_units},
        "threshold": threshold,
        "duty_cycle": round(duty_cycle, 3),
        "keyed_candidate": keyed_candidate,
        "run_count": len(runs),
        "runs": [{"active": active, "duration": round(duration, 4)} for active, duration in runs[:180]],
    }
    if not keyed_candidate:
        attempt.update({"decoded": False, "reason": "tone is not keyed like CW", "score": -5.0})
        return attempt
    timing = estimate_dit(runs, expected_wpm_min, expected_wpm_max, use_wpm_prior)
    attempt["timing"] = timing
    if not timing.get("ok"):
        attempt.update({"decoded": False, "reason": timing.get("reason", "timing failed"), "score": -4.0})
        return attempt
    decoded = decode_runs(runs, float(timing["dit_seconds"]), char_gap_units, word_gap_units)
    attempt.update(decoded)
    attempt["score"] = score_decode(decoded, runs, duty_cycle, float(tone.get("confidence", 0.0) or 0.0))
    return attempt


def decode_wav(path: Path, low_hz: int = 300, high_hz: int = 2000, frame_ms: int = 10, smooth_frames: int = 0, expected_wpm_min: float = 8.0, expected_wpm_max: float = 30.0, use_wpm_prior: bool = True, char_gap_units: float = 2.25, word_gap_units: float = 5.50, label_min_confidence: float = 0.72, on_fraction: float = 0.55, off_fraction: float = 0.45, auto_tune: bool = True, max_attempts_reported: int = 8) -> dict[str, Any]:
    sample_rate, samples = read_wav_mono(path)
    duration = len(samples) / sample_rate if sample_rate else 0.0
    tone = estimate_tone(sample_rate, samples, low_hz, high_hz)
    result: dict[str, Any] = {"engine": "cw_decode_dsp_v5.1", "file": path.name, "sample_rate": sample_rate, "duration_sec": round(duration, 3), "tone": tone, "decoded": False, "text": "", "callsigns": [], "confidence": 0.0}
    if not tone.get("detected") or not tone.get("frequency_hz"):
        result["reason"] = tone.get("reason", "tone not detected")
        return result

    if auto_tune:
        frame_values = sorted(set([frame_ms, 8, 10, 12, 15]))
        smooth_values = sorted(set([smooth_frames, 0, 1, 2, 3]))
        gate_values = [(0.48, 0.38), (0.55, 0.45), (0.62, 0.50), (0.70, 0.58)]
        gap_values = [(2.0, 5.0), (2.25, 5.5), (2.7, 6.3), (3.0, 7.0)]
    else:
        frame_values = [frame_ms]
        smooth_values = [smooth_frames]
        gate_values = [(on_fraction, off_fraction)]
        gap_values = [(char_gap_units, word_gap_units)]

    attempts: list[dict[str, Any]] = []
    for fm in frame_values:
        for sm in smooth_values:
            for onf, offf in gate_values:
                if offf >= onf:
                    continue
                for cgap, wgap in gap_values:
                    attempts.append(decode_attempt(sample_rate, samples, tone, fm, sm, 0, onf, offf, expected_wpm_min, expected_wpm_max, use_wpm_prior, cgap, wgap))
    attempts.sort(key=lambda item: float(item.get("score", -999.0)), reverse=True)
    best = attempts[0] if attempts else {"decoded": False, "reason": "no attempts"}
    result.update({
        "attempt_count": len(attempts),
        "best_attempt": {k: v for k, v in best.items() if k != "runs"},
        "attempts": [{k: v for k, v in attempt.items() if k != "runs"} for attempt in attempts[:max_attempts_reported]],
        "runs": best.get("runs", []),
        "threshold": best.get("threshold", {}),
        "frame_ms": best.get("params", {}).get("frame_ms", frame_ms),
        "smooth_frames": best.get("params", {}).get("smooth_frames", smooth_frames),
        "duty_cycle": best.get("duty_cycle", 0.0),
        "keyed_candidate": best.get("keyed_candidate", False),
        "timing": best.get("timing", {}),
        "decoded": bool(best.get("decoded")),
        "symbols": best.get("symbols", ""),
        "text": best.get("text", ""),
        "confidence": best.get("confidence", 0.0),
        "base_confidence": best.get("base_confidence", 0.0),
        "callsigns": best.get("callsigns", []),
        "symbol_stats": best.get("symbol_stats", {}),
        "unknown_symbols": best.get("unknown_symbols", 0),
        "score": best.get("score", 0.0),
    })
    if not result["decoded"]:
        result["reason"] = best.get("reason", "decode failed")
    result["label_candidates"] = [
        {"type": "cw_callsign", "label": callsign, "value": callsign, "confidence": result.get("confidence", 0.0), "source": "cw_decode_dsp_v5.1", "tone_hz": tone.get("frequency_hz"), "score": result.get("score", 0.0)}
        for callsign in result.get("callsigns", [])
        if float(result.get("confidence", 0.0)) >= label_min_confidence and valid_callsign_candidate(callsign, result.get("symbols", ""))
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
    parser.add_argument("--auto-tune", dest="auto_tune", action="store_true", default=True, help="Try multiple gate/timing settings and pick the best decode. Default: enabled")
    parser.add_argument("--no-auto-tune", dest="auto_tune", action="store_false", help="Use only the exact gate/timing settings supplied")
    parser.add_argument("--max-attempts-reported", type=int, default=8)
    parser.add_argument("--text-only", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = decode_wav(args.wav, low_hz=args.low_hz, high_hz=args.high_hz, frame_ms=args.frame_ms, smooth_frames=args.smooth_frames, expected_wpm_min=args.expected_wpm_min, expected_wpm_max=args.expected_wpm_max, use_wpm_prior=not args.no_wpm_prior, char_gap_units=args.char_gap_units, word_gap_units=args.word_gap_units, label_min_confidence=args.label_min_confidence, on_fraction=args.on_fraction, off_fraction=args.off_fraction, auto_tune=args.auto_tune, max_attempts_reported=args.max_attempts_reported)
    if args.text_only:
        print(result.get("text", ""))
    else:
        print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
