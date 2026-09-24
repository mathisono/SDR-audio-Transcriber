#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

printf '\nSDR Audio Transcriber installer\n'
printf 'Repository: %s\n\n' "${ROOT_DIR}"

if [[ "${EUID}" -eq 0 ]]; then
  echo "Do not run this installer with sudo. It will ask for sudo only for apt packages."
  exit 1
fi

# The pinned faster-whisper/PyAV runtime is intentionally unchanged. Select an
# explicit supported interpreter instead of applying Python 3.8 pins to 3.13.
PYTHON="${PYTHON:-python3}"
"${PYTHON}" - <<'PY'
import sys
if not (3, 8) <= sys.version_info[:2] < (3, 12):
    raise SystemExit('Pinned ASR dependencies require Python 3.8-3.11. Use PYTHON=python3.11 bash install.sh with that interpreter installed.')
PY

if command -v apt-get >/dev/null 2>&1; then
  echo "Installing Debian/Ubuntu system packages..."
  sudo apt-get update
  sudo apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    build-essential \
    pkg-config \
    rtl-sdr \
    sox \
    ffmpeg \
    libsndfile1 \
    libavformat-dev \
    libavcodec-dev \
    libavdevice-dev \
    libavutil-dev \
    libavfilter-dev \
    libswscale-dev \
    libswresample-dev

  if apt-cache show gnuradio >/dev/null 2>&1; then
    sudo apt-get install -y gnuradio gr-osmosdr || true
  fi
else
  echo "apt-get not found. Install these packages manually if needed:"
  echo "  python3 python3-venv python3-pip python3-dev build-essential pkg-config rtl-sdr sox ffmpeg libsndfile gnuradio gr-osmosdr"
fi

echo "Creating runtime folder structure..."
mkdir -p \
  "${ROOT_DIR}/runtime/queue" \
  "${ROOT_DIR}/runtime/tmp" \
  "${ROOT_DIR}/runtime/processing" \
  "${ROOT_DIR}/runtime/done" \
  "${ROOT_DIR}/runtime/failed" \
  "${ROOT_DIR}/runtime/transcripts"

for dir in queue tmp processing done failed transcripts; do
  touch "${ROOT_DIR}/runtime/${dir}/.gitkeep"
done

echo "Creating Python virtual environment..."
"${PYTHON}" -m venv "${VENV_DIR}"
source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip wheel setuptools
python -m pip install "Cython==0.29.37"
# Modern pip isolates build constraints; older Python/pip still uses PIP_CONSTRAINT.
PIP_BUILD_CONSTRAINT="${ROOT_DIR}/constraints-python38.txt" \
PIP_CONSTRAINT="${ROOT_DIR}/constraints-python38.txt" python -m pip install -r "${ROOT_DIR}/requirements.txt"

chmod +x \
  "${ROOT_DIR}/scripts/clip_writer.py" \
  "${ROOT_DIR}/scripts/transcribe_worker.py" \
  "${ROOT_DIR}/scripts/enrichment_worker.py" \
  "${ROOT_DIR}/scripts/start_rtl_fm_receiver.sh" \
  "${ROOT_DIR}/scripts/verify_asr.py" \
  "${ROOT_DIR}/scripts/build_transcript_page.py" \
  "${ROOT_DIR}/scripts/audio_fft_ppm_finder_terminal.py" \
  "${ROOT_DIR}/scripts/ppm_config.py"

# Reinstallation must not replace a working transcript dashboard.
if [[ ! -e "${ROOT_DIR}/runtime/transcripts/index.html" ]]; then
  cat > "${ROOT_DIR}/runtime/transcripts/index.html" <<'HTML'
<!doctype html>
<html lang="en"><meta charset="utf-8"><title>SDR Audio Transcripts</title>
<h1>SDR Audio Transcripts</h1><p>No transcripts yet.</p></html>
HTML
fi

cat <<'EOF'

Install complete. From the repository root:

  source .venv/bin/activate
  bash scripts/start_rtl_fm_receiver.sh --mode nfm --frequency 162.4M --no-calibrate --verbose

Speech-only worker (another terminal):

  python3 scripts/transcribe_worker.py --whisper-model small.en --device cpu --compute-type int8 --no-cleanup

Optional CW: add --enable-classifier to that worker and start another process:

  python3 scripts/enrichment_worker.py

Cleanup is now opt-in: add --enable-cleanup, omit --no-cleanup, and run the same
sidecar. --lmstudio-host/--lmstudio-url still configure its endpoint.

Serve locally:

  cd runtime/transcripts
  python3 -m http.server 8090 --bind 127.0.0.1

See README.md and docs/speech-first-reliability.md for validation and migration.
EOF
