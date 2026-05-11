# SDR Audio Transcriber

A small SDR audio capture and transcription pipeline for RTL-SDR / GNU Radio experiments.

Current goal:

```text
Osmocom Source
 ├─> QT GUI Frequency Sink
 ├─> QT GUI Waterfall Sink
 └─> Frequency Xlating FIR Filter
      └─> WBFM Receive
           ├─> Audio Sink
           └─> squelch-gated clip writer
                └─> runtime/queue/*.wav
                     └─> faster-whisper transcription worker
                          └─> LM Studio / Qwen cleanup model
                               └─> runtime/transcripts/index.html
```

The GNU Radio flowgraph can stay focused on RF/demodulation while the clip writer, transcription worker, and web page are separate Python processes.

## What is included

- `scripts/clip_writer.py`  
  Reads mono 16-bit PCM audio from stdin, opens a WAV file when RMS rises above the squelch threshold, waits for hang time after audio drops, and moves completed clips into `runtime/queue`.

- `scripts/transcribe_worker.py`  
  Watches `runtime/queue`, moves completed WAV files into `runtime/processing`, runs `faster-whisper`, optionally cleans up the rough text through an OpenAI-compatible model server such as LM Studio, writes transcript JSON, and appends to `runtime/transcripts/index.jsonl`.

- `scripts/build_transcript_page.py`  
  Builds a simple auto-refreshing static web page at `runtime/transcripts/index.html`.

- `scripts/audio_fft_ppm_finder_terminal.py`  
  Existing terminal FFT and coarse PPM helper.

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

## First test using rtl_fm

This bypasses GNU Radio and proves the audio capture/transcription pipeline first.

Terminal 1: record squelch-gated clips from 90.7 MHz WBFM:

```bash
source .venv/bin/activate
rtl_fm -M wbfm -f 90.7M -s 240k -r 48k -g 25 -p 135 - | \
  python3 scripts/clip_writer.py \
    --source MSE-88 \
    --frequency 90700000 \
    --receiver receiver1 \
    --sample-rate 48000 \
    --threshold 650 \
    --hang-ms 1200
```

Terminal 2: transcribe completed clips without LM Studio cleanup:

```bash
source .venv/bin/activate
python3 scripts/transcribe_worker.py \
  --whisper-model small.en \
  --device cpu \
  --compute-type int8 \
  --no-cleanup
```

Terminal 2 alternative: transcribe with LM Studio cleanup on a remote box:

```bash
source .venv/bin/activate
python3 scripts/transcribe_worker.py \
  --whisper-model small.en \
  --device cpu \
  --compute-type int8 \
  --lmstudio-host 192.168.3.28
```

You can also pass the full OpenAI-compatible URL:

```bash
python3 scripts/transcribe_worker.py \
  --whisper-model small.en \
  --device cpu \
  --compute-type int8 \
  --lmstudio-url http://192.168.3.28:1234/v1
```

Terminal 3: serve the transcript web page:

```bash
cd runtime/transcripts
python3 -m http.server 8090
```

Open:

```text
http://localhost:8090/
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
python3 scripts/transcribe_worker.py --lmstudio-host 192.168.3.28
```

If LM Studio is on a non-default port:

```bash
python3 scripts/transcribe_worker.py --lmstudio-host 192.168.3.28 --lmstudio-port 1234
```

Or pass the full OpenAI-compatible base URL:

```bash
python3 scripts/transcribe_worker.py --lmstudio-url http://192.168.3.28:1234/v1
```

If LM Studio is not running yet, disable cleanup:

```bash
python3 scripts/transcribe_worker.py --no-cleanup
```

## Runtime folders

```text
runtime/
  queue/          completed WAV clips waiting for transcription
  tmp/            partial WAV clips while squelch is open
  processing/     files currently owned by the worker
  done/           processed WAV clips and transcript JSON
  failed/         failed clips
  transcripts/    index.jsonl and index.html
```

Only `.gitkeep` placeholders are committed. Audio files and generated transcripts are ignored by Git.

## GNU Radio integration notes

The current GNU Radio flow has this structure:

```text
Osmocom Source
 ├─> QT GUI Frequency Sink
 ├─> QT GUI Waterfall Sink
 └─> Frequency Xlating FIR Filter
      └─> WBFM Receive
           └─> Audio Sink
```

The easiest next integration is to tee demodulated audio into a pipe or UDP stream and feed `clip_writer.py` with mono 48 kHz signed 16-bit PCM.

For example, conceptually:

```text
WBFM Receive
 ├─> Audio Sink
 └─> Float to Short / PCM path
      └─> File Sink or pipe
           └─> scripts/clip_writer.py
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

If it records too much noise, raise `--threshold`. If it clips off the end of speech, raise `--hang-ms`.

## GPU transcription

For NVIDIA CUDA systems, use a larger model and float16:

```bash
python3 scripts/transcribe_worker.py \
  --whisper-model medium.en \
  --device cuda \
  --compute-type float16
```

For small CPU systems, keep:

```bash
python3 scripts/transcribe_worker.py \
  --whisper-model small.en \
  --device cpu \
  --compute-type int8
```
