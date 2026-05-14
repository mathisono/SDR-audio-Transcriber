#!/usr/bin/env bash
set -euo pipefail

CONFIG="configs/shared_baseband_radio_server.json"
RECEIVER="rx-1"
SOURCE="MSE-88"
FREQUENCY_OVERRIDE=""
MODE_OVERRIDE=""
GAIN=""
AGC="0"
THRESHOLD=""
SAMPLE_RATE_OVERRIDE=""
AUDIO_RATE_OVERRIDE=""
HANG_MS_OVERRIDE=""
VERBOSE=""
CALIBRATE="ask"
CAL_DURATION="10s"
CAL_SPAN_KHZ="200"
CAL_BIN_HZ="1000"

usage() {
  cat <<'EOF'
Usage: scripts/start_rtl_fm_receiver.sh [options]

Starts rtl_fm and clip_writer with the same active receiver frequency and mode.
Before starting, it can optionally run scripts/rtl_signal_check.py to verify
signal strength, gain/AGC, and PPM. If calibration is accepted, the checker can
update configs/shared_baseband_radio_server.json, then this launcher reloads
PPM/gain/frequency/mode before starting rtl_fm.

Options:
  --config PATH         Config JSON path. Default: configs/shared_baseband_radio_server.json
  --receiver ID         Receiver id/name from config. Default: rx-1
  --frequency FREQ      Override tuned frequency for this run, e.g. 442.275M or 442275000
  --mode MODE           Override mode for this run: wbfm, widebandfm, nfm, narrowbandfm, or fm
  --source NAME         Source label for metadata. Default: MSE-88
  --gain DB             Override SDR tuner gain. NFM default is higher than WBFM.
  --agc                 Do not pass a fixed -g gain to rtl_fm; allow tuner auto gain.
  --threshold RMS       Override clip_writer RMS threshold. NFM default is lower than WBFM.
  --sample-rate HZ      Override rtl_fm output sample rate.
  --audio-rate HZ       Override clip_writer WAV sample rate metadata. Defaults to sample-rate.
  --hang-ms MS          Override clip hang time.
  --calibrate           Always run signal calibration before starting.
  --no-calibrate        Skip signal calibration prompt.
  --cal-duration DUR    Calibration duration for rtl_power. Default: 10s
  --cal-span-khz KHZ    Calibration scan span. Default: 200
  --cal-bin-hz HZ       Calibration bin width. Default: 1000
  --verbose             Pass --verbose to clip_writer so you can see RMS levels.
  -h, --help            Show this help

Examples:
  scripts/start_rtl_fm_receiver.sh --receiver rx-1 --mode wbfm --frequency 90.7M
  scripts/start_rtl_fm_receiver.sh --receiver rx-1 --mode nfm --frequency 442.275M --threshold 120 --gain 38 --verbose
  scripts/start_rtl_fm_receiver.sh --receiver rx-1 --mode nfm --frequency 162.4M --calibrate --verbose
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --receiver) RECEIVER="$2"; shift 2 ;;
    --frequency) FREQUENCY_OVERRIDE="$2"; shift 2 ;;
    --mode) MODE_OVERRIDE="$2"; shift 2 ;;
    --source) SOURCE="$2"; shift 2 ;;
    --gain) GAIN="$2"; AGC="0"; shift 2 ;;
    --agc) AGC="1"; GAIN=""; shift ;;
    --threshold) THRESHOLD="$2"; shift 2 ;;
    --sample-rate) SAMPLE_RATE_OVERRIDE="$2"; shift 2 ;;
    --audio-rate) AUDIO_RATE_OVERRIDE="$2"; shift 2 ;;
    --hang-ms) HANG_MS_OVERRIDE="$2"; shift 2 ;;
    --calibrate) CALIBRATE="yes"; shift ;;
    --no-calibrate) CALIBRATE="no"; shift ;;
    --cal-duration) CAL_DURATION="$2"; shift 2 ;;
    --cal-span-khz) CAL_SPAN_KHZ="$2"; shift 2 ;;
    --cal-bin-hz) CAL_BIN_HZ="$2"; shift 2 ;;
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

receiver_value() {
  local key="$1"
  ${PY} - "$CONFIG" "$RECEIVER" "$key" <<'PY'
import json, sys
path, receiver_id, key = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(path))
for rx in data.get('receivers', []):
    if receiver_id in {str(rx.get('id', '')), str(rx.get('name', ''))}:
        print(rx.get(key, ''))
        raise SystemExit(0)
raise SystemExit(f'receiver not found: {receiver_id}')
PY
}

