#!/usr/bin/env python3
"""Adapter shim for testing an external MorseAngel-style CW decoder.

This script gives the SDR transcription pipeline a stable command target for CW
model experiments while the exact MorseAngel invocation is still being tested.

It accepts a WAV file, runs an external command if configured, prints decoded CW
text to stdout for the existing clip_classifier.py hook, and optionally writes a
normalized JSON sidecar.

Examples:

  # Dry run / wiring test; returns no decoded text but writes JSON if requested.
  python3 scripts/morseangel_adapter.py --input runtime/done/example.wav --pretty

  # Run an external decoder command. Use {wav} where the input path belongs.
  python3 scripts/morseangel_adapter.py \
    --input runtime/done/example.wav \
    --command "some-cw-decoder --input {wav}" \
    --output-json runtime/done/example.cw.json

You can also set MORSEANGEL_COMMAND instead of passing --command.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

CALLSIGN_RE = re.compile(r"\b(?:[AKNW][A-Z]?\d[A-Z]{1,3}|[A-Z]{1,2}\d[A-Z]{1,4})\b", re.IGNORECASE)


def extract_callsigns(text: str) -> list[str]:
    return sorted(set(match.group(0).upper() for match in CALLSIGN_RE.finditer(text or "")))


def build_argv(command: str, wav_path: Path) -> list[str]:
    if "{wav}" in command:
        return shlex.split(command.replace("{wav}", str(wav_path)))
    return shlex.split(command) + [str(wav_path)]


def maybe_parse_json(text: str) -> dict[str, Any] | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def normalize_result(
    *,
    wav_path: Path,
    command: str,
    argv: list[str],
    returncode: int | None,
    stdout: str,
    stderr: str,
    error: str | None,
) -> dict[str, Any]:
    parsed = maybe_parse_json(stdout)
    text = ""
    confidence = None
    wpm = None

    if parsed:
        text = str(parsed.get("text") or parsed.get("decoded_text") or parsed.get("message") or "").strip()
        confidence = parsed.get("confidence")
        wpm = parsed.get("wpm")
    else:
        text = (stdout or "").strip()

    callsigns = extract_callsigns(text)
    decoded = bool(text and not error and (returncode in (0, None)))
    return {
        "engine": "morseangel-adapter",
        "input": str(wav_path),
        "decoded": decoded,
        "text": text,
        "callsigns": callsigns,
        "confidence": confidence,
        "wpm": wpm,
        "command": command,
        "argv": argv,
        "returncode": returncode,
        "stderr": stderr[-4000:] if stderr else "",
        "error": error,
    }


def run_adapter(args: argparse.Namespace) -> dict[str, Any]:
    wav_path = args.input
    command = args.command or os.environ.get("MORSEANGEL_COMMAND", "")

    if not wav_path.exists():
        return normalize_result(
            wav_path=wav_path,
            command=command,
            argv=[],
            returncode=None,
            stdout="",
            stderr="",
            error=f"input WAV does not exist: {wav_path}",
        )

    if not command:
        return normalize_result(
            wav_path=wav_path,
            command="",
            argv=[],
            returncode=None,
            stdout="",
            stderr="",
            error="no MorseAngel command configured; pass --command or set MORSEANGEL_COMMAND",
        )

    argv = build_argv(command, wav_path)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=args.timeout, check=False)
    except Exception as exc:
        return normalize_result(
            wav_path=wav_path,
            command=command,
            argv=argv,
            returncode=None,
            stdout="",
            stderr="",
            error=str(exc),
        )

    return normalize_result(
        wav_path=wav_path,
        command=command,
        argv=argv,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        error=None if proc.returncode == 0 else f"decoder exited {proc.returncode}",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a MorseAngel-style CW decoder and normalize output")
    parser.add_argument("--input", required=True, type=Path, help="Input WAV file")
    parser.add_argument("--command", default="", help="External decoder command. Use {wav} placeholder or WAV path is appended. Can also use MORSEANGEL_COMMAND.")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--output-json", type=Path, default=None, help="Optional normalized JSON output path")
    parser.add_argument("--json", action="store_true", help="Print normalized JSON to stdout instead of decoded text")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON when used with --json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_adapter(args)

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False))
    else:
        # Existing clip_classifier.py reads stdout as decoded CW text and extracts callsigns.
        # Keep stderr/errors out of stdout so no-error/no-decode remains a clean empty result.
        if result.get("decoded") and result.get("text"):
            print(result["text"])

    return 0 if not result.get("error") else 2


if __name__ == "__main__":
    raise SystemExit(main())
