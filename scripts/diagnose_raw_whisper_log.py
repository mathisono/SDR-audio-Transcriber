#!/usr/bin/env python3
"""Diagnose problems visible on the Raw Whisper Log page.

The Raw Whisper Log page is generated from runtime/transcripts/index.jsonl and
uses each record's `raw_text` field. This tool inspects recent transcript
records and points out common failure modes:

- worker errors
- empty raw_text
- empty segment lists
- very short clips
- sample-rate metadata mismatches
- cleanup/classifier data hiding the underlying raw text

It does not read or re-transcribe audio; use scripts/whisper_probe.py for that.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        raise SystemExit(f"missing JSONL log: {path}")
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                records.append({"_line": line_no, "_json_error": str(exc), "raw_line": line[:500]})
                continue
            record["_line"] = line_no
            records.append(record)
    return records


def short(text: object, limit: int = 220) -> str:
    value = str(text or "").replace("\n", " ").strip()
    if len(value) > limit:
        return value[: limit - 3] + "..."
    return value


def issue_list(record: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if record.get("_json_error"):
        issues.append(f"bad JSONL line: {record['_json_error']}")
        return issues

    raw = str(record.get("raw_text") or "").strip()
    text = str(record.get("text") or "").strip()
    segments = record.get("segments") or []
    duration = record.get("duration_sec") or record.get("duration")
    sample_rate = record.get("sample_rate")
    error = record.get("error")
    cleanup_error = record.get("cleanup_error")

    if error:
        issues.append(f"worker error: {short(error, 120)}")
    if cleanup_error:
        issues.append(f"cleanup error: {short(cleanup_error, 120)}")
    if not raw:
        issues.append("raw_text is empty")
    if not segments:
        issues.append("segments list is empty")
    try:
        if duration is not None and float(duration) < 1.0:
            issues.append(f"very short clip: {duration}s")
    except (TypeError, ValueError):
        pass
    if sample_rate not in (8000, 12000, 16000, 24000, 32000, 44100, 48000, None):
        issues.append(f"unusual sample_rate metadata: {sample_rate}")
    if raw and text and raw != text and record.get("cleanup_model"):
        issues.append("processed text differs from raw_text; check processed.html too")

    classification = record.get("classification") or {}
    external = classification.get("external_cw_decoder") or {}
    if external.get("error"):
        issues.append(f"external CW error: {short(external.get('error'), 120)}")

    return issues


def print_record(record: dict[str, Any]) -> None:
    line = record.get("_line", "?")
    created = record.get("created_utc") or "?"
    started = record.get("started_utc") or "?"
    file_name = record.get("file") or "?"
    source = record.get("source") or "?"
    receiver = record.get("receiver") or "?"
    mode = record.get("mode") or "?"
    freq = record.get("frequency_label") or record.get("frequency_hz") or "?"
    duration = record.get("duration_sec") or record.get("duration") or "?"
    sample_rate = record.get("sample_rate") or "?"
    raw = record.get("raw_text") or ""
    segments = record.get("segments") or []
    issues = issue_list(record)

    print(f"line={line} created={created} started={started}")
    print(f"  file={file_name}")
    print(f"  source={source} receiver={receiver} mode={mode} freq={freq} duration={duration}s sample_rate={sample_rate}")
    print(f"  raw_text={short(raw) or '-'}")
    print(f"  segments={len(segments)} language={record.get('language')} prob={record.get('language_probability')}")
    if segments:
        first = segments[0]
        last = segments[-1]
        print(f"  first_segment={short(first.get('text'))} [{first.get('start')}..{first.get('end')}]")
        if len(segments) > 1:
            print(f"  last_segment={short(last.get('text'))} [{last.get('start')}..{last.get('end')}]")
    if issues:
        print("  issues:")
        for issue in issues:
            print(f"    - {issue}")
    else:
        print("  issues: none obvious")


def summarize(records: list[dict[str, Any]]) -> None:
    total = len(records)
    bad_json = sum(1 for r in records if r.get("_json_error"))
    errors = sum(1 for r in records if r.get("error"))
    empty_raw = sum(1 for r in records if not str(r.get("raw_text") or "").strip() and not r.get("_json_error"))
    empty_segments = sum(1 for r in records if not (r.get("segments") or []) and not r.get("_json_error"))
    print("Summary")
    print(f"  records={total}")
    print(f"  bad_json_lines={bad_json}")
    print(f"  worker_errors={errors}")
    print(f"  empty_raw_text={empty_raw}")
    print(f"  empty_segments={empty_segments}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Raw Whisper Log records")
    parser.add_argument("--jsonl", type=Path, default=Path("runtime/transcripts/index.jsonl"))
    parser.add_argument("--last", type=int, default=20)
    parser.add_argument("--only-problems", action="store_true")
    args = parser.parse_args()

    records = load_jsonl(args.jsonl)
    summarize(records)
    print("-" * 80)

    selected = records[-max(0, args.last):]
    for record in reversed(selected):
        if args.only_problems and not issue_list(record):
            continue
        print_record(record)
        print("-" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