parse_frequency_hz() {
  local freq="$1"
  ${PY} - "$freq" <<'PY'
import sys
text = sys.argv[1].strip().lower().replace('hz', '')
mult = 1
if text.endswith('mhz'):
    text = text[:-3]
    mult = 1_000_000
elif text.endswith('m'):
    text = text[:-1]
    mult = 1_000_000
elif text.endswith('khz'):
    text = text[:-3]
    mult = 1_000
elif text.endswith('k'):
    text = text[:-1]
    mult = 1_000
print(int(round(float(text) * mult)))
PY
}

format_rtl_frequency() {
  local freq_hz="$1"
  ${PY} - "$freq_hz" <<'PY'
import sys
freq = int(sys.argv[1])
if freq >= 1_000_000:
    print(f"{freq / 1_000_000.0:.6f}M")
elif freq >= 1_000:
    print(f"{freq / 1_000.0:.3f}k")
else:
    print(str(freq))
PY
}

normalize_mode() {
  local mode="${1,,}"
  case "${mode}" in
    wbfm|wide|widefm|wideband|widebandfm) echo "wbfm" ;;
    nfm|fm|narrow|narrowfm|narrowband|narrowbandfm) echo "fm" ;;
    *) echo "unknown mode: $1 (use wbfm or nfm)" >&2; exit 2 ;;
  esac
}

metadata_mode() {
  local rtl_mode="$1"
  case "${rtl_mode}" in
    wbfm) echo "wbfm" ;;
    fm) echo "nfm" ;;
    *) echo "${rtl_mode}" ;;
  esac
}

load_receiver_settings() {
  if [[ -n "${FREQUENCY_OVERRIDE}" ]]; then
    FREQ_HZ="$(parse_frequency_hz "${FREQUENCY_OVERRIDE}")"
    RTL_FREQ="$(format_rtl_frequency "${FREQ_HZ}")"
  else
    FREQ_HZ="$(${PY} scripts/receiver_config.py --config "${CONFIG}" --receiver "${RECEIVER}" frequency-hz)"
    RTL_FREQ="$(${PY} scripts/receiver_config.py --config "${CONFIG}" --receiver "${RECEIVER}" rtl-fm-frequency)"
  fi

  if [[ -n "${MODE_OVERRIDE}" ]]; then
    MODE="${MODE_OVERRIDE}"
  else
    MODE="$(receiver_value "mode" 2>/dev/null || echo wbfm)"
  fi
  RTL_MODE="$(normalize_mode "${MODE}")"
  CLIP_MODE="$(metadata_mode "${RTL_MODE}")"
}

load_runtime_settings() {
  PPM_ARGS="$(${PY} scripts/ppm_config.py --config "${CONFIG}" rtl-fm-args)"
  PPM_VALUE="$(read_config_value "source.ppm_correction" 2>/dev/null || echo 0)"
  CONFIG_SAMPLE_RATE="$(read_config_value "source.sample_rate" 2>/dev/null || echo 240000)"
  CONFIG_HANG_MS="$(read_config_value "clip_writer.hang_time_ms" 2>/dev/null || echo 1200)"
  MIN_SEC="$(read_config_value "clip_writer.min_clip_seconds" 2>/dev/null || echo 1.0)"
  MAX_SEC="$(read_config_value "clip_writer.max_clip_seconds" 2>/dev/null || echo 60.0)"
  QUEUE_DIR="$(read_config_value "clip_writer.queue_directory" 2>/dev/null || echo runtime/queue)"
  TMP_DIR="$(read_config_value "clip_writer.tmp_directory" 2>/dev/null || echo runtime/tmp)"

  if [[ -n "${SAMPLE_RATE_OVERRIDE}" ]]; then
    SAMPLE_RATE="${SAMPLE_RATE_OVERRIDE}"
  elif [[ "${CLIP_MODE}" == "nfm" ]]; then
    SAMPLE_RATE="24000"
  else
    SAMPLE_RATE="${CONFIG_SAMPLE_RATE}"
  fi

  if [[ -n "${AUDIO_RATE_OVERRIDE}" ]]; then
    AUDIO_RATE="${AUDIO_RATE_OVERRIDE}"
  else
    AUDIO_RATE="${SAMPLE_RATE}"
  fi

  if [[ -n "${HANG_MS_OVERRIDE}" ]]; then
    HANG_MS="${HANG_MS_OVERRIDE}"
  else
    HANG_MS="${CONFIG_HANG_MS}"
  fi

  if [[ "${AGC}" == "1" ]]; then
    GAIN_ARGS=()
    GAIN_LABEL="auto"
  elif [[ -n "${GAIN}" ]]; then
    GAIN_ARGS=(-g "${GAIN}")
    GAIN_LABEL="${GAIN}"
  else
    CONFIG_AGC="$(read_config_value "source.agc_enabled" 2>/dev/null || echo false)"
    CONFIG_GAIN_MODE="$(read_config_value "source.gain_mode" 2>/dev/null || echo manual)"
    if [[ "${CONFIG_AGC}" == "True" || "${CONFIG_AGC}" == "true" || "${CONFIG_GAIN_MODE}" == "auto" ]]; then
      GAIN_ARGS=()
      GAIN_LABEL="auto"
      AGC="1"
    else
      if [[ "${CLIP_MODE}" == "nfm" ]]; then
        GAIN="$(read_config_value "source.gain_db" 2>/dev/null || echo 38)"
      else
        GAIN="$(read_config_value "source.gain_db" 2>/dev/null || echo 25)"
      fi
      GAIN_ARGS=(-g "${GAIN}")
      GAIN_LABEL="${GAIN}"
    fi
  fi

  if [[ -z "${THRESHOLD}" ]]; then
    if [[ "${CLIP_MODE}" == "nfm" ]]; then
      THRESHOLD="120"
    else
      THRESHOLD="$(read_config_value "clip_writer.squelch_threshold_rms" 2>/dev/null || echo 650)"
    fi
  fi
}

