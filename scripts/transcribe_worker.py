#!/usr/bin/env python3
"""Transcribe completed SDR WAV clips and publish transcript records.

The worker treats runtime/queue/*.wav as complete input files. It moves each file
into runtime/processing, runs faster-whisper for speech-to-text, optionally asks
an OpenAI-compatible model server such as LM Studio to clean up the rough
transcript, optionally classifies CW/tone/spoken callsign evidence, writes
JSON/JSONL outputs, rebuilds the simple HTML page, then moves finished audio into
runtime/done.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from faster_whisper import WhisperModel

CALLSIGN_RE = re.compile(r"\b(?:[AKNW][A-Z]?\d[A-Z]{1,3}|[A-Z]{1,2}\d[A-Z]{1,4})\b", re.IGNORECASE)


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_lmstudio_url(host: str | None, port: int, url: str | None) -> str:
    """Return an OpenAI-compatible /v1 base URL for LM Studio."""
    if url:
        base = url.strip().rstrip("/")
    else:
        value = (host or "127.0.0.1").strip().rstrip("/")
        if value.startswith("http://") or value.startswith("https://"):
            base = value
        else:
            if ":" in value:
                base = f"http://{value}"
            else:
                base = f"http://{value}:{port}"

    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def load_sidecar(wav_path: Path) -> dict[str, Any]:
    sidecar = wav_path.with_suffix(".json")
    if not sidecar.exists():
        return {}
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def move_sidecar(src_wav: Path, dst_dir: Path) -> None:
    sidecar = src_wav.with_suffix(".json")
    if sidecar.exists():
        shutil.move(str(sidecar), str(dst_dir / sidecar.name))


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def rebuild_page(transcripts_dir: Path) -> None:
    script_path = Path(__file__).with_name("build_transcript_page.py")
    subprocess.run(
        ["python3", str(script_path), "--transcripts", str(transcripts_dir)],
        check=False,
    )


def call_cleanup_model(text: str, base_url: str, model: str, timeout: int) -> str:
    prompt = f"""
Clean up this radio transcription.

Rules:
- Preserve callsigns, names, frequencies, and technical terms.
- Do not invent missing words.
- If uncertain, mark the word or phrase as [unclear].
- Remove obvious repeated filler caused by radio noise.
- Return only the cleaned transcript.

