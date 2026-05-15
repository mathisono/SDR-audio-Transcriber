#!/usr/bin/env python3
"""Inspect WAV clips for CW-like tone/keying evidence.

This tool is intentionally independent of MorseAngel or the internal CW decoder.
It answers the first debugging question: did the captured WAV actually contain a
keyed CW-like audio tone in the expected range?

It prints:
- WAV format and duration
- RMS / peak level
- strongest audio tone in a search band
- tone-vs-total energy ratio
- simple on/off keyed duty estimate
- likely diagnosis

Examples:

  .venv/bin/python3 scripts/inspect_cw_clip.py runtime/done/example.wav

  .venv/bin/python3 scripts/inspect_cw_clip.py runtime/done/example.wav --band-low 400 --band-high 1000

  .venv/bin/python3 scripts/inspect_cw_clip.py --latest 5
"""
from __future__ import annotations

import argparse
import json
import math
import wave
from pathlib import Path
from typing import Any

import audioop


def read_wav(path: Path) -> tuple[dict[str, Any], bytes]:
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        frames = wf.getnframes()
        data = wf.readframes(frames)
    info = {
        "path": str(path),
        "channels": channels,
        "sample_width_bytes": sample_width,
        "sample_rate": sample_rate,
        "frames": frames,
        "duration_sec": frames / sample_rate if sample_rate else 0,
    }
    return info, data


def iter_windows(data: bytes, sample_rate: int, sample_width: int, window_ms: int) -> list[bytes]:
    bytes_per_window = max(2, int(sample_rate * window_ms / 1000.0) * sample_width)
    bytes_per_window -= bytes_per_window % sample_width
    return [data[i : i + bytes_per_window] for i in range(0, len(data) - bytes_per_window + 1, bytes_per_window)]


def goertzel_power(samples: list[float], sample_rate: int, freq: float) -> float:
    if not samples:
        return 0.0
    n = len(samples)
    k = int(0.5 + (n * freq / sample_rate))
    omega = 2.0 * math.pi * k / n
    coeff = 2.0 * math.cos(omega)
    s_prev = 0.0
    s_prev2 = 0.0
    for sample in samples:
        s = sample + coeff * s_prev - s_prev2
        s_prev2 = s_prev
        s_prev = s
    return s_prev2 * s_prev2 + s_prev * s_prev - coeff * s_prev * s_prev2


def bytes_to_samples(window: bytes, sample_width: int) -> list[float]:
    if sample_width != 2:
        raise SystemExit(f"unsupported WAV sample width: {sample_width}; expected 16-bit PCM")
    count = len(window) // 2
    # Avoid struct dependency on huge unpack strings; manual little-endian int16 conversion.
    out: list[float] = []
    for i in range(count):
        lo = window[2 * i]
        hi = window[2 * i + 1]
        value = lo | (hi << 8)
        if value >= 32768:
            value -= 65536
        out.append(float(value))
    return out


def strongest_tone(samples: list[float], sample_rate: int, band_low: int, band_high: int, step: int) -> tuple[int, float, list[tuple[int, float]]]:
    powers: list[tuple[int, float]] = []
    for freq in range(band_low, band_high + 1, step):
        powers.append((freq, goertzel_power(samples, sample_rate, freq)))
    powers.sort(key=lambda item: item[1], reverse=True)
    if not powers:
        return 0, 0.0, []
    return powers[0][0], powers[0][1], powers[:8]


