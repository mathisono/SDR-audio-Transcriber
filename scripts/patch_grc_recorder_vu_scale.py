#!/usr/bin/env python3
"""Patch generated FIFO GRC files with Receiver 1 recorder gain and level UI.

Adds two GRC-side visual controls in the Receiver 1 Qt area:

1. A moving recorder level indicator using qtgui_number_sink on the recorder audio
   branch. This shows actual activity from the demodulated audio being sent to
   the FIFO/clip writer.
2. A dB/log recorder gain slider with reference marks:
   -40 | -30 | -20 | -10 | -3 | 0 unity | +3 | +10

The level meter uses a float absolute-value block, not complex magnitude, because
the recorder audio branch is a float stream.
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

GAIN_AND_METER_BLOCKS = """- name: audio_gain
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
    comment: 'Receiver 1 recorder gain shown as a dB/log control. 0 dB is unity gain.'
    gui_hint: 3,5,1,2
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

LEVEL_BLOCKS = """- name: blocks_abs_xx_0
  id: blocks_abs_xx
  parameters:
    affinity: ''
    alias: ''
    comment: 'Recorder level detector before FIFO. Input is float audio; output is absolute value.'
    maxoutbuf: '0'
    minoutbuf: '0'
    type: float
    vlen: '1'
  states:
    bus_sink: false
    bus_source: false
    bus_structure: null
    coordinate: [1220, 560]
    rotation: 0
    state: enabled
- name: blocks_moving_average_xx_0
  id: blocks_moving_average_xx
  parameters:
    affinity: ''
    alias: ''
    comment: 'Smooths recorder level display.'
    length: '1024'
    max_iter: '4000'
    maxoutbuf: '0'
    minoutbuf: '0'
    scale: '1.0/1024'
    type: float
    vlen: '1'
  states:
    bus_sink: false
    bus_source: false
    bus_structure: null
    coordinate: [1440, 560]
    rotation: 0
    state: enabled
- name: qtgui_number_sink_0
  id: qtgui_number_sink
  parameters:
    affinity: ''
    alias: ''
    autoscale: 'False'
    average: '0.15'
    color1: blue
    color2: red
    comment: 'Receiver 1 recorder level meter. Movement here confirms audio is entering the recorder/FIFO path.'
    factor1: '1'
    factor2: '1'
    graph_type: horiz
    gui_hint: 3,4,1,2
    label1: Recorder Level    -40 | -30 | -20 | -10 | -3 | 0 unity | +3 | +10
    label2: ''
    max: '1.0'
    min: '0.0'
    name: Receiver 1 Recorder Level
    nconnections: '1'
    type: float
    unit1: linear
    unit2: ''
    update_time: '0.10'
  states:
    bus_sink: false
    bus_source: false
    bus_structure: null
    coordinate: [1660, 560]
    rotation: 0
    state: enabled
"""

OLD_BAD_LEVEL_BLOCK_NAME = "blocks_complex_to_mag_squared_0"


def insert_before_connections(text: str, blocks: str) -> str:
    marker = "connections:\n"
    if marker not in text:
        raise SystemExit("could not find connections marker")
    return text.replace(marker, blocks + "\n" + marker, 1)


def add_connection(text: str, connection: str) -> str:
    if connection in text:
        return text
    marker = "connections:\n"
    if marker not in text:
        raise SystemExit("could not find connections marker")
    return text.replace(marker, marker + connection, 1)


def remove_bad_complex_meter(text: str) -> str:
    # Remove the older incorrect complex magnitude block/links if a previously
    # generated GRC file is being patched in-place instead of regenerated.
    if OLD_BAD_LEVEL_BLOCK_NAME not in text:
        return text
    start = text.find("- name: blocks_complex_to_mag_squared_0\n")
    end = text.find("- name: blocks_moving_average_xx_0\n", start)
    if start != -1 and end != -1:
        text = text[:start] + text[end:]
    text = text.replace("- [blocks_multiply_const_vxx_0, '0', blocks_complex_to_mag_squared_0, '0']\n", "")
    text = text.replace("- [blocks_complex_to_mag_squared_0, '0', blocks_moving_average_xx_0, '0']\n", "")
    return text


def patch_file(path: Path) -> bool:
    if not path.exists():
        print(f"skip missing {path}")
        return False
    text = path.read_text(encoding="utf-8")
    original = text

    text = remove_bad_complex_meter(text)

    if "name: recorder_gain_db" not in text:
        if OLD_AUDIO_GAIN not in text:
            raise SystemExit(f"could not find expected Receiver 1 Recorder Audio Gain block in {path}")
        text = text.replace(OLD_AUDIO_GAIN, GAIN_AND_METER_BLOCKS, 1)

    if "name: qtgui_number_sink_0" not in text:
        text = insert_before_connections(text, LEVEL_BLOCKS)
    elif "name: blocks_abs_xx_0" not in text:
        text = insert_before_connections(text, """- name: blocks_abs_xx_0
  id: blocks_abs_xx
  parameters:
    affinity: ''
    alias: ''
    comment: 'Recorder level detector before FIFO. Input is float audio; output is absolute value.'
    maxoutbuf: '0'
    minoutbuf: '0'
    type: float
    vlen: '1'
  states:
    bus_sink: false
    bus_source: false
    bus_structure: null
    coordinate: [1220, 560]
    rotation: 0
    state: enabled
""")

    text = add_connection(text, "- [blocks_multiply_const_vxx_0, '0', blocks_abs_xx_0, '0']\n")
    text = add_connection(text, "- [blocks_abs_xx_0, '0', blocks_moving_average_xx_0, '0']\n")
    text = add_connection(text, "- [blocks_moving_average_xx_0, '0', qtgui_number_sink_0, '0']\n")

    if text != original:
        path.write_text(text, encoding="utf-8")
        print(f"patched {path}")
        return True
    print(f"already patched {path}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch generated FIFO GRC files with Receiver 1 recorder level meter and dB gain scale")
    parser.add_argument("grc", nargs="*", type=Path, default=DEFAULT_GRC)
    args = parser.parse_args()
    for path in args.grc:
        patch_file(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
