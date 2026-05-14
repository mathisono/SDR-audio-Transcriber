#!/usr/bin/env bash
set -euo pipefail

# Start clip_writer.py on a FIFO that a GNU Radio Companion flowgraph writes to.
# This lets GRC handle signal acquisition/visual verification while the existing
# recorder/transcriber pipeline receives the same mono s16le PCM stream.

SOURCE="MSE-88"
RECEIVER="rx-grc-1"
FREQUENCY="442.275M"
MODE="nfm"
SAMPLE_RATE=""
THRESHOLD=""
HANG_MS="1800"
QUEUE="runtime/queue"
TMP="runtime/tmp"
FIFO=""
VERBOSE=""

usage() {
  cat <<'EOF'
Usage: scripts/start_grc_clip_writer.sh [options]

Starts clip_writer.py reading from a FIFO. Open the matching GRC flowgraph and
make its File Sink write mono signed 16-bit PCM to the same FIFO path.

The default FIFO is the repo-absolute path:
  /path/to/SDR-audio-Transcriber/runtime/grc_audio.pcm

That matches the generated GRC files from:
  .venv/bin/python3 scripts/make_shared_baseband_fifo_grc.py

Options:
  --source NAME         Metadata source label. Default: MSE-88
  --receiver ID         Metadata receiver label. Default: rx-grc-1
  --frequency FREQ      Frequency metadata, e.g. 442.275M, 162.4M, or 90.7M
  --mode MODE           Metadata mode: nfm, fm, wbfm, widefm
  --sample-rate HZ      PCM sample rate from GRC. Must match GRC output rate.
                        Default: 24000 for nfm/fm, 48000 for wbfm/widefm.
  --threshold RMS       clip_writer RMS threshold.
                        Default: 80 for nfm/fm, 60 for wbfm/widefm.
  --hang-ms MS          clip_writer hang time. Default: 1800
  --fifo PATH           FIFO path. Default: repo/runtime/grc_audio.pcm absolute path
  --queue PATH          Queue directory. Default: runtime/queue
  --tmp PATH            Temp directory. Default: runtime/tmp
  --verbose             Pass --verbose to clip_writer
  -h, --help            Show this help

Typical NFM test:
  scripts/start_grc_clip_writer.sh \
    --receiver rx-grc-nfm --source MSE-88 --mode nfm --frequency 162.4M \
    --threshold 80 --hang-ms 1800 --verbose

Typical WBFM test:
  scripts/start_grc_clip_writer.sh \
    --receiver rx-grc-wbfm --source MSE-88 --mode wbfm --frequency 90.7M \
    --threshold 60 --hang-ms 1800 --verbose

Then start/open the matching generated GRC flowgraph and press Run.
EOF
}

parse_frequency_hz() {
  local freq="$1"
  python3 - "$freq" <<'PY'
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
    --source) SOURCE="$2"; shift 2 ;;
    --receiver) RECEIVER="$2"; shift 2 ;;
    --frequency) FREQUENCY="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --sample-rate) SAMPLE_RATE="$2"; shift 2 ;;
    --threshold) THRESHOLD="$2"; shift 2 ;;
    --hang-ms) HANG_MS="$2"; shift 2 ;;
    --fifo) FIFO="$2"; shift 2 ;;
    --queue) QUEUE="$2"; shift 2 ;;
    --tmp) TMP="$2"; shift 2 ;;
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

if [[ "${QUEUE}" != /* ]]; then
  QUEUE="${ROOT_DIR}/${QUEUE}"
fi
if [[ "${TMP}" != /* ]]; then
  TMP="${ROOT_DIR}/${TMP}"
fi

if [[ -z "${SAMPLE_RATE}" ]]; then
  if [[ "${MODE}" == "nfm" ]]; then
    SAMPLE_RATE="24000"
  else
    SAMPLE_RATE="48000"
  fi
fi

if [[ -z "${THRESHOLD}" ]]; then
  if [[ "${MODE}" == "nfm" ]]; then
    THRESHOLD="80"
  else
    THRESHOLD="60"
  fi
fi

FREQUENCY_HZ="$(parse_frequency_hz "${FREQUENCY}")"
mkdir -p "${QUEUE}" "${TMP}" "$(dirname "${FIFO}")"
rm -f "${FIFO}"
mkfifo "${FIFO}"

cleanup() {
  rm -f "${FIFO}"
}
trap cleanup EXIT

echo "grc_clip_writer: waiting on fifo=${FIFO} source=${SOURCE} receiver=${RECEIVER} mode=${MODE} frequency_hz=${FREQUENCY_HZ} sample_rate=${SAMPLE_RATE} threshold=${THRESHOLD} hang_ms=${HANG_MS} queue=${QUEUE} tmp=${TMP}"

cat "${FIFO}" | "${PY}" scripts/clip_writer.py \
  --queue "${QUEUE}" \
  --tmp "${TMP}" \
  --source "${SOURCE}" \
  --receiver "${RECEIVER}" \
  --frequency-hz "${FREQUENCY_HZ}" \
  --mode "${MODE}" \
  --sample-rate "${SAMPLE_RATE}" \
  --threshold "${THRESHOLD}" \
  --hang-ms "${HANG_MS}" \
  ${VERBOSE}
