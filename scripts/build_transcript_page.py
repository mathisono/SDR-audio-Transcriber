#!/usr/bin/env python3
"""Build static HTML transcript pages from runtime/transcripts/index.jsonl."""
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
    return bool(record.get("label_candidates") or classification.get("tone_id") or classification.get("cw_id") or record.get("stable_label"))


def render_label_block(record: dict) -> str:
    label = record.get("label") or {}
    stable_label = record.get("stable_label") or {}
    label_text = stable_label.get("label") or label.get("label")
    label_confidence = stable_label.get("stable_confidence", label.get("confidence", 0.0))
    label_source = label.get("source") or "stable_state"
    candidates = record.get("label_candidates") or []
    classification = record.get("classification") or {}
    tone = classification.get("tone_id") or {}
    cw = classification.get("cw_id") or {}
    state = record.get("classification_state") or {}
    promoted = state.get("promoted_label") or {}

    parts: list[str] = []
    if label_text:
        parts.append(
            f'<div class="label-best">Best label: <strong>{esc(label_text)}</strong> '
            f'<span class="pill">{esc(round(float(label_confidence), 3))}</span> '
            f'<span class="muted">{esc(label_source)}</span></div>'
        )
    if promoted.get("label"):
        parts.append(
            f'<div class="muted">Stable label: <strong>{esc(promoted.get("label"))}</strong> '
            f'<span class="pill">{esc(promoted.get("stable_confidence"))}</span> '
            f'count={esc(promoted.get("count"))}</div>'
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
    frequency_label = esc(record.get("frequency_label", ""))
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
    freq_display = frequency_label or f"{frequency}Hz"

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
        <span>receiver={receiver} frequency={freq_display} duration={duration}s</span>{status_line}
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


def stylesheet() -> str:
    return """
    :root { color-scheme: dark; }
    html { scroll-behavior: smooth; }
    body {
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      max-width: 1100px;
      margin: 2rem auto;
      padding: 0 1rem;
      background: #101114;
      color: #f0f0f0;
    }
    header { margin-bottom: 1.5rem; }
    h1 { margin: 0 0 .25rem; font-size: 1.9rem; }
    .subtle, .muted { color: #a8a8a8; }
    .tabs {
      display: flex;
      gap: .5rem;
      flex-wrap: wrap;
      margin: 1rem 0 1.5rem;
      border-bottom: 1px solid #333842;
      padding-bottom: .75rem;
    }
    .tab {
      display: inline-block;
      text-decoration: none;
      color: #e6edf7;
      background: #252933;
      border: 1px solid #3a4050;
      border-radius: 999px;
      padding: .55rem .9rem;
      font-size: .95rem;
    }
    .tab:hover, .tab.active { background: #303747; border-color: #6d7891; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 1rem; }
    .summary-card {
      background: #1b1d22;
      border: 1px solid #333842;
      border-radius: 14px;
      padding: 1rem 1.1rem;
      text-decoration: none;
      color: #f0f0f0;
      box-shadow: 0 8px 24px rgba(0, 0, 0, .18);
    }
    .summary-card:hover { border-color: #6d7891; background: #20242d; }
    .summary-card strong { display: block; font-size: 1.25rem; margin-bottom: .35rem; }
    .section-title { margin: 1.5rem 0 .5rem; font-size: 1.35rem; }
    .card {
      background: #1b1d22;
      border: 1px solid #333842;
      border-radius: 14px;
      padding: 1rem 1.1rem;
      margin: 1rem 0;
      box-shadow: 0 8px 24px rgba(0, 0, 0, .18);
    }
    .meta { color: #aeb4bf; font-size: .9rem; line-height: 1.4; margin-bottom: .7rem; }
    .label-block { background: #141821; border: 1px solid #2c3444; border-radius: 10px; padding: .7rem .8rem; margin: .75rem 0; }
    .label-best { margin-bottom: .35rem; }
    .pill { display: inline-block; border: 1px solid #4a5366; border-radius: 999px; padding: .08rem .45rem; margin-left: .25rem; font-size: .8rem; color: #d8e1f0; }
    .candidates { margin: .45rem 0 0; padding-left: 1.25rem; }
    .candidates li { margin: .25rem 0; }
    p { font-size: 1.08rem; line-height: 1.5; }
    pre { white-space: pre-wrap; color: #d1d5db; }
    details { margin-top: .75rem; }
    summary { cursor: pointer; color: #c9d4e5; }
    .error { color: #ffb4b4; }
    """


def nav(active: str, counts: dict[str, int]) -> str:
    items = [
        ("index.html", "Dashboard", "dashboard", ""),
        ("raw.html", "Raw Whisper Log", "raw", counts.get("raw", 0)),
        ("processed.html", "Post-Processed Log", "processed", counts.get("processed", 0)),
        ("classification.html", "Classification / Labels", "classification", counts.get("classification", 0)),
    ]
    links = []
    for href, label, key, count in items:
        text = f"{label} ({count})" if count != "" else label
        cls = "tab active" if key == active else "tab"
        links.append(f'<a class="{cls}" href="{href}">{esc(text)}</a>')
    return f'<nav class="tabs" aria-label="Transcript views">{"".join(links)}</nav>'


def page_shell(title: str, subtitle: str, active: str, counts: dict[str, int], body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="20">
  <title>{esc(title)}</title>
  <style>{stylesheet()}</style>
</head>
<body>
  <header>
    <h1>{esc(title)}</h1>
    <div class="subtle">{esc(subtitle)} Auto-refreshes every 20 seconds.</div>
    {nav(active, counts)}
  </header>
  {body}
</body>
</html>
"""


def build_pages(records: list[dict], title: str) -> dict[str, str]:
    processed_records = [record for record in records if is_post_processed(record)]
    classified_records = [record for record in records if has_classification(record)]
    counts = {
        "raw": len(records),
        "processed": len(processed_records),
        "classification": len(classified_records),
    }

    dashboard_body = f"""
    <section class="grid">
      <a class="summary-card" href="raw.html"><strong>Raw Whisper Log</strong><span class="subtle">{len(records)} clips. Direct ASR output.</span></a>
      <a class="summary-card" href="processed.html"><strong>Post-Processed Log</strong><span class="subtle">{len(processed_records)} clips. Qwen/LM Studio cleanup output.</span></a>
      <a class="summary-card" href="classification.html"><strong>Classification / Labels</strong><span class="subtle">{len(classified_records)} clips. CW, tone ID, spoken callsign, and stable label evidence.</span></a>
    </section>
    <section>
      <h2 class="section-title">Status</h2>
      <article class="card">
        <p>Newest records appear at the bottom of each log page. Use the pages above instead of scrolling through one combined view.</p>
      </article>
    </section>
    """

    raw_body = f"""
    <section>
      <h2 class="section-title">Raw Whisper Log</h2>
      {render_cards(records, text_key="raw_text", include_compare=False, include_labels=True)}
    </section>
    """

    processed_body = f"""
    <section>
      <h2 class="section-title">Post-Processed Log</h2>
      {render_cards(processed_records, text_key="text", include_compare=True, include_labels=True)}
    </section>
    """

    classification_body = f"""
    <section>
      <h2 class="section-title">Classification / Labels</h2>
      {render_cards(classified_records, text_key="raw_text", include_compare=False, include_labels=True)}
    </section>
    """

    return {
        "index.html": page_shell(title, "Dashboard for SDR audio transcription views.", "dashboard", counts, dashboard_body),
        "raw.html": page_shell("Raw Whisper Log", f"Showing latest {len(records)} raw clips, oldest at top and newest at bottom.", "raw", counts, raw_body),
        "processed.html": page_shell("Post-Processed Log", f"Showing latest {len(processed_records)} Qwen/LM Studio processed clips, oldest at top and newest at bottom.", "processed", counts, processed_body),
        "classification.html": page_shell("Classification / Labels", f"Showing latest {len(classified_records)} classified clips, oldest at top and newest at bottom.", "classification", counts, classification_body),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build transcript HTML pages from JSONL log")
    parser.add_argument("--transcripts", default="runtime/transcripts")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--title", default="SDR Audio Transcripts")
    args = parser.parse_args()

    transcript_dir = Path(args.transcripts)
    transcript_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = transcript_dir / "index.jsonl"

    records = load_records(jsonl_path, args.limit)
    pages = build_pages(records, args.title)
    for filename, content in pages.items():
        path = transcript_dir / filename
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
