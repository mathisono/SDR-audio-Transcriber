#!/usr/bin/env python3
"""Check RTL-SDR signal strength, gain setting, and PPM centering with rtl_power.

This is a preflight / diagnostic tool for the rtl_fm ingest path. It scans a
small span around a target frequency, finds the strongest bin, estimates signal
level, noise floor, SNR, frequency offset, and approximate PPM error.

Examples:
  .venv/bin/python3 scripts/rtl_signal_check.py --frequency 162.4M --gain 42 --ppm -41
  .venv/bin/python3 scripts/rtl_signal_check.py --frequency 442.275M --gain 42 --ppm -41 --span-khz 200
  .venv/bin/python3 scripts/rtl_signal_check.py --frequency 162.4M --gain auto --ppm -41
"""
from __future__ import annotations

import argparse
import csv
import math
import shutil
import statistics
import subprocess
import tempfile
from pathlib import Path


def parse_frequency_hz(text: str) -> int:
    value = text.strip().lower().replace("hz", "")
    mult = 1.0
    if value.endswith("mhz"):
        value = value[:-3]
        mult = 1_000_000.0
    elif value.endswith("m"):
        value = value[:-1]
        mult = 1_000_000.0
    elif value.endswith("khz"):
        value = value[:-3]
        mult = 1_000.0
    elif value.endswith("k"):
        value = value[:-1]
        mult = 1_000.0
    return int(round(float(value) * mult))


def format_hz(hz: float) -> str:
    if abs(hz) >= 1_000_000:
        return f"{hz / 1_000_000:.6f} MHz"
    if abs(hz) >= 1_000:
        return f"{hz / 1_000:.3f} kHz"
    return f"{hz:.1f} Hz"


def parse_power_csv(path: Path) -> list[tuple[float, float]]:
    """Return [(frequency_hz, db), ...] from rtl_power CSV output."""
    points: list[tuple[float, float]] = []
    with path.open("r", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 7:
                continue
            try:
                start_hz = float(row[2])
                stop_hz = float(row[3])
                step_hz = float(row[4])
                values = [float(item) for item in row[6:] if item.strip()]
            except ValueError:
                continue
            if not values or step_hz <= 0:
                continue
            for idx, db in enumerate(values):
                freq = start_hz + (idx * step_hz)
                if freq <= stop_hz:
                    points.append((freq, db))
    return points


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return float("nan")
    sorted_values = sorted(values)
    index = (len(sorted_values) - 1) * pct
    lo = math.floor(index)
    hi = math.ceil(index)
    if lo == hi:
        return sorted_values[int(index)]
    return sorted_values[lo] * (hi - index) + sorted_values[hi] * (index - lo)


def run_rtl_power(args: argparse.Namespace, output: Path) -> list[str]:
    freq_hz = parse_frequency_hz(args.frequency)
    half_span_hz = args.span_khz * 500.0
    start_hz = int(freq_hz - half_span_hz)
    stop_hz = int(freq_hz + half_span_hz)
    bin_hz = int(args.bin_hz)

    freq_spec = f"{start_hz}:{stop_hz}:{bin_hz}"
    cmd = ["rtl_power", "-f", freq_spec, "-i", str(args.interval), "-e", str(args.duration)]
    if args.ppm is not None:
        cmd.extend(["-p", str(args.ppm)])
    if args.gain and args.gain.lower() not in {"auto", "agc"}:
        cmd.extend(["-g", str(args.gain)])
    cmd.append(str(output))

    print("running:", " ".join(cmd), flush=True)
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())
    if result.returncode != 0:
        raise SystemExit(f"rtl_power failed with exit code {result.returncode}")
    return cmd


def analyze(points: list[tuple[float, float]], target_hz: int, ignore_center_hz: float = 0) -> dict[str, float]:
    if not points:
        raise SystemExit("No rtl_power points were parsed")
    usable = points
    if ignore_center_hz > 0:
        usable = [(f, p) for f, p in points if abs(f - target_hz) > ignore_center_hz]
        if not usable:
            usable = points
    powers = [p for _, p in usable]
    noise_floor = percentile(powers, 0.20)
    median_power = statistics.median(powers)
    peak_freq, peak_power = max(usable, key=lambda item: item[1])
    offset_hz = peak_freq - target_hz
    ppm_error = (offset_hz / target_hz) * 1_000_000.0 if target_hz else 0.0
    snr = peak_power - noise_floor
    return {
        "target_hz": float(target_hz),
        "peak_freq_hz": peak_freq,
        "offset_hz": offset_hz,
        "ppm_error_estimate": ppm_error,
        "peak_db": peak_power,
        "noise_floor_db": noise_floor,
        "median_db": median_power,
        "snr_db": snr,
        "bins": float(len(usable)),
    }


