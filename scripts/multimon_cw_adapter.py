#!/usr/bin/env python3
"""Offline adapter for multimon-ng's conventional MORSE_CW demodulator."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time

from cw_decode import estimate_tone, read_wav_mono
from safe_runtime import run_command, sha256


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, default=Path("/usr/bin/ffmpeg"))
    parser.add_argument("--upstream-revision", required=True)
    parser.add_argument("--derived-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--dit-ms", type=int)
    parser.add_argument("--gap-ms", type=int)
    parser.add_argument("--threshold", type=int)
    args = parser.parse_args()
    result = {"status": "blocked", "text": "", "decoded": False,
              "real_inference_run": False, "engine": "multimon-ng-MORSE_CW",
              "decoder_kind": "conventional"}
    code = 2
    try:
        for path, label in ((args.wav, "input WAV"), (args.binary, "multimon-ng binary"),
                            (args.ffmpeg, "ffmpeg binary")):
            if not path.is_file():
                raise FileNotFoundError(f"{label} unavailable")
        source_hash = sha256(args.wav)
        sample_rate, samples = read_wav_mono(args.wav)
        tone = estimate_tone(sample_rate, samples, 100, min(2000, sample_rate // 2 - 1))
        args.derived_dir.mkdir(parents=True, exist_ok=True)
        target = args.derived_dir / f"{source_hash}.multimon-22050.raw"
        fd, temporary = tempfile.mkstemp(prefix=".multimon-", suffix=".raw",
                                         dir=str(args.derived_dir))
        os.close(fd)
        try:
            conversion = run_command([str(args.ffmpeg), "-nostdin", "-v", "error", "-y",
                                      "-i", str(args.wav), "-ac", "1", "-ar", "22050",
                                      "-f", "s16le", "-acodec", "pcm_s16le", temporary],
                                     args.timeout)
            if conversion["error"]:
                raise RuntimeError("ffmpeg conversion failed: " + conversion["error"])
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        argv = [str(args.binary), "-q", "-c", "-a", "MORSE_CW", "-t", "raw"]
        for option, value in (("-d", args.dit_ms), ("-g", args.gap_ms),
                              ("-o", args.threshold)):
            if value is not None:
                argv += [option, str(value)]
        argv.append(str(target))
        started = time.monotonic()
        proc = run_command(argv, args.timeout)
        elapsed = time.monotonic() - started
        if proc["error"]:
            result.update(status="error", error=proc["error"], stderr=proc["stderr"],
                          wall_seconds=elapsed)
        else:
            text = proc["stdout"].strip()
            result.update(status="completed" if text else "no_decode", text=text,
                          decoded=bool(text), real_inference_run=True,
                          stderr=proc["stderr"], wall_seconds=elapsed,
                          input_sha256=source_hash, upstream_revision=args.upstream_revision,
                          tone_measurement=tone,
                          derived_audio={"path": str(target), "sha256": sha256(target),
                              "sample_rate": 22050, "channels": 1,
                              "sample_format": "signed 16-bit little-endian raw",
                              "source_start_seconds": 0.0,
                              "source_end_seconds": round(len(samples) / sample_rate, 6)},
                          preprocessing={"command": "ffmpeg PCM resample/downmix",
                              "frequency_translation_hz": 0,
                              "dit_ms": args.dit_ms, "gap_ms": args.gap_ms,
                              "threshold": args.threshold,
                              "auto_timing": args.dit_ms is None and args.gap_ms is None,
                              "auto_threshold": args.threshold is None})
            code = 0
        if sha256(args.wav) != source_hash:
            raise ValueError("original WAV changed during multimon inference")
    except (OSError, ValueError, RuntimeError) as exc:
        result.update(status="error" if result.get("real_inference_run") else "blocked",
                      error=f"{type(exc).__name__}: {exc}")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
