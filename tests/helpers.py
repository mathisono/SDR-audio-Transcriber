"""Synthetic fixtures/test double. These are NOT recognition/RF accuracy tests."""
from __future__ import annotations
import math
import random
import struct
import wave
from pathlib import Path
from types import SimpleNamespace

MORSE = dict(zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', '.- -... -.-. -.. . ..-. --. .... .. .--- -.- .-.. -- -. --- .--. --.- .-. ... - ..- ...- .-- -..- -.-- --.. ----- .---- ..--- ...-- ....- ..... -.... --... ---.. ----.'.split()))


def tone(seconds=0.6, rate=8000, frequency=700, amplitude=10000):
    return b''.join(struct.pack('<h', round(amplitude * math.sin(2 * math.pi * frequency * i / rate)))
                    for i in range(round(seconds * rate)))


def wav(path: Path, pcm: bytes | None = None, rate=8000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as out:
        out.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        out.writeframes(pcm if pcm is not None else tone(rate=rate))
    return path


def morse(text='DE KJ6DZB', rate=8000, wpm=18, frequency=700, lead=0.4, noise_db=None):
    dot = 1.2 / wpm
    samples = [0.0] * round(lead * rate)
    for wi, word in enumerate(text.split()):
        if wi:
            samples.extend([0.0] * round(7 * dot * rate))
        for ci, char in enumerate(word):
            if ci:
                samples.extend([0.0] * round(3 * dot * rate))
            for mi, mark in enumerate(MORSE[char]):
                if mi:
                    samples.extend([0.0] * round(dot * rate))
                n = round(dot * rate * (1 if mark == '.' else 3))
                ramp = max(1, min(round(0.005 * rate), n // 3))
                for i in range(n):
                    edge = min(1.0, i / ramp, (n - 1 - i) / ramp)
                    samples.append(0.5 * math.sin(2 * math.pi * frequency * i / rate) * edge)
    samples.extend([0.0] * round(0.5 * rate))
    rng = random.Random(20260923)
    sigma = (0.5 / math.sqrt(2)) / 10 ** (noise_db / 20) if noise_db is not None else 0
    return b''.join(struct.pack('<h', round(32767 * max(-0.99, min(0.99, x + rng.gauss(0, sigma))))) for x in samples)


class FakeModel:
    """Deliberately fake ASR for persistence/fault-injection tests only."""
    calls = 0
    def __init__(self, *args, **kwargs):
        pass
    def transcribe(self, path, **kwargs):
        type(self).calls += 1
        def segments():
            yield SimpleNamespace(text=' Test speech ', start=0.0, end=0.2, avg_logprob=-0.2, no_speech_prob=0.01)
            yield SimpleNamespace(text=' was preserved. ', start=0.2, end=0.6)
        return segments(), SimpleNamespace(language='en', language_probability=1.0)
