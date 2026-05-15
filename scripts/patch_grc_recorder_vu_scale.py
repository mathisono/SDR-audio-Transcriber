#!/usr/bin/env python3
"""Patch generated FIFO GRC files with a Receiver 1 recorder VU/gain scale.

This is a post-generation patch because GRC's QT Range widget does not support
custom non-linear tick marks. The practical GRC-side control is a dB/log slider:

  Receiver 1 Recorder Gain dB    -40 | -30 | -20 | -10 | -3 | 0 unity | +3 | +10

The flowgraph then computes the linear multiplier as:

  audio_gain = 10 ** (recorder_gain_db / 20.0)

Run after scripts/make_shared_baseband_fifo_grc.py.
"""
from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_GRC = [
    Path("grc/shared_baseband_one_channel_fifo_nfm.grc"),
    Path("grc/shared_baseband_one_channel_fifo_wbfm.grc"),
]

OLD_AUDIO_GAIN = """- name: audio_gain
  id: variable_qtgui_range
  parameters:
    comment: 'Audio scale before recorder output. Lower if clipped; raise if quiet.'
    gui_hint: 3,4,1,2
    label: Receiver 1 Recorder Audio Gain
    min_len: '200'
    orient: Qt.Horizontal
    rangeType: float
    start: '0.1'
    step: '0.05'
    stop: '2.0'
    value: '0.85'
    widget: counter_slider
  states:
    bus_sink: false
    bus_source: false
    bus_structure: null
    coordinate: [1220, 350]
    rotation: 0
    state: enabled
"""

NEW_GAIN_BLOCKS = """- name: audio_gain
  id: variable
  parameters:
    comment: 'Linear recorder multiplier derived from Recorder Gain dB: 10 ** (recorder_gain_db / 20).'
    value: 10**(recorder_gain_db / 20.0)
  states:
    bus_sink: false
    bus_source: false
    bus_structure: null
    coordinate: [160, 320]
    rotation: 0
    state: enabled
- name: recorder_gain_db
  id: variable_qtgui_range
  parameters:
    comment: 'Receiver 1 recorder gain shown as a VU-style dB/log control. 0 dB is unity gain.'
    gui_hint: 3,4,1,2
    label: Receiver 1 Recorder Gain dB    -40 | -30 | -20 | -10 | -3 | 0 unity | +3 | +10
    min_len: '300'
    orient: Qt.Horizontal
    rangeType: float
    start: '-40'
    step: '1'
    stop: '10'
    value: '0'
    widget: counter_slider
  states:
    bus_sink: false
    bus_source: false
    bus_structure: null
    coordinate: [1220, 350]
    rotation: 0
    state: enabled
"""


def patch_file(path: Path) -> bool:
    if not path.exists():
        print(f"skip missing {path}")
        return False
    text = path.read_text(encoding="utf-8")
    if "name: recorder_gain_db" in text:
        print(f"already patched {path}")
        return False
    if OLD_AUDIO_GAIN not in text:
        raise SystemExit(f"could not find expected Receiver 1 Recorder Audio Gain block in {path}")
    text = text.replace(OLD_AUDIO_GAIN, NEW_GAIN_BLOCKS, 1)
    path.write_text(text, encoding="utf-8")
    print(f"patched {path}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch generated FIFO GRC files with dB/VU recorder gain scale")
    parser.add_argument("grc", nargs="*", type=Path, default=DEFAULT_GRC)
    args = parser.parse_args()
    for path in args.grc:
        patch_file(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
