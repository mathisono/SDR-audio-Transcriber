#!/usr/bin/env python3
"""Squelch-gated WAV clip writer for SDR audio.

This script reads 16-bit signed little-endian mono PCM from stdin, measures
short-window RMS energy, opens a temporary WAV file when the squelch opens, and
renames the completed WAV into runtime/queue only after the hang timer expires.

The important safety rule is that the transcription worker only sees complete
*.wav files, never partially written files.
"""
from __future__ import annotations

import argparse
import audioop
import json
import os
import re
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

CHUNK_MS = 100


def utc_stamp_for_filename() -> str:
    # Include milliseconds so multiple receivers opening in the same second do
    # not collide. Keep the format sortable by time.
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d_%H%M%S") + f".{int(now.microsecond / 1000):03d}Z"


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_token(value: object) -> str:
    text = str(value).strip().replace(" ", "_")
    text = re.sub(r"[^A-Za-z0-9_.+-]+", "_", text)
    return text.strip("_") or "unknown"


def frequency_label(frequency_hz: int) -> str:
    if frequency_hz >= 1_000_000:
        mhz = frequency_hz / 1_000_000.0
        return f"{mhz:.6f}MHz"
    if frequency_hz >= 1_000:
        khz = frequency_hz / 1_000.0
        return f"{khz:.3f}kHz"
    return f"{frequency_hz}Hz"


def open_wav(path: Path, sample_rate: int) -> wave.Wave_write:
    wf = wave.open(str(path), "wb")
    wf.setnchannels(1)
    wf.setsampwidth(2)  # 16-bit PCM
    wf.setframerate(sample_rate)
    return wf


def write_sidecar(path: Path, metadata: dict) -> None:
    path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record squelch-gated WAV clips from mono 16-bit PCM on stdin."
    )
    parser.add_argument("--queue", default="runtime/queue", help="Completed WAV output directory")
    parser.add_argument("--tmp", default="runtime/tmp", help="Temporary partial-file directory")
    parser.add_argument("--receiver", default="receiver1", help="Receiver ID/name for filenames")
    parser.add_argument("--source", default="unknown", help="Source hostname or SDR label")
    parser.add_argument("--mode", default="wbfm", help="Receiver mode metadata")
    parser.add_argument("--frequency", type=int, default=None, help="Active tuned receiver frequency in Hz")
    parser.add_argument("--frequency-hz", type=int, default=None, help="Active tuned receiver frequency in Hz")
    parser.add_argument("--frequency-mhz", type=float, default=None, help="Active tuned receiver frequency in MHz, for example 441.000")
    parser.add_argument("--sample-rate", type=int, default=48000, help="PCM sample rate in Hz")
    parser.add_argument("--threshold", type=int, default=650, help="RMS threshold that opens squelch")
    parser.add_argument("--hang-ms", type=int, default=1200, help="Audio hang time after RMS drops")
    parser.add_argument("--min-sec", type=float, default=1.0, help="Drop clips shorter than this")
    parser.add_argument("--max-sec", type=float, default=60.0, help="Force-close clips after this long")
    parser.add_argument("--verbose", action="store_true", help="Print periodic RMS levels")
    return parser.parse_args()


def resolve_frequency_hz(args: argparse.Namespace) -> int:
    if args.frequency_mhz is not None:
        return int(round(args.frequency_mhz * 1_000_000))
    if args.frequency_hz is not None:
        return int(args.frequency_hz)
    if args.frequency is not None:
        return int(args.frequency)
    # Preserve old behavior for compatibility, but make startup output explicit.
    return 90700000


def main() -> int:
    args = parse_args()
    active_frequency_hz = resolve_frequency_hz(args)
    active_frequency_label = frequency_label(active_frequency_hz)

    queue_dir = Path(args.queue)
    tmp_dir = Path(args.tmp)
    queue_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    bytes_per_chunk = int(args.sample_rate * (CHUNK_MS / 1000.0) * 2)

    recording = False
    wf: wave.Wave_write | None = None
    tmp_wav: Path | None = None
    tmp_json: Path | None = None
    final_wav: Path | None = None
    final_json: Path | None = None
    started_utc: str | None = None
    last_active: float | None = None
    frames_written = 0
    last_verbose = 0.0

    print(
        "clip_writer: waiting for mono s16le PCM on stdin "
        f"source={args.source} receiver={args.receiver} "
        f"frequency_hz={active_frequency_hz} frequency={active_frequency_label}",
        flush=True,
    )

    while True:
        data = os.read(0, bytes_per_chunk)
        if not data:
            time.sleep(0.05)
            continue
        if len(data) < 2:
            continue

        rms = audioop.rms(data, 2)
        now = time.time()
        active = rms >= args.threshold

        if args.verbose and now - last_verbose > 1.0:
            print(f"clip_writer: rms={rms} active={active} recording={recording}", flush=True)
            last_verbose = now

        if active:
            last_active = now

        if active and not recording:
            stamp = utc_stamp_for_filename()
            started_utc = utc_iso()
            safe_source = safe_token(args.source)
            safe_receiver = safe_token(args.receiver)
            safe_mode = safe_token(args.mode)
            safe_freq = safe_token(active_frequency_label)
            # Filename format is sortable and works when many receivers are running:
            # time__source__receiver__frequency__mode__pid.wav
            base = f"{stamp}__{safe_source}__{safe_receiver}__{safe_freq}__{safe_mode}__pid{os.getpid()}"
            tmp_wav = tmp_dir / f"{base}.wav.part"
            tmp_json = tmp_dir / f"{base}.json.part"
            final_wav = queue_dir / f"{base}.wav"
            final_json = queue_dir / f"{base}.json"

            wf = open_wav(tmp_wav, args.sample_rate)
            recording = True
            frames_written = 0
            print(f"clip_writer: OPEN {tmp_wav} rms={rms}", flush=True)

        if not recording or wf is None:
            continue

        wf.writeframes(data)
        frames_written += len(data) // 2
        duration = frames_written / args.sample_rate

        hang_expired = last_active is not None and ((now - last_active) * 1000.0 >= args.hang_ms)
        max_expired = duration >= args.max_sec

        if not (hang_expired or max_expired):
            continue

        wf.close()
        wf = None
        recording = False

        assert tmp_wav is not None
        assert final_wav is not None
        assert started_utc is not None

        if duration >= args.min_sec:
            metadata = {
                "receiver": args.receiver,
                "source": args.source,
                "frequency_hz": active_frequency_hz,
                "frequency_label": active_frequency_label,
                "mode": args.mode,
                "sample_rate": args.sample_rate,
                "started_utc": started_utc,
                "duration_sec": round(duration, 3),
                "squelch_threshold_rms": args.threshold,
                "hang_time_ms": args.hang_ms,
                "writer_pid": os.getpid(),
            }
            if tmp_json and final_json:
                write_sidecar(tmp_json, metadata)
                tmp_json.rename(final_json)
            tmp_wav.rename(final_wav)
            reason = "max" if max_expired else "hang"
            print(f"clip_writer: CLOSE {final_wav} duration={duration:.2f}s reason={reason}", flush=True)
        else:
            tmp_wav.unlink(missing_ok=True)
            if tmp_json:
                tmp_json.unlink(missing_ok=True)
            print(f"clip_writer: DROP short clip duration={duration:.2f}s", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
