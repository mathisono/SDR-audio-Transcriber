#!/usr/bin/env python3
"""Reprocess past transcript records into a larger formatted transcript/report.

This is an offline/batch pass. It does not modify the raw log. It reads
runtime/transcripts/index.jsonl, groups recent records into a larger transcript,
asks an OpenAI-compatible model server such as LM Studio/Qwen to format it, and
writes:

  runtime/transcripts/formatted_transcript.json
  runtime/transcripts/formatted.html

Example:
  .venv/bin/python3 scripts/reprocess_history.py \
    --lmstudio-host 192.168.3.38 \
    --model qwen3508b-transcriber-15k-03 \
    --format monitoring-report \
    --limit 100 \
    --source-text best
"""
from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

CALLSIGN_RE = re.compile(r"\b(?:[AKNW][A-Z]?\d[A-Z]{1,3}|[A-Z]{1,2}\d[A-Z]{1,4})\b", re.IGNORECASE)

FORMAT_CHOICES = ["transcript", "monitoring-report", "incident-log", "callsign-evidence"]


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


def available_models(base_url: str, timeout: int = 20) -> list[str]:
    response = requests.get(f"{base_url.rstrip('/')}/models", timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    return [str(item.get("id")) for item in payload.get("data", []) if item.get("id")]


def resolve_model(base_url: str, requested_model: str, timeout: int = 20) -> str:
    if requested_model != "auto":
        return requested_model
    models = available_models(base_url, timeout=timeout)
    if not models:
        raise SystemExit(f"No models returned by {base_url}/models")
    preferred = [m for m in models if "transcriber" in m.lower() or "qwen" in m.lower()]
    chosen = preferred[0] if preferred else models[0]
    print(f"model auto-selected: {chosen}")
    return chosen


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


def best_label(record: dict[str, Any]) -> str:
    stable = (record.get("stable_label") or {}).get("label")
    label = (record.get("label") or {}).get("label")
    return str(stable or label or "")


def candidate_summary(record: dict[str, Any]) -> str:
    bits: list[str] = []
    classification = record.get("classification") or {}
    tone = classification.get("tone_id") or {}
    cw = classification.get("cw_id") or {}
    if tone.get("detected") and tone.get("frequency_hz"):
        bits.append(f"tone={tone.get('frequency_hz')}Hz conf={tone.get('confidence')}")
    if cw.get("decoded"):
        bits.append(f"cw={cw.get('text')} conf={cw.get('confidence')}")
    candidates = record.get("label_candidates") or []
    if candidates:
        short = []
        for item in candidates[:4]:
            short.append(f"{item.get('label')}:{item.get('type')}:{item.get('confidence')}")
        bits.append("candidates=" + ", ".join(short))
    return " | ".join(bits)


def record_line(record: dict[str, Any], source_text: str, include_evidence: bool = True) -> str:
    text = choose_text(record, source_text)
    if not text:
        return ""
    timestamp = record.get("started_utc") or record.get("created_utc") or "unknown time"
    receiver = record.get("receiver") or "unknown receiver"
    frequency = record.get("frequency_label") or record.get("frequency_hz") or "unknown frequency"
    label = best_label(record)
    label_part = f" label={label}" if label else " label=[unknown]"
    evidence = candidate_summary(record) if include_evidence else ""
    evidence_part = f" evidence=({evidence})" if evidence else ""
    record_no = record.get("_record_number") or "?"
    filename = record.get("file") or "unknown_file"
    return f"[record #{record_no}] [{timestamp}] receiver={receiver} frequency={frequency}{label_part} file={filename}{evidence_part}: {text}"


def extract_context(records: list[dict[str, Any]]) -> dict[str, Any]:
    callsigns: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    frequencies: Counter[str] = Counter()
    receivers: Counter[str] = Counter()
    tones: Counter[str] = Counter()
    cw_texts: Counter[str] = Counter()
    candidate_sources: Counter[str] = Counter()

    for record in records:
        for text in [record.get("raw_text") or "", record.get("text") or ""]:
            callsigns.update(match.group(0).upper() for match in CALLSIGN_RE.finditer(text))
        label = best_label(record)
        if label:
            labels.update([label])
        freq = record.get("frequency_label") or record.get("frequency_hz")
        if freq:
            frequencies.update([str(freq)])
        if record.get("receiver"):
            receivers.update([str(record.get("receiver"))])
        classification = record.get("classification") or {}
        tone = classification.get("tone_id") or {}
        cw = classification.get("cw_id") or {}
        if tone.get("detected") and tone.get("frequency_hz"):
            tones.update([str(tone.get("frequency_hz")) + "Hz"])
        if cw.get("decoded") and cw.get("text"):
            cw_texts.update([str(cw.get("text"))])
        for candidate in record.get("label_candidates") or []:
            source = candidate.get("source") or "unknown"
            typ = candidate.get("type") or "unknown"
            candidate_sources.update([f"{source}/{typ}"])

    def top(counter: Counter[str], n: int = 12) -> list[dict[str, Any]]:
        return [{"value": value, "count": count} for value, count in counter.most_common(n)]

    return {
        "callsigns_seen": top(callsigns),
        "labels_seen": top(labels),
        "frequencies_seen": top(frequencies),
        "receivers_seen": top(receivers),
        "tone_ids_seen": top(tones),
        "cw_text_seen": top(cw_texts),
        "candidate_sources": top(candidate_sources),
    }


def trim_to_max_chars(lines: list[str], max_chars: int) -> list[str]:
    if max_chars <= 0:
        return lines
    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        line_len = len(line) + 1
        if kept and total + line_len > max_chars:
            break
        kept.append(line)
        total += line_len
    return list(reversed(kept))


def format_instructions(report_format: str, title: str) -> str:
    common_rules = f"""
Global rules:
- Title the output: {title}
- Preserve exact timestamps, record numbers, receiver names, frequencies, filenames, callsigns, numbers, and technical terms.
- Do not invent speakers, callsigns, agencies, locations, or missing words.
- If the speaker/station is not known, write [unknown].
- If the transcript appears to be noise, repeated ASR garbage, or uncertain copy, mark it [low confidence] or [unclear].
- Keep record references visible, for example: record #123 or file name when useful.
- Use clear Markdown headings and compact structure.
""".strip()

    if report_format == "transcript":
        return common_rules + """

Output format:
# Title
## Time Range / Source
Briefly state time range, receivers, frequencies, and number of records.
## Clean Chronological Transcript
Format each transmission as:
- [timestamp] [receiver/frequency] [label or unknown] — cleaned text. (record #N)
## Notes / Uncertain Items
List unclear callsigns, uncertain phrases, and likely noise clips.
"""

    if report_format == "monitoring-report":
        return common_rules + """

Output format:
# Title
## 1. Monitoring Summary
Summarize what was monitored: time range, receivers, frequencies, number of records, and dominant labels/callsigns if any.
## 2. Key Activity
Bullet the most important traffic or repeated patterns. Do not overstate uncertain items.
## 3. Chronological Traffic Log
Group related clips into short Traffic Event blocks when timestamps are close and content appears related.
For each event use:
### Traffic Event: [short neutral description]
- Time span:
- Receiver / Frequency:
- Known or suspected station/label:
- Transcript:
  - [timestamp] [label or unknown] — cleaned text. (record #N)
- Confidence / notes:
## 4. Identification Evidence
Summarize evidence for station/repeater labels: CW decode candidates, spoken callsigns, tone ID frequency, stable labels, repeated candidates.
## 5. Needs Human Review
List low-confidence items, unclear callsigns, odd ASR output, and anything that should be checked against audio.
"""

    if report_format == "incident-log":
        return common_rules + """

Output format:
# Title
## 1. Incident / Activity Summary
Summarize only observable events from the transcript.
## 2. Timeline
Use a concise chronological table-like list:
- [timestamp] [receiver/frequency] [label or unknown] — event/traffic summary. (record #N)
## 3. Direct Transcript Excerpts
Include only the clearest relevant transmission text, preserving record numbers.
## 4. Evidence and Confidence
Separate high-confidence, medium-confidence, and low-confidence observations.
## 5. Follow-up / Review Needed
List items requiring audio review or additional confirmation.
"""

    if report_format == "callsign-evidence":
        return common_rules + """

Output format:
# Title
## 1. Callsign / Label Candidates
For each callsign or label candidate, list:
- Candidate:
- Evidence type: CW, spoken transcript, tone ID, stable state, or repeated candidate
- Supporting records:
- Confidence notes:
## 2. Tone ID Evidence
Summarize tone frequencies and keyed/CW evidence.
## 3. Spoken Callsign Evidence
Summarize spoken callsigns and record references.
## 4. CW Decode Evidence
Summarize decoded CW text and record references.
## 5. Recommended Stable Labels
Recommend labels only when repeated evidence supports them. Otherwise write [do not promote yet].
"""

    raise ValueError(f"unknown format: {report_format}")


def build_prompt(records: list[dict[str, Any]], source_text: str, title: str, max_chars: int, report_format: str) -> tuple[str, int]:
    include_evidence = report_format in {"monitoring-report", "callsign-evidence", "incident-log"}
    lines = [line for record in records if (line := record_line(record, source_text, include_evidence=include_evidence))]
    lines = trim_to_max_chars(lines, max_chars)
    context = extract_context(records)
    body = "\n".join(lines)
    prompt = f"""
You are formatting SDR/radio-monitoring transcripts from many short audio clips.
Your job is to create a structured, reviewable monitoring product from imperfect ASR and classifier data.

{format_instructions(report_format, title)}

Known context extracted from classifier/transcripts:
{json.dumps(context, indent=2)}

Source clip log, chronological order:
{body}
""".strip()
    return prompt, len(lines)


def call_model(prompt: str, base_url: str, model: str, timeout: int, max_tokens: int) -> str:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You format radio monitoring logs conservatively. You never invent details. You preserve timestamps, record numbers, callsigns, labels, and uncertainty.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    url = f"{base_url.rstrip('/')}/chat/completions"
    response = requests.post(url, json=payload, timeout=timeout)
    if response.status_code >= 400:
        body = response.text.strip()
        raise RuntimeError(f"LM Studio request failed: HTTP {response.status_code} {response.reason}\nURL: {url}\nModel: {model}\nResponse body:\n{body[:4000]}")
    return response.json()["choices"][0]["message"]["content"].strip()


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def markdownish_to_html(text: str) -> str:
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
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 1050px; margin: 2rem auto; padding: 0 1rem 4rem; background: #101114; color: #f0f0f0; }}
    a {{ color: #c9d4e5; }}
    .tabs {{ display:flex; gap:.5rem; flex-wrap:wrap; margin: 1rem 0 1.5rem; border-bottom: 1px solid #333842; padding-bottom:.75rem; }}
    .tab {{ display:inline-block; text-decoration:none; color:#e6edf7; background:#252933; border:1px solid #3a4050; border-radius:999px; padding:.55rem .9rem; font-size:.95rem; }}
    .card {{ background:#1b1d22; border:1px solid #333842; border-radius:14px; padding:1rem 1.1rem; margin:1rem 0; box-shadow:0 8px 24px rgba(0,0,0,.18); }}
    .muted {{ color:#a8a8a8; }}
    h1, h2, h3 {{ line-height: 1.2; }}
    h2 {{ border-top: 1px solid #333842; padding-top: 1rem; margin-top: 1.5rem; }}
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
    parser = argparse.ArgumentParser(description="Reprocess transcript history into a larger Qwen-formatted transcript/report")
    parser.add_argument("--transcripts", default="runtime/transcripts")
    parser.add_argument("--limit", type=int, default=200, help="Number of latest records to format. Use 0 for all records.")
    parser.add_argument("--max-chars", type=int, default=24000, help="Maximum source-log characters sent to the model. Use 0 for no cap.")
    parser.add_argument("--source-text", choices=["best", "raw", "processed"], default="best")
    parser.add_argument("--format", choices=FORMAT_CHOICES, default="monitoring-report", help="Output structure to request from Qwen")
    parser.add_argument("--title", default="Formatted Radio Transcript")
    parser.add_argument("--lmstudio-host", default="127.0.0.1")
    parser.add_argument("--lmstudio-port", type=int, default=1234)
    parser.add_argument("--lmstudio-url", default=None)
    parser.add_argument("--model", default="auto", help="Model id or auto. auto selects the first qwen/transcriber-looking loaded model.")
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--list-models", action="store_true", help="List models from LM Studio and exit")
    parser.add_argument("--dry-run", action="store_true", help="Write prompt file only; do not call model")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    transcript_dir = Path(args.transcripts)
    jsonl_path = transcript_dir / "index.jsonl"
    base_url = normalize_lmstudio_url(args.lmstudio_host, args.lmstudio_port, args.lmstudio_url)

    if args.list_models:
        for model in available_models(base_url, timeout=args.timeout):
            print(model)
        return 0

    records = load_records(jsonl_path)
    if args.limit and args.limit > 0:
        records = records[-args.limit:]
    if not records:
        raise SystemExit(f"no records found in {jsonl_path}")

    model = resolve_model(base_url, args.model, timeout=args.timeout)
    generated_utc = utc_iso()
    prompt, prompt_record_lines = build_prompt(records, args.source_text, args.title, args.max_chars, args.format)
    prompt_path = transcript_dir / "formatted_prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    metadata = {
        "generated_utc": generated_utc,
        "report_format": args.format,
        "records_loaded": len(records),
        "records_in_prompt": prompt_record_lines,
        "prompt_chars": len(prompt),
        "max_chars": args.max_chars,
        "first_record_time": records[0].get("started_utc") or records[0].get("created_utc"),
        "last_record_time": records[-1].get("started_utc") or records[-1].get("created_utc"),
        "source_text": args.source_text,
        "model": model,
        "endpoint": base_url,
    }

    if args.dry_run:
        formatted = "Dry run only. Prompt was written to formatted_prompt.txt."
    else:
        formatted = call_model(prompt, base_url, model, args.timeout, args.max_tokens)

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