def print_report(metrics: dict[str, float], gain: str, ppm: str | None) -> None:
    print("\nRTL signal check")
    print("================")
    print(f"Target:       {format_hz(metrics['target_hz'])}")
    print(f"Peak:         {format_hz(metrics['peak_freq_hz'])}")
    print(f"Offset:       {format_hz(metrics['offset_hz'])}")
    print(f"PPM estimate: {metrics['ppm_error_estimate']:+.2f} ppm relative to target")
    print(f"Peak level:   {metrics['peak_db']:.1f} dB")
    print(f"Noise floor:  {metrics['noise_floor_db']:.1f} dB")
    print(f"Median level: {metrics['median_db']:.1f} dB")
    print(f"SNR estimate: {metrics['snr_db']:.1f} dB")
    print(f"Gain used:    {gain}")
    print(f"PPM used:     {ppm if ppm is not None else 'not specified'}")

    print("\nInterpretation")
    print("--------------")
    snr = metrics["snr_db"]
    ppm_abs = abs(metrics["ppm_error_estimate"])

    if snr >= 25:
        print("Signal strength: strong / easy copy.")
    elif snr >= 15:
        print("Signal strength: usable.")
    elif snr >= 8:
        print("Signal strength: weak; transcription may be unreliable.")
    else:
        print("Signal strength: very weak or wrong frequency / antenna / gain.")

    if ppm_abs <= 2:
        print("PPM: very close.")
    elif ppm_abs <= 10:
        print("PPM: usable, but could be tightened.")
    else:
        print("PPM: likely needs correction or the detected peak is not the intended signal.")

    peak = metrics["peak_db"]
    floor = metrics["noise_floor_db"]
    if peak > -5:
        print("Gain: peak is very high; if audio is distorted, reduce gain.")
    elif snr < 8 and floor > -45:
        print("Gain: noise floor is high and SNR is poor; reduce gain or avoid AGC.")
    elif snr < 8:
        print("Gain: try more gain, better antenna, or verify frequency/PPM.")
    else:
        print("Gain: likely reasonable for this signal.")

    suggested_ppm_delta = metrics["ppm_error_estimate"]
    print("\nPPM note")
    print("--------")
    print("If this peak is the intended carrier, adjust your stored PPM by approximately the negative of the estimate above.")
    print(f"Example: if current PPM is X, try X {(-suggested_ppm_delta):+.2f} ppm adjustment.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Use rtl_power to check signal strength, gain, and PPM centering")
    parser.add_argument("--frequency", required=True, help="Target frequency, e.g. 162.4M or 442.275M")
    parser.add_argument("--span-khz", type=float, default=200.0, help="Total scan span in kHz. Default: 200")
    parser.add_argument("--bin-hz", type=float, default=1000.0, help="rtl_power bin width in Hz. Default: 1000")
    parser.add_argument("--duration", default="10s", help="rtl_power duration, e.g. 10s, 30s, 1m")
    parser.add_argument("--interval", default="1s", help="rtl_power integration interval. Default: 1s")
    parser.add_argument("--gain", default="auto", help="Gain dB, or auto/agc to omit -g. Default: auto")
    parser.add_argument("--ppm", type=float, default=None, help="PPM correction passed to rtl_power")
    parser.add_argument("--keep-csv", default="", help="Optional path to keep rtl_power CSV output")
    parser.add_argument("--ignore-center-hz", type=float, default=0.0, help="Ignore bins near exact center, useful for DC spike checks")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not shutil.which("rtl_power"):
        raise SystemExit("rtl_power not found. Install rtl-sdr package first.")
    target_hz = parse_frequency_hz(args.frequency)

    if args.keep_csv:
        output = Path(args.keep_csv)
        output.parent.mkdir(parents=True, exist_ok=True)
        run_rtl_power(args, output)
        points = parse_power_csv(output)
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "rtl_power.csv"
            run_rtl_power(args, output)
            points = parse_power_csv(output)

    metrics = analyze(points, target_hz, args.ignore_center_hz)
    print_report(metrics, args.gain, None if args.ppm is None else str(args.ppm))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
