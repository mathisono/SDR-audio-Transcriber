#!/usr/bin/env python3
"""Read or update the shared RTL-SDR PPM correction value.

PPM correction belongs to the SDR source, not to individual receivers. All
receiver commands should read this one value from:

    configs/shared_baseband_radio_server.json -> source.ppm_correction

Examples:
    python3 scripts/ppm_config.py show
    python3 scripts/ppm_config.py set 135
    python3 scripts/ppm_config.py rtl-fm-args
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


def get_ppm(data: dict[str, Any]) -> int:
    source = data.get("source", {})
    return int(source.get("ppm_correction", 0))


def set_ppm(data: dict[str, Any], ppm: int) -> None:
    source = data.setdefault("source", {})
    source["ppm_correction"] = int(ppm)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show or set the shared source.ppm_correction value."
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG)

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("show", help="Print the current shared PPM correction")

    set_parser = subparsers.add_parser("set", help="Set the shared PPM correction")
    set_parser.add_argument("ppm", type=int, help="PPM correction value, for example 135")

    subparsers.add_parser("rtl-fm-args", help="Print rtl_fm args for the configured PPM correction")

    subparsers.add_parser("json", help="Print a small JSON object with the configured PPM correction")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    data = load_config(config_path)

    if args.command == "show":
        print(get_ppm(data))
        return 0

    if args.command == "set":
        old_ppm = get_ppm(data)
        set_ppm(data, args.ppm)
        save_config(config_path, data)
        print(f"updated {config_path}: source.ppm_correction {old_ppm} -> {args.ppm}")
        return 0

    if args.command == "rtl-fm-args":
        print(f"-p {get_ppm(data)}")
        return 0

    if args.command == "json":
        print(json.dumps({"ppm_correction": get_ppm(data)}))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
