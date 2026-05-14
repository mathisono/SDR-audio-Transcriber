#!/usr/bin/env python3
"""Reprocess past transcript records into a larger formatted transcript.

This is an offline/batch pass. It does not modify the raw log. It reads
runtime/transcripts/index.jsonl, groups recent records into a larger transcript,
asks an OpenAI-compatible model server such as LM Studio/Qwen to format it, and
writes:

  runtime/transcripts/formatted_transcript.json
  runtime/transcripts/formatted.html

Example:
  .venv/bin/python3 scripts/reprocess_history.py \
    --lmstudio-host 192.168.3.28 \
    --limit 200 \
    --source-text best
"""
from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

CALLSIGN_RE = re.compile(r"\b(?:[AKNW][A-Z]?\d[A-Z]{1,3}|[A-Z]{1,2}\d[A-Z]{1,4})\b", re.IGNORECASE)


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def normalize_lmstudio_url(host: str | None, port: int, url: str | None) -> str:
    if url:
        base = url.strip().rstrip("/")
    else:
        value = (host or "127.0.0.1").strip().rstrip("/")
        if value.startswith("http://") or value.startswith("https://"):
            base = value
        elif ":" in value:
            base = f"http://{value}"
        else:
            base = f"http://{value}:{port}"
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def load_records(jsonl_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not jsonl_path.exists():
        return records
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            record["_record_number"] = line_number
            records.append(record)
    return sorted(records, key=lambda r: (parse_time(r.get("started_utc") or r.get("created_utc")), int(r.get("_record_number") or 0)))


def choose_text(record: dict[str, Any], source_text: str) -> str:
    raw = (record.get("raw_text") or "").strip()
    processed = (record.get("text") or "").strip()
    if source_text == "raw":
        return raw
    if source_text == "processed":
        return processed or raw
    return processed or raw


def record_line(record: dict[str, Any], source_text: str) -> str:
    text = choose_text(record, source_text)
    if not text:
        return ""
    timestamp = record.get("started_utc") or record.get("created_utc") or "unknown time"
    receiver = record.get("receiver") or "unknown receiver"
    frequency = record.get("frequency_label") or record.get("frequency_hz") or "unknown frequency"
    stable_label = ((record.get("stable_label") or {}).get("label") or (record.get("label") or {}).get("label") or "")
    label_part = f" label={stable_label}" if stable_label else ""
    return f"[{timestamp}] receiver={receiver} frequency={frequency}{label_part}: {text}"


def extract_context(records: list[dict[str, Any]]) -> dict[str, Any]:
    callsigns: set[str] = set()
    labels: set[str] = set()
    frequencies: set[str] = set()
    receivers: set[str] = set()
    for record in records:
        for text in [record.get("raw_text") or "", record.get("text") or ""]:
            callsigns.update(match.group(0).upper() for match in CALLSIGN_RE.finditer(text))
        label = ((record.get("stable_label") or {}).get("label") or (record.get("label") or {}).get("label"))
        if label:
            labels.add(str(label))
        freq = record.get("frequency_label") or record.get("frequency_hz")
        if freq:
            frequencies.add(str(freq))
        if record.get("receiver"):
            receivers.add(str(record.get("receiver")))
    return {
        "callsigns_seen": sorted(callsigns),
        "labels_seen": sorted(labels),
        "frequencies_seen": sorted(frequencies),
        "receivers_seen": sorted(receivers),
    }


def build_prompt(records: list[dict[str, Any]], source_text: str, title: str) -> str:
    lines = [line for record in records if (line := record_line(record, source_text))]
    context = extract_context(records)
    body = "\n".join(lines)
    return f"""
You are formatting a radio-monitoring transcript from SDR audio clips.

Task:
- Build a readable chronological transcript titled: {title}
- Preserve timestamps, callsigns, frequencies, names, numbers, and technical terms.
- Do not invent missing words or facts.
- Use [unclear] where the source is not understandable.
- Merge obvious adjacent fragments from the same conversation when safe.
- Keep each speaker/transmission as a separate line when possible.
- Add light punctuation and paragraphing.
- If a callsign or repeater label is repeated, keep it consistent.
- At the end, add a short "Notes / uncertain items" section.

Known context extracted from classifier/transcripts:
{json.dumps(context, indent=2)}

Source clip log:
{body}
""".strip()


def call_model(prompt: str, base_url: str, model: str, timeout: int) -> str:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You format radio transcripts conservatively. You never invent details and you preserve callsigns and timestamps.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    response = requests.post(f"{base_url.rstrip('/')}/chat/completions", json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def markdownish_to_html(text: str) -> str:
    # Simple safe renderer: escape all text, then preserve paragraphs/line breaks.
    escaped = esc(text)
    escaped = re.sub(r"^###\s+(.+)$", r"<h3>\1</h3>", escaped, flags=re.MULTILINE)
    escaped = re.sub(r"^##\s+(.+)$", r"<h2>\1</h2>", escaped, flags=re.MULTILINE)
    escaped = re.sub(r"^#\s+(.+)$", r"<h1>\1</h1>", escaped, flags=re.MULTILINE)
    paragraphs = []
    for block in escaped.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("<h"):
            paragraphs.append(block)
        else:
            paragraphs.append(f"<p>{block.replace(chr(10), '<br>')}</p>")
    return "\n".join(paragraphs)


def write_html(path: Path, title: str, formatted_text: str, metadata: dict[str, Any]) -> None:
    body = markdownish_to_html(formatted_text)
    meta_items = "".join(f"<li><strong>{esc(k)}:</strong> {esc(v)}</li>" for k, v in metadata.items())
    content = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Cache-Control" content="no-store">
  <title>{esc(title)}</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 1000px; margin: 2rem auto; padding: 0 1rem 4rem; background: #101114; color: #f0f0f0; }}
    a {{ color: #c9d4e5; }}
    .tabs {{ display:flex; gap:.5rem; flex-wrap:wrap; margin: 1rem 0 1.5rem; border-bottom: 1px solid #333842; padding-bottom:.75rem; }}
    .tab {{ display:inline-block; text-decoration:none; color:#e6edf7; background:#252933; border:1px solid #3a4050; border-radius:999px; padding:.55rem .9rem; font-size:.95rem; }}
    .card {{ background:#1b1d22; border:1px solid #333842; border-radius:14px; padding:1rem 1.1rem; margin:1rem 0; box-shadow:0 8px 24px rgba(0,0,0,.18); }}
    .muted {{ color:#a8a8a8; }}
    p {{ font-size:1.05rem; line-height:1.55; }}
    li {{ margin:.35rem 0; }}
  </style>
</head>
<body>
  <header>
    <h1>{esc(title)}</h1>
    <nav class="tabs">
      <a class="tab" href="index.html">Dashboard</a>
      <a class="tab" href="raw.html">Raw Whisper Log</a>
      <a class="tab" href="processed.html">Post-Processed Log</a>
      <a class="tab" href="classification.html">Classification / Labels</a>
      <a class="tab" href="formatted.html">Formatted Transcript</a>
    </nav>
  </header>
  <section class="card muted"><ul>{meta_items}</ul></section>
  <main class="card">{body}</main>
</body>
</html>
"""
    path.write_text(content, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reprocess transcript history into a larger Qwen-formatted transcript")
    parser.add_argument("--transcripts", default="runtime/transcripts")
    parser.add_argument("--limit", type=int, default=200, help="Number of latest records to format. Use 0 for all records.")
    parser.add_argument("--source-text", choices=["best", "raw", "processed"], default="best")
    parser.add_argument("--title", default="Formatted Radio Transcript")
    parser.add_argument("--lmstudio-host", default="127.0.0.1")
    parser.add_argument("--lmstudio-port", type=int, default=1234)
    parser.add_argument("--lmstudio-url", default=None)
    parser.add_argument("--model", default="bingbangboom/Qwen3508B-transcriber-15k-03")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--dry-run", action="store_true", help="Write prompt file only; do not call model")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    transcript_dir = Path(args.transcripts)
    jsonl_path = transcript_dir / "index.jsonl"
    records = load_records(jsonl_path)
    if args.limit and args.limit > 0:
        records = records[-args.limit:]
    if not records:
        raise SystemExit(f"no records found in {jsonl_path}")

    base_url = normalize_lmstudio_url(args.lmstudio_host, args.lmstudio_port, args.lmstudio_url)
    generated_utc = utc_iso()
    prompt = build_prompt(records, args.source_text, args.title)
    prompt_path = transcript_dir / "formatted_prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    metadata = {
        "generated_utc": generated_utc,
        "record_count": len(records),
        "first_record_time": records[0].get("started_utc") or records[0].get("created_utc"),
        "last_record_time": records[-1].get("started_utc") or records[-1].get("created_utc"),
        "source_text": args.source_text,
        "model": args.model,
        "endpoint": base_url,
    }

    if args.dry_run:
        formatted = "Dry run only. Prompt was written to formatted_prompt.txt."
    else:
        formatted = call_model(prompt, base_url, args.model, args.timeout)

    output = {
        **metadata,
        "formatted_text": formatted,
        "prompt_file": str(prompt_path),
    }
    json_path = transcript_dir / "formatted_transcript.json"
    html_path = transcript_dir / "formatted.html"
    json_path.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_html(html_path, args.title, formatted, metadata)

    print(f"wrote {json_path}")
    print(f"wrote {html_path}")
    print(f"wrote {prompt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
