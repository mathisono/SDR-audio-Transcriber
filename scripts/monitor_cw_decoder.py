#!/usr/bin/env python3
"""Monitor SDR transcript JSON files for CW decoder evidence.

This is meant to run in a separate terminal while the GRC transcription stack is
running. It watches runtime/done/*.transcript.json and prints compact CW decoder
status lines, including internal cw_id output and external CW adapter output.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def short(value: object, max_len: int = 160) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def format_candidates(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "-"
    parts: list[str] = []
    for item in candidates[:6]:
        label = item.get("label") or item.get("value") or "?"
        kind = item.get("type") or "?"
        source = item.get("source") or "?"
        confidence = item.get("confidence")
        if isinstance(confidence, (float, int)):
            parts.append(f"{label} ({kind}/{source}/{confidence:.2f})")
        else:
            parts.append(f"{label} ({kind}/{source})")
    return "; ".join(parts)


def summarize_record(path: Path, record: dict[str, Any]) -> str:
    classification = record.get("classification") or {}
    cw_id = classification.get("cw_id") or {}
    external = classification.get("external_cw_decoder") or {}
    tone = classification.get("tone_id") or {}
    label_candidates = record.get("label_candidates") or classification.get("label_candidates") or []

    ts = record.get("created_utc") or datetime.now().isoformat(timespec="seconds")
    source = record.get("source") or "?"
    receiver = record.get("receiver") or "?"
    freq = record.get("frequency_label") or record.get("frequency_hz") or "?"
    mode = record.get("mode") or "?"

    cw_text = short(cw_id.get("text"), 120)
    cw_decoded = cw_id.get("decoded")
    cw_conf = cw_id.get("confidence")
    cw_callsigns = ",".join(cw_id.get("callsigns") or []) or "-"

    ext_enabled = external.get("enabled")
    ext_text = short(external.get("stdout") or external.get("text"), 120)
    ext_callsigns = ",".join(external.get("callsigns") or []) or "-"
    ext_error = short(external.get("error") or external.get("stderr"), 120)
    ext_rc = external.get("returncode")

    tone_desc = "-"
    if tone.get("detected"):
        tone_desc = f"{tone.get('frequency_hz')}Hz conf={tone.get('confidence')}"

    lines = [
        f"[{ts}] {path.name}",
        f"  source={source} receiver={receiver} mode={mode} freq={freq}",
        f"  internal_cw decoded={cw_decoded} conf={cw_conf} callsigns={cw_callsigns} text={cw_text or '-'}",
        f"  external_cw enabled={ext_enabled} rc={ext_rc} callsigns={ext_callsigns} text={ext_text or '-'} error={ext_error or '-'}",
        f"  tone={tone_desc}",
        f"  label_candidates={format_candidates(label_candidates)}",
    ]
    stable = record.get("stable_label")
    if stable:
        lines.append(f"  stable_label={stable}")
    return "\n".join(lines)


def find_records(done_dir: Path) -> list[Path]:
    return sorted(done_dir.glob("*.transcript.json"), key=lambda p: p.stat().st_mtime)


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor CW decoder evidence from transcript JSON files")
    parser.add_argument("--done", default="runtime/done", type=Path)
    parser.add_argument("--poll-seconds", default=2.0, type=float)
    parser.add_argument("--last", default=5, type=int, help="Print the last N existing records at startup")
    parser.add_argument("--all", action="store_true", help="Print all existing records at startup")
    parser.add_argument("--only-cw", action="store_true", help="Only print clips with CW text, CW callsigns, external output, or CW label candidates")
    args = parser.parse_args()

    done_dir = args.done
    done_dir.mkdir(parents=True, exist_ok=True)

    seen: set[Path] = set()
    existing = find_records(done_dir)
    startup = existing if args.all else existing[-max(0, args.last):]

    print(f"cw-monitor: watching {done_dir.resolve()} for *.transcript.json")
    print("cw-monitor: Ctrl-C to stop")

    for path in startup:
        record = load_json(path)
        seen.add(path)
        if record is None:
            continue
        if args.only_cw and not has_cw_evidence(record):
            continue
        print(summarize_record(path, record), flush=True)
        print("-" * 80, flush=True)

    try:
        while True:
            for path in find_records(done_dir):
                if path in seen:
                    continue
                record = load_json(path)
                if record is None:
                    continue
                seen.add(path)
                if args.only_cw and not has_cw_evidence(record):
                    continue
                print(summarize_record(path, record), flush=True)
                print("-" * 80, flush=True)
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print("cw-monitor: stopped")
        return 0


def has_cw_evidence(record: dict[str, Any]) -> bool:
    classification = record.get("classification") or {}
    cw_id = classification.get("cw_id") or {}
    external = classification.get("external_cw_decoder") or {}
    candidates = record.get("label_candidates") or classification.get("label_candidates") or []
    if cw_id.get("text") or cw_id.get("callsigns"):
        return True
    if external.get("stdout") or external.get("text") or external.get("callsigns"):
        return True
    for item in candidates:
        if str(item.get("type") or "").startswith("cw") or item.get("source") in {"cw_audio_decode", "external_cw_decoder"}:
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
