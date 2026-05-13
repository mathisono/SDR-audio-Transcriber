# SDR Audio Transcriber

A small SDR audio capture and transcription pipeline for RTL-SDR / GNU Radio experiments.

Current goal:

```text
Osmocom Source / rtl_fm receiver
 ├─> frequency/mode controlled from config or launcher args
 └─> squelch-gated clip writer
      └─> runtime/queue/*.wav
           └─> faster-whisper transcription worker
                └─> optional LM Studio / Qwen cleanup model
                     └─> runtime/transcripts/*.html
```

The RF receiver, clip writer, transcription worker, classifier, and web page are separate pieces. The preferred test path is now `scripts/start_rtl_fm_receiver.sh` because it keeps the tuned frequency, FM mode, WAV filename, and sidecar JSON metadata synchronized.

## What is included

- `scripts/start_rtl_fm_receiver.sh`  
  Starts `rtl_fm` and `clip_writer.py` together. Reads receiver frequency/mode from config or accepts one-off command-line overrides like `--frequency 442.275M --mode nfm`.

- `scripts/receiver_config.py`  
  Shows or updates receiver frequency and mode in `configs/shared_baseband_radio_server.json`.

- `scripts/clip_writer.py`  
  Reads mono 16-bit PCM from stdin, opens a WAV file when RMS rises above the squelch threshold, waits for hang time after audio drops, and moves completed clips into `runtime/queue`. Filenames include source, receiver, tuned frequency, mode, and process ID.

- `scripts/transcribe_worker.py`  
  Watches `runtime/queue`, moves completed WAV files into `runtime/processing`, runs `faster-whisper`, optionally cleans up the rough text through LM Studio/Qwen, optionally classifies CW/tone/spoken callsign evidence, writes transcript JSON, and appends to `runtime/transcripts/index.jsonl`.

- `scripts/clip_classifier.py`  
  Optional first-pass detector for CW/Morse IDs, tone ID frequency, keyed tone candidates, and spoken callsign label candidates.

- `scripts/build_transcript_page.py`  
  Builds static web pages: `index.html`, `raw.html`, `processed.html`, and `classification.html`.

- `scripts/ppm_config.py`  
  Shows or sets the shared SDR source PPM correction.

- `scripts/audio_fft_ppm_finder_terminal.py`  
  Existing terminal FFT and coarse PPM finder helper.

- `install.sh`  
  Installs common Debian/Ubuntu packages, creates a Python virtual environment, installs Python dependencies, and creates the runtime folder structure.

## Install

On Debian/Ubuntu/Raspberry Pi OS style systems:

```bash
./install.sh
```

Then activate the Python environment:

```bash
source .venv/bin/activate
```

If you are not on an apt-based system, install these manually:

```text
python3 python3-venv python3-pip rtl-sdr sox ffmpeg libsndfile gnuradio gr-osmosdr
```

Then run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
mkdir -p runtime/{queue,tmp,processing,done,failed,transcripts}
```

## Shared PPM correction

PPM correction is a single source-level setting. Set it once here:

```text
configs/shared_baseband_radio_server.json -> source.ppm_correction
```

Show the current value:

```bash
.venv/bin/python3 scripts/ppm_config.py show
```

Set the value once:

```bash
.venv/bin/python3 scripts/ppm_config.py set 135
```

Generate `rtl_fm` arguments from config:

```bash
.venv/bin/python3 scripts/ppm_config.py rtl-fm-args
```

The receiver launcher uses this automatically.

The existing coarse finder can also write the shared value directly:

```bash
.venv/bin/python3 scripts/audio_fft_ppm_finder_terminal.py ppm \
  --frequency 90700000 \
  --write-config
