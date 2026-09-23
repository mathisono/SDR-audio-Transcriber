#!/usr/bin/env python3
"""Speech-first worker. Durable raw results precede optional enrichment.

Run enrichment_worker.py separately for queued CW/cleanup requests. One speech
worker owns a runtime; kernel locks make interrupted processing safe to resume.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import signal
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline_store import record_path, refresh_views
from safe_runtime import archive_file, atomic_json, exclusive_lock, fsync_directory, positive_timeout, read_json, run_command, sha256

# Import lazily so capture, CW and offline regression tests need no ASR runtime.
WhisperModel = None
ASR_OPTIONS = {'language': 'en', 'beam_size': 5, 'vad_filter': True}
CLEANUP_MODES = ['plain', 'radio-log', 'conservative']


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def normalize_lmstudio_url(host: str | None, port: int, url: str | None) -> str:
    value = (url or host or '127.0.0.1').strip().rstrip('/')
    if not value.startswith(('http://', 'https://')):
        value = 'http://' + value + ('' if ':' in value else f':{port}')
    return value if value.endswith('/v1') else value + '/v1'


def normalize_mode_label(value: object) -> str:
    mode = str(value or 'unknown_mode').lower().strip()
    if mode in {'fm', 'nfm', 'narrow', 'narrowfm', 'narrowband', 'narrowbandfm'}:
        return 'nfm'
    if mode in {'wbfm', 'wide', 'widefm', 'wideband', 'widebandfm'}:
        return 'wbfm'
    return mode


def mode_allowed(mode: object, allowed: str) -> bool:
    return allowed.strip().lower() == 'all' or normalize_mode_label(mode) in {
        normalize_mode_label(x) for x in allowed.split(',')}


def load_sidecar(wav_path: Path) -> dict[str, Any]:
    path = wav_path.with_suffix('.json')
    if not path.exists():
        return {'metadata_warning': 'capture sidecar is missing'}
    try:
        return read_json(path)
    except (ValueError, OSError) as exc:
        return {'metadata_warning': str(exc)}


def transcribe_file(model: Any, wav_path: Path) -> tuple[str, list[dict[str, Any]], Any]:
    segments, info = model.transcribe(str(wav_path), **ASR_OPTIONS)
    parts, details = [], []
    for segment in segments:  # faster-whisper inference occurs during iteration.
        text = segment.text.strip()
        if text:
            parts.append(text)
        item = {'start': round(float(segment.start), 3), 'end': round(float(segment.end), 3), 'text': text}
        for name in ('avg_logprob', 'no_speech_prob', 'compression_ratio'):
            value = getattr(segment, name, None)
            if isinstance(value, (float, int)) and math.isfinite(value):
                item[name] = value
        details.append(item)
    return ' '.join(parts).strip(), details, info


def runtime_versions() -> dict[str, str]:
    result = {'python': sys.version.split()[0]}
    for name in ('faster-whisper', 'ctranslate2', 'av', 'onnxruntime', 'numpy'):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = 'not installed'
    return result


def call_cleanup_model(text: str, base_url: str, model: str, timeout: float,
                       mode: str = 'radio-log', metadata: dict | None = None,
                       classification: dict | None = None, max_tokens: int = 512) -> str:
    import requests
    if mode not in CLEANUP_MODES:
        raise ValueError('invalid cleanup mode')
    # Do not seed cleaned speech with uncertain CW identities or past labels.
    prompt = ('Conservatively copy-edit this radio transcript. Preserve callsigns, names, '
              'frequencies, numbers and technical wording. Never invent details. Mark uncertainty '
              'as [unclear]. For noise/ASR garbage use [no reliable speech detected]. '
              'Return only the cleaned text. Formatting mode: ' + mode + '\n\n' + text)
    response = requests.post(base_url.rstrip('/') + '/chat/completions', json={
        'model': model, 'temperature': 0.05, 'max_tokens': max_tokens,
        'messages': [{'role': 'system', 'content': 'Copy-edit conservatively; never invent details.'},
                     {'role': 'user', 'content': prompt}]}, timeout=positive_timeout(timeout))
    response.raise_for_status()
    value = response.json()['choices'][0]['message']['content']
    if not isinstance(value, str) or not value.strip():
        raise ValueError('cleanup returned no usable text')
    return value.strip()


def run_clip_classifier(wav_path: Path, cw_external_command: str = '', cw_external_timeout: float = 20) -> dict:
    """Compatibility helper; the speech main loop deliberately never calls it."""
    command = [sys.executable, str(Path(__file__).with_name('clip_classifier.py')), str(wav_path),
               '--cw-internal-timeout', str(cw_external_timeout),
               '--cw-external-timeout', str(cw_external_timeout)]
    if cw_external_command:
        command += ['--cw-external-command', cw_external_command]
    result = run_command(command, positive_timeout(cw_external_timeout) + 5)
    if result['error']:
        return {'enabled': True, 'error': result['error'], 'label_candidates': []}
    try:
        value = json.loads(result['stdout'])
        if not isinstance(value, dict):
            raise ValueError('classifier must return an object')
        return value
    except ValueError as exc:
        return {'enabled': True, 'error': str(exc), 'label_candidates': []}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('queue', 'processing', 'done', 'failed', 'transcripts'):
        p.add_argument('--' + name, default='runtime/' + name)
    p.add_argument('--classification-state', default='runtime/classification_state.json',
                   help='Legacy option accepted; automatic station-label promotion is disabled.')
    p.add_argument('--whisper-model', default='small.en')
    p.add_argument('--model-revision', default=None, help='Optional operator-supplied model artifact revision for provenance')
    p.add_argument('--device', default='cpu')
    p.add_argument('--compute-type', default='int8')
    p.add_argument('--lmstudio-host', default='127.0.0.1')
    p.add_argument('--lmstudio-port', type=int, default=1234)
    p.add_argument('--lmstudio-url')
    p.add_argument('--cleanup-model', default='bingbangboom/Qwen3508B-transcriber-15k-03')
    p.add_argument('--cleanup-mode', choices=CLEANUP_MODES, default='radio-log')
    p.add_argument('--cleanup-max-tokens', type=int, default=512)
    p.add_argument('--cleanup-timeout', type=float, default=120)
    p.add_argument('--enable-cleanup', action='store_true', help='Queue cleanup for the separate enrichment worker')
    p.add_argument('--no-cleanup', action='store_true', help='Compatibility flag; overrides --enable-cleanup')
    p.add_argument('--enable-classifier', action='store_true', help='Queue CW for the separate enrichment worker')
    p.add_argument('--classify-modes', default='nfm')
    p.add_argument('--cw-external-command', default='')
    p.add_argument('--cw-external-timeout', type=float, default=20)
    p.add_argument('--cw-internal-timeout', type=float, default=20)
    p.add_argument('--poll-seconds', type=float, default=2)
    p.add_argument('--once', action='store_true', help='Drain currently available work and exit')
    return p.parse_args()


def enrichment_request(args: argparse.Namespace, mode: object) -> dict[str, Any]:
    classifier = args.enable_classifier and mode_allowed(mode, args.classify_modes)
    cleanup = args.enable_cleanup and not args.no_cleanup
    return {'status': 'pending' if classifier or cleanup else 'disabled',
            'classifier': bool(classifier), 'cleanup': bool(cleanup),
            'cw_external_command': args.cw_external_command,
            'cw_external_timeout': args.cw_external_timeout, 'cw_internal_timeout': args.cw_internal_timeout,
            'cleanup_endpoint': normalize_lmstudio_url(args.lmstudio_host, args.lmstudio_port, args.lmstudio_url),
            'cleanup_model': args.cleanup_model, 'cleanup_mode': args.cleanup_mode,
            'cleanup_timeout': args.cleanup_timeout, 'cleanup_max_tokens': args.cleanup_max_tokens}


def finish_checkpoint(checkpoint: Path, args: argparse.Namespace) -> None:
    record = read_json(checkpoint)
    filename = record['file']
    record_path(Path(args.done), filename)  # Validate before constructing paths.
    proc = Path(args.processing) / filename
    target = Path(record['audio_file'])
    if target.parent.resolve() not in {Path(args.done).resolve(), Path(args.failed).resolve()} or target.name != filename:
        raise ValueError('checkpoint archive path does not match configured runtime')
    if proc.exists():
        archive_file(proc, target)
    elif not target.exists():
        raise FileNotFoundError(f'checkpoint audio missing: {filename}')
    sidecar = proc.with_suffix('.json')
    if sidecar.exists():
        archive_file(sidecar, target.with_suffix('.json'))
    # This ready marker is only visible to enrichment after the audio is archived.
    canonical = record_path(Path(args.done), filename)
    if canonical.exists():
        current = read_json(canonical)
        if any(current.get(k) != record.get(k) for k in ('file', 'raw_text', 'segments', 'audio_sha256')):
            raise ValueError('existing record disagrees with checkpoint; refusing overwrite')
        # Enrichment may have advanced the record after its initial publication.
        checkpoint.unlink()
        fsync_directory(checkpoint.parent)
    else:
        archive_file(checkpoint, canonical)
    refresh_views(Path(args.done), Path(args.transcripts))


def process_clip(wav: Path, model: Any, args: argparse.Namespace) -> None:
    proc = Path(args.processing) / wav.name
    canonical = record_path(Path(args.done), wav.name)
    if canonical.exists():
        previous = read_json(canonical)
        if previous.get('audio_sha256') != sha256(wav):
            raise ValueError('duplicate WAV basename has different content; refusing overwrite')
        target = Path(previous['audio_file'])
        if target.parent.resolve() not in {Path(args.done).resolve(), Path(args.failed).resolve()} or target.name != wav.name:
            raise ValueError('previous archive location is outside configured runtime')
        archive_file(wav, target)
        if wav.with_suffix('.json').exists():
            archive_file(wav.with_suffix('.json'), target.with_suffix('.json'))
        refresh_views(Path(args.done), Path(args.transcripts))
        return
    if wav != proc:
        archive_file(wav, proc)
        sidecar = wav.with_suffix('.json')
        if sidecar.exists():
            archive_file(sidecar, proc.with_suffix('.json'))
    # Recover metadata if a crash happened between the two claim operations.
    queued_metadata = Path(args.queue) / proc.with_suffix('.json').name
    if not proc.with_suffix('.json').exists() and queued_metadata.exists():
        archive_file(queued_metadata, proc.with_suffix('.json'))
    checkpoint = proc.with_suffix('.transcript.json')
    if checkpoint.exists():
        finish_checkpoint(checkpoint, args)
        return
    metadata = load_sidecar(proc)
    start = time.monotonic()
    record = {**metadata, 'file': proc.name, 'created_utc': utc_iso(), 'raw_text': '', 'text': '',
              'segments': [], 'label_candidates': [], 'label': {'label': None, 'confidence': None},
              'classification': {'enabled': False, 'label_candidates': []}}
    try:
        with wave.open(str(proc), 'rb') as wf:
            record['duration_sec'] = wf.getnframes() / wf.getframerate()
            record['audio_format'] = {'sample_rate': wf.getframerate(), 'channels': wf.getnchannels(),
                                      'sample_width': wf.getsampwidth()}
        record['audio_sha256'] = sha256(proc)
        raw, segments, info = transcribe_file(model, proc)
        record.update(raw_text=raw, text=raw, segments=segments, speech_status='complete',
                      language=getattr(info, 'language', None),
                      language_probability=getattr(info, 'language_probability', None),
                      asr={'model': args.whisper_model, 'model_revision': args.model_revision,
                           'device': args.device, 'compute_type': args.compute_type,
                           'options': ASR_OPTIONS, 'versions': runtime_versions(),
                           'elapsed_seconds': round(time.monotonic() - start, 3)},
                      enrichment=enrichment_request(args, metadata.get('mode')))
        destination = Path(args.done)
    except Exception as exc:
        record.update(error=str(exc), speech_status='failed', enrichment={'status': 'disabled'})
        destination = Path(args.failed)
    record['audio_file'] = str((destination / proc.name).resolve())
    # No renderer, classifier, cleanup server, or archive movement precedes this.
    # Disk errors propagate: never erase the WAV or claim a durable success.
    atomic_json(checkpoint, record)
    finish_checkpoint(checkpoint, args)
    print(f"worker: {record['speech_status']} {proc.name}", flush=True)


def main() -> int:
    args = parse_args()
    for timeout in (args.cw_internal_timeout, args.cw_external_timeout, args.cleanup_timeout, args.poll_seconds):
        positive_timeout(timeout)
    for name in ('queue', 'processing', 'done', 'failed', 'transcripts'):
        setattr(args, name, str(Path(getattr(args, name)).resolve()))
        Path(getattr(args, name)).mkdir(parents=True, exist_ok=True)
    devices = {Path(getattr(args, name)).stat().st_dev for name in ('queue', 'processing', 'done', 'failed')}
    if len(devices) != 1:
        raise ValueError('queue, processing, done and failed must share a filesystem')
    if len({args.queue, args.processing, args.done, args.failed}) != 4:
        raise ValueError('queue, processing, done and failed must be distinct directories')
    # Also share an ASR/enrichment namespace lock to prevent mismatched --processing
    # directories from starting two publishers into one archive.
    with exclusive_lock(Path(args.queue) / '.speech.lock'), exclusive_lock(Path(args.done) / '.speech.lock'), exclusive_lock(Path(args.processing) / '.worker.lock'):
        refresh_views(Path(args.done), Path(args.transcripts))
        # Durable checkpoints do not require loading the model to recover.
        for checkpoint in sorted(Path(args.processing).glob('*.transcript.json')):
            finish_checkpoint(checkpoint, args)
        model_class = WhisperModel
        if model_class is None:
            from faster_whisper import WhisperModel as model_class
        print(f'worker: loading {args.whisper_model} ({args.device}/{args.compute_type})', flush=True)
        model = model_class(args.whisper_model, device=args.device, compute_type=args.compute_type)
        if args.enable_classifier or (args.enable_cleanup and not args.no_cleanup):
            print('worker: optional jobs queued; run scripts/enrichment_worker.py with matching --done and --transcripts', flush=True)
        stopped = []
        def stop(signum, frame):
            stopped.append(signum)
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, stop)
        while not stopped:
            wavs = sorted(Path(args.processing).glob('*.wav')) or sorted(Path(args.queue).glob('*.wav'))
            if not wavs:
                if args.once:
                    break
                time.sleep(args.poll_seconds)
                continue
            process_clip(wavs[0], model, args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
