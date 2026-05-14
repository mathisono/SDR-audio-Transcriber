# SDR Audio Transcriber

A small SDR audio capture, transcription, and radio-labeling pipeline for RTL-SDR / GNU Radio experiments.

Primary working path:

```text
rtl_fm receiver
 └─> scripts/clip_writer.py
      └─> runtime/queue/*.wav
           └─> scripts/transcribe_worker.py
                ├─> faster-whisper raw transcript
                ├─> optional LM Studio / Qwen cleanup
                ├─> optional CW / tone / spoken-callsign classifier
                └─> runtime/transcripts/*.html
```

The preferred production/test ingest path is now:

```bash
scripts/start_rtl_fm_receiver.sh
```

GNU Radio Companion is still useful for visual signal inspection, but the `rtl_fm` launcher is the most reliable ingest path because it keeps tuned frequency, FM mode, gain/AGC, PPM, WAV filename, and sidecar JSON metadata synchronized.

---

## Current operating model

Use separate terminals.

### Terminal 1 — receiver / clip writer

NFM / repeater / NOAA example:

```bash
cd /home/mat/SDR-audio-Transcriber

scripts/start_rtl_fm_receiver.sh \
  --receiver rx-1 \
  --source MSE-88 \
  --mode fm \
  --frequency 162.4M \
  --gain 42 \
  --threshold 8000 \
  --hang-ms 1800 \
  --sample-rate 48000 \
  --audio-rate 48000 \
  --verbose
```

442.275 MHz NFM example:

```bash
scripts/start_rtl_fm_receiver.sh \
  --receiver rx-1 \
  --source MSE-88 \
  --mode fm \
  --frequency 442.275M \
  --gain 42 \
  --threshold 60 \
  --hang-ms 1800 \
  --sample-rate 48000 \
  --audio-rate 48000 \
  --verbose
```

WBFM broadcast example:

```bash
scripts/start_rtl_fm_receiver.sh \
  --receiver rx-1 \
  --source MSE-88 \
  --mode wbfm \
  --frequency 90.7M \
  --gain 42 \
  --threshold 60 \
  --hang-ms 1800 \
  --verbose
```

When `clip_writer.py` prints something like this:

```text
clip_writer: rms=7554 active=True recording=True
```

that means the audio is above threshold and a clip is being written in `runtime/tmp`. When the signal drops below threshold for `--hang-ms`, the completed WAV is moved to `runtime/queue` for transcription.

Monitor file movement:

```bash
watch -n 1 'find runtime/tmp runtime/queue runtime/processing runtime/done -maxdepth 1 -type f | sort | tail -40'
```

### Terminal 2 — transcription worker

Raw Whisper + classifier only:

```bash
.venv/bin/python3 scripts/transcribe_worker.py \
  --whisper-model small.en \
  --device cpu \
  --compute-type int8 \
  --no-cleanup \
  --enable-classifier \
  --classify-modes nfm
```

With LM Studio / Qwen cleanup:

```bash
.venv/bin/python3 scripts/transcribe_worker.py \
  --whisper-model small.en \
  --device cpu \
  --compute-type int8 \
  --lmstudio-host 192.168.3.38 \
  --lmstudio-port 1234 \
  --cleanup-model "qwen3508b-transcriber-15k-03" \
  --cleanup-mode radio-log \
  --enable-classifier \
  --classify-modes nfm
```

If LM Studio is not reachable, use `--no-cleanup`. A cleanup error like this means the worker tried to use the default local LM Studio endpoint and nothing was listening there:

```text
HTTPConnectionPool(host='127.0.0.1', port=1234): Connection refused
```

Use `--lmstudio-host 192.168.3.38` or `--lmstudio-url http://192.168.3.38:1234/v1` when LM Studio runs on another machine.

