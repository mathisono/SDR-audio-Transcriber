#!/usr/bin/env bash
set -euo pipefail

# Launch the GNU Radio Companion visual receiver path plus the SDR audio
# transcription stack.
#
# This keeps GRC in the loop for visual signal acquisition/tuning while the
# existing clip_writer -> transcribe_worker -> web pages pipeline keeps running.
#
# The GRC flowgraph must write mono signed 16-bit PCM to the FIFO created by
# scripts/start_grc_clip_writer.sh. The generated FIFO flowgraphs already do this.

SOURCE="MSE-88"
RECEIVER="rx-grc-1"
FREQUENCY="442.275M"
MODE="nfm"
SAMPLE_RATE=""
THRESHOLD=""
HANG_MS="1800"
FIFO=""
WHISPER_MODEL="small.en"
DEVICE="cpu"
COMPUTE_TYPE="int8"
CLASSIFY_MODES="nfm"
NO_CLEANUP="1"
LMSTUDIO_HOST="192.168.3.38"
LMSTUDIO_PORT="1234"
CLEANUP_MODEL="qwen3508b-transcriber-15k-03"
CLEANUP_MODE="radio-log"
CW_EXTERNAL_COMMAND=""
CW_EXTERNAL_TIMEOUT="30"
WEB_PORT="8090"
WEB_BIND="0.0.0.0"
OPEN_GRC="1"
GENERATE_GRC="1"
VERBOSE=""
TERMINAL_CMD=""

usage() {
  cat <<'EOF'
Usage: scripts/start_grc_transcription_stack.sh [options]

Starts the GRC visual receiver path and the SDR transcription stack:

  1. creates/opens the FIFO clip writer
  2. starts transcribe_worker.py
  3. starts the runtime/transcripts web server
  4. opens GNU Radio Companion with the matching FIFO flowgraph

You still use the GNU Radio GUI to visually acquire/tune the signal and press Run.
The audio output from GRC is sent to the same queue used by the normal pipeline.

Common NFM/repeater test:

  scripts/start_grc_transcription_stack.sh \
    --mode nfm \
    --frequency 442.275M \
    --receiver rx-grc-442 \
    --threshold 80 \
    --verbose

NOAA/NFM test:

  scripts/start_grc_transcription_stack.sh \
    --mode nfm \
    --frequency 162.4M \
    --receiver rx-grc-noaa \
    --threshold 80 \
    --verbose

WBFM test:

  scripts/start_grc_transcription_stack.sh \
    --mode wbfm \
    --frequency 90.7M \
    --receiver rx-grc-wbfm \
    --threshold 60 \
    --classify-modes nfm \
    --verbose

Options:
  --source NAME                 Metadata source label. Default: MSE-88
  --receiver ID                 Metadata receiver label. Default: rx-grc-1
  --frequency FREQ              Metadata/display frequency, e.g. 442.275M
  --mode MODE                   nfm/fm or wbfm/widefm. Default: nfm
  --sample-rate HZ              PCM rate from GRC. Default: 24000 nfm, 48000 wbfm
  --threshold RMS               clip_writer threshold. Default: 80 nfm, 60 wbfm
  --hang-ms MS                  clip_writer hang time. Default: 1800
  --fifo PATH                   FIFO path. Default: runtime/grc_audio.pcm absolute path

  --whisper-model NAME          faster-whisper model. Default: small.en
  --device cpu|cuda             faster-whisper device. Default: cpu
  --compute-type TYPE           faster-whisper compute type. Default: int8
  --classify-modes MODES        Worker classify modes. Default: nfm

  --with-cleanup                Enable LM Studio/Qwen cleanup
  --no-cleanup                  Disable cleanup. Default.
  --lmstudio-host HOST          Default: 192.168.3.38
  --lmstudio-port PORT          Default: 1234
  --cleanup-model NAME          Default: qwen3508b-transcriber-15k-03
  --cleanup-mode MODE           radio-log, conservative, or plain

  --cw-adapter                  Use scripts/morseangel_adapter.py as external CW hook
  --cw-external-command CMD     Custom external CW command for transcribe_worker.py
  --cw-external-timeout SEC     Default: 30

  --web-port PORT               Transcript web server port. Default: 8090
  --web-bind ADDR               Web bind address. Default: 0.0.0.0

  --no-open-grc                 Start backend stack but do not open GNU Radio Companion
  --no-generate-grc             Do not regenerate FIFO GRC files before launch
  --terminal CMD                Terminal emulator command override
                               Examples: xfce4-terminal, gnome-terminal, xterm
  --verbose                     Verbose clip writer
  -h, --help                    Show this help

Notes:
  - This script intentionally opens GRC, not rtl_fm.
  - Start/Run the flowgraph inside GRC after the clip writer window is waiting.
  - The generated FIFO GRC files are:
      grc/shared_baseband_one_channel_fifo_nfm.grc
      grc/shared_baseband_one_channel_fifo_wbfm.grc
EOF
}

