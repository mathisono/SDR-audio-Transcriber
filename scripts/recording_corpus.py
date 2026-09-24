#!/usr/bin/env python3
"""Local recording corpus: collect immutable WAVs, review, replay and compare.

This tool never deletes recordings or consumes the live transcription queue.
Collection/scoring use only the standard library. Decoder execution additionally
uses safe_runtime and the decoder entry points in the speech-first branch.
"""
from __future__ import annotations

import argparse
from array import array
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
import wave

SPLITS = ('quarantine', 'train', 'validation', 'test')
ORIGINS = ('rf', 'synthetic', 'unknown')
ENGINES = ('speech', 'cw', 'external-cw')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, split TEXT NOT NULL, origin TEXT NOT NULL, settings TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assets (
 id TEXT PRIMARY KEY, created TEXT NOT NULL, quality TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observations (
 session TEXT NOT NULL REFERENCES sessions(id), name TEXT NOT NULL,
 asset TEXT NOT NULL REFERENCES assets(id), metadata TEXT NOT NULL,
 PRIMARY KEY(session, name, asset));
CREATE TABLE IF NOT EXISTS reviews (
 id TEXT PRIMARY KEY, asset TEXT NOT NULL REFERENCES assets(id),
 created TEXT NOT NULL, reviewer TEXT NOT NULL, reference TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, created TEXT NOT NULL, config TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS results (
 run TEXT NOT NULL REFERENCES runs(id), asset TEXT NOT NULL REFERENCES assets(id),
 review TEXT REFERENCES reviews(id), result TEXT NOT NULL,
 PRIMARY KEY(run, asset));
'''


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def read_object(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('expected a JSON object: ' + str(path))
    encode(value)  # Reject NaN/Infinity even though the Python parser accepts them.
    return value


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def blob_path(root, asset):
    if not re.fullmatch('[0-9a-f]{64}', asset):
        raise ValueError('invalid recording ID')
    return root / 'audio' / (asset + '.wav')


def open_db(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / 'audio').mkdir(exist_ok=True)
    # Prevent accidental commits when an operator chooses a non-default corpus.
    guard = root / '.gitignore'
    try:
        with guard.open('x', encoding='utf-8') as f:
            f.write('*\n')
    except FileExistsError:
        pass
    db = sqlite3.connect(str(root / 'corpus.sqlite3'), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA journal_mode=WAL')
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version not in (0, 1):
        db.close()
        raise ValueError('unsupported corpus schema; do not downgrade this database')
    db.executescript(SCHEMA)
    db.execute('PRAGMA user_version=1')
    return db


def wav_quality(path):
    """Measured PCM diagnostics, NOT RF SNR, intelligibility or model accuracy."""
    total = energy = clipped = zeros = peak = 0
    with wave.open(str(path), 'rb') as w:
        rate, channels, width, frames = (w.getframerate(), w.getnchannels(),
                                       w.getsampwidth(), w.getnframes())
        if rate <= 0 or channels != 1 or width != 2 or frames <= 0:
            raise ValueError('requires nonempty mono 16-bit PCM WAV with a positive rate')
        while True:
            raw = w.readframes(32768)
            if not raw:
                break
            if len(raw) % 2:
                raise ValueError('incomplete PCM sample')
            samples = array('h', raw)
            if sys.byteorder != 'little':
                samples.byteswap()
            total += len(samples)
            for value in samples:
                magnitude = abs(value)
                peak = max(peak, magnitude)
                energy += value * value
                clipped += magnitude >= 32760
                zeros += value == 0
        if total != frames:
            raise ValueError('truncated WAV: sample count does not match header')
    return {'sample_rate': rate, 'channels': channels, 'sample_width': width,
            'frames': frames, 'duration_seconds': frames / rate,
            'rms_dbfs': 10 * math.log10(energy / total / 32768**2) if energy else None,
            'peak_fraction': peak / 32768, 'near_full_scale_fraction': clipped / total,
            'zero_fraction': zeros / total, 'snr_db': None}


def collect_one(root, source, session, split='quarantine', origin='unknown',
                settings=None, settle=2.0):
    """Snapshot one finalized file, never move/hardlink the operator's original.

    Identical audio is stored once, but independent capture observations remain
    separate. An identical blob cannot cross train/validation/test partitions.
    """
    root, source = Path(root).resolve(), Path(source).absolute()
    if not session.strip() or split not in SPLITS or origin not in ORIGINS:
        raise ValueError('session, split or origin is invalid')
    if not math.isfinite(settle) or settle < 0:
        raise ValueError('settle must be finite and nonnegative')
    if source.suffix.lower() != '.wav' or source.is_symlink():
        raise ValueError('only finalized, non-symlink .wav files may be collected')
    before = source.stat()
    if time.time() - before.st_mtime < settle:
        return {'file': source.name, 'status': 'not_settled'}
    settings = {} if settings is None else settings
    if not isinstance(settings, dict):
        raise ValueError('capture settings must be a JSON object')
    encoded_settings = encode(settings)
    metadata = {}
    sidecar = source.with_suffix('.json')
    if sidecar.exists():
        metadata = read_object(sidecar)
    else:
        metadata = {'collection_warning': 'capture metadata unavailable'}
    with closing(open_db(root)) as db:
        fd, tmp_name = tempfile.mkstemp(prefix='.import-', dir=str(root / 'audio'))
        temp = Path(tmp_name)
        try:
            with os.fdopen(fd, 'wb') as dst, source.open('rb') as src:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            after = source.stat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError('source changed during import; retry after finalization')
            quality, asset = wav_quality(temp), file_hash(temp)
            declared = metadata.get('sample_rate')
            if declared is not None and declared != quality['sample_rate']:
                raise ValueError('capture metadata sample rate disagrees with WAV header')
            with db:
                db.execute('BEGIN IMMEDIATE')
                prior = db.execute('SELECT * FROM sessions WHERE id=?', (session,)).fetchone()
                if prior and (prior['split'], prior['origin'], prior['settings']) != (
                        split, origin, encoded_settings):
                    raise ValueError('session settings/split/origin changed; use a new session ID')
                other_splits = {r[0] for r in db.execute(
                    'SELECT DISTINCT s.split FROM observations o JOIN sessions s '
                    'ON s.id=o.session WHERE o.asset=?', (asset,))}
                if other_splits and other_splits != {split}:
                    raise ValueError('identical recording already belongs to a different split')
                target = blob_path(root, asset)
                try:
                    os.link(str(temp), str(target))  # no-overwrite publication
                except FileExistsError:
                    if file_hash(target) != asset:
                        raise ValueError('corpus audio integrity error; refusing overwrite')
                directory = os.open(str(target.parent), os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                db.execute('INSERT OR IGNORE INTO sessions VALUES(?,?,?,?)',
                           (session, split, origin, encoded_settings))
                db.execute('INSERT OR IGNORE INTO assets VALUES(?,?,?)',
                           (asset, utc_now(), encode(quality)))
                cursor = db.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?)',
                                    (session, source.name, asset, encode(metadata)))
                status = 'collected' if cursor.rowcount else 'already_collected'
            return {'file': source.name, 'id': asset, 'status': status, 'quality': quality}
        finally:
            temp.unlink(missing_ok=True)


def validate_reference(value):
    if not isinstance(value, dict) or value.get('provenance') not in ('human', 'synthetic'):
        raise ValueError('reference provenance must be human or synthetic, never a prediction')
    for name in ('speech', 'cw'):
        if name not in value or (value[name] is not None and not isinstance(value[name], str)):
            raise ValueError(name + ' must be text, empty text (absent), or null (unknown)')
    if value['speech'] is None and value['cw'] is None:
        raise ValueError('review at least one decoder target')
    if not isinstance(value.get('protected', False), bool):
        raise ValueError('protected must be boolean')
    for key in ('callsigns', 'required_tokens'):
        entries = value.get(key)  # null means not reviewed, [] means none present.
        if entries is not None and (not isinstance(entries, list) or any(
                not isinstance(x, str) or not x.strip() for x in entries)):
            raise ValueError(key + ' must be null or a list of nonempty strings')
    if value.get('callsigns') and not value['cw']:
        raise ValueError('CW callsigns require reviewed nonempty CW text')
    if value.get('required_tokens') and not value['speech']:
        raise ValueError('speech required_tokens require reviewed nonempty speech')
    encode(value)
    return value


def annotate(root, asset, reviewer, reference):
    blob_path(Path(root), asset)
    if not reviewer.strip():
        raise ValueError('reviewer is required')
    reference = validate_reference(reference)
    review = uuid.uuid4().hex
    with closing(open_db(root)) as db, db:
        if not db.execute('SELECT 1 FROM assets WHERE id=?', (asset,)).fetchone():
            raise ValueError('recording not found')
        db.execute('INSERT INTO reviews VALUES(?,?,?,?,?)',
                   (review, asset, utc_now(), reviewer, encode(reference)))
    return {'review': review, 'asset': asset}


def tokens(text):
    # Fixed/versioned normalization; no phonetic callsign or number guessing.
    return re.findall(r'[a-z0-9]+', text.lower())


def distance(a, b):
    row = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        nxt = [i]
        for j, right in enumerate(b, 1):
            nxt.append(min(nxt[-1] + 1, row[j] + 1, row[j-1] + (left != right)))
        row = nxt
    return row[-1]


def score(reference, text, engine):
    """A missing reference is unscored; empty reference is a negative control."""
    key = 'speech' if engine == 'speech' else 'cw'
    ref = reference.get(key)
    if ref is None:
        return None
    if key == 'cw':
        a, b = (re.findall(r'[A-Z0-9/?]+', x.upper()) for x in (ref, text))
    else:
        a, b = tokens(ref), tokens(text)
    char_a, char_b = ''.join(a), ''.join(b)
    measured = {'normalization': 'cw-alnum-slash-unknown-v1' if key == 'cw' else 'ascii-alnum-v1', 'word_errors': distance(a, b),
                'reference_words': len(a), 'character_errors': distance(char_a, char_b),
                'reference_characters': len(char_a), 'exact': a == b and (bool(a) or not text.strip()),
                'negative_control': not a, 'false_text': bool(not a and text.strip())}
    measured['wer'] = measured['word_errors'] / len(a) if a else None
    measured['cer'] = measured['character_errors'] / len(char_a) if char_a else None
    if key == 'speech' and reference.get('required_tokens') is not None:
        padded = ' ' + ' '.join(b) + ' '
        measured['required_tokens'] = {x: (' ' + ' '.join(tokens(x)) + ' ') in padded
                                       for x in reference['required_tokens']}
    if key == 'cw' and reference.get('callsigns') is not None:
        predicted = set(re.findall(r'\b[A-Z]{1,2}\d[A-Z]{1,4}(?:/[A-Z0-9]+)?\b', text.upper()))
        expected = {c.upper() for c in reference['callsigns']}
        measured['callsigns'] = {'correct': sorted(expected & predicted),
                                'missed': sorted(expected - predicted),
                                'extra': sorted(predicted - expected)}
    return measured


def summarize(results):
    counts = Counter(r['status'] for r in results)
    scored = [r['metrics'] for r in results if r.get('metrics') is not None]
    positive = [m for m in scored if not m['negative_control']]
    negatives = [m for m in scored if m['negative_control']]
    words = sum(m['reference_words'] for m in positive)
    chars = sum(m['reference_characters'] for m in positive)
    return {'attempted': len(results), 'statuses': dict(counts), 'scored': len(scored),
            'positive_clips': len(positive), 'negative_clips': len(negatives),
            'exact_positive_clips': sum(m['exact'] for m in positive),
            'word_error_rate': sum(m['word_errors'] for m in positive) / words if words else None,
            'character_error_rate': sum(m['character_errors'] for m in positive) / chars if chars else None,
            'negative_false_text_clips': sum(m['false_text'] for m in negatives),
            'callsigns_correct': sum(len(m.get('callsigns', {}).get('correct', [])) for m in scored),
            'callsigns_missed': sum(len(m.get('callsigns', {}).get('missed', [])) for m in scored),
            'callsigns_extra': sum(len(m.get('callsigns', {}).get('extra', [])) for m in scored),
            'accuracy_claim': 'measured subset only; blocked/error/unreviewed clips are not passes'}


def provenance(repo, config):
    import importlib.metadata
    packages = {}
    for name in ('faster-whisper', 'ctranslate2', 'av', 'onnxruntime'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    paths = ('recording_corpus.py', 'corpus_decode.py', 'transcribe_worker.py',
             'cw_decode.py', 'safe_runtime.py')
    return {'configuration': config, 'python': sys.version, 'packages': packages,
            'evaluator_sha256': {p: file_hash(Path(__file__).parent / p)
                                 for p in ('recording_corpus.py', 'safe_runtime.py')
                                 if (Path(__file__).parent / p).is_file()},
            'source_sha256': {p: file_hash(repo / 'scripts' / p)
                              for p in paths if (repo / 'scripts' / p).is_file()},
            'model_identity_note': 'model name alone does not pin weights; record a local snapshot for acceptance runs'}


def execute_decoder(repo, wav, config):
    from safe_runtime import run_command, decoded_output, command_argv
    engine = config['engine']
    if engine == 'external-cw':
        if not config.get('command'):
            return {'status': 'blocked', 'error': 'external CW command not configured'}
        argv = command_argv(config['command'], wav)
    else:
        script = repo / 'scripts' / ('corpus_decode.py' if engine == 'speech' else 'cw_decode.py')
        if not script.is_file():
            return {'status': 'blocked', 'error': 'decoder entry point missing: ' + str(script)}
        argv = [sys.executable, str(script), str(wav)]
        if engine == 'speech':
            argv += ['--model', config['model'], '--device', config['device'],
                     '--compute-type', config['compute_type']]
    started = time.monotonic()
    output = run_command(argv, config['timeout'])
    elapsed = time.monotonic() - started
    if engine == 'speech':
        try:
            value = json.loads(output['stdout'])
            if not isinstance(value, dict):
                raise ValueError('expected object')
        except (ValueError, TypeError):
            value = {'status': 'error', 'error': 'invalid speech decoder output'}
        if output['error']:
            # A well-formed blocked result uses a nonzero exit code by design.
            if value.get('status') != 'blocked':
                value = {'status': 'error', 'error': output['error']}
        if value.get('status') == 'measured' and not isinstance(value.get('text'), str):
            value = {'status': 'error', 'error': 'speech decoder text must be a string'}
        if value.get('status') not in ('measured', 'blocked', 'error'):
            value = {'status': 'error', 'error': 'invalid speech decoder status'}
    else:
        # Some adapters express failure with a status field rather than error/decoded.
        try:
            structured = json.loads(output['stdout'])
        except ValueError:
            structured = None
        if isinstance(structured, dict) and structured.get('status') in (
                'error', 'failed', 'failure', 'blocked', 'not_completed'):
            output['error'] = output['error'] or 'external decoder reported ' + structured['status']
        value = decoded_output(output['stdout'], output['error'])
        value['status'] = 'error' if value.get('error') else 'measured'
        value['confidence_note'] = 'heuristic/unvalidated; never an expiration criterion'
    value['wall_seconds'] = elapsed
    value['stderr'] = output['stderr']
    return value


def evaluate(root, repo, config, split='test', origin=None, runner=None):
    """Cold-process benchmark; references are never provided to the decoder."""
    root, repo = Path(root).resolve(), Path(repo).resolve()
    if config.get('engine') not in ENGINES or split not in SPLITS:
        raise ValueError('invalid engine/split')
    if origin is not None and origin not in ORIGINS:
        raise ValueError('invalid origin')
    timeout = config.get('timeout', 120)
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be finite and positive')
    runner = execute_decoder if runner is None else runner
    run = uuid.uuid4().hex
    run_config = provenance(repo, {**config, 'split': split, 'origin': origin})
    with closing(open_db(root)) as db:
        selected = db.execute('SELECT DISTINCT a.id, a.quality FROM assets a '
                              'JOIN observations o ON o.asset=a.id JOIN sessions s '
                              'ON s.id=o.session WHERE s.split=? AND (? IS NULL OR s.origin=?) '
                              'ORDER BY a.id', (split, origin, origin)).fetchall()
        if not selected:
            raise ValueError('no recordings match the requested split/origin')
        # Freeze the whole membership/reference snapshot before the first decoder.
        # On interruption, ALL unfinished recordings remain in the denominator.
        work = []
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO runs VALUES(?,?,?)', (run, utc_now(), encode(run_config)))
            for asset in selected:
                ref = db.execute('SELECT * FROM reviews WHERE asset=? ORDER BY rowid DESC LIMIT 1',
                                 (asset['id'],)).fetchone()
                db.execute('INSERT INTO results VALUES(?,?,?,?)',
                           (run, asset['id'], ref['id'] if ref else None,
                            encode({'status': 'not_completed', 'metrics': None})))
                work.append((asset, ref))
        outcomes = []
        for asset, ref in work:
            try:
                wav = blob_path(root, asset['id'])
                if file_hash(wav) != asset['id']:
                    raise ValueError('audio checksum mismatch')
                result = runner(repo, wav, config)
                if not isinstance(result, dict) or result.get('status') not in ('measured', 'error', 'blocked'):
                    raise ValueError('decoder returned an invalid result')
                if result['status'] == 'measured' and not isinstance(result.get('text'), str):
                    raise ValueError('measured result needs text, including empty text for no decode')
                if file_hash(wav) != asset['id']:
                    raise ValueError('decoder changed the archived recording')
                reference = json.loads(ref['reference']) if ref else {}
                result['metrics'] = score(reference, result['text'], config['engine']) if (
                    result['status'] == 'measured' and ref) else None
                result['reference_provenance'] = reference.get('provenance')
                duration = json.loads(asset['quality'])['duration_seconds']
                result['cold_wall_real_time_factor'] = result.get('wall_seconds', 0) / duration
                encode(result)
            except Exception as exc:
                result = {'status': 'error', 'error': str(exc), 'metrics': None}
            with db:
                db.execute('UPDATE results SET result=? WHERE run=? AND asset=?',
                           (encode(result), run, asset['id']))
            outcomes.append(result)
    return {'run': run, 'summary': summarize(outcomes)}


def report(root, run):
    with closing(open_db(root)) as db:
        header = db.execute('SELECT * FROM runs WHERE id=?', (run,)).fetchone()
        if not header:
            raise ValueError('run not found')
        results = []
        for row in db.execute('SELECT * FROM results WHERE run=? ORDER BY asset', (run,)):
            result = json.loads(row['result'])
            result.update(asset=row['asset'], review=row['review'])
            result['captures'] = [dict(c) for c in db.execute(
                'SELECT o.session, s.split, s.origin, s.settings, o.metadata '
                'FROM observations o JOIN sessions s ON s.id=o.session WHERE o.asset=?',
                (row['asset'],))]
            for capture in result['captures']:
                capture['settings'] = json.loads(capture['settings'])
                capture['metadata'] = json.loads(capture['metadata'])
            results.append(result)
    return {'run': run, 'created': header['created'], 'provenance': json.loads(header['config']),
            'summary': summarize(results), 'results': results}


def compare(root, baseline, candidate):
    a, b = report(root, baseline), report(root, candidate)
    if a['provenance']['configuration']['engine'] != b['provenance']['configuration']['engine']:
        raise ValueError('compare runs of the same engine type')
    old = {r['asset']: r for r in a['results']}
    pairs, excluded = [], []
    for result in b['results']:
        previous = old.pop(result['asset'], None)
        if not previous or previous['review'] != result['review'] or not (
                previous.get('metrics') is not None and result.get('metrics') is not None):
            excluded.append(result['asset'])
            continue
        pairs.append({'asset': result['asset'], 'word_error_delta': result['metrics']['word_errors'] - previous['metrics']['word_errors'],
                      'character_error_delta': result['metrics']['character_errors'] - previous['metrics']['character_errors'],
                      'baseline': previous['metrics'], 'candidate': result['metrics']})
    excluded.extend(old)
    return {'baseline': baseline, 'candidate': candidate, 'paired': len(pairs),
            'excluded_assets': excluded, 'pairs': pairs,
            'note': 'negative error deltas mean fewer errors on the same audio/reference; not proof of RF improvement'}


def inventory(root):
    """List immutable recordings and latest review IDs; never expose a web server."""
    with closing(open_db(root)) as db:
        recordings = []
        for row in db.execute('SELECT * FROM assets ORDER BY created, id'):
            latest = db.execute('SELECT id, reviewer FROM reviews WHERE asset=? '
                                'ORDER BY rowid DESC LIMIT 1', (row['id'],)).fetchone()
            recordings.append({'id': row['id'], 'created': row['created'],
                'quality': json.loads(row['quality']),
                'latest_review': dict(latest) if latest else None,
                'observations': [dict(o) for o in db.execute(
                    'SELECT o.session, o.name, s.split, s.origin FROM observations o '
                    'JOIN sessions s ON o.session=s.id WHERE o.asset=?', (row['id'],))]})
        runs = [dict(r) for r in db.execute('SELECT id, created FROM runs ORDER BY created')]
    return {'recordings': recordings, 'runs': runs}


def file_signature(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def retention_plan(root):
    """Phase-one audit only. There is intentionally no apply/delete option."""
    with closing(open_db(root)) as db:
        decisions = []
        for asset in db.execute('SELECT * FROM assets ORDER BY id'):
            references = [json.loads(r[0]) for r in db.execute(
                'SELECT reference FROM reviews WHERE asset=?', (asset['id'],))]
            results = [json.loads(r[0]) for r in db.execute(
                'SELECT result FROM results WHERE asset=?', (asset['id'],))]
            splits = {r[0] for r in db.execute('SELECT s.split FROM observations o '
                      'JOIN sessions s ON s.id=o.session WHERE o.asset=?', (asset['id'],))}
            reasons = ['retention_not_validated']
            if not references:
                reasons.append('unreviewed')
            if splits & {'test', 'validation'} or any(r.get('protected') for r in references):
                reasons.append('protected_reference_or_holdout')
            if not results:
                reasons.append('not_evaluated')
            if any(r['status'] != 'measured' or (r.get('metrics') is not None and not r['metrics']['exact']) for r in results):
                reasons.append('failure_or_disagreement')
            decisions.append({'asset': asset['id'], 'action': 'keep', 'reasons': reasons})
    return {'mode': 'audit_only', 'deletion_enabled': False, 'metadata_action': 'keep', 'decisions': decisions}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path('runtime/corpus'))
    sub = p.add_subparsers(dest='operation', required=True)
    c = sub.add_parser('collect', help='copy finalized WAVs from done, failed, or an offline directory')
    c.add_argument('directory', type=Path); c.add_argument('--session', required=True)
    c.add_argument('--split', choices=SPLITS, default='quarantine')
    c.add_argument('--origin', choices=ORIGINS, default='unknown')
    c.add_argument('--settings', type=Path); c.add_argument('--settle-seconds', type=float, default=2)
    c.add_argument('--watch', action='store_true'); c.add_argument('--interval', type=float, default=10)
    a = sub.add_parser('annotate', help='append a reviewed reference; never overwrite historical reviews')
    a.add_argument('asset'); a.add_argument('--reviewer', required=True); a.add_argument('--reference', type=Path, required=True)
    e = sub.add_parser('run', help='execute a decoder on preserved audio without passing reference text')
    e.add_argument('--engine', choices=ENGINES, required=True)
    e.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    e.add_argument('--split', choices=SPLITS, default='test'); e.add_argument('--origin', choices=ORIGINS)
    e.add_argument('--model', default='small.en'); e.add_argument('--device', default='cpu')
    e.add_argument('--compute-type', default='int8'); e.add_argument('--timeout', type=float, default=120)
    e.add_argument('--command', default='', help='trusted external CW command with {wav}; no shell')
    r = sub.add_parser('report'); r.add_argument('run')
    d = sub.add_parser('compare'); d.add_argument('baseline'); d.add_argument('candidate')
    sub.add_parser('inventory', help='list recordings, capture sessions, reviews and run IDs')
    sub.add_parser('retention-plan', help='audit what must be kept; cannot delete anything')
    args = p.parse_args(argv)
    try:
        if args.operation == 'collect':
            if not args.directory.is_dir():
                raise ValueError('source directory does not exist')
            if args.directory.resolve().name in ('queue', 'processing', 'tmp'):
                raise ValueError('collect from finalized archives, not the live queue/processing/tmp')
            if not math.isfinite(args.interval) or args.interval <= 0:
                raise ValueError('interval must be finite and positive')
            if not math.isfinite(args.settle_seconds) or args.settle_seconds < 0:
                raise ValueError('settle must be finite and nonnegative')
            settings = read_object(args.settings) if args.settings else {}
            seen = {}  # Watch-mode optimization only; restart always revalidates files.
            while True:
                errors = 0
                for source in sorted(args.directory.glob('*.wav')):
                    try:
                        sidecar = source.with_suffix('.json')
                        signature = (file_signature(source),
                                     file_signature(sidecar) if sidecar.exists() else None)
                        if args.watch and seen.get(source) == signature:
                            continue
                        result = collect_one(args.root, source, args.session, args.split,
                                             args.origin, settings, args.settle_seconds)
                        if result['status'] in ('collected', 'already_collected'):
                            seen[source] = signature
                    except Exception as exc:
                        errors += 1
                        result = {'file': source.name, 'status': 'error', 'error': str(exc)}
                    print(encode(result), flush=True)
                if not args.watch:
                    return 1 if errors else 0
                time.sleep(args.interval)
        elif args.operation == 'annotate':
            result = annotate(args.root, args.asset, args.reviewer, read_object(args.reference))
        elif args.operation == 'run':
            config = {'engine': args.engine, 'model': args.model, 'device': args.device,
                      'compute_type': args.compute_type, 'timeout': args.timeout, 'command': args.command}
            result = evaluate(args.root, args.repo, config, args.split, args.origin)
        elif args.operation == 'report':
            result = report(args.root, args.run)
        elif args.operation == 'compare':
            result = compare(args.root, args.baseline, args.candidate)
        elif args.operation == 'inventory':
            result = inventory(args.root)
        else:
            result = retention_plan(args.root)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        if args.operation == 'run' and any(k != 'measured' and v for k, v in result['summary']['statuses'].items()):
            return 2
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print('corpus: ' + str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
