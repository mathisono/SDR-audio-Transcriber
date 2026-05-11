from __future__ import annotations

import argparse
import json
import math
import shutil
import struct
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Terminal audio FFT plus coarse PPM finder helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fft_parser = subparsers.add_parser("fft", help="render terminal FFT from 16-bit mono PCM on stdin")
    fft_parser.add_argument("--rate", type=int, default=24000)
    fft_parser.add_argument("--fft-size", type=int, default=1024)
    fft_parser.add_argument("--bins", type=int, default=48)
    fft_parser.add_argument("--height", type=int, default=16)

    ppm_parser = subparsers.add_parser("ppm", help="search coarse PPM around a known signal")
    ppm_parser.add_argument("--frequency", type=int, required=True)
    ppm_parser.add_argument("--start-ppm", type=int, default=-150)
    ppm_parser.add_argument("--stop-ppm", type=int, default=150)
    ppm_parser.add_argument("--step-ppm", type=int, default=5)
    ppm_parser.add_argument("--gain", type=float, default=25.0)
    ppm_parser.add_argument("--device-index", type=int, default=0)
    ppm_parser.add_argument("--config", default="configs/shared_baseband_radio_server.json")
    ppm_parser.add_argument("--write-config", action="store_true")
    ppm_parser.add_argument("--override-ppm", type=int, default=None)

    return parser.parse_args()


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        print(f"missing required tool: {name}", file=sys.stderr)
        raise SystemExit(2)
    return path


def hanning(n: int) -> list[float]:
    if n <= 1:
        return [1.0]
    return [0.5 - 0.5 * math.cos((2.0 * math.pi * i) / (n - 1)) for i in range(n)]


def compute_magnitudes(samples: list[float], bins: int) -> list[float]:
    n = len(samples)
    half = n // 2
    spectrum = []
    for k in range(half):
        real = 0.0
        imag = 0.0
        for t, value in enumerate(samples):
            angle = 2.0 * math.pi * k * t / n
            real += value * math.cos(angle)
            imag -= value * math.sin(angle)
        spectrum.append(math.sqrt(real * real + imag * imag))
    step = max(1, len(spectrum) // bins)
    reduced = []
    for i in range(0, len(spectrum), step):
        chunk = spectrum[i:i + step]
        reduced.append(sum(chunk) / len(chunk))
        if len(reduced) >= bins:
            break
    return reduced


def normalize(values: list[float], height: int) -> list[int]:
    if not values:
        return []
    max_value = max(values) or 1.0
    return [min(height, int((value / max_value) * height)) for value in values]


def render(levels: list[int], height: int) -> str:
    rows = []
    for row in range(height, 0, -1):
        rows.append(''.join('█' if level >= row else ' ' for level in levels))
    rows.append(''.join('─' for _ in levels))
    return '\n'.join(rows)


def run_fft(args: argparse.Namespace) -> int:
    window = hanning(args.fft_size)
    frame_bytes = args.fft_size * 2
    while True:
        chunk = sys.stdin.buffer.read(frame_bytes)
        if len(chunk) < frame_bytes:
            break
        ints = struct.unpack('<' + 'h' * args.fft_size, chunk)
        samples = [(sample / 32768.0) * window[i] for i, sample in enumerate(ints)]
        magnitudes = compute_magnitudes(samples, args.bins)
        levels = normalize(magnitudes, args.height)
        sys.stdout.write('\x1b[2J\x1b[H')
        sys.stdout.write(render(levels, args.height))
        sys.stdout.write(f"\nmode=fft rate={args.rate} fft={args.fft_size} bins={args.bins}\n")
        sys.stdout.flush()
    return 0


def run_rtl_power(freq_hz: int, ppm: int, gain: float, device_index: int) -> float | None:
    freq_mhz = freq_hz / 1_000_000.0
    start = freq_mhz - 0.10
    stop = freq_mhz + 0.10
    command = [
        require_tool("rtl_power"),
        "-d", str(device_index),
        "-f", f"{start:.6f}M:{stop:.6f}M:1k",
        "-g", str(gain),
        "-p", str(ppm),
        "-i", "1",
        "-1",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.count(',') > 6:
            parts = [part.strip() for part in line.split(',')]
            try:
                values = [float(v) for v in parts[6:]]
            except ValueError:
                return None
            if values:
                return max(values)
    return None


def write_ppm_to_config(config_path: Path, ppm: int) -> None:
    data = {}
    if config_path.exists():
        data = json.loads(config_path.read_text(encoding="utf-8"))
    source = data.setdefault("source", {})
    source["ppm_correction"] = ppm
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def run_ppm(args: argparse.Namespace) -> int:
    best_ppm = None
    best_power = -math.inf
    print("ppm search results:")
    for ppm in range(args.start_ppm, args.stop_ppm + 1, args.step_ppm):
        power = run_rtl_power(args.frequency, ppm, args.gain, args.device_index)
        if power is None:
            print(f"ppm={ppm:>4}  power=error")
            continue
        print(f"ppm={ppm:>4}  power={power:>8.2f} dB")
        if power > best_power:
            best_power = power
            best_ppm = ppm
    if best_ppm is None:
        print("no valid ppm result found", file=sys.stderr)
        return 1
    print(f"\nbest ppm: {best_ppm} (peak power {best_power:.2f} dB)")
    chosen_ppm = args.override_ppm if args.override_ppm is not None else best_ppm
    if args.override_ppm is not None:
        print(f"override ppm selected: {chosen_ppm}")
    if args.write_config:
        config_path = Path(args.config)
        write_ppm_to_config(config_path, chosen_ppm)
        print(f"wrote ppm_correction={chosen_ppm} to {config_path}")
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "fft":
        return run_fft(args)
    if args.command == "ppm":
        return run_ppm(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
