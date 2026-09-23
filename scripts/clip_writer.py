#!/usr/bin/env python3
"""Publish complete s16le mono WAVs. EOF/SIGINT/SIGTERM finalize active audio."""
from __future__ import annotations

import argparse
import array
import json
import math
import os
import select
import signal
import sys
import time
import uuid
import wave
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from safe_runtime import atomic_json, archive_file, fsync_directory, read_json

CHUNK_MS = 100


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def safe_token(value: object) -> str:
    import re
    return re.sub(r'[^A-Za-z0-9_.+-]+', '_', str(value)).strip('_') or 'unknown'


def frequency_label(hz: int) -> str:
    return f'{hz / 1_000_000:.6f}MHz' if hz >= 1_000_000 else f'{hz}Hz'


def rms_s16le(data: bytes) -> int:
    samples = array.array('h')
    samples.frombytes(data)
    if sys.byteorder != 'little':
        samples.byteswap()
    return math.isqrt(sum(x * x for x in samples) // len(samples)) if samples else 0


def resolve_frequency_hz(args: argparse.Namespace) -> int:
    if args.frequency_mhz is not None:
        return round(args.frequency_mhz * 1_000_000)
    if args.frequency_hz is not None:
        return args.frequency_hz
    return args.frequency if args.frequency is not None else 90700000


def validate_settings(threshold: int, hang_ms: int, minimum: float, maximum: float) -> None:
    if not 0 <= threshold <= 32768 or hang_ms < 0:
        raise ValueError('threshold must be 0..32768 and hang-ms must be nonnegative')
    if not all(math.isfinite(v) for v in (minimum, maximum)) or not 0 <= minimum <= maximum or maximum <= 0:
        raise ValueError('require 0 <= min-sec <= max-sec and max-sec > 0')


class ClipWriter:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.queue, self.tmp = Path(args.queue), Path(args.tmp)
        for directory in (self.queue, self.tmp):
            directory.mkdir(parents=True, exist_ok=True)
        if self.queue.stat().st_dev != self.tmp.stat().st_dev:
            raise ValueError('queue and tmp must share a filesystem for atomic publication')
        self.rate = args.sample_rate
        if not 1000 <= self.rate <= 384000 or args.pre_roll_ms < 0:
            raise ValueError('sample-rate must be 1000..384000; pre-roll-ms must be nonnegative')
        self.threshold, self.hang = args.threshold, args.hang_ms
        self.minimum, self.maximum = args.min_sec, args.max_sec
        validate_settings(self.threshold, self.hang, self.minimum, self.maximum)
        self.pre_roll = deque(maxlen=max(0, math.ceil(args.pre_roll_ms / CHUNK_MS)))
        self.wf = None
        self.frames = self.silent_frames = 0
        self.pending = bytearray()
        self.control = Path(args.threshold_control) if args.threshold_control else None
        self.control_mtime = None
        if self.control and not self.control.exists():
            atomic_json(self.control, {'threshold': self.threshold, 'hang_ms': self.hang,
                                      'min_sec': self.minimum, 'max_sec': self.maximum})
        self.last_control_check = self.last_verbose = 0.0

    def update_control(self) -> None:
        if not self.control or time.monotonic() - self.last_control_check < 0.5:
            return
        self.last_control_check = time.monotonic()
        try:
            stamp = self.control.stat().st_mtime_ns
            if stamp == self.control_mtime:
                return
            self.control_mtime = stamp
            value = read_json(self.control)
            settings = (int(value.get('threshold', value.get('threshold_rms', self.threshold))),
                        int(value.get('hang_ms', self.hang)), float(value.get('min_sec', self.minimum)),
                        float(value.get('max_sec', self.maximum)))
            validate_settings(*settings)
            self.threshold, self.hang, self.minimum, self.maximum = settings
        except (OSError, ValueError, TypeError) as exc:
            print(f'clip_writer: invalid live settings ignored: {exc}', file=sys.stderr, flush=True)

    def open(self) -> None:
        now = datetime.now(timezone.utc)
        pre_frames = sum(len(x) // 2 for x in self.pre_roll)
        self.started = (now - timedelta(seconds=pre_frames / self.rate)).isoformat().replace('+00:00', 'Z')
        hz = resolve_frequency_hz(self.args)
        name = '__'.join([now.strftime('%Y-%m-%d_%H%M%S.%fZ'), safe_token(self.args.source),
                          safe_token(self.args.receiver), frequency_label(hz), safe_token(self.args.mode),
                          uuid.uuid4().hex[:12]])
        self.partial = self.tmp / (name + '.wav.part')
        self.final = self.queue / (name + '.wav')
        self.wf = wave.open(str(self.partial), 'wb')
        self.wf.setparams((1, 2, self.rate, 0, 'NONE', 'not compressed'))
        self.frames = self.silent_frames = 0
        # Bound pre-roll by the maximum clip size, even with unusual CLI settings.
        capacity = max(0, int(self.maximum * self.rate) - 1) * 2
        pre = b''.join(self.pre_roll)[-capacity:] if capacity else b''
        self.started = (now - timedelta(seconds=len(pre) / (2 * self.rate))).isoformat().replace('+00:00', 'Z')
        if pre:
            self.wf.writeframesraw(pre)
            self.frames += len(pre) // 2
        self.pre_roll.clear()
        print(f'clip_writer: OPEN {self.partial}', flush=True)

    def close(self, reason: str) -> None:
        if self.wf is None:
            return
        self.wf.close()
        self.wf = None
        duration = self.frames / self.rate
        if duration < self.minimum:
            self.partial.unlink()
            print(f'clip_writer: DROP short clip {duration:.3f}s', flush=True)
            return
        with self.partial.open('rb') as stream:
            os.fsync(stream.fileno())
        hz = resolve_frequency_hz(self.args)
        metadata = {'source': self.args.source, 'receiver': self.args.receiver, 'mode': self.args.mode,
                    'frequency_hz': hz, 'frequency_label': frequency_label(hz), 'sample_rate': self.rate,
                    'started_utc': self.started, 'duration_sec': round(duration, 6),
                    'squelch_threshold_rms': self.threshold, 'hang_time_ms': self.hang,
                    'writer_pid': os.getpid(), 'close_reason': reason}
        # Completed metadata FIRST; WAV is the worker's ready marker.
        atomic_json(self.final.with_suffix('.json'), metadata)
        archive_file(self.partial, self.final)
        print(f'clip_writer: CLOSE {self.final} duration={duration:.3f}s reason={reason}', flush=True)

    def process(self, data: bytes) -> None:
        self.update_control()
        rms = rms_s16le(data)
        active = rms >= self.threshold
        if self.args.verbose and time.monotonic() - self.last_verbose >= 1:
            print(f'clip_writer: rms={rms} threshold={self.threshold} active={active}', flush=True)
            self.last_verbose = time.monotonic()
        if self.wf is None:
            if not active:
                self.pre_roll.append(data)
                return
            self.open()
        # Never let a partial OS read or fast replay change hang-time semantics.
        offset = 0
        while offset < len(data):
            if self.wf is None:
                if not active:
                    self.pre_roll.append(data[offset:])
                    break
                self.open()
            remaining = max(1, int(self.maximum * self.rate)) - self.frames
            count = min(len(data) - offset, max(1, remaining) * 2)
            block = data[offset:offset + count]
            self.wf.writeframesraw(block)
            frames = len(block) // 2
            self.frames += frames
            self.silent_frames = 0 if active else self.silent_frames + frames
            offset += count
            if self.frames >= max(1, int(self.maximum * self.rate)):
                self.close('max')
            elif not active and self.silent_frames * 1000 >= self.hang * self.rate:
                self.close('hang')

    def feed(self, data: bytes, final: bool = False) -> None:
        self.pending.extend(data)
        size = max(2, self.rate * CHUNK_MS // 1000 * 2)
        while len(self.pending) >= size:
            block = bytes(self.pending[:size])
            del self.pending[:size]
            self.process(block)
        if final:
            complete = len(self.pending) // 2 * 2
            if complete:
                self.process(bytes(self.pending[:complete]))
            if len(self.pending) % 2:
                print('clip_writer: discarded one incomplete PCM byte at shutdown', file=sys.stderr)
            self.pending.clear()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--queue', default='runtime/queue')
    p.add_argument('--tmp', default='runtime/tmp')
    p.add_argument('--receiver', default='receiver1')
    p.add_argument('--source', default='unknown')
    p.add_argument('--mode', default='wbfm')
    p.add_argument('--frequency', type=int)
    p.add_argument('--frequency-hz', type=int)
    p.add_argument('--frequency-mhz', type=float)
    p.add_argument('--sample-rate', type=int, default=48000)
    p.add_argument('--threshold', type=int, default=650)
    p.add_argument('--threshold-control', default='')
    p.add_argument('--hang-ms', type=int, default=1200)
    p.add_argument('--min-sec', type=float, default=1.0)
    p.add_argument('--max-sec', type=float, default=60.0)
    p.add_argument('--pre-roll-ms', type=int, default=200)
    p.add_argument('--verbose', action='store_true')
    return p.parse_args()


def main() -> int:
    writer = ClipWriter(parse_args())
    stopped = []
    def stop(signum, frame):
        stopped.append(signum)
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop)
    reason = 'eof'
    print('clip_writer: waiting for mono s16le PCM on stdin', flush=True)
    try:
        while not stopped:
            readable, _, _ = select.select([0], [], [], 0.2)
            if not readable:
                continue
            data = os.read(0, 65536)
            if not data:
                break
            writer.feed(data)
        reason = 'signal' if stopped else 'eof'
    finally:
        writer.feed(b'', final=True)
        writer.close(reason)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