Raw transcript:
{text}
""".strip()

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You clean up SDR/radio speech-to-text transcripts without inventing details.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }

    response = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def transcribe_file(model: WhisperModel, wav_path: Path) -> tuple[str, list[dict[str, Any]], Any]:
    segments, info = model.transcribe(
        str(wav_path),
        language="en",
        beam_size=5,
        vad_filter=True,
    )

    parts: list[str] = []
    segment_data: list[dict[str, Any]] = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            parts.append(text)
        segment_data.append(
            {
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "text": text,
            }
        )
    return " ".join(parts).strip(), segment_data, info


def spoken_callsign_candidates(*texts: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for text in texts:
        for match in CALLSIGN_RE.finditer(text or ""):
            callsign = match.group(0).upper()
            if callsign in seen:
                continue
            seen.add(callsign)
            candidates.append({
                "type": "spoken_callsign",
                "label": callsign,
                "value": callsign,
                "confidence": 0.55,
                "source": "transcript_regex",
            })
    return candidates


def run_clip_classifier(wav_path: Path) -> dict[str, Any]:
    script_path = Path(__file__).with_name("clip_classifier.py")
    result = subprocess.run(
        ["python3", str(script_path), str(wav_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return {
            "enabled": True,
            "error": result.stderr.strip() or f"classifier exited {result.returncode}",
            "label_candidates": [],
        }
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"enabled": True, "error": str(exc), "label_candidates": []}


def merge_label_candidates(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for candidate in group:
            key = (str(candidate.get("type", "")), str(candidate.get("label", "")))
            if key in seen:
                continue
            seen.add(key)
            merged.append(candidate)
    merged.sort(key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
    return merged


def choose_label(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {"label": None, "confidence": 0.0, "source": None}
    best = candidates[0]
    return {
        "label": best.get("label"),
        "confidence": best.get("confidence", 0.0),
        "source": best.get("source"),
        "type": best.get("type"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch runtime/queue and transcribe completed WAV clips")
    parser.add_argument("--queue", default="runtime/queue")
    parser.add_argument("--processing", default="runtime/processing")
    parser.add_argument("--done", default="runtime/done")
    parser.add_argument("--failed", default="runtime/failed")
    parser.add_argument("--transcripts", default="runtime/transcripts")
    parser.add_argument("--whisper-model", default="small.en")
    parser.add_argument("--device", default="cpu", help="cpu or cuda")
    parser.add_argument("--compute-type", default="int8", help="int8 for CPU, float16 for CUDA")
    parser.add_argument("--lmstudio-host", default="127.0.0.1", help="LM Studio host/IP, for example 192.168.3.28")
    parser.add_argument("--lmstudio-port", type=int, default=1234, help="LM Studio server port")
    parser.add_argument("--lmstudio-url", default=None, help="Full OpenAI-compatible base URL, for example http://192.168.3.28:1234/v1")
    parser.add_argument("--cleanup-model", default="bingbangboom/Qwen3508B-transcriber-15k-03")
    parser.add_argument("--cleanup-timeout", type=int, default=120)
    parser.add_argument("--no-cleanup", action="store_true", help="Skip local LLM cleanup step")
    parser.add_argument("--enable-classifier", action="store_true", help="Enable CW/tone/spoken callsign label candidates")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    queue = Path(args.queue)
    processing = Path(args.processing)
    done = Path(args.done)
    failed = Path(args.failed)
    transcripts = Path(args.transcripts)
    lmstudio_url = normalize_lmstudio_url(args.lmstudio_host, args.lmstudio_port, args.lmstudio_url)

    for directory in [queue, processing, done, failed, transcripts]:
        directory.mkdir(parents=True, exist_ok=True)

    jsonl_path = transcripts / "index.jsonl"

    print(f"worker: loading faster-whisper model {args.whisper_model}", flush=True)
    whisper = WhisperModel(
        args.whisper_model,
        device=args.device,
        compute_type=args.compute_type,
    )

    rebuild_page(transcripts)
    print("worker: watching queue", flush=True)
    if args.no_cleanup:
        print("worker: cleanup disabled", flush=True)
    else:
        print(f"worker: cleanup endpoint {lmstudio_url} model={args.cleanup_model}", flush=True)
    if args.enable_classifier:
        print("worker: classifier enabled", flush=True)

    while True:
        wavs = sorted(queue.glob("*.wav"))
        if not wavs:
            time.sleep(args.poll_seconds)
            continue

        wav = wavs[0]
        proc = processing / wav.name
        sidecar_metadata: dict[str, Any] = {}

        try:
            sidecar_metadata = load_sidecar(wav)
            shutil.move(str(wav), str(proc))
            move_sidecar(wav, processing)

            print(f"worker: transcribing {proc.name}", flush=True)
            raw_text, segments, info = transcribe_file(whisper, proc)

            cleanup_error = None
            if args.no_cleanup or not raw_text:
                clean_text = raw_text
            else:
                try:
                    clean_text = call_cleanup_model(
                        raw_text,
                        lmstudio_url,
                        args.cleanup_model,
                        args.cleanup_timeout,
                    )
                except Exception as exc:  # Keep the transcript even if cleanup fails.
                    cleanup_error = str(exc)
                    clean_text = raw_text

            classification: dict[str, Any] = {"enabled": False, "label_candidates": []}
            if args.enable_classifier:
                classification = run_clip_classifier(proc)

            spoken_candidates = spoken_callsign_candidates(raw_text, clean_text)
            label_candidates = merge_label_candidates(
                classification.get("label_candidates", []),
                spoken_candidates,
            )
            label = choose_label(label_candidates)

            duration = sidecar_metadata.get("duration_sec", getattr(info, "duration", None))
            record: dict[str, Any] = {
                **sidecar_metadata,
                "file": proc.name,
                "created_utc": utc_iso(),
                "duration_sec": duration,
                "language": getattr(info, "language", None),
                "language_probability": getattr(info, "language_probability", None),
                "raw_text": raw_text,
                "text": clean_text,
                "segments": segments,
                "cleanup_model": None if args.no_cleanup else args.cleanup_model,
                "cleanup_endpoint": None if args.no_cleanup else lmstudio_url,
                "cleanup_error": cleanup_error,
                "classification": classification,
                "label_candidates": label_candidates,
                "label": label,
            }

            output_json = done / f"{proc.stem}.transcript.json"
            output_json.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
            append_jsonl(jsonl_path, record)
            rebuild_page(transcripts)

            shutil.move(str(proc), str(done / proc.name))
            move_sidecar(proc, done)
            print(f"worker: done {proc.name}", flush=True)

        except Exception as exc:
            print(f"worker: FAILED {wav.name}: {exc}", flush=True)
            error_record = {
                **sidecar_metadata,
                "file": wav.name,
                "created_utc": utc_iso(),
                "text": "",
                "raw_text": "",
                "error": str(exc),
            }
            append_jsonl(jsonl_path, error_record)
            rebuild_page(transcripts)
            try:
                if proc.exists():
                    shutil.move(str(proc), str(failed / proc.name))
                    move_sidecar(proc, failed)
                elif wav.exists():
                    shutil.move(str(wav), str(failed / wav.name))
                    move_sidecar(wav, failed)
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
