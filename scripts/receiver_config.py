#!/usr/bin/env python3
"""Read or update receiver tuning in shared_baseband_radio_server.json.

This keeps the active receiver frequency in one place:

    configs/shared_baseband_radio_server.json -> receivers[].frequency_hz

The rtl_fm launcher and clip writer should both use this same value so WAV file
names and sidecar JSON always match the tuned receiver frequency.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = "configs/shared_baseband_radio_server.json"


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_config(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def find_receiver(data: dict[str, Any], receiver: str) -> dict[str, Any]:
    receivers = data.get("receivers", [])
    for item in receivers:
        if receiver in {str(item.get("id", "")), str(item.get("name", ""))}:
            return item
    available = ", ".join(str(item.get("id") or item.get("name")) for item in receivers)
    raise SystemExit(f"receiver not found: {receiver}. Available: {available}")


def frequency_label(frequency_hz: int) -> str:
    if frequency_hz >= 1_000_000:
        return f"{frequency_hz / 1_000_000.0:.6f}M"
    if frequency_hz >= 1_000:
        return f"{frequency_hz / 1_000.0:.3f}k"
    return f"{frequency_hz}"


def parse_frequency(value: str) -> int:
    text = value.strip().lower().replace("hz", "")
    multiplier = 1
    if text.endswith("mhz"):
        text = text[:-3]
        multiplier = 1_000_000
    elif text.endswith("m"):
        text = text[:-1]
        multiplier = 1_000_000
    elif text.endswith("khz"):
        text = text[:-3]
        multiplier = 1_000
    elif text.endswith("k"):
        text = text[:-1]
        multiplier = 1_000
    return int(round(float(text) * multiplier))


def receiver_summary(receiver: dict[str, Any]) -> dict[str, Any]:
    frequency_hz = int(receiver.get("frequency_hz", 0))
    return {
        "id": receiver.get("id"),
        "name": receiver.get("name"),
        "enabled": receiver.get("enabled", True),
        "mode": receiver.get("mode", "wbfm"),
        "frequency_hz": frequency_hz,
        "frequency_arg": frequency_label(frequency_hz),
        "bandwidth_hz": receiver.get("bandwidth_hz"),
        "squelch": receiver.get("squelch"),
        "transcription_enabled": receiver.get("transcription_enabled", False),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read/update configured receiver frequency")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--receiver", default="rx-1", help="Receiver id or name")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("show", help="Print receiver JSON summary")
    subparsers.add_parser("frequency-hz", help="Print receiver frequency in Hz")
    subparsers.add_parser("rtl-fm-frequency", help="Print rtl_fm -f compatible frequency, e.g. 90.700000M")

    set_parser = subparsers.add_parser("set-frequency", help="Set receiver frequency")
    set_parser.add_argument("frequency", help="Frequency like 441.000M, 90.7M, or 441000000")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    data = load_config(config_path)
    receiver = find_receiver(data, args.receiver)

    if args.command == "show":
        print(json.dumps(receiver_summary(receiver), indent=2))
        return 0

    if args.command == "frequency-hz":
        print(int(receiver.get("frequency_hz", 0)))
        return 0

    if args.command == "rtl-fm-frequency":
        print(frequency_label(int(receiver.get("frequency_hz", 0))))
        return 0

    if args.command == "set-frequency":
        old = int(receiver.get("frequency_hz", 0))
        new = parse_frequency(args.frequency)
        receiver["frequency_hz"] = new
        save_config(config_path, data)
        print(f"updated {args.receiver}: frequency_hz {old} -> {new}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
