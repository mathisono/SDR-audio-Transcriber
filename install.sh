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
  "${ROOT_DIR}/scripts/audio_fft_ppm_finder_terminal.py" \
  "${ROOT_DIR}/scripts/ppm_config.py"

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

Show the shared SDR source PPM correction:
  python3 scripts/ppm_config.py show

Set the shared SDR source PPM correction once:
  python3 scripts/ppm_config.py set 135

Start a first WBFM recorder test using the configured PPM value:
  PPM_ARGS="\$(python3 scripts/ppm_config.py rtl-fm-args)"
  rtl_fm -M wbfm -f 90.7M -s 240k -r 48k -g 25 \${PPM_ARGS} - | \\
    python3 scripts/clip_writer.py --source MSE-88 --frequency 90700000 --receiver receiver1

Start the transcription worker without LM Studio cleanup:
  source .venv/bin/activate
  python3 scripts/transcribe_worker.py --whisper-model small.en --device cpu --compute-type int8 --no-cleanup

Start the transcription worker with LM Studio cleanup on another box:
  source .venv/bin/activate
  python3 scripts/transcribe_worker.py --whisper-model small.en --device cpu --compute-type int8 --lmstudio-host 192.168.3.28

You can also pass a full OpenAI-compatible URL:
  python3 scripts/transcribe_worker.py --lmstudio-url http://192.168.3.28:1234/v1

Serve the web page in another terminal:
  cd runtime/transcripts
  python3 -m http.server 8090

Then open:
  http://localhost:8090/
EOF