### Terminal 3 — web server

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
http://MSE-88:8090/formatted.html
```

---

## Signal calibration / PPM / gain / AGC

`start_rtl_fm_receiver.sh` can optionally run a preflight signal check before starting `rtl_fm`.

The calibration uses:

```text
scripts/rtl_signal_check.py
```

which calls `rtl_power`, scans around the target frequency, and reports:

```text
peak frequency
frequency offset
estimated PPM error
peak level
noise floor
SNR estimate
gain / AGC recommendation
PPM recommendation
```

### Normal prompt mode

By default, the launcher asks whether to run calibration:

```bash
scripts/start_rtl_fm_receiver.sh \
  --receiver rx-1 \
  --source MSE-88 \
  --mode fm \
  --frequency 162.4M \
  --gain 42 \
  --threshold 8000 \
  --verbose
```

Prompt:

```text
Run signal calibration now? [y/N]
```

If you answer `y`, it runs `rtl_signal_check.py`, prints progress and results, updates safe config values, reloads config, then starts `rtl_fm`.

### Always calibrate

```bash
scripts/start_rtl_fm_receiver.sh \
  --receiver rx-1 \
  --source MSE-88 \
  --mode fm \
  --frequency 162.4M \
  --gain 42 \
  --threshold 8000 \
  --calibrate \
  --verbose
```

### Skip calibration

```bash
scripts/start_rtl_fm_receiver.sh \
  --receiver rx-1 \
  --source MSE-88 \
  --mode fm \
  --frequency 162.4M \
  --gain 42 \
  --threshold 8000 \
  --no-calibrate \
  --verbose
```

### Calibration duration and resolution

```bash
--cal-duration 20s
--cal-span-khz 200
--cal-bin-hz 1000
```

Example:

```bash
scripts/start_rtl_fm_receiver.sh \
  --receiver rx-1 \
  --mode fm \
  --frequency 162.4M \
  --gain 42 \
  --threshold 8000 \
  --calibrate \
  --cal-duration 20s \
  --cal-span-khz 200 \
  --cal-bin-hz 1000 \
  --verbose
```

### Standalone signal check

You can run the checker without starting the receiver:

```bash
.venv/bin/python3 scripts/rtl_signal_check.py \
  --frequency 162.4M \
  --gain 42 \
  --ppm -41 \
  --span-khz 200 \
  --duration 10s
```

To allow it to update the shared config:

```bash
.venv/bin/python3 scripts/rtl_signal_check.py \
  --frequency 162.4M \
  --mode nfm \
  --receiver rx-1 \
  --gain 42 \
  --ppm -41 \
  --span-khz 200 \
  --duration 10s \
  --write-config
```

### PPM safety guard

The signal checker will **not** write unsafe PPM corrections unless `--force-ppm` is used.

Blocked by default:

```text
PPM delta greater than 25 ppm
absolute PPM greater than 150 ppm
SNR below 12 dB
```

This protects against bad results like:

```text
Tuner error set to -494 ppm
```

A value that large usually means the scan locked onto the wrong peak, a DC spike, or a nearby signal. Reset PPM manually if needed:

```bash
.venv/bin/python3 scripts/ppm_config.py set -41
.venv/bin/python3 scripts/ppm_config.py show
```

Only use `--force-ppm` if you are calibrating against a known reference carrier and you are sure the detected peak is correct.

---

## PPM configuration

Shared PPM lives here:

```text
configs/shared_baseband_radio_server.json -> source.ppm_correction
```

Show current value:

```bash
.venv/bin/python3 scripts/ppm_config.py show
```

Set value:

```bash
.venv/bin/python3 scripts/ppm_config.py set -41
```

Generate `rtl_fm` arguments:

```bash
.venv/bin/python3 scripts/ppm_config.py rtl-fm-args
```

The receiver launcher uses this automatically.

---

## Gain and AGC

Manual gain:

```bash
scripts/start_rtl_fm_receiver.sh \
  --mode fm \
  --frequency 162.4M \
  --gain 42 \
  --threshold 8000 \
  --verbose
```

AGC / auto gain:

```bash
scripts/start_rtl_fm_receiver.sh \
  --mode fm \
  --frequency 162.4M \
  --agc \
  --threshold 8000 \
  --verbose
