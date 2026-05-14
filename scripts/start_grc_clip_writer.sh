#!/usr/bin/env bash
set -euo pipefail

# Start clip_writer.py on a FIFO that a GNU Radio Companion flowgraph writes to.
# This lets GRC handle signal acquisition/visual verification while the existing
# recorder/transcriber pipeline receives the same mono s16le PCM stream.

SOURCE="MSE-88"
RECEIVER="rx-grc-1"
FREQUENCY="442.275M"
MODE="nfm"
SAMPLE_RATE="24000"
THRESHOLD="60"
HANG_MS="1800"
QUEUE="runtime/queue"
TMP="runtime/tmp"
FIFO="runtime/grc_audio.pcm"
VERBOSE=""

usage() {
  cat <<'EOF'
Usage: scripts/start_grc_clip_writer.sh [options]

Starts clip_writer.py reading from a FIFO. Open the matching GRC flowgraph and
make its File Sink write mono signed 16-bit PCM to the same FIFO path.

Options:
  --source NAME         Metadata source label. Default: MSE-88
  --receiver ID         Metadata receiver label. Default: rx-grc-1
  --frequency FREQ      Frequency metadata, e.g. 442.275M or 162.4M
  --mode MODE           Metadata mode: nfm or wbfm
  --sample-rate HZ      PCM sample rate from GRC. Must match GRC output rate.
  --threshold RMS       clip_writer RMS threshold
  --hang-ms MS          clip_writer hang time
  --fifo PATH           FIFO path. Default: runtime/grc_audio.pcm
  --queue PATH          Queue directory. Default: runtime/queue
  --tmp PATH            Temp directory. Default: runtime/tmp
  --verbose             Pass --verbose to clip_writer
  -h, --help            Show this help

Typical NFM test:
  scripts/start_grc_clip_writer.sh \
    --receiver rx-grc-1 --source MSE-88 --mode nfm --frequency 442.275M \
    --sample-rate 24000 --threshold 60 --hang-ms 1800 --verbose

Then start/open the GRC flowgraph and press Run.
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

FREQUENCY_HZ="$(parse_frequency_hz "${FREQUENCY}")"
mkdir -p "${QUEUE}" "${TMP}" "$(dirname "${FIFO}")"
rm -f "${FIFO}"
mkfifo "${FIFO}"

cleanup() {
  rm -f "${FIFO}"
}
trap cleanup EXIT

echo "grc_clip_writer: waiting on fifo=${FIFO} source=${SOURCE} receiver=${RECEIVER} mode=${MODE} frequency_hz=${FREQUENCY_HZ} sample_rate=${SAMPLE_RATE} threshold=${THRESHOLD} hang_ms=${HANG_MS}"

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
