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

if command -v apt-get >/dev/null 2>&1; then
  echo "Installing Debian/Ubuntu system packages..."
  sudo apt-get update
  sudo apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    rtl-sdr \
    sox \
    ffmpeg \
    libsndfile1

  if apt-cache show gnuradio >/dev/null 2>&1; then
    sudo apt-get install -y gnuradio gr-osmosdr || true
  fi
else
  echo "apt-get not found. Install these packages manually if needed:"
  echo "  python3 python3-venv python3-pip rtl-sdr sox ffmpeg libsndfile gnuradio gr-osmosdr"
fi

echo "Creating runtime folder structure..."
mkdir -p \
  "${ROOT_DIR}/runtime/queue" \
  "${ROOT_DIR}/runtime/tmp" \
  "${ROOT_DIR}/runtime/processing" \
  "${ROOT_DIR}/runtime/done" \
  "${ROOT_DIR}/runtime/failed" \
  "${ROOT_DIR}/runtime/transcripts"

# Keep runtime directories in git without committing captured audio/transcripts.
for dir in queue tmp processing done failed transcripts; do
  touch "${ROOT_DIR}/runtime/${dir}/.gitkeep"
done

echo "Creating Python virtual environment..."
python3 -m venv "${VENV_DIR}"
# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip wheel setuptools
python -m pip install -r "${ROOT_DIR}/requirements.txt"

chmod +x \
  "${ROOT_DIR}/scripts/clip_writer.py" \
  "${ROOT_DIR}/scripts/transcribe_worker.py" \
  "${ROOT_DIR}/scripts/build_transcript_page.py" \
  "${ROOT_DIR}/scripts/audio_fft_ppm_finder_terminal.py"

cat > "${ROOT_DIR}/runtime/transcripts/index.html" <<'HTML'
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>SDR Audio Transcripts</title></head>
<body><h1>SDR Audio Transcripts</h1><p>No transcripts yet.</p></body>
</html>
HTML

cat <<EOF

Install complete.

Activate the venv:
  source .venv/bin/activate

Start a first WBFM recorder test:
  rtl_fm -M wbfm -f 90.7M -s 240k -r 48k -g 25 -p 135 - | \\
    python3 scripts/clip_writer.py --source MSE-88 --frequency 90700000 --receiver receiver1

Start the transcription worker in another terminal:
  source .venv/bin/activate
  python3 scripts/transcribe_worker.py --whisper-model small.en --device cpu --compute-type int8

Serve the web page in another terminal:
  cd runtime/transcripts
  python3 -m http.server 8090

Then open:
  http://localhost:8090/

If LM Studio cleanup is not running, add --no-cleanup to transcribe_worker.py.
EOF
