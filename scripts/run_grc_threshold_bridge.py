#!/usr/bin/env python3
"""Run a generated GRC Python file and bridge its Recorder Threshold slider.

GNU Radio Companion's QT slider updates a generated set_record_threshold()
method. This wrapper patches that generated Python file immediately before
running it so every slider movement writes runtime/recorder_control.json.

The generated GRC run command should call this instead of running the generated
file directly:

    python3 -u scripts/run_grc_threshold_bridge.py {filename}

The flowgraph must define:
  - record_threshold variable_qtgui_range
  - record_control_path variable

clip_writer.py must be running with --threshold-control pointing at the same JSON
file, which the GRC stack launcher now does through start_grc_clip_writer.sh.
"""
from __future__ import annotations

import re
import runpy
import sys
from pathlib import Path

HELPER = r'''

def _openclaw_write_record_control(path, threshold):
    try:
        if path is None:
            path = 'runtime/recorder_control.json'
        path = os.path.expanduser(str(path))
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        data = {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
        except Exception:
            data = {}
        data['threshold'] = int(float(threshold))
        data.setdefault('hang_ms', 1800)
        data.setdefault('min_sec', 1.0)
        data.setdefault('max_sec', 60.0)
        tmp_name = path + '.tmp'
        with open(tmp_name, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
            f.write('\n')
        os.replace(tmp_name, path)
    except Exception as exc:
        print('GRC recorder threshold bridge write failed:', exc, flush=True)
'''


def patch_source(source: str) -> str:
    if "_openclaw_write_record_control" in source:
        return source

    if "import sys\n" in source:
        source = source.replace("import sys\n", "import sys\nimport json\nimport os\n", 1)
    else:
        source = "import json\nimport os\n" + source

    class_match = re.search(r"\nclass\s+\w+\(gr\.top_block", source)
    if not class_match:
        raise SystemExit("Could not find generated GRC top_block class to patch")
    source = source[: class_match.start()] + HELPER + source[class_match.start():]

    pattern = r"(\n    def set_record_threshold\(self, record_threshold\):\n)"
    if not re.search(pattern, source):
        raise SystemExit("Generated GRC file has no set_record_threshold(); regenerate the FIFO GRC file first")

    replacement = (
        "\n    def set_record_threshold(self, record_threshold):\n"
        "        _openclaw_write_record_control(getattr(self, 'record_control_path', 'runtime/recorder_control.json'), record_threshold)\n"
    )
    return re.sub(pattern, replacement, source, count=1)


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: run_grc_threshold_bridge.py GENERATED_GRC_PY [args...]")

    generated = Path(sys.argv[1]).resolve()
    if not generated.exists():
        raise SystemExit(f"generated file not found: {generated}")

    source = generated.read_text(encoding="utf-8")
    patched = patch_source(source)
    patched_path = generated.with_suffix(generated.suffix + ".openclaw_threshold_bridge.py")
    patched_path.write_text(patched, encoding="utf-8")

    sys.argv = [str(patched_path), *sys.argv[2:]]
    runpy.run_path(str(patched_path), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