```

AGC can help mixed weak/strong signals, but it can also raise the noise floor and keep squelch open. If clips never close or noise is constantly recorded, use manual gain and raise the threshold.

---

## FM modes

Accepted NFM names:

```text
fm
nfm
narrow
narrowfm
narrowbandfm
```

For `rtl_fm`, these map to:

```text
-M fm
```

Clip metadata uses:

```text
nfm
```

Accepted WBFM names:

```text
wbfm
wide
widefm
widebandfm
```

For `rtl_fm`, these map to:

```text
-M wbfm
```

Clip metadata uses:

```text
wbfm
```

The WAV filename includes source, receiver, frequency, mode, and PID, for example:

```text
2026-05-13_153012.428Z__MSE-88__rx-1__442.275000MHz__nfm__pid1234.wav
```

---

## Classifier state and NFM/WBFM separation

The classifier is designed for radio/repeater style traffic:

```text
CW ID candidates
tone ID frequency
spoken callsign candidates
stable label promotion over time
```

By default, the worker now classifies only NFM clips:

```bash
--classify-modes nfm
```

This prevents WBFM broadcast audio from contaminating repeater/callsign label evidence.

Classifier state is separated by source, receiver, mode, and frequency:

```text
source/receiver/mode@frequencyHz
```

Examples:

```text
MSE-88/rx-1/nfm@162400000Hz
MSE-88/rx-1/nfm@442275000Hz
MSE-88/rx-1/wbfm@90700000Hz
```

To classify both NFM and WBFM:

```bash
--classify-modes nfm,wbfm
```

To classify everything:

```bash
--classify-modes all
```

For normal repeater/CW/tone/callsign work, keep:

```bash
--classify-modes nfm
```

Persistent classifier evidence is stored in:

```text
runtime/classification_state.json
```

---

## Qwen cleanup modes

Whisper/faster-whisper does the audio-to-text transcription. Qwen/LM Studio is optional cleanup after Whisper.

Cleanup modes:

```text
--cleanup-mode radio-log      # default; compact radio-log style
--cleanup-mode conservative   # smallest possible changes; marks uncertainty aggressively
--cleanup-mode plain          # readable paragraph cleanup
```

Recommended live command:

```bash
.venv/bin/python3 scripts/transcribe_worker.py \
  --whisper-model small.en \
  --device cpu \
  --compute-type int8 \
  --lmstudio-host 192.168.3.38 \
  --lmstudio-port 1234 \
  --cleanup-model "qwen3508b-transcriber-15k-03" \
  --cleanup-mode radio-log \
  --enable-classifier \
  --classify-modes nfm
```

For stricter cleanup:

```bash
--cleanup-mode conservative
```

For no Qwen cleanup:

```bash
--no-cleanup
```

Page behavior:

```text
raw.html             raw Whisper output
processed.html       Qwen-cleaned output when cleanup ran successfully
classification.html  classifier evidence / labels
formatted.html       larger reprocessed reports
```

---

## Larger report generation

Build a normal monitoring report:

```bash
scripts/build_monitoring_report.sh
```

Build callsign / label evidence report:

```bash
scripts/build_callsign_report.sh
```

Or call the reprocessor directly:

```bash
.venv/bin/python3 scripts/reprocess_history.py \
  --limit 100 \
  --max-chars 20000 \
  --source-text best \
  --format monitoring-report \
  --title "MSE-88 Monitoring Report" \
  --lmstudio-host 192.168.3.38 \
  --lmstudio-port 1234 \
  --model "qwen3508b-transcriber-15k-03"