def analyze(path: Path, band_low: int, band_high: int, step: int, window_ms: int) -> dict[str, Any]:
    info, data = read_wav(path)
    sample_rate = int(info["sample_rate"])
    sample_width = int(info["sample_width_bytes"])
    channels = int(info["channels"])
    if channels != 1:
        return {"file": str(path), "error": f"expected mono WAV, got {channels} channels", "wav": info}
    if sample_width != 2:
        return {"file": str(path), "error": f"expected 16-bit WAV, got sample_width={sample_width}", "wav": info}

    rms = audioop.rms(data, sample_width) if data else 0
    peak = audioop.max(data, sample_width) if data else 0
    samples = bytes_to_samples(data, sample_width)
    peak_freq, peak_power, top = strongest_tone(samples, sample_rate, band_low, band_high, step)

    windows = iter_windows(data, sample_rate, sample_width, window_ms)
    window_rows: list[dict[str, Any]] = []
    tone_powers: list[float] = []
    rms_values: list[int] = []
    for idx, window in enumerate(windows):
        win_samples = bytes_to_samples(window, sample_width)
        win_rms = audioop.rms(window, sample_width)
        win_power = goertzel_power(win_samples, sample_rate, peak_freq) if peak_freq else 0.0
        tone_powers.append(win_power)
        rms_values.append(win_rms)
        window_rows.append({"index": idx, "rms": win_rms, "tone_power": win_power})

    if tone_powers:
        sorted_power = sorted(tone_powers)
        median_power = sorted_power[len(sorted_power) // 2]
        max_power = max(tone_powers)
        threshold = median_power + (max_power - median_power) * 0.35
        keyed_windows = sum(1 for value in tone_powers if value >= threshold and value > 0)
        duty = keyed_windows / len(tone_powers)
    else:
        median_power = max_power = threshold = 0.0
        keyed_windows = 0
        duty = 0.0

    avg_rms = sum(rms_values) / len(rms_values) if rms_values else 0.0
    rms_min = min(rms_values) if rms_values else 0
    rms_max = max(rms_values) if rms_values else 0

    # Heuristic verdicts. This is a verifier, not a decoder.
    if rms < 20 and peak < 100:
        verdict = "silent_or_nearly_silent"
    elif peak > 32000:
        verdict = "clipped_or_too_hot"
    elif duty < 0.02:
        verdict = "no_keyed_cw_tone_detected"
    elif duty > 0.95 and float(info["duration_sec"]) >= 55:
        verdict = "continuous_tone_or_squelch_stuck_not_keyed_cw"
    elif peak_freq < band_low or peak_freq > band_high:
        verdict = "tone_outside_expected_band"
    else:
        verdict = "cw_like_tone_present"

    return {
        "file": str(path),
        "wav": info,
        "levels": {"rms": rms, "peak": peak, "window_rms_avg": round(avg_rms, 1), "window_rms_min": rms_min, "window_rms_max": rms_max},
        "tone_search": {"band_low_hz": band_low, "band_high_hz": band_high, "step_hz": step, "peak_freq_hz": peak_freq, "top_peaks": [(f, round(p, 2)) for f, p in top]},
        "keying_estimate": {"window_ms": window_ms, "windows": len(windows), "keyed_windows": keyed_windows, "duty": round(duty, 3), "threshold": round(threshold, 2), "median_power": round(median_power, 2), "max_power": round(max_power, 2)},
        "verdict": verdict,
    }


def print_result(result: dict[str, Any]) -> None:
    if result.get("error"):
        print(f"{result['file']}: ERROR {result['error']}")
        return
    wav = result["wav"]
    levels = result["levels"]
    tone = result["tone_search"]
    keying = result["keying_estimate"]
    print(f"file: {result['file']}")
    print(f"  wav: {wav['duration_sec']:.3f}s {wav['sample_rate']}Hz channels={wav['channels']} width={wav['sample_width_bytes']}B")
    print(f"  levels: rms={levels['rms']} peak={levels['peak']} window_rms={levels['window_rms_min']}..{levels['window_rms_max']} avg={levels['window_rms_avg']}")
    print(f"  tone: strongest={tone['peak_freq_hz']}Hz search={tone['band_low_hz']}..{tone['band_high_hz']}Hz")
    print(f"  keying: duty={keying['duty']} keyed_windows={keying['keyed_windows']}/{keying['windows']} window={keying['window_ms']}ms")
    print(f"  verdict: {result['verdict']}")


def latest_wavs(done_dir: Path, count: int) -> list[Path]:
    wavs = sorted(done_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    return wavs[-count:]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect WAV clips for CW-like tone/keying evidence")
    parser.add_argument("wav", nargs="*", type=Path)
    parser.add_argument("--latest", type=int, default=0, help="Inspect latest N WAV files from --done")
    parser.add_argument("--done", type=Path, default=Path("runtime/done"))
    parser.add_argument("--band-low", type=int, default=400)
    parser.add_argument("--band-high", type=int, default=1000)
    parser.add_argument("--step", type=int, default=25)
    parser.add_argument("--window-ms", type=int, default=50)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = list(args.wav)
    if args.latest:
        paths.extend(latest_wavs(args.done, args.latest))
    if not paths:
        raise SystemExit("provide WAV path(s) or --latest N")

    results = [analyze(path, args.band_low, args.band_high, args.step, args.window_ms) for path in paths]
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for result in results:
            print_result(result)
            print("-" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