```

## Receiver frequency and FM mode

Receiver tuning lives in:

```text
configs/shared_baseband_radio_server.json -> receivers[].frequency_hz
configs/shared_baseband_radio_server.json -> receivers[].mode
```

The preferred launcher is:

```bash
scripts/start_rtl_fm_receiver.sh
```

It uses the same active frequency and mode for both:

```text
rtl_fm -f / -M
clip_writer.py --frequency-hz / --mode
```

That keeps WAV filenames and JSON metadata correct.

### One-off wideband FM

Use wideband FM for broadcast FM or other wide FM sources:

```bash
scripts/start_rtl_fm_receiver.sh \
  --receiver rx-1 \
  --source MSE-88 \
  --mode wbfm \
  --frequency 90.7M \
  --verbose
```

Accepted wideband names:

```text
wbfm
widebandfm
widefm
wide
```

Internally this runs `rtl_fm -M wbfm`, and the clip metadata/file name uses `wbfm`.

### One-off narrowband FM

Use narrowband FM for typical ham/public-service voice channels and repeaters:

```bash
scripts/start_rtl_fm_receiver.sh \
  --receiver rx-1 \
  --source MSE-88 \
  --mode nfm \
  --frequency 442.275M \
  --threshold 450 \
  --verbose
```

Accepted narrowband names:

```text
nfm
narrowbandfm
narrowfm
narrow
fm
```

For `rtl_fm`, narrowband FM maps to:

```text
-M fm
```

The clip metadata/file name uses:

```text
nfm
```

Example output filename:

```text
2026-05-13_153012.428Z__MSE-88__rx-1__442.275000MHz__nfm__pid1234.wav
```

### Persist frequency and mode in config

Set receiver frequency:

```bash
.venv/bin/python3 scripts/receiver_config.py --receiver rx-1 set-frequency 442.275M
```

Set receiver to narrowband FM:

```bash
.venv/bin/python3 scripts/receiver_config.py --receiver rx-1 set-mode nfm
```

Set receiver to wideband FM:

```bash
.venv/bin/python3 scripts/receiver_config.py --receiver rx-1 set-mode wbfm
```

Show current receiver settings:

```bash
.venv/bin/python3 scripts/receiver_config.py --receiver rx-1 show
```

Start using config defaults:

```bash
scripts/start_rtl_fm_receiver.sh --receiver rx-1 --source MSE-88 --verbose
```

### Frequency format examples

All of these are valid:

```text
442.275M
442.275MHz
442275000
90.7M
90700000
```

## First test using rtl_fm

This bypasses GNU Radio and proves the audio capture/transcription pipeline first.

Terminal 1: start the receiver and squelch-gated clip writer.

For a narrowband repeater:

```bash
cd /home/mat/SDR-audio-Transcriber
scripts/start_rtl_fm_receiver.sh \
  --receiver rx-1 \
  --source MSE-88 \
  --mode nfm \
  --frequency 442.275M \
  --threshold 450 \
  --verbose
```

For broadcast FM testing:

```bash
cd /home/mat/SDR-audio-Transcriber
scripts/start_rtl_fm_receiver.sh \
  --receiver rx-1 \
  --source MSE-88 \
  --mode wbfm \
  --frequency 90.7M \
  --verbose
```

Terminal 2: transcribe completed clips without LM Studio cleanup:

```bash
cd /home/mat/SDR-audio-Transcriber
.venv/bin/python3 scripts/transcribe_worker.py \
  --whisper-model small.en \
  --device cpu \
  --compute-type int8 \
  --no-cleanup \
  --enable-classifier
```

Terminal 2 alternative: transcribe with LM Studio/Qwen cleanup on a remote box:

```bash
cd /home/mat/SDR-audio-Transcriber
.venv/bin/python3 scripts/transcribe_worker.py \
  --whisper-model small.en \
  --device cpu \
  --compute-type int8 \
  --lmstudio-host 192.168.3.28 \
  --lmstudio-port 1234 \
  --enable-classifier