run_signal_calibration() {
  local answer=""
  if [[ "${CALIBRATE}" == "ask" ]]; then
    if [[ -t 0 ]]; then
      echo
      echo "Signal calibration preflight is available."
      echo "It will run rtl_power around ${RTL_FREQ}, estimate signal/SNR/PPM/gain, update ${CONFIG}, then start rtl_fm."
      read -r -p "Run signal calibration now? [y/N] " answer
      case "${answer,,}" in
        y|yes) CALIBRATE="yes" ;;
        *) CALIBRATE="no" ;;
      esac
    else
      CALIBRATE="no"
    fi
  fi

  if [[ "${CALIBRATE}" != "yes" ]]; then
    echo "signal_calibration: skipped"
    return 0
  fi

  local cal_gain="${GAIN_LABEL}"
  if [[ "${AGC}" == "1" ]]; then
    cal_gain="auto"
  fi

  echo
  echo "signal_calibration: starting rtl_signal_check.py"
  echo "signal_calibration: frequency=${RTL_FREQ} mode=${CLIP_MODE} ppm=${PPM_VALUE} gain=${cal_gain} duration=${CAL_DURATION} span_khz=${CAL_SPAN_KHZ}"
  echo

  "${PY}" scripts/rtl_signal_check.py \
    --config "${CONFIG}" \
    --receiver "${RECEIVER}" \
    --frequency "${RTL_FREQ}" \
    --mode "${CLIP_MODE}" \
    --gain "${cal_gain}" \
    --ppm "${PPM_VALUE}" \
    --span-khz "${CAL_SPAN_KHZ}" \
    --bin-hz "${CAL_BIN_HZ}" \
    --duration "${CAL_DURATION}" \
    --write-config

  echo
  echo "signal_calibration: complete; reloading updated config before starting rtl_fm"
  echo
  # If user did not explicitly force frequency/mode/gain, let config updates take effect.
  if [[ -z "${FREQUENCY_OVERRIDE}" ]]; then :; fi
  if [[ -z "${MODE_OVERRIDE}" ]]; then :; fi
  if [[ -z "${GAIN}" ]]; then AGC="0"; fi
  load_receiver_settings
  load_runtime_settings
}

load_receiver_settings
load_runtime_settings
run_signal_calibration

mkdir -p "${QUEUE_DIR}" "${TMP_DIR}"

echo "receiver_launcher: source=${SOURCE} receiver=${RECEIVER} mode=${CLIP_MODE} rtl_fm_mode=${RTL_MODE} frequency_hz=${FREQ_HZ} rtl_fm_frequency=${RTL_FREQ} ppm=${PPM_ARGS} gain=${GAIN_LABEL} agc=${AGC} sample_rate=${SAMPLE_RATE} audio_rate=${AUDIO_RATE} threshold=${THRESHOLD} hang_ms=${HANG_MS}"

# shellcheck disable=SC2086
rtl_fm -M "${RTL_MODE}" -f "${RTL_FREQ}" -s "${SAMPLE_RATE}" "${GAIN_ARGS[@]}" ${PPM_ARGS} - | \
  "${PY}" scripts/clip_writer.py \
    --queue "${QUEUE_DIR}" \
    --tmp "${TMP_DIR}" \
    --source "${SOURCE}" \
    --receiver "${RECEIVER}" \
    --frequency-hz "${FREQ_HZ}" \
    --mode "${CLIP_MODE}" \
    --sample-rate "${AUDIO_RATE}" \
    --threshold "${THRESHOLD}" \
    --hang-ms "${HANG_MS}" \
    --min-sec "${MIN_SEC}" \
    --max-sec "${MAX_SEC}" \
    ${VERBOSE}
