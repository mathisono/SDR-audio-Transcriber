#!/usr/bin/env bash
set -euo pipefail

# One-command launcher for the GRC visual receiver transcription stack plus a
# separate CW decoder monitor terminal.
#
# This intentionally wraps scripts/start_grc_transcription_stack.sh instead of
# duplicating it. Pass normal GRC stack options through to this script.

CW_MONITOR="1"
CW_MONITOR_ONLY_CW="0"
CW_MONITOR_LAST="5"
CW_MONITOR_DONE="runtime/done"
TERMINAL_CMD=""
PASSTHRU_ARGS=()

usage() {
  cat <<'EOF'
Usage: scripts/start_grc_stack_with_cw_monitor.sh [stack options] [monitor options]

Starts:
  1. GRC FIFO clip writer
  2. transcribe_worker.py
  3. transcript web server
  4. GNU Radio Companion GUI
  5. CW decoder monitor terminal

Most options are passed directly to scripts/start_grc_transcription_stack.sh.

Common use:

  scripts/start_grc_stack_with_cw_monitor.sh \
    --mode nfm \
    --frequency 442.275M \
    --receiver rx-grc-442 \
    --threshold 80 \
    --cw-adapter \
    --verbose

Monitor options:
  --no-cw-monitor              Do not launch the CW monitor terminal
  --cw-monitor-only-cw         Only print clips with CW evidence
  --cw-monitor-last N          Print last N transcript records at startup. Default: 5
  --cw-monitor-done PATH       Done/transcript JSON directory. Default: runtime/done
  --terminal CMD               Terminal emulator override; also passed to stack launcher
  -h, --help                   Show this help

Everything else is passed through to start_grc_transcription_stack.sh.
EOF
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
    --no-cw-monitor)
      CW_MONITOR="0"; shift ;;
    --cw-monitor-only-cw)
      CW_MONITOR_ONLY_CW="1"; shift ;;
    --cw-monitor-last)
      CW_MONITOR_LAST="$2"; shift 2 ;;
    --cw-monitor-done)
      CW_MONITOR_DONE="$2"; shift 2 ;;
    --terminal)
      TERMINAL_CMD="$2"; PASSTHRU_ARGS+=("$1" "$2"); shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      PASSTHRU_ARGS+=("$1")
      shift ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PY="${ROOT_DIR}/.venv/bin/python3"
if [[ ! -x "${PY}" ]]; then
  PY="python3"
fi

# Launch the main stack. It opens its own terminals and returns after launching.
scripts/start_grc_transcription_stack.sh "${PASSTHRU_ARGS[@]}"

if [[ "${CW_MONITOR}" == "1" ]]; then
  MONITOR_CMD=("${PY}" scripts/monitor_cw_decoder.py --done "${CW_MONITOR_DONE}" --last "${CW_MONITOR_LAST}")
  if [[ "${CW_MONITOR_ONLY_CW}" == "1" ]]; then
    MONITOR_CMD+=(--only-cw)
  fi
  sleep 1
  launch_terminal "SDR CW decoder monitor" "${MONITOR_CMD[@]}"
fi

cat <<EOF
cw-monitor launcher: done.

CW monitor command:
  ${PY} scripts/monitor_cw_decoder.py --done ${CW_MONITOR_DONE} --last ${CW_MONITOR_LAST}

EOF
