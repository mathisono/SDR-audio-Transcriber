#!/usr/bin/env python3
"""Wrap a configured external CW decoder; this adapter is not a neural model."""
from __future__ import annotations

import argparse
import os
import json
from pathlib import Path
from typing import Any

from clip_classifier import extract_callsigns
from safe_runtime import atomic_json, command_argv, decoded_output, run_command


def build_argv(command: str, wav_path: Path) -> list[str]:
    return command_argv(command, wav_path)


def normalize_result(*, wav_path: Path, command: str, argv: list[str], returncode: int | None,
                     stdout: str, stderr: str, error: str | None) -> dict[str, Any]:
    if returncode not in (None, 0):
        error = error or f'decoder exited {returncode}'
    result = decoded_output(stdout, error)
    result.update(engine='morseangel-adapter', input=str(wav_path), command=command, argv=argv,
                  returncode=returncode, stderr=stderr[-4000:], verified=False)
    result['callsigns'] = extract_callsigns(result['text']) if result['decoded'] else []
    return result


def run_adapter(args: argparse.Namespace) -> dict[str, Any]:
    command = args.command or os.environ.get('MORSEANGEL_COMMAND', '')
    proc = {'argv': [], 'returncode': None, 'stdout': '', 'stderr': '', 'error': None}
    if not args.input.is_file():
        proc['error'] = f'input WAV does not exist: {args.input}'
    elif not command:
        proc['error'] = 'no MorseAngel command configured; pass --command or set MORSEANGEL_COMMAND'
    else:
        try:
            proc = run_command(build_argv(command, args.input), args.timeout)
        except Exception as exc:
            proc['error'] = str(exc)
    return normalize_result(wav_path=args.input, command=command, **proc)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', required=True, type=Path)
    p.add_argument('--command', default='')
    p.add_argument('--timeout', type=float, default=30)
    p.add_argument('--output-json', type=Path)
    p.add_argument('--json', action='store_true')
    p.add_argument('--pretty', action='store_true')
    args = p.parse_args()
    result = run_adapter(args)
    if args.output_json:
        atomic_json(args.output_json, result)
    if args.json:
        print(json.dumps(result, indent=2 if args.pretty else None, allow_nan=False))
    elif result['decoded']:
        print(result['text'])
    return 2 if result['error'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
