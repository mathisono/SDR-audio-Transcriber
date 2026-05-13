#!/usr/bin/env python3
"""Build a small static HTML page from runtime/transcripts/index.jsonl."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def load_records(jsonl_path: Path, limit: int) -> list[dict]:
    records: list[dict] = []
    if not jsonl_path.exists():
        return records
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    # Keep only the latest records, but preserve chronological order so the
    # newest transcript appears at the bottom like a live radio log.
    return records[-limit:]


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def is_post_processed(record: dict) -> bool:
    raw = (record.get("raw_text") or "").strip()
    text = (record.get("text") or "").strip()
    cleanup_model = record.get("cleanup_model")
    cleanup_error = record.get("cleanup_error")
    return bool(cleanup_model and not cleanup_error and text and text != raw)


def has_classification(record: dict) -> bool:
    classification = record.get("classification") or {}
    return bool(record.get("label_candidates") or classification.get("tone_id") or classification.get("cw_id"))


def render_label_block(record: dict) -> str:
    label = record.get("label") or {}
    label_text = label.get("label")
    label_confidence = label.get("confidence", 0.0)
    label_source = label.get("source") or ""
    candidates = record.get("label_candidates") or []
    classification = record.get("classification") or {}
    tone = classification.get("tone_id") or {}
    cw = classification.get("cw_id") or {}

    parts: list[str] = []
    if label_text:
        parts.append(
            f'<div class="label-best">Best label: <strong>{esc(label_text)}</strong> '
            f'<span class="pill">{esc(round(float(label_confidence), 3))}</span> '
            f'<span class="muted">{esc(label_source)}</span></div>'
        )
    if tone.get("detected"):
        parts.append(
            f'<div class="muted">Tone: {esc(tone.get("frequency_hz"))} Hz, '
            f'confidence {esc(tone.get("confidence"))}, keyed={esc(tone.get("keyed_candidate"))}</div>'
        )
    if cw.get("decoded"):
        parts.append(
            f'<div class="muted">CW decode: <strong>{esc(cw.get("text"))}</strong> '
            f'<span class="pill">{esc(cw.get("confidence"))}</span></div>'
        )
    if candidates:
        rows = []
        for item in candidates[:8]:
            rows.append(
                f'<li><strong>{esc(item.get("label"))}</strong> '
                f'<span class="pill">{esc(item.get("confidence"))}</span> '
                f'<span class="muted">{esc(item.get("type"))} / {esc(item.get("source"))}</span></li>'
            )
        parts.append(f'<ul class="candidates">{"".join(rows)}</ul>')
    if not parts:
        return ""
    return f'<div class="label-block">{"".join(parts)}</div>'


def render_card(record: dict, text_key: str, include_compare: bool = False, include_labels: bool = True) -> str:
    created = esc(record.get("created_utc", ""))
    filename = esc(record.get("file", ""))
    receiver = esc(record.get("receiver", ""))
    frequency = esc(record.get("frequency_hz", ""))
    duration = esc(record.get("duration_sec", record.get("duration", "")))
    raw_text = esc(record.get("raw_text", ""))
    text = esc(record.get(text_key, ""))
    error = esc(record.get("error", ""))
    cleanup_model = esc(record.get("cleanup_model", ""))
    cleanup_endpoint = esc(record.get("cleanup_endpoint", ""))
    cleanup_error = esc(record.get("cleanup_error", ""))

    status_bits: list[str] = []
    if cleanup_model:
        status_bits.append(f"cleanup_model={cleanup_model}")
    if cleanup_endpoint:
        status_bits.append(f"endpoint={cleanup_endpoint}")
    status_line = "<br>" + esc(" ".join(status_bits)) if status_bits else ""

    error_block = f'<p class="error">{error}</p>' if error else ""
    cleanup_error_block = f'<p class="error">cleanup_error={cleanup_error}</p>' if cleanup_error else ""
    label_block = render_label_block(record) if include_labels else ""

    compare_block = ""
    if include_compare and raw_text and raw_text != text:
        compare_block = f"""
        <details>
          <summary>Show raw Whisper transcript</summary>
          <pre>{raw_text}</pre>
        </details>
        """

    return f"""
    <article class="card">
      <div class="meta">
        <strong>{created}</strong><br>
        <span>{filename}</span><br>
        <span>receiver={receiver} frequency={frequency}Hz duration={duration}s</span>{status_line}
      </div>
      {error_block}
      {cleanup_error_block}
      {label_block}
      <p>{text}</p>
      {compare_block}
    </article>
    """


def render_cards(records: list[dict], text_key: str, include_compare: bool = False, include_labels: bool = True) -> str:
    cards = [render_card(record, text_key=text_key, include_compare=include_compare, include_labels=include_labels) for record in records]
    if not cards:
        cards.append('<article class="card"><p>No transcripts yet.</p></article>')
    return "".join(cards)


def build_page(records: list[dict], title: str) -> str:
    processed_records = [record for record in records if is_post_processed(record)]
    classified_records = [record for record in records if has_classification(record)]
    raw_cards = render_cards(records, text_key="raw_text", include_compare=False)
    processed_cards = render_cards(processed_records, text_key="text", include_compare=True)
    classification_cards = render_cards(classified_records, text_key="raw_text", include_compare=False, include_labels=True)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="20">
  <title>{esc(title)}</title>
  <style>
    :root {{ color-scheme: dark; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      max-width: 1100px;
      margin: 2rem auto;
      padding: 0 1rem;
      background: #101114;
      color: #f0f0f0;
    }}
    header {{ margin-bottom: 1.5rem; }}
    h1 {{ margin: 0 0 .25rem; font-size: 1.9rem; }}
    .subtle, .muted {{ color: #a8a8a8; }}
    .tabs {{
      display: flex;
      gap: .5rem;
      flex-wrap: wrap;
      margin: 1rem 0 1.5rem;
      border-bottom: 1px solid #333842;
      padding-bottom: .75rem;
    }}
    .tab {{
      display: inline-block;
      text-decoration: none;
      color: #e6edf7;
      background: #252933;
      border: 1px solid #3a4050;
      border-radius: 999px;
      padding: .55rem .9rem;
      font-size: .95rem;
    }}
    .tab:hover {{ background: #303747; }}
    .section-title {{ margin: 1.5rem 0 .5rem; font-size: 1.35rem; }}
    .card {{
      background: #1b1d22;
      border: 1px solid #333842;
      border-radius: 14px;
      padding: 1rem 1.1rem;
      margin: 1rem 0;
      box-shadow: 0 8px 24px rgba(0, 0, 0, .18);
    }}
    .meta {{ color: #aeb4bf; font-size: .9rem; line-height: 1.4; margin-bottom: .7rem; }}
    .label-block {{ background: #141821; border: 1px solid #2c3444; border-radius: 10px; padding: .7rem .8rem; margin: .75rem 0; }}
    .label-best {{ margin-bottom: .35rem; }}
    .pill {{ display: inline-block; border: 1px solid #4a5366; border-radius: 999px; padding: .08rem .45rem; margin-left: .25rem; font-size: .8rem; color: #d8e1f0; }}
    .candidates {{ margin: .45rem 0 0; padding-left: 1.25rem; }}
    .candidates li {{ margin: .25rem 0; }}
    p {{ font-size: 1.08rem; line-height: 1.5; }}
    pre {{ white-space: pre-wrap; color: #d1d5db; }}
    details {{ margin-top: .75rem; }}
    summary {{ cursor: pointer; color: #c9d4e5; }}
    .error {{ color: #ffb4b4; }}
    .bottom-anchor {{ height: 1px; }}
  </style>
</head>
<body>
  <header>
    <h1>{esc(title)}</h1>
    <div class="subtle">Showing latest {len(records)} raw clips, {len(processed_records)} post-processed clips, and {len(classified_records)} classified clips. Oldest at top and newest at bottom. Auto-refreshes every 20 seconds.</div>
    <nav class="tabs" aria-label="Transcript views">
      <a class="tab" href="#raw-log">Raw Whisper Log ({len(records)})</a>
      <a class="tab" href="#post-processed-log">Post-Processed Log ({len(processed_records)})</a>
      <a class="tab" href="#classification-log">Classification / Labels ({len(classified_records)})</a>
    </nav>
  </header>

  <section id="raw-log">
    <h2 class="section-title">Raw Whisper Log</h2>
    {raw_cards}
  </section>

  <section id="post-processed-log">
    <h2 class="section-title">Post-Processed Log</h2>
    {processed_cards}
  </section>

  <section id="classification-log">
    <h2 class="section-title">Classification / Labels</h2>
    {classification_cards}
  </section>

  <div id="bottom" class="bottom-anchor"></div>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Build transcript index.html from JSONL log")
    parser.add_argument("--transcripts", default="runtime/transcripts")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--title", default="SDR Audio Transcripts")
    args = parser.parse_args()

    transcript_dir = Path(args.transcripts)
    transcript_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = transcript_dir / "index.jsonl"
    html_path = transcript_dir / "index.html"

    records = load_records(jsonl_path, args.limit)
    html_path.write_text(build_page(records, args.title), encoding="utf-8")
    print(f"wrote {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
