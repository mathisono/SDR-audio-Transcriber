#!/usr/bin/env python3
"""Remember/apply the PPM value used by GNU Radio Companion flowgraphs.

The GRC GUI has a `ppm` QT range variable. This helper keeps that value in sync
with the existing shared config used by the rest of the SDR tools:

    configs/shared_baseband_radio_server.json -> source.ppm_correction

The parser intentionally accepts --grc either before or after the command because
older launch wrappers used both forms.

Examples:

    python3 scripts/grc_ppm_config.py --grc grc/shared_baseband_one_channel_fifo_nfm.grc remember
    python3 scripts/grc_ppm_config.py remember --grc grc/shared_baseband_one_channel_fifo_nfm.grc
    python3 scripts/grc_ppm_config.py apply --grc grc/shared_baseband_one_channel_fifo_nfm.grc
    python3 scripts/grc_ppm_config.py show --grc grc/shared_baseband_one_channel_fifo_nfm.grc
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path("configs/shared_baseband_radio_server.json")
DEFAULT_GRC_FILES = [
    Path("grc/shared_baseband_one_channel.grc"),
    Path("grc/shared_baseband_one_channel_fifo_nfm.grc"),
    Path("grc/shared_baseband_one_channel_fifo_wbfm.grc"),
]

PPM_BLOCK_RE = re.compile(
    r"(?P<head>- name: ppm\n  id: variable_qtgui_range\n  parameters:\n(?:(?!\n- name: ).)*?\n    value: )'(?P<value>-?\d+)'",
    re.DOTALL,
)


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_config(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def get_config_ppm(path: Path) -> int:
    data = load_config(path)
    return int(data.get("source", {}).get("ppm_correction", 0))


def set_config_ppm(path: Path, ppm: int) -> tuple[int, int]:
    data = load_config(path)
    source = data.setdefault("source", {})
    old = int(source.get("ppm_correction", 0))
    source["ppm_correction"] = int(ppm)
    save_config(path, data)
    return old, int(ppm)


def read_grc_ppm(path: Path) -> int:
    if not path.exists():
        raise SystemExit(f"GRC file not found: {path}")
    text = path.read_text(encoding="utf-8")
    match = PPM_BLOCK_RE.search(text)
    if not match:
        raise SystemExit(f"could not find ppm variable block in {path}")
    return int(match.group("value"))


def write_grc_ppm(path: Path, ppm: int) -> tuple[int, int]:
    if not path.exists():
        raise SystemExit(f"GRC file not found: {path}")
    text = path.read_text(encoding="utf-8")
    match = PPM_BLOCK_RE.search(text)
    if not match:
        raise SystemExit(f"could not find ppm variable block in {path}")
    old = int(match.group("value"))
    new_text = PPM_BLOCK_RE.sub(lambda m: f"{m.group('head')}'{int(ppm)}'", text, count=1)
    path.write_text(new_text, encoding="utf-8")
    return old, int(ppm)


def existing_grc_files(paths: list[Path]) -> list[Path]:
    return [path for path in paths if path.exists()]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"show", "remember", "apply", "set"}

    command = None
    ppm_value = None
    config = DEFAULT_CONFIG
    grc_files: list[Path] = []
    passthrough: list[str] = []

    i = 0
    while i < len(argv):
        token = argv[i]
        if token in commands and command is None:
            command = token
            i += 1
            if command == "set":
                if i >= len(argv):
                    raise SystemExit("set requires a ppm value")
                ppm_value = int(argv[i])
                i += 1
            continue
        if token == "--config":
            if i + 1 >= len(argv):
                raise SystemExit("--config requires a path")
            config = Path(argv[i + 1])
            i += 2
            continue
        if token == "--grc":
            if i + 1 >= len(argv):
                raise SystemExit("--grc requires a path")
            grc_files.append(Path(argv[i + 1]))
            i += 2
            continue
        if token in {"-h", "--help"}:
            print_help()
            raise SystemExit(0)
        passthrough.append(token)
        i += 1

    if command is None:
        print_help()
        raise SystemExit("missing command: use show, remember, apply, or set")
    if passthrough:
        raise SystemExit(f"unrecognized arguments: {' '.join(passthrough)}")

    return argparse.Namespace(command=command, ppm=ppm_value, config=config, grc=grc_files)


def print_help() -> None:
    print(
        "usage: grc_ppm_config.py [--config CONFIG] [--grc GRC ...] {show,remember,apply,set} [ppm]\n\n"
        "Commands:\n"
        "  show       Show shared config PPM and GRC file PPM\n"
        "  remember   Read PPM from the first GRC file and save it to shared config\n"
        "  apply      Apply shared config PPM to GRC file(s)\n"
        "  set PPM    Set shared config PPM and apply it to GRC file(s)\n\n"
        "Notes:\n"
        "  --grc may be placed before or after the command.\n"
    )


def main() -> int:
    args = parse_args()
    grc_files = args.grc or DEFAULT_GRC_FILES
    grc_files = existing_grc_files(grc_files)

    if args.command == "show":
        print(f"config {args.config}: ppm={get_config_ppm(args.config)}")
        for path in grc_files:
            print(f"grc {path}: ppm={read_grc_ppm(path)}")
        return 0

    if args.command == "remember":
        if not grc_files:
            raise SystemExit("no existing GRC file supplied/found to remember from")
        ppm = read_grc_ppm(grc_files[0])
        old, new = set_config_ppm(args.config, ppm)
        print(f"remembered GRC ppm from {grc_files[0]}: config {old} -> {new}")
        return 0

    if args.command == "apply":
        ppm = get_config_ppm(args.config)
        if not grc_files:
            raise SystemExit("no existing GRC files supplied/found to apply to")
        for path in grc_files:
            old, new = write_grc_ppm(path, ppm)
            print(f"applied config ppm to {path}: {old} -> {new}")
        return 0

    if args.command == "set":
        old, new = set_config_ppm(args.config, args.ppm)
        print(f"updated config ppm: {old} -> {new}")
        for path in grc_files:
            old_grc, new_grc = write_grc_ppm(path, new)
            print(f"applied ppm to {path}: {old_grc} -> {new_grc}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