normalize_mode() {
  local mode="${1,,}"
  case "${mode}" in
    nfm|fm|narrow|narrowfm|narrowband|narrowbandfm) echo "nfm" ;;
    wbfm|wide|widefm|wideband|widebandfm) echo "wbfm" ;;
    *) echo "unknown mode: $1 (use nfm or wbfm)" >&2; exit 2 ;;
  esac
}

quote_cmd() {
  printf '%q ' "$@"
}

find_terminal() {
  if [[ -n "${TERMINAL_CMD}" ]]; then
    echo "${TERMINAL_CMD}"
    return
  fi
  for candidate in xfce4-terminal gnome-terminal konsole mate-terminal xterm x-terminal-emulator; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      echo "${candidate}"
      return
    fi
  done
  echo ""
}

launch_terminal() {
  local title="$1"
  shift
  local term
  term="$(find_terminal)"
  local cmd
  cmd="$(quote_cmd "$@")"
  cmd="cd $(printf '%q' "${ROOT_DIR}"); ${cmd}; echo; echo '[${title}] exited. Press Enter to close.'; read -r _"

  if [[ -z "${term}" ]]; then
    echo "No terminal emulator found; running ${title} in background. Logs may be mixed in this shell." >&2
    bash -lc "${cmd}" &
    return
  fi

  case "${term}" in
    xfce4-terminal)
      "${term}" --title="${title}" --command="bash -lc ${cmd@Q}" &
      ;;
    gnome-terminal)
      "${term}" --title="${title}" -- bash -lc "${cmd}" &
      ;;
    konsole)
      "${term}" --new-tab --title "${title}" -e bash -lc "${cmd}" &
      ;;
    mate-terminal)
      "${term}" --title="${title}" -- bash -lc "${cmd}" &
      ;;
    xterm)
      "${term}" -T "${title}" -e bash -lc "${cmd}" &
      ;;
    x-terminal-emulator)
      "${term}" -T "${title}" -e bash -lc "${cmd}" &
      ;;
    *)
      "${term}" -e bash -lc "${cmd}" &
      ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --receiver) RECEIVER="$2"; shift 2 ;;
    --frequency) FREQUENCY="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --sample-rate) SAMPLE_RATE="$2"; shift 2 ;;
    --threshold) THRESHOLD="$2"; shift 2 ;;
    --hang-ms) HANG_MS="$2"; shift 2 ;;
    --fifo) FIFO="$2"; shift 2 ;;
    --whisper-model) WHISPER_MODEL="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --compute-type) COMPUTE_TYPE="$2"; shift 2 ;;
    --classify-modes) CLASSIFY_MODES="$2"; shift 2 ;;
    --with-cleanup) NO_CLEANUP="0"; shift ;;
    --no-cleanup) NO_CLEANUP="1"; shift ;;
    --lmstudio-host) LMSTUDIO_HOST="$2"; shift 2 ;;
    --lmstudio-port) LMSTUDIO_PORT="$2"; shift 2 ;;
    --cleanup-model) CLEANUP_MODEL="$2"; shift 2 ;;
    --cleanup-mode) CLEANUP_MODE="$2"; shift 2 ;;
    --cw-adapter) CW_EXTERNAL_COMMAND=".venv/bin/python3 scripts/morseangel_adapter.py --input {wav}"; shift ;;
    --cw-external-command) CW_EXTERNAL_COMMAND="$2"; shift 2 ;;
    --cw-external-timeout) CW_EXTERNAL_TIMEOUT="$2"; shift 2 ;;
    --web-port) WEB_PORT="$2"; shift 2 ;;
    --web-bind) WEB_BIND="$2"; shift 2 ;;
    --no-open-grc) OPEN_GRC="0"; shift ;;
    --no-generate-grc) GENERATE_GRC="0"; shift ;;
    --terminal) TERMINAL_CMD="$2"; shift 2 ;;
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

MODE="$(normalize_mode "${MODE}")"

if [[ -z "${FIFO}" ]]; then
  FIFO="${ROOT_DIR}/runtime/grc_audio.pcm"
