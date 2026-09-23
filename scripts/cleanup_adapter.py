#!/usr/bin/env python3
"""One-shot cleanup command so the sidecar can enforce an overall deadline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from safe_runtime import read_json
from transcribe_worker import call_cleanup_model


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('record', type=Path)
    a = p.parse_args()
    try:
        record = read_json(a.record)
        request = record['enrichment']
        text = call_cleanup_model(record['raw_text'], request['cleanup_endpoint'], request['cleanup_model'],
                                  request['cleanup_timeout'], mode=request['cleanup_mode'],
                                  max_tokens=request['cleanup_max_tokens'])
        print(json.dumps({'text': text}))
        return 0
    except Exception as exc:
        print(json.dumps({'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
