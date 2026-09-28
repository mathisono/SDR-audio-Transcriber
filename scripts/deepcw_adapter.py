#!/usr/bin/env python3
"""Offline adapter for the upstream DeepCW ONNX model and WAV example.

The upstream model accepts 5-20 second, 3.2 kHz windows.  This adapter measures
the dominant tone, creates explicit derived PCM WAV windows, records their
checksums/time mapping, and invokes the unmodified upstream example.  Originals
are opened read-only and checked again after inference.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import tempfile
import time
import wave

from cw_decode import estimate_tone, read_wav_mono
from safe_runtime import run_command, sha256


def load_metadata(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {"sample_rate", "spectrogram_min_freq_hz", "spectrogram_max_freq_hz",
                "onnx_input_name", "onnx_output_name", "onnx_input_layout"}
    if not isinstance(value, dict) or not required.issubset(value):
        raise ValueError("DeepCW metadata is incomplete")
    if value["onnx_input_layout"] != ["batch", "channel", "time", "frequency"]:
        raise ValueError("unsupported DeepCW input layout")
    return value


def resample_linear(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    import numpy as np
    if source_rate == target_rate:
        return audio.astype(np.float32, copy=False)
    length = int(round(len(audio) * target_rate / source_rate))
    positions = np.arange(length, dtype=np.float64) * source_rate / target_rate
    left = np.floor(positions).astype(np.int64)
    right = np.minimum(left + 1, len(audio) - 1)
    fraction = (positions - left).astype(np.float32)
    return (audio[left] * (1.0 - fraction) + audio[right] * fraction).astype(np.float32)


def translate_frequency(audio: np.ndarray, sample_rate: int, shift_hz: float) -> np.ndarray:
    """SSB-style frequency translation using an FFT analytic signal.

    Multiplication by a complex oscillator changes pitch without changing the
    sample count or Morse timing.  This is applied only to a derived copy.
    """
    import numpy as np
    if not shift_hz or not len(audio):
        return audio.astype(np.float32, copy=False)
    spectrum = np.fft.fft(audio)
    multiplier = np.zeros(len(audio), dtype=np.float64)
    multiplier[0] = 1.0
    if len(audio) % 2 == 0:
        multiplier[1:len(audio) // 2] = 2.0
        multiplier[len(audio) // 2] = 1.0
    else:
        multiplier[1:(len(audio) + 1) // 2] = 2.0
    analytic = np.fft.ifft(spectrum * multiplier)
    oscillator = np.exp(2j * np.pi * shift_hz * np.arange(len(audio)) / sample_rate)
    shifted = np.real(analytic * oscillator)
    peak = np.max(np.abs(shifted)) if len(shifted) else 0.0
    if peak > 1.0:
        shifted /= peak
    return shifted.astype(np.float32)


def windows(duration: float, maximum: float = 20.0, overlap: float = 4.0,
            minimum: float = 5.0) -> list[tuple[float, float]]:
    if duration < minimum:
        return []
    if duration <= maximum:
        return [(0.0, duration)]
    starts = []
    start = 0.0
    while start <= max(0.0, duration - maximum) + 0.001:
        starts.append(start)
        start += maximum - overlap
    last = duration - maximum
    if not starts or last - starts[-1] > 0.01:
        starts.append(last)
    return [(round(start, 6), round(min(duration, start + maximum), 6))
            for start in starts]


def write_pcm16(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    import numpy as np
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(audio, -1.0, 0.9999695)
    raw = np.round(data * 32768.0).astype("<i2").tobytes()
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    os.close(fd)
    try:
        with wave.open(temporary, "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(sample_rate)
            wav.writeframes(raw)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--decoder-script", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--upstream-revision", required=True)
    parser.add_argument("--derived-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    result = {"status": "blocked", "text": "", "decoded": False,
              "real_inference_run": False, "engine": "deepcw-onnx"}
    code = 2
    try:
        import numpy as np
        for path, label in ((args.wav, "input WAV"), (args.python, "Python runtime"),
                            (args.decoder_script, "upstream decoder script"),
                            (args.model, "DeepCW checkpoint"), (args.metadata, "DeepCW metadata")):
            if not path.is_file():
                raise FileNotFoundError(f"{label} unavailable")
        metadata = load_metadata(args.metadata)
        source_hash = sha256(args.wav)
        source_rate, samples_list = read_wav_mono(args.wav)
        samples = np.asarray(samples_list, dtype=np.float32)
        duration = len(samples) / source_rate
        tone = estimate_tone(source_rate, samples_list, 100, min(2000, source_rate // 2 - 1))
        low = float(metadata["spectrogram_min_freq_hz"])
        high = float(metadata["spectrogram_max_freq_hz"])
        shift = 0.0
        measured = tone.get("frequency_hz") if tone.get("detected") else None
        if isinstance(measured, (int, float)) and not low <= measured <= high:
            target = low + 100.0 if measured < low else high - 100.0
            shift = target - measured
            samples = translate_frequency(samples, source_rate, shift)
        target_rate = int(metadata["sample_rate"])
        samples = resample_linear(samples, source_rate, target_rate)
        intervals = windows(duration)
        if not intervals:
            raise ValueError("DeepCW requires at least five seconds of audio")
        args.derived_dir.mkdir(parents=True, exist_ok=True)
        outputs, derived = [], []
        started = time.monotonic()
        for index, (start, end) in enumerate(intervals):
            lo_i, hi_i = round(start * target_rate), round(end * target_rate)
            path = args.derived_dir / f"{source_hash}.deepcw-{index:03d}.wav"
            write_pcm16(path, samples[lo_i:hi_i], target_rate)
            checksum = sha256(path)
            proc = run_command([str(args.python), str(args.decoder_script),
                                "--model", str(args.model), "--metadata", str(args.metadata),
                                "--wav", str(path)], args.timeout)
            item = {"index": index, "source_start_seconds": start,
                    "source_end_seconds": end, "sample_rate": target_rate,
                    "sha256": checksum, "path": str(path),
                    "text": proc["stdout"] if not proc["error"] else "",
                    "stderr": proc["stderr"], "error": proc["error"]}
            derived.append({k: item[k] for k in ("index", "source_start_seconds",
                            "source_end_seconds", "sample_rate", "sha256", "path")})
            outputs.append(item)
        errors = [item for item in outputs if item["error"]]
        elapsed = time.monotonic() - started
        if errors:
            result.update(status="error", error="one or more DeepCW windows failed",
                          windows=outputs, wall_seconds=elapsed)
        else:
            text = " ".join(item["text"].strip() for item in outputs if item["text"].strip())
            result.update(status="completed" if text else "no_decode", text=text,
                          decoded=bool(text), windows=outputs, derived_audio=derived,
                          real_inference_run=True, wall_seconds=elapsed,
                          input_sha256=source_hash, model_sha256=sha256(args.model),
                          metadata_sha256=sha256(args.metadata),
                          upstream_revision=args.upstream_revision, tone_measurement=tone,
                          preprocessing={"source_sample_rate": source_rate,
                              "target_sample_rate": target_rate,
                              "frequency_translation_hz": round(shift, 3),
                              "timing_preserved_by_translation": True,
                              "window_seconds": 20.0, "overlap_seconds": 4.0,
                              "supported_spectrogram_tone_hz": [low, high]})
            code = 0
        if sha256(args.wav) != source_hash:
            raise ValueError("original WAV changed during DeepCW inference")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        result.update(status="error" if result.get("real_inference_run") else "blocked",
                      error=f"{type(exc).__name__}: {exc}")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
