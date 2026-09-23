#!/usr/bin/env python3
"""Optional CW/cleanup sidecar. Pending jobs survive shutdowns and crashes.

Each job reads the same immutable archived WAV; it never claims the speech queue.
Only one sidecar owns a --done directory. Speech capture/ASR runs independently.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from clip_classifier import classify_wav
from pipeline_store import refresh_views, save_record
from safe_runtime import exclusive_lock, positive_timeout, read_json, run_command, sha256
from transcribe_worker import utc_iso


def cleanup(record_path: Path, timeout: float) -> dict:
    result = run_command([sys.executable, str(Path(__file__).with_name('cleanup_adapter.py')), str(record_path)], timeout)
    if result['error']:
        return {'error': result['error'], 'diagnostic': result['stdout'][-4000:]}
    value = json.loads(result['stdout'])
    if not isinstance(value, dict) or not isinstance(value.get('text'), str) or not value['text'].strip():
        raise ValueError('cleanup did not return a text object')
    return value


def enrich_record(path: Path, done: Path, transcripts: Path) -> None:
    record = read_json(path)
    request = record['enrichment']
    audio = Path(record['audio_file'])
    # Reject corrupted/moved inputs, but never overwrite the raw result.
    try:
        if audio.resolve().parent != done.resolve() or audio.name != record['file']:
            raise ValueError('audio is not in this worker archive')
        if sha256(audio) != record['audio_sha256']:
            raise ValueError('archived audio checksum changed')
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {}
            if request.get('classifier'):
                futures['classification'] = pool.submit(classify_wav, audio,
                    external_command=request['cw_external_command'],
                    external_timeout=request['cw_external_timeout'], internal_timeout=request['cw_internal_timeout'])
            if request.get('cleanup') and record.get('raw_text'):
                futures['cleanup'] = pool.submit(cleanup, path, positive_timeout(request['cleanup_timeout']) + 5)
            errors = {}
            for name, future in futures.items():
                try:
                    result = future.result()
                    if not isinstance(result, dict):
                        raise ValueError('enrichment must return an object')
                    if name == 'classification':
                        record['classification'] = result
                        for branch in ('cw_id', 'external_cw_decoder'):
                            if (result.get(branch) or {}).get('error'):
                                errors[branch] = result[branch]['error']
                        if result.get('error'):
                            errors[name] = result['error']
                    elif result.get('error'):
                        errors[name] = result['error']
                        record['cleanup_error'] = result['error']
                    else:
                        record['text'] = result['text']
                        record['cleanup_model'] = request['cleanup_model']
                        record['cleanup_endpoint'] = request['cleanup_endpoint']
                        record['cleanup_mode'] = request['cleanup_mode']
                        record['cleanup_error'] = None
                except Exception as exc:
                    errors[name] = str(exc)
                    if name == 'cleanup':
                        record['cleanup_error'] = str(exc)
            request.update(status='complete_with_errors' if errors else 'complete', errors=errors, completed_utc=utc_iso())
    except Exception as exc:
        request.update(status='complete_with_errors', errors={'job': str(exc)}, completed_utc=utc_iso())
    # No promotion, no raw-text replacement, and no reclassification of cleaned text.
    save_record(done, record)
    refresh_views(done, transcripts)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--done', type=Path, default=Path('runtime/done'))
    p.add_argument('--transcripts', type=Path, default=Path('runtime/transcripts'))
    p.add_argument('--poll-seconds', type=float, default=2)
    p.add_argument('--once', action='store_true', help='Drain currently pending records and exit')
    a = p.parse_args()
    positive_timeout(a.poll_seconds)
    a.done, a.transcripts = a.done.resolve(), a.transcripts.resolve()
    a.done.mkdir(parents=True, exist_ok=True)
    stopped = []
    def stop(signum, frame):
        stopped.append(signum)
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop)
    with exclusive_lock(a.done / '.enrichment.lock'):
        refresh_views(a.done, a.transcripts)
        while not stopped:
            pending = []
            for path in sorted(a.done.glob('*.transcript.json')):
                try:
                    record = read_json(path)
                    if (record.get('enrichment') or {}).get('status') == 'pending':
                        pending.append(path)
                except (ValueError, OSError) as exc:
                    print(f'enrichment: cannot read {path}: {exc}', file=sys.stderr)
            if not pending:
                if a.once:
                    break
                time.sleep(a.poll_seconds)
                continue
            for path in pending:
                if stopped:
                    break
                enrich_record(path, a.done, a.transcripts)
                print(f'enrichment: finished {path.name}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
