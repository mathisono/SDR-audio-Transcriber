#!/usr/bin/env python3
"""One-shot local Qwen3-ASR adapter using the official qwen-asr package."""
from __future__ import annotations

import argparse
import contextlib
import json
from pathlib import Path
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--dtype", choices=("float32", "bfloat16"), default="float32")
    parser.add_argument("--max-new-tokens", type=int, default=256)
    args = parser.parse_args()
    result = {"status": "blocked", "text": "", "real_inference_run": False,
              "engine": "qwen3-asr-transformers"}
    code = 2
    try:
        if not args.wav.is_file():
            raise FileNotFoundError("input WAV is unavailable")
        # Keep import/model diagnostics off the JSON protocol on stdout.
        with contextlib.redirect_stdout(sys.stderr):
            import torch
            from qwen_asr import Qwen3ASRModel
            dtype = torch.float32 if args.dtype == "float32" else torch.bfloat16
            load_started = time.monotonic()
            model = Qwen3ASRModel.from_pretrained(
                args.model, dtype=dtype, device_map=args.device,
                max_inference_batch_size=1, max_new_tokens=args.max_new_tokens)
            loaded = time.monotonic()
            result.update(real_inference_run=True,
                          model_load_seconds=loaded-load_started)
            outputs = model.transcribe(audio=str(args.wav), language="English")
            finished = time.monotonic()
        if not outputs or not isinstance(outputs[0].text, str):
            raise ValueError("Qwen3-ASR returned no string transcript")
        text = outputs[0].text.strip()
        result.update(status="completed" if text else "no_decode", text=text,
                      real_inference_run=True, language=getattr(outputs[0], "language", None),
                      model=args.model, model_revision=args.model_revision,
                      device=args.device, dtype=args.dtype,
                      model_load_seconds=loaded-load_started,
                      inference_seconds=finished-loaded,
                      preprocessing={"language": "English", "timestamp_aligner": None,
                                     "prompt_or_callsign_hint": None,
                                     "runtime": "official qwen-asr transformers backend"})
        code = 0
    except ImportError as exc:
        result["error"] = "Qwen3-ASR runtime unavailable: " + str(exc)
    except (OSError, ValueError, RuntimeError) as exc:
        result.update(status="error" if result.get("real_inference_run") else "blocked",
                      error=f"{type(exc).__name__}: {exc}")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
