"""Durable per-clip records; index/HTML are disposable, recoverable views."""
from __future__ import annotations

import html
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from safe_runtime import atomic_json, atomic_text, exclusive_lock, read_json, run_command


def record_path(done: Path, filename: str) -> Path:
    if Path(filename).name != filename or not filename.endswith('.wav'):
        raise ValueError('record file must be a plain WAV basename')
    return done / (Path(filename).stem + '.transcript.json')


def save_record(done: Path, record: dict[str, Any]) -> None:
    atomic_json(record_path(done, record['file']), record)


def load_all(done: Path, transcripts: Path) -> list[dict[str, Any]]:
    # Preserve older log entries that have no per-clip JSON, including errors.
    records: dict[str, dict[str, Any]] = {}
    index = transcripts / 'index.jsonl'
    if index.exists():
        for i, line in enumerate(index.read_text(encoding='utf-8').splitlines()):
            try:
                record = json.loads(line)
                if isinstance(record, dict):
                    records[str(record.get('file') or f'legacy:{i}')] = record
            except ValueError:
                print(f'store: ignored malformed index line {i + 1}; original backup retained', file=sys.stderr)
    for path in sorted(done.glob('*.transcript.json')):
        try:
            record = read_json(path)
            filename = record['file']
            record_path(done, filename)  # Validate before adding a canonical record.
            records[filename] = record
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f'store: cannot read {path}: {exc}', file=sys.stderr)
    return sorted(records.values(), key=lambda r: (str(r.get('started_utc') or r.get('created_utc') or ''), str(r.get('file', ''))))


def evidence_page(records: list[dict[str, Any]]) -> str:
    esc = lambda value: html.escape(str(value if value is not None else ''))
    cards = []
    for record in reversed(records[-2000:]):
        enrichment = record.get('enrichment') or {}
        classification = record.get('classification') or {}
        internal = classification.get('cw_id') or {}
        external = classification.get('external_cw_decoder') or {}
        cards.append('<article><h2>' + esc(record.get('file')) + '</h2><p>Enrichment: '
                     + esc(enrichment.get('status', 'not requested')) + '</p><p>Raw speech: '
                     + esc(record.get('raw_text')) + '</p><p>Internal CW (unverified): '
                     + esc(internal.get('text')) + '</p><p>External CW (unverified): '
                     + esc(external.get('text')) + '</p><details><summary>Decoder diagnostics</summary><pre>'
                     + esc(json.dumps({'classification': classification, 'enrichment': enrichment}, indent=2))
                     + '</pre></details></article>')
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<meta http-equiv="refresh" content="20"><title>Optional decoder evidence</title>'
            '<style>body{font:16px system-ui;max-width:1000px;margin:2em auto;padding:1em}'
            'article{border-bottom:1px solid;padding:1em 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
            '<h1>Optional decoder evidence</h1><p><a href="raw.html">Raw speech log</a></p>'
            '<p>CW results are experimental evidence, not verified station identifications. '
            'Scores are not accuracy probabilities. No automatic station-label promotion is performed.</p>'
            + ''.join(cards) + '</html>')


def rebuild_views(done: Path, transcripts: Path) -> None:
    transcripts.mkdir(parents=True, exist_ok=True)
    # Both workers use this same lock; no partial JSONL and no stale-view race.
    with exclusive_lock(transcripts / '.views.lock', blocking=True):
        index = transcripts / 'index.jsonl'
        backup = transcripts / 'index.pre-durable.jsonl'
        if index.exists() and not backup.exists():
            atomic_text(backup, index.read_text(encoding='utf-8'))
        records = load_all(done, transcripts)
        text = ''.join(json.dumps(r, ensure_ascii=False, allow_nan=False) + '\n' for r in records)
        atomic_text(index, text)
        atomic_text(transcripts / 'evidence.html', evidence_page(records))
        # Preserve the existing renderer/layout; stage pages before publication.
        with tempfile.TemporaryDirectory(prefix='.render-', dir=str(transcripts)) as tmp:
            staged = Path(tmp)
            (staged / 'index.jsonl').write_text(text, encoding='utf-8')
            result = run_command([sys.executable, str(Path(__file__).with_name('build_transcript_page.py')),
                                  '--transcripts', str(staged)], 15)
            if result['error']:
                raise RuntimeError('HTML render failed: ' + str(result['error']) + ': ' + result['stderr'])
            for path in staged.glob('*.html'):
                atomic_text(transcripts / path.name, path.read_text(encoding='utf-8'))


def refresh_views(done: Path, transcripts: Path) -> None:
    try:
        rebuild_views(done, transcripts)
    except Exception as exc:
        # A broken renderer must not turn successful speech into a failed job.
        print(f'store: view rebuild failed; per-clip records are safe: {exc}', file=sys.stderr, flush=True)