elif [[ "${FIFO}" != /* ]]; then
  FIFO="${ROOT_DIR}/${FIFO}"
fi

if [[ -z "${SAMPLE_RATE}" ]]; then
  if [[ "${MODE}" == "nfm" ]]; then SAMPLE_RATE="24000"; else SAMPLE_RATE="48000"; fi
fi

if [[ -z "${THRESHOLD}" ]]; then
  if [[ "${MODE}" == "nfm" ]]; then THRESHOLD="80"; else THRESHOLD="60"; fi
fi

GRC_FILE="${ROOT_DIR}/grc/shared_baseband_one_channel_fifo_${MODE}.grc"
mkdir -p runtime/{queue,tmp,processing,done,failed,transcripts,test}

if [[ "${GENERATE_GRC}" == "1" ]]; then
  echo "launcher: regenerating FIFO GRC files for fifo=${FIFO}"
  "${PY}" scripts/make_shared_baseband_fifo_grc.py --fifo "${FIFO}"
fi

if [[ ! -f "${GRC_FILE}" ]]; then
  echo "missing GRC file: ${GRC_FILE}" >&2
  echo "try: ${PY} scripts/make_shared_baseband_fifo_grc.py --fifo ${FIFO}" >&2
  exit 1
fi

CLIP_CMD=(scripts/start_grc_clip_writer.sh
  --source "${SOURCE}"
  --receiver "${RECEIVER}"
  --frequency "${FREQUENCY}"
  --mode "${MODE}"
  --sample-rate "${SAMPLE_RATE}"
  --threshold "${THRESHOLD}"
  --hang-ms "${HANG_MS}"
  --fifo "${FIFO}"
)
if [[ -n "${VERBOSE}" ]]; then CLIP_CMD+=("${VERBOSE}"); fi

WORKER_CMD=("${PY}" scripts/transcribe_worker.py
  --whisper-model "${WHISPER_MODEL}"
  --device "${DEVICE}"
  --compute-type "${COMPUTE_TYPE}"
  --enable-classifier
  --classify-modes "${CLASSIFY_MODES}"
)
if [[ "${NO_CLEANUP}" == "1" ]]; then
  WORKER_CMD+=(--no-cleanup)
else
  WORKER_CMD+=(--lmstudio-host "${LMSTUDIO_HOST}" --lmstudio-port "${LMSTUDIO_PORT}" --cleanup-model "${CLEANUP_MODEL}" --cleanup-mode "${CLEANUP_MODE}")
fi
if [[ -n "${CW_EXTERNAL_COMMAND}" ]]; then
  WORKER_CMD+=(--cw-external-command "${CW_EXTERNAL_COMMAND}" --cw-external-timeout "${CW_EXTERNAL_TIMEOUT}")
fi

WEB_CMD=(python3 -m http.server "${WEB_PORT}" --bind "${WEB_BIND}" --directory "${ROOT_DIR}/runtime/transcripts")

cat <<EOF
launcher: GRC visual receiver stack
  repo:        ${ROOT_DIR}
  mode:        ${MODE}
  frequency:   ${FREQUENCY}
  source:      ${SOURCE}
  receiver:    ${RECEIVER}
  fifo:        ${FIFO}
  grc file:    ${GRC_FILE}
  web:         http://$(hostname -s 2>/dev/null || echo localhost):${WEB_PORT}/

Starting windows:
  1. GRC FIFO clip writer
  2. transcription worker
  3. transcript web server
  4. GNU Radio Companion GUI

In GNU Radio Companion: visually acquire/tune the signal, then press Run.
EOF

launch_terminal "SDR GRC clip writer" "${CLIP_CMD[@]}"
sleep 1
launch_terminal "SDR transcribe worker" "${WORKER_CMD[@]}"
sleep 1
launch_terminal "SDR transcript web" "${WEB_CMD[@]}"
sleep 1

if [[ "${OPEN_GRC}" == "1" ]]; then
  if command -v gnuradio-companion >/dev/null 2>&1; then
    echo "launcher: opening GNU Radio Companion: ${GRC_FILE}"
    gnuradio-companion "${GRC_FILE}" &
  else
    echo "gnuradio-companion not found. Open this file manually:" >&2
    echo "  ${GRC_FILE}" >&2
  fi
fi

cat <<EOF

launcher: stack launched.

Useful checks:
  watch -n 1 'find runtime/tmp runtime/queue runtime/processing runtime/done -maxdepth 1 -type f | sort | tail -40'
  xdg-open http://localhost:${WEB_PORT}/

EOF
