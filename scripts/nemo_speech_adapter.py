#!/usr/bin/env python3
"""One-shot adapter for the local NeMo-Speech.cpp CLI.

The adapter accepts only local files and never supplies transcript prompts,
callsign hints, or reference text to the recognizer.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from safe_runtime import run_command, sha256


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    result = {"status": "blocked", "text": "", "real_inference_run": False,
              "engine": "nemo-speech-parakeet-tdt-0.6b-v3-q8_0"}
    code = 2
    if not args.wav.is_file():
        result["error"] = "input WAV is unavailable"
    elif not args.binary.is_file():
        result["error"] = "nemo-speech binary is unavailable"
    elif not args.model.is_file():
        result["error"] = "Parakeet GGUF is unavailable"
    else:
        started = time.monotonic()
        proc = run_command([str(args.binary), "--json", "transcribe", str(args.wav),
                            "--model", str(args.model), "--device", args.device], args.timeout)
        elapsed = time.monotonic() - started
        if proc["error"]:
            result.update(status="error", error=proc["error"], stderr=proc["stderr"],
                          wall_seconds=elapsed)
        else:
            try:
                value = json.loads(proc["stdout"])
                if not isinstance(value, dict) or not isinstance(value.get("text"), str):
                    raise ValueError("nemo-speech JSON lacks string text")
                words = value.get("words", [])
                if not isinstance(words, list):
                    raise ValueError("nemo-speech words must be a list")
                result.update(status="completed" if value["text"].strip() else "no_decode",
                              text=value["text"].strip(), words=words,
                              segments=words, real_inference_run=True,
                              wall_seconds=elapsed, stderr=proc["stderr"],
                              audio_duration_seconds=value.get("duration"),
                              reported_confidence=value.get("confidence"),
                              confidence_note="backend diagnostic; not comparable across models",
                              model=str(args.model), model_sha256=sha256(args.model),
                              model_revision=args.model_revision, device=args.device,
                              preprocessing={"runtime": "NeMo-Speech.cpp",
                                             "runtime_resamples_input": True,
                                             "target_sample_rate_hz": 16000,
                                             "prompt_or_callsign_hint": None})
                code = 0
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                result.update(status="error", error=str(exc), stderr=proc["stderr"],
                              wall_seconds=elapsed)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
