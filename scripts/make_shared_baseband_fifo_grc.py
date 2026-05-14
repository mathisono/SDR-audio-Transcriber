#!/usr/bin/env python3
"""Generate a FIFO-enabled GRC from shared_baseband_one_channel.grc.

The output keeps the same GUI/layout style as the reference flowgraph and adds:
- fifo_path variable
- audio_gain QT slider
- Multiply Const on demod audio
- Float to Short
- File Sink writing mono s16le PCM to runtime/grc_audio.pcm

Run from repo root:
  .venv/bin/python3 scripts/make_shared_baseband_fifo_grc.py
"""
from __future__ import annotations

import argparse
from pathlib import Path


def block(name: str, block_id: str, parameters: dict[str, str], coordinate: str) -> str:
    params = "\n".join(f"    {k}: {v}" for k, v in parameters.items())
    return f"""- name: {name}
  id: {block_id}
  parameters:
{params}
  states:
    bus_sink: false
    bus_source: false
    bus_structure: null
    coordinate: {coordinate}
    rotation: 0
    state: enabled
"""


def replace_first(text: str, old: str, new: str) -> str:
    if old not in text:
        raise SystemExit(f"Could not find expected text to replace:\n{old}")
    return text.replace(old, new, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate shared_baseband_one_channel_fifo.grc")
    parser.add_argument("--input", default="grc/shared_baseband_one_channel.grc")
    parser.add_argument("--output", default="grc/shared_baseband_one_channel_fifo.grc")
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    text = in_path.read_text(encoding="utf-8")

    text = replace_first(text, "id: shared_baseband_one_channel", "id: shared_baseband_one_channel_fifo")
    text = replace_first(text, "title: Shared Baseband One Channel", "title: Shared Baseband One Channel FIFO")
    text = replace_first(
        text,
        "description: RTL-SDR shared baseband with one WBFM demod branch",
        "description: RTL-SDR shared baseband with one WBFM demod branch plus FIFO PCM output for SDR-audio-Transcriber",
    )
    text = replace_first(
        text,
        "comment: First working shared-baseband one-channel test flowgraph",
        "comment: Shared-baseband one-channel test flowgraph with FIFO recorder output",
    )

    extra_vars = "\n".join([
        block(
            "fifo_path",
            "variable",
            {
                "comment": "'FIFO created by scripts/start_grc_clip_writer.sh'",
                "value": "'\"runtime/grc_audio.pcm\"'",
            },
            "[160, 200]",
        ),
        block(
            "audio_gain",
            "variable_qtgui_range",
            {
                "comment": "'Audio scale before recorder output. Lower if clipped; raise if quiet.'",
                "gui_hint": "2,6,1,2",
                "label": "Audio Gain to Recorder",
                "min_len": "'200'",
                "orient": "Qt.Horizontal",
                "rangeType": "float",
                "start": "'0.1'",
                "step": "'0.05'",
                "stop": "'2.0'",
                "value": "'0.85'",
                "widget": "counter_slider",
            },
            "[1220, 350]",
        ),
    ])

    marker = "- name: chan1_cutoff\n"
    text = replace_first(text, marker, extra_vars + "\n" + marker)

    fifo_blocks = "\n".join([
        block(
            "blocks_multiply_const_vxx_0",
            "blocks_multiply_const_vxx",
            {
                "affinity": "''",
                "alias": "''",
                "comment": "'Recorder audio gain before Float to Short'",
                "const": "audio_gain",
                "maxoutbuf": "'0'",
                "minoutbuf": "'0'",
                "type": "float",
                "vlen": "'1'",
            },
            "[1225, 700]",
        ),
        block(
            "blocks_float_to_short_0",
            "blocks_float_to_short",
            {
                "affinity": "''",
                "alias": "''",
                "comment": "'Converts float audio to mono signed 16-bit PCM for clip_writer.py'",
                "maxoutbuf": "'0'",
                "minoutbuf": "'0'",
                "scale": "'16000'",
                "vlen": "'1'",
            },
            "[1440, 700]",
        ),
        block(
            "blocks_file_sink_0",
            "blocks_file_sink",
            {
                "affinity": "''",
                "alias": "''",
                "append": "'False'",
                "comment": "'Writes mono s16le PCM into FIFO created by scripts/start_grc_clip_writer.sh'",
                "file": "fifo_path",
                "maxoutbuf": "'0'",
                "minoutbuf": "'0'",
                "type": "short",
                "unbuffered": "'True'",
                "vlen": "'1'",
            },
            "[1440, 800]",
        ),
    ])

    text = replace_first(text, "connections:\n", fifo_blocks + "\nconnections:\n")

    text = text.replace("- [analog_wfm_rcv_0, '0', audio_sink_0, '0']\n", "")
    text = replace_first(
        text,
        "connections:\n",
        "connections:\n"
        "- [analog_wfm_rcv_0, '0', audio_sink_0, '0']\n"
        "- [analog_wfm_rcv_0, '0', blocks_multiply_const_vxx_0, '0']\n"
        "- [blocks_multiply_const_vxx_0, '0', blocks_float_to_short_0, '0']\n"
        "- [blocks_float_to_short_0, '0', blocks_file_sink_0, '0']\n",
    )

    out_path.write_text(text, encoding="utf-8")
    print(f"wrote {out_path}")
    print("Open it with: gnuradio-companion grc/shared_baseband_one_channel_fifo.grc")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