```

Formats:

```text
transcript
monitoring-report
incident-log
callsign-evidence
```

---

## Important files

```text
scripts/start_rtl_fm_receiver.sh       primary rtl_fm ingest launcher
scripts/rtl_signal_check.py            rtl_power-based signal / PPM / gain checker
scripts/clip_writer.py                 squelch-gated WAV clip writer
scripts/transcribe_worker.py           Whisper + cleanup + classifier worker
scripts/clip_classifier.py             CW/tone/spoken-callsign classifier
scripts/cw_decode.py                   one-shot WAV -> CW text/JSON decoder
scripts/build_transcript_page.py       builds static HTML pages
scripts/reprocess_history.py           larger Qwen report builder
scripts/build_monitoring_report.sh     monitoring report wrapper
scripts/build_callsign_report.sh       callsign evidence wrapper
scripts/ppm_config.py                  show/set shared PPM
scripts/receiver_config.py             show/set receiver frequency and mode
```

---

## Runtime folders

```text
runtime/
  tmp/                       partial WAV clips while squelch is open
  queue/                     completed WAV clips waiting for transcription
  processing/                files currently owned by the worker
  done/                      processed WAV clips and transcript JSON
  failed/                    failed clips
  transcripts/               index.jsonl and static HTML pages
  test/                      local test WAV/MP3 files; ignored by Git
  classification_state.json  persistent evidence state for label promotion
```

Only `.gitkeep` placeholders are committed. Runtime audio, generated transcripts, local test files, and classifier state are ignored by Git.

---

## CW decoder and Fldigi credit

This project includes a lightweight in-repo DSP Morse/CW decoder because the SDR transcription pipeline needs a toolable one-shot command:

```text
WAV file -> CW text / JSON evidence -> label_candidates -> classification_state.json
```

The CW decoder work is informed by long-standing amateur-radio digital-mode tooling. **Fldigi** by W1HKJ and contributors is a credible open-source reference for CW and other digital-mode decoding. Fldigi is not bundled here and this repository does not copy Fldigi source code; it is credited as an important reference and comparison target.

Fldigi project:

```text
https://github.com/w1hkj/fldigi
```

Test a WAV with the internal decoder:

```bash
.venv/bin/python3 scripts/cw_decode.py \
  runtime/done/some_repeater_clip.wav \
  --low-hz 300 \
  --high-hz 2000 \
  --expected-wpm-min 8 \
  --expected-wpm-max 30 \
  --pretty
```

Run the full classifier:

```bash
.venv/bin/python3 scripts/clip_classifier.py \
  runtime/done/some_repeater_clip.wav \
  --expected-wpm-min 8 \
  --expected-wpm-max 30 \
  --pretty
```

External decoder hook:

```bash
.venv/bin/python3 scripts/clip_classifier.py \
  runtime/done/some_repeater_clip.wav \
  --cw-external-command "some-cw-decoder --input {wav}" \
  --pretty
```

---

## GNU Radio notes

GRC is useful for visual acquisition and checking spectrum/waterfall/audio, but for reliable live transcription use the `rtl_fm` launcher as the main ingest path.

GRC FIFO helper files exist for experiments:

```text
scripts/start_grc_clip_writer.sh
grc/shared_baseband_one_channel_fifo_wbfm.grc
grc/shared_baseband_one_channel_fifo_nfm.grc
scripts/make_shared_baseband_fifo_grc.py
```

The FIFO path must match exactly between GRC and the bridge script. The generator writes an absolute FIFO path by default to avoid working-directory problems.

---

## Install

On Debian/Ubuntu/Raspberry Pi OS style systems:

```bash
./install.sh
source .venv/bin/activate
```

Manual setup:

```bash
sudo apt-get install -y python3 python3-venv python3-pip rtl-sdr sox ffmpeg libsndfile1 gnuradio gr-osmosdr
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
mkdir -p runtime/{queue,tmp,processing,done,failed,transcripts,test}
```

For small CPU systems, use:

```bash
.venv/bin/python3 scripts/transcribe_worker.py \
  --whisper-model small.en \
  --device cpu \
  --compute-type int8 \
  --enable-classifier \
  --classify-modes nfm
```

For NVIDIA CUDA systems:

```bash
.venv/bin/python3 scripts/transcribe_worker.py \
  --whisper-model medium.en \
  --device cuda \
  --compute-type float16 \
  --enable-classifier \
  --classify-modes nfm
```
