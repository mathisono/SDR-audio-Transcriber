#!/usr/bin/env bash
set -euo pipefail

# Build a structured Qwen-formatted monitoring report from transcript history.
# Override defaults with environment variables, for example:
#   LMSTUDIO_HOST=192.168.3.38 LIMIT=200 MAX_CHARS=30000 scripts/build_monitoring_report.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PY="${ROOT_DIR}/.venv/bin/python3"
if [[ ! -x "${PY}" ]]; then
  PY="python3"
fi

LMSTUDIO_HOST="${LMSTUDIO_HOST:-192.168.3.38}"
LMSTUDIO_PORT="${LMSTUDIO_PORT:-1234}"
MODEL="${MODEL:-qwen3508b-transcriber-15k-03}"
LIMIT="${LIMIT:-100}"
MAX_CHARS="${MAX_CHARS:-20000}"
SOURCE_TEXT="${SOURCE_TEXT:-best}"
TITLE="${TITLE:-MSE-88 442.275 Monitoring Report}"

"${PY}" scripts/reprocess_history.py \
  --limit "${LIMIT}" \
  --max-chars "${MAX_CHARS}" \
  --source-text "${SOURCE_TEXT}" \
  --format monitoring-report \
  --title "${TITLE}" \
  --lmstudio-host "${LMSTUDIO_HOST}" \
  --lmstudio-port "${LMSTUDIO_PORT}" \
  --model "${MODEL}"

echo "open: http://MSE-88:8090/formatted.html"
