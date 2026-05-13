#!/usr/bin/env bash
set -euo pipefail

CONFIG="configs/shared_baseband_radio_server.json"
RECEIVER="rx-1"
SOURCE="MSE-88"
GAIN=""
THRESHOLD=""
VERBOSE=""

usage() {
  cat <<'EOF'
Usage: scripts/start_rtl_fm_receiver.sh [options]

Starts rtl_fm and clip_writer with the same active receiver frequency read from
configs/shared_baseband_radio_server.json. This keeps WAV filenames and JSON
metadata matched to the actual tuned receiver frequency.

Options:
  --config PATH       Config JSON path. Default: configs/shared_baseband_radio_server.json
  --receiver ID       Receiver id/name from config. Default: rx-1
  --source NAME       Source label for metadata. Default: MSE-88
  --gain DB           Override SDR gain. Default: source.gain_db from config
  --threshold RMS     Override clip_writer RMS threshold. Default: clip_writer.squelch_threshold_rms
  --verbose           Pass --verbose to clip_writer
  -h, --help          Show this help

Examples:
  scripts/start_rtl_fm_receiver.sh --receiver rx-1
  scripts/start_rtl_fm_receiver.sh --receiver rx-1 --threshold 450 --verbose

Change the active receiver frequency first:
  .venv/bin/python3 scripts/receiver_config.py --receiver rx-1 set-frequency 441.000M
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --receiver) RECEIVER="$2"; shift 2 ;;
    --source) SOURCE="$2"; shift 2 ;;
    --gain) GAIN="$2"; shift 2 ;;
    --threshold) THRESHOLD="$2"; shift 2 ;;
    --verbose) VERBOSE="--verbose"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PY="${ROOT_DIR}/.venv/bin/python3"
if [[ ! -x "${PY}" ]]; then
  PY="python3"
fi

FREQ_HZ="$(${PY} scripts/receiver_config.py --config "${CONFIG}" --receiver "${RECEIVER}" frequency-hz)"
RTL_FREQ="$(${PY} scripts/receiver_config.py --config "${CONFIG}" --receiver "${RECEIVER}" rtl-fm-frequency)"
PPM_ARGS="$(${PY} scripts/ppm_config.py --config "${CONFIG}" rtl-fm-args)"

read_config_value() {
  local expr="$1"
  ${PY} - "$CONFIG" "$expr" <<'PY'
import json, sys
path, expr = sys.argv[1], sys.argv[2]
data = json.load(open(path))
cur = data
for part in expr.split('.'):
    cur = cur[part]
print(cur)
PY
}

MODE="$(read_config_value "receivers.0.mode" 2>/dev/null || echo wbfm)"
SAMPLE_RATE="$(read_config_value "source.sample_rate" 2>/dev/null || echo 240000)"
AUDIO_RATE="$(read_config_value "audio.sample_rate" 2>/dev/null || echo 48000)"
HANG_MS="$(read_config_value "clip_writer.hang_time_ms" 2>/dev/null || echo 1200)"
MIN_SEC="$(read_config_value "clip_writer.min_clip_seconds" 2>/dev/null || echo 1.0)"
MAX_SEC="$(read_config_value "clip_writer.max_clip_seconds" 2>/dev/null || echo 60.0)"
QUEUE_DIR="$(read_config_value "clip_writer.queue_directory" 2>/dev/null || echo runtime/queue)"
TMP_DIR="$(read_config_value "clip_writer.tmp_directory" 2>/dev/null || echo runtime/tmp)"

if [[ -z "${GAIN}" ]]; then
  GAIN="$(read_config_value "source.gain_db" 2>/dev/null || echo 25)"
fi
if [[ -z "${THRESHOLD}" ]]; then
  THRESHOLD="$(read_config_value "clip_writer.squelch_threshold_rms" 2>/dev/null || echo 650)"
fi

case "${MODE}" in
  wbfm|WBFM) RTL_MODE="wbfm" ;;
  fm|nfm|NFM) RTL_MODE="fm" ;;
  *) RTL_MODE="wbfm" ;;
esac

mkdir -p "${QUEUE_DIR}" "${TMP_DIR}"

echo "receiver_launcher: source=${SOURCE} receiver=${RECEIVER} mode=${RTL_MODE} frequency_hz=${FREQ_HZ} rtl_fm_frequency=${RTL_FREQ} ppm=${PPM_ARGS} gain=${GAIN}"

# shellcheck disable=SC2086
rtl_fm -M "${RTL_MODE}" -f "${RTL_FREQ}" -s "${SAMPLE_RATE}" -r "${AUDIO_RATE}" -g "${GAIN}" ${PPM_ARGS} - | \
  "${PY}" scripts/clip_writer.py \
    --queue "${QUEUE_DIR}" \
    --tmp "${TMP_DIR}" \
    --source "${SOURCE}" \
    --receiver "${RECEIVER}" \
    --frequency-hz "${FREQ_HZ}" \
    --mode "${RTL_MODE}" \
    --sample-rate "${AUDIO_RATE}" \
    --threshold "${THRESHOLD}" \
    --hang-ms "${HANG_MS}" \
    --min-sec "${MIN_SEC}" \
    --max-sec "${MAX_SEC}" \
    ${VERBOSE}