```

You can also pass the full OpenAI-compatible URL:

```bash
.venv/bin/python3 scripts/transcribe_worker.py \
  --whisper-model small.en \
  --device cpu \
  --compute-type int8 \
  --lmstudio-url http://192.168.3.28:1234/v1 \
  --enable-classifier
```

Terminal 3: serve the transcript web page:

```bash
cd /home/mat/SDR-audio-Transcriber/runtime/transcripts
python3 -m http.server 8090 --bind 0.0.0.0
```

Open:

```text
http://MSE-88:8090/
http://MSE-88:8090/raw.html
http://MSE-88:8090/processed.html
http://MSE-88:8090/classification.html
```

## LM Studio cleanup model

The worker uses Whisper/faster-whisper for actual audio-to-text. The configured cleanup model is used after Whisper to improve readability without inventing missing words.

Default cleanup model:

```text
bingbangboom/Qwen3508B-transcriber-15k-03
```

Default endpoint if no host or URL is provided:

```text
http://127.0.0.1:1234/v1
```

For LM Studio running on another machine, pass just the host/IP:

```bash
.venv/bin/python3 scripts/transcribe_worker.py --lmstudio-host 192.168.3.28 --enable-classifier
```

If LM Studio is on a non-default port:

```bash
.venv/bin/python3 scripts/transcribe_worker.py --lmstudio-host 192.168.3.28 --lmstudio-port 1234 --enable-classifier
```

If LM Studio is not running yet, disable cleanup:

```bash
.venv/bin/python3 scripts/transcribe_worker.py --no-cleanup --enable-classifier
```

## Runtime folders

```text
runtime/
  queue/                     completed WAV clips waiting for transcription
  tmp/                       partial WAV clips while squelch is open
  processing/                files currently owned by the worker
  done/                      processed WAV clips and transcript JSON
  failed/                    failed clips
  transcripts/               index.jsonl and static HTML pages
  classification_state.json  persistent evidence state for label promotion
```

Only `.gitkeep` placeholders are committed. Audio files, generated transcripts, and runtime classification state are ignored by Git.

## GNU Radio integration notes

The current GNU Radio flow has this structure:

```text
Osmocom Source
 ├─> QT GUI Frequency Sink
 ├─> QT GUI Waterfall Sink
 └─> Frequency Xlating FIR Filter
      └─> WBFM or NFM receive path
           └─> Audio Sink
```

The easiest integration is to tee demodulated audio into a pipe or UDP stream and feed `clip_writer.py` with mono 48 kHz signed 16-bit PCM. Make sure the GNU Radio/control layer passes the actual tuned frequency and mode to `clip_writer.py`, or use the launcher approach as the reference pattern.

For example, conceptually:

```text
FM Receive
 ├─> Audio Sink
 └─> Float to Short / PCM path
      └─> File Sink or pipe
           └─> scripts/clip_writer.py --frequency-hz <active frequency> --mode <wbfm|nfm>
```

Do not send partially written files directly to the worker. The clip writer writes `*.wav.part` in `runtime/tmp` and only renames a completed file into `runtime/queue` after squelch closes.

## Tuning knobs

Important recorder settings:

```bash
--threshold 650       # RMS level that opens squelch
--hang-ms 1200        # keep recording this long after audio drops
--min-sec 1.0         # drop tiny false openings
--max-sec 60.0        # force-close long clips
```

If it records too much noise, raise `--threshold`. If it misses quiet audio, lower `--threshold`. If it clips off the end of speech, raise `--hang-ms`.

## GPU transcription

For NVIDIA CUDA systems, use a larger model and float16:

```bash
.venv/bin/python3 scripts/transcribe_worker.py \
  --whisper-model medium.en \
  --device cuda \
  --compute-type float16 \
  --enable-classifier
```

For small CPU systems, keep:

```bash
.venv/bin/python3 scripts/transcribe_worker.py \
  --whisper-model small.en \
  --device cpu \
  --compute-type int8 \
  --enable-classifier
```
