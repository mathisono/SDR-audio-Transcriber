#!/usr/bin/env python3
"""Generate FIFO-enabled GRC variants from shared_baseband_one_channel.grc.

The outputs keep the same GUI/layout style as the reference flowgraph and add:
- fifo_path variable
- record_control_path variable
- audio_gain QT slider
- Receiver 1 Recorder Threshold RMS QT slider in the Receiver 1 control block
- Multiply Const on demod audio
- Float to Short
- File Sink writing mono s16le PCM to the FIFO used by start_grc_clip_writer.sh

By default this writes both:
  grc/shared_baseband_one_channel_fifo_wbfm.grc
  grc/shared_baseband_one_channel_fifo_nfm.grc

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


def grc_string_value(value: str) -> str:
    escaped = value.replace('"', '\\"')
    return f"'\"{escaped}\"'"


def extra_fifo_vars(fifo_path: str, record_control_path: str, record_threshold_default: int) -> str:
    return "\n".join([
        block(
            "fifo_path",
            "variable",
            {
                "comment": "'FIFO created by scripts/start_grc_clip_writer.sh. Absolute path avoids GRC working-directory problems.'",
                "value": grc_string_value(fifo_path),
            },
            "[160, 200]",
        ),
        block(
            "record_control_path",
            "variable",
            {
                "comment": "'JSON control file watched by clip_writer.py for live recorder threshold changes.'",
                "value": grc_string_value(record_control_path),
            },
            "[160, 260]",
        ),
        block(
            "audio_gain",
            "variable_qtgui_range",
            {
                "comment": "'Audio scale before recorder output. Lower if clipped; raise if quiet.'",
                "gui_hint": "3,4,1,2",
                "label": "Receiver 1 Recorder Audio Gain",
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
        block(
            "record_threshold",
            "variable_qtgui_range",
            {
                "comment": "'Live recorder RMS threshold. This is visually placed in the Receiver 1 Qt control block and bridged to runtime/recorder_control.json by scripts/run_grc_threshold_bridge.py.'",
                "gui_hint": "3,6,1,2",
                "label": "Receiver 1 Recorder Threshold RMS",
                "min_len": "'200'",
                "orient": "Qt.Horizontal",
                "rangeType": "int",
                "start": "'0'",
                "step": "'100'",
                "stop": "'20000'",
                "value": f"'{record_threshold_default}'",
                "widget": "counter_slider",
            },
            "[1220, 410]",
        ),
    ])


def fifo_blocks() -> str:
    return "\n".join([
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


def nbfm_block() -> str:
    return block(
        "analog_nbfm_rx_0",
        "analog_nbfm_rx",
        {
            "affinity": "''",
            "alias": "''",
            "audio_rate": "audio_rate",
            "comment": "'Narrowband FM demod for repeater / NOAA / land-mobile voice monitoring'",
            "max_dev": "'5000'",
            "maxoutbuf": "'0'",
            "minoutbuf": "'0'",
            "quad_rate": "int(samp_rate/chan1_decim)",
            "tau": "'75e-6'",
        },
        "[1016, 620]",
    )


def generate_variant(source_text: str, mode: str, fifo_path: str, record_control_path: str, record_threshold_default: int) -> str:
    mode = mode.lower()
    if mode not in {"wbfm", "nfm"}:
        raise ValueError(mode)

    text = source_text
    suffix = f"fifo_{mode}"
    title_mode = mode.upper()

    text = replace_first(text, "id: shared_baseband_one_channel", f"id: shared_baseband_one_channel_{suffix}")
    text = replace_first(text, "title: Shared Baseband One Channel", f"title: Shared Baseband One Channel FIFO {title_mode}")
    text = replace_first(
        text,
        "description: RTL-SDR shared baseband with one WBFM demod branch",
        f"description: RTL-SDR shared baseband with one {title_mode} demod branch plus FIFO PCM output and Receiver 1 recorder threshold control",
    )
    text = replace_first(
        text,
        "comment: First working shared-baseband one-channel test flowgraph",
        f"comment: Shared-baseband one-channel {title_mode} test flowgraph with FIFO recorder output and Receiver 1 threshold slider",
    )
    text = replace_first(
        text,
        "run_command: '{python} -u {filename}'",
        "run_command: '{python} -u scripts/run_grc_threshold_bridge.py {filename}'",
    )

    if mode == "nfm":
        text = text.replace("value: '48000'", "value: '24000'", 1)
        text = text.replace("value: '90700000'", "value: '162400000'", 1)
        text = text.replace("value: '25'", "value: '42'", 1)
        text = text.replace("value: '75000'", "value: '12000'", 1)
        text = text.replace("value: '10000'", "value: '3000'", 1)

        start = text.find("- name: analog_wfm_rcv_0\n")
        end = text.find("- name: audio_sink_0\n", start)
        if start == -1 or end == -1:
            raise SystemExit("Could not locate WBFM block to replace for NFM variant")
        text = text[:start] + nbfm_block() + "\n" + text[end:]
        text = text.replace("analog_wfm_rcv_0", "analog_nbfm_rx_0")

    marker = "- name: chan1_cutoff\n"
    text = replace_first(text, marker, extra_fifo_vars(fifo_path, record_control_path, record_threshold_default) + "\n" + marker)
    text = replace_first(text, "connections:\n", fifo_blocks() + "\nconnections:\n")

    demod_name = "analog_nbfm_rx_0" if mode == "nfm" else "analog_wfm_rcv_0"
    text = text.replace(f"- [{demod_name}, '0', audio_sink_0, '0']\n", "")
    text = replace_first(
        text,
        "connections:\n",
        "connections:\n"
        f"- [{demod_name}, '0', audio_sink_0, '0']\n"
        f"- [{demod_name}, '0', blocks_multiply_const_vxx_0, '0']\n"
        "- [blocks_multiply_const_vxx_0, '0', blocks_float_to_short_0, '0']\n"
        "- [blocks_float_to_short_0, '0', blocks_file_sink_0, '0']\n",
    )
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate shared-baseband FIFO GRC variants")
    parser.add_argument("--input", default="grc/shared_baseband_one_channel.grc")
    parser.add_argument("--mode", choices=["both", "wbfm", "nfm"], default="both")
    parser.add_argument("--fifo", default=None, help="Absolute FIFO path to write in generated GRC. Defaults to repo/runtime/grc_audio.pcm")
    parser.add_argument("--record-control", default=None, help="Absolute recorder control JSON path. Defaults to repo/runtime/recorder_control.json")
    parser.add_argument("--record-threshold", type=int, default=10000, help="Default Recorder Threshold slider value in generated GRC")
    parser.add_argument("--output", default=None, help="Output path. Only valid with --mode wbfm or --mode nfm.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    source_text = (repo_root / args.input).read_text(encoding="utf-8") if not Path(args.input).is_absolute() else Path(args.input).read_text(encoding="utf-8")
    fifo_path = str(Path(args.fifo).expanduser().resolve()) if args.fifo else str((repo_root / "runtime" / "grc_audio.pcm").resolve())
    record_control_path = str(Path(args.record_control).expanduser().resolve()) if args.record_control else str((repo_root / "runtime" / "recorder_control.json").resolve())
    modes = ["wbfm", "nfm"] if args.mode == "both" else [args.mode]

    for mode in modes:
        if args.output and len(modes) == 1:
            out_path = Path(args.output)
            if not out_path.is_absolute():
                out_path = repo_root / out_path
        elif args.output:
            raise SystemExit("--output can only be used when --mode is wbfm or nfm")
        else:
            out_path = repo_root / f"grc/shared_baseband_one_channel_fifo_{mode}.grc"
        out_path.write_text(generate_variant(source_text, mode, fifo_path, record_control_path, args.record_threshold), encoding="utf-8")
        print(f"wrote {out_path}")

    print(f"GRC FIFO path is: {fifo_path}")
    print(f"GRC recorder control path is: {record_control_path}")
    print("Open with: gnuradio-companion grc/shared_baseband_one_channel_fifo_wbfm.grc")
    print("      or: gnuradio-companion grc/shared_baseband_one_channel_fifo_nfm.grc")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
