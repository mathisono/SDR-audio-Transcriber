#!/usr/bin/env bash
set -euo pipefail

# Launch the GRC visual receiver stack using the remembered shared PPM value.
#
# This wrapper regenerates the FIFO-enabled GRC files, applies the shared PPM
# from configs/shared_baseband_radio_server.json into the GRC ppm slider value,
# then launches the existing GRC stack with GRC regeneration disabled.
#
# If you tune the PPM slider in GRC and want to remember that value for next
# time, run:
#
#   .venv/bin/python3 scripts/grc_ppm_config.py \
#     --grc grc/shared_baseband_one_channel_fifo_nfm.grc \
#     remember
#
# or for WBFM:
#
#   .venv/bin/python3 scripts/grc_ppm_config.py \
#     --grc grc/shared_baseband_one_channel_fifo_wbfm.grc \
#     remember

MODE="nfm"
FIFO=""
WITH_CW_MONITOR="1"
PASSTHRU_ARGS=()

usage() {
  cat <<'EOF'
Usage: scripts/start_grc_stack_remembered_ppm.sh [stack options]

Regenerates the FIFO GRC files, applies the remembered shared PPM value, then
launches the GRC stack.

Default launches the CW monitor wrapper too:

  scripts/start_grc_stack_remembered_ppm.sh \
    --mode nfm \
    --frequency 442.275M \
    --receiver rx-grc-442 \
    --threshold 80 \
    --cw-adapter \
    --verbose

Options handled by this wrapper:
  --mode MODE              nfm or wbfm. Also passed to the stack launcher.
  --fifo PATH              FIFO path. Also passed to the stack launcher.
  --no-cw-monitor-wrapper  Use start_grc_transcription_stack.sh instead of
                           start_grc_stack_with_cw_monitor.sh.
  -h, --help               Show this help

All other options are passed through to the selected stack launcher.

Remember a PPM value after editing the GRC slider:

  .venv/bin/python3 scripts/grc_ppm_config.py \
    --grc grc/shared_baseband_one_channel_fifo_nfm.grc \
    remember

Show remembered/applied PPM values:

  .venv/bin/python3 scripts/grc_ppm_config.py show
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

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="$2"; PASSTHRU_ARGS+=("$1" "$2"); shift 2 ;;
    --fifo)
      FIFO="$2"; PASSTHRU_ARGS+=("$1" "$2"); shift 2 ;;
    --no-cw-monitor-wrapper)
      WITH_CW_MONITOR="0"; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      PASSTHRU_ARGS+=("$1"); shift ;;
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

mkdir -p runtime/{queue,tmp,processing,done,failed,transcripts,test}

echo "remembered-ppm launcher: regenerating FIFO GRC files"
"${PY}" scripts/make_shared_baseband_fifo_grc.py --fifo "${FIFO}"

echo "remembered-ppm launcher: applying shared PPM into GRC files"
"${PY}" scripts/grc_ppm_config.py \
  --grc grc/shared_baseband_one_channel.grc \
  --grc grc/shared_baseband_one_channel_fifo_nfm.grc \
  --grc grc/shared_baseband_one_channel_fifo_wbfm.grc \
  apply

echo "remembered-ppm launcher: current PPM state"
"${PY}" scripts/grc_ppm_config.py \
  --grc "grc/shared_baseband_one_channel_fifo_${MODE}.grc" \
  show

if [[ "${WITH_CW_MONITOR}" == "1" ]]; then
  exec scripts/start_grc_stack_with_cw_monitor.sh --no-generate-grc "${PASSTHRU_ARGS[@]}"
else
  exec scripts/start_grc_transcription_stack.sh --no-generate-grc "${PASSTHRU_ARGS[@]}"
fi
