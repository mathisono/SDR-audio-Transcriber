#!/usr/bin/env python3
"""Set live recorder threshold/hang settings for clip_writer.py.

clip_writer.py can watch runtime/recorder_control.json while running. This tool
updates that file safely so recording threshold can be adjusted without
restarting the GRC receiver stack.

Examples:

  .venv/bin/python3 scripts/set_recorder_control.py --threshold 10000
  .venv/bin/python3 scripts/set_recorder_control.py --threshold 6000 --hang-ms 2500
  .venv/bin/python3 scripts/set_recorder_control.py --show
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_CONTROL = Path("runtime/recorder_control.json")


def load_control(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Set live recorder control values")
    parser.add_argument("--control", type=Path, default=DEFAULT_CONTROL)
    parser.add_argument("--threshold", type=int, help="RMS threshold that opens recording")
    parser.add_argument("--hang-ms", type=int, help="Hang time after RMS drops")
    parser.add_argument("--min-sec", type=float, help="Minimum clip length")
    parser.add_argument("--max-sec", type=float, help="Maximum clip length")
    parser.add_argument("--show", action="store_true", help="Show current control values")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = load_control(args.control)
    changed = False

    if args.threshold is not None:
        data["threshold"] = int(args.threshold)
        changed = True
    if args.hang_ms is not None:
        data["hang_ms"] = int(args.hang_ms)
        changed = True
    if args.min_sec is not None:
        data["min_sec"] = float(args.min_sec)
        changed = True
    if args.max_sec is not None:
        data["max_sec"] = float(args.max_sec)
        changed = True

    if changed:
        atomic_write_json(args.control, data)
        print(f"updated {args.control}")

    if args.show or not changed:
        data = load_control(args.control)
        print(json.dumps(data, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
