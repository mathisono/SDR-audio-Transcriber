#!/usr/bin/env python3
"""Check RTL-SDR signal strength, gain setting, and PPM centering with rtl_power.

This diagnostic scans a small span around a target frequency, reports peak/SNR,
estimates PPM error, recommends gain/AGC/PPM settings, and can update
configs/shared_baseband_radio_server.json with --write-config.

PPM writes are guarded. Very large apparent PPM errors are usually caused by
locking onto the wrong peak, a DC spike, or a neighboring carrier, so the script
will not write unsafe PPM values unless --force-ppm is used.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import statistics
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def parse_frequency_hz(text: str) -> int:
    value = text.strip().lower().replace("hz", "")
    mult = 1.0
    if value.endswith("mhz"):
        value, mult = value[:-3], 1_000_000.0
    elif value.endswith("m"):
        value, mult = value[:-1], 1_000_000.0
    elif value.endswith("khz"):
        value, mult = value[:-3], 1_000.0
    elif value.endswith("k"):
        value, mult = value[:-1], 1_000.0
    return int(round(float(value) * mult))


def format_hz(hz: float) -> str:
    if abs(hz) >= 1_000_000:
        return f"{hz / 1_000_000:.6f} MHz"
    if abs(hz) >= 1_000:
        return f"{hz / 1_000:.3f} kHz"
    return f"{hz:.1f} Hz"


def parse_power_csv(path: Path) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    with path.open("r", newline="") as f:
        for row in csv.reader(f):
            if len(row) < 7:
                continue
            try:
                start_hz = float(row[2])
                stop_hz = float(row[3])
                step_hz = float(row[4])
                values = [float(item) for item in row[6:] if item.strip()]
            except ValueError:
                continue
            for idx, db in enumerate(values):
                freq = start_hz + (idx * step_hz)
                if freq <= stop_hz:
                    points.append((freq, db))
    return points


def percentile(values: list[float], pct: float) -> float:
    sorted_values = sorted(values)
    index = (len(sorted_values) - 1) * pct
    lo = math.floor(index)
    hi = math.ceil(index)
    if lo == hi:
        return sorted_values[int(index)]
    return sorted_values[lo] * (hi - index) + sorted_values[hi] * (index - lo)


def normalize_gain(value: str) -> str:
    text = str(value).strip().lower()
    if text in {"auto", "agc"}:
        return "auto"
    return str(float(text)).rstrip("0").rstrip(".")


def normalize_mode(mode: str) -> str:
    text = mode.lower().strip()
    if text in {"nfm", "fm", "narrow", "narrowfm", "narrowband", "narrowbandfm"}:
        return "nfm"
    if text in {"wbfm", "wide", "widefm", "wideband", "widebandfm"}:
        return "wbfm"
    return text


def run_rtl_power(args: argparse.Namespace, output: Path) -> None:
    freq_hz = parse_frequency_hz(args.frequency)
    half_span_hz = args.span_khz * 500.0
    start_hz = int(freq_hz - half_span_hz)
    stop_hz = int(freq_hz + half_span_hz)
    freq_spec = f"{start_hz}:{stop_hz}:{int(args.bin_hz)}"
    cmd = ["rtl_power", "-f", freq_spec, "-i", str(args.interval), "-e", str(args.duration)]
    if args.ppm is not None:
        cmd.extend(["-p", str(args.ppm)])
    if normalize_gain(args.gain) != "auto":
        cmd.extend(["-g", str(args.gain)])
    cmd.append(str(output))

    print("signal_check: running", " ".join(cmd), flush=True)
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if result.stdout.strip():
        print(result.stdout.strip(), flush=True)
    if result.stderr.strip():
        print(result.stderr.strip(), flush=True)
    if result.returncode != 0:
        raise SystemExit(f"rtl_power failed with exit code {result.returncode}")


def analyze(points: list[tuple[float, float]], target_hz: int, ignore_center_hz: float = 0) -> dict[str, float]:
    if not points:
        raise SystemExit("No rtl_power points were parsed")
    usable = points
    if ignore_center_hz > 0:
        usable = [(f, p) for f, p in points if abs(f - target_hz) > ignore_center_hz] or points
    powers = [p for _, p in usable]
    noise_floor = percentile(powers, 0.20)
    median_power = statistics.median(powers)
    peak_freq, peak_power = max(usable, key=lambda item: item[1])
    offset_hz = peak_freq - target_hz
    ppm_error = (offset_hz / target_hz) * 1_000_000.0 if target_hz else 0.0
    return {
        "target_hz": float(target_hz),
        "peak_freq_hz": peak_freq,
        "offset_hz": offset_hz,
        "ppm_error_estimate": ppm_error,
        "peak_db": peak_power,
        "noise_floor_db": noise_floor,
        "median_db": median_power,
        "snr_db": peak_power - noise_floor,
        "bins": float(len(usable)),
    }


def recommended_settings(metrics: dict[str, float], args: argparse.Namespace) -> dict[str, Any]:
    gain_text = normalize_gain(args.gain)
    current_ppm = float(args.ppm or 0.0)
    ppm_estimate = float(metrics["ppm_error_estimate"])
    snr = float(metrics["snr_db"])
    peak = float(metrics["peak_db"])
    floor = float(metrics["noise_floor_db"])
    raw_suggested_ppm = int(round(current_ppm - ppm_estimate))

    ppm_safe = True
    ppm_warning = ""
    if abs(ppm_estimate) > args.max_ppm_delta:
        ppm_safe = False
        ppm_warning = f"Unsafe PPM delta {ppm_estimate:+.2f} exceeds max {args.max_ppm_delta}; probably wrong peak/DC/spur."
    if abs(raw_suggested_ppm) > args.max_abs_ppm:
        ppm_safe = False
        ppm_warning = f"Unsafe absolute PPM {raw_suggested_ppm:+d} exceeds max {args.max_abs_ppm}; not writing PPM."
    if snr < args.min_snr_for_ppm:
        ppm_safe = False
        ppm_warning = f"SNR {snr:.1f} dB below minimum {args.min_snr_for_ppm}; not writing PPM."

    suggested_ppm = raw_suggested_ppm if (ppm_safe or args.force_ppm) else int(round(current_ppm))

    use_agc = gain_text == "auto"
    suggested_gain: float | None = None
    if not use_agc:
        suggested_gain = float(gain_text)
        if peak > -5:
            suggested_gain = max(0.0, suggested_gain - 7.0)
        elif snr < 8 and floor > -45:
            suggested_gain = max(0.0, suggested_gain - 5.0)
        elif snr < 8 and peak < -45:
            suggested_gain = min(50.0, suggested_gain + 6.0)
        elif 8 <= snr < 15 and peak < -35:
            suggested_gain = min(50.0, suggested_gain + 3.0)
        suggested_gain = round(suggested_gain, 1)

    if snr < 8 and floor > -45:
        suggested_agc = False
        gain_note = "noise floor high / SNR poor: avoid AGC or reduce manual gain"
    elif use_agc and snr >= 15:
        suggested_agc = True
        gain_note = "AGC/auto gain appears usable"
    elif use_agc:
        suggested_agc = False
        gain_note = "AGC may be lifting noise; compare with manual gain"
    else:
        suggested_agc = False
        gain_note = "manual gain appears usable" if snr >= 8 else "manual gain needs adjustment"

    return {
        "target_frequency_hz": int(metrics["target_hz"]),
        "raw_suggested_ppm_correction": raw_suggested_ppm,
        "suggested_ppm_correction": suggested_ppm,
        "ppm_delta": round(-ppm_estimate, 2),
        "ppm_write_safe": ppm_safe or args.force_ppm,
        "ppm_warning": ppm_warning,
        "suggested_gain_db": suggested_gain,
        "suggested_agc_enabled": suggested_agc,
        "suggested_gain_mode": "auto" if suggested_agc else "manual",
        "gain_note": gain_note,
        "snr_db": round(snr, 1),
        "peak_db": round(peak, 1),
        "noise_floor_db": round(floor, 1),
        "ppm_note": "very close" if abs(ppm_estimate) <= 2 else "usable but can be tightened" if abs(ppm_estimate) <= 10 else "unsafe/verify peak before changing PPM",
    }


def print_report(metrics: dict[str, float], recs: dict[str, Any], gain: str, ppm: str | None) -> None:
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
    print("\nRecommended receiver settings")
    print("-----------------------------")
    print(f"source.ppm_correction: {recs['suggested_ppm_correction']}  ({recs['ppm_delta']:+.2f} ppm delta from this run)")
    if not recs["ppm_write_safe"]:
        print(f"PPM write blocked: {recs['ppm_warning']}")
    print(f"source.gain_mode: {recs['suggested_gain_mode']}")
    print(f"source.agc_enabled: {recs['suggested_agc_enabled']}")
    if recs["suggested_gain_db"] is not None:
        print(f"source.gain_db: {recs['suggested_gain_db']}")
    print(f"SNR: {recs['snr_db']} dB")
    print(f"Gain note: {recs['gain_note']}")
    print(f"PPM note: {recs['ppm_note']}")
    print("\nJSON recommendation")
    print(json.dumps(recs, indent=2))


def update_config(path: Path, recs: dict[str, Any], args: argparse.Namespace) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    source = data.setdefault("source", {})
    if recs["ppm_write_safe"]:
        source["ppm_correction"] = recs["suggested_ppm_correction"]
    else:
        print(f"\nsignal_check: PPM not updated: {recs['ppm_warning']}")
    source["agc_enabled"] = bool(recs["suggested_agc_enabled"])
    source["gain_mode"] = recs["suggested_gain_mode"]
    if recs["suggested_gain_db"] is not None:
        source["gain_db"] = recs["suggested_gain_db"]
    source["center_frequency_hz"] = recs["target_frequency_hz"]

    receiver_id = args.receiver
    receivers = data.setdefault("receivers", [])
    for rx in receivers:
        if str(rx.get("id")) == receiver_id or str(rx.get("name")) == receiver_id:
            rx["frequency_hz"] = recs["target_frequency_hz"]
            rx["mode"] = normalize_mode(args.mode)
            break
    else:
        receivers.append({
            "id": receiver_id,
            "name": receiver_id,
            "enabled": True,
            "mode": normalize_mode(args.mode),
            "frequency_hz": recs["target_frequency_hz"],
            "offset_hz": 0,
            "transcription_enabled": True,
        })

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    print(f"\nsignal_check: updated {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Use rtl_power to check signal strength, gain, and PPM centering")
    parser.add_argument("--frequency", required=True)
    parser.add_argument("--mode", default="nfm")
    parser.add_argument("--receiver", default="rx-1")
    parser.add_argument("--config", default="configs/shared_baseband_radio_server.json")
    parser.add_argument("--write-config", action="store_true")
    parser.add_argument("--force-ppm", action="store_true", help="Allow unsafe PPM writes. Use only with a known reference carrier.")
    parser.add_argument("--max-ppm-delta", type=float, default=25.0, help="Maximum PPM change allowed without --force-ppm")
    parser.add_argument("--max-abs-ppm", type=float, default=150.0, help="Maximum absolute PPM allowed without --force-ppm")
    parser.add_argument("--min-snr-for-ppm", type=float, default=12.0, help="Minimum SNR required to write PPM")
    parser.add_argument("--span-khz", type=float, default=200.0)
    parser.add_argument("--bin-hz", type=float, default=1000.0)
    parser.add_argument("--duration", default="10s")
    parser.add_argument("--interval", default="1s")
    parser.add_argument("--gain", default="auto")
    parser.add_argument("--ppm", type=float, default=None)
    parser.add_argument("--keep-csv", default="")
    parser.add_argument("--ignore-center-hz", type=float, default=0.0)
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
    recs = recommended_settings(metrics, args)
    print_report(metrics, recs, args.gain, None if args.ppm is None else str(args.ppm))
    if args.write_config:
        update_config(Path(args.config), recs, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
