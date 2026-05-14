# GNU Radio Companion FIFO acquisition workflow

This workflow lets GNU Radio Companion handle signal acquisition and visual verification while the existing SDR-audio-Transcriber pipeline handles squelch-gated WAV clips, Whisper transcription, classifier output, and the web pages.

## Files added

```text
grc/sdr_audio_transcriber_nfm_fifo.grc
scripts/start_grc_clip_writer.sh
```

The GRC flowgraph writes demodulated mono signed 16-bit PCM audio into:

```text
runtime/grc_audio.pcm
```

The bridge script reads that FIFO and feeds:

```text
scripts/clip_writer.py
```

Completed clips still go into:

```text
runtime/queue/*.wav
```

The normal `transcribe_worker.py` then processes them exactly like clips produced by `scripts/start_rtl_fm_receiver.sh`.

## Terminal order

### Terminal 1 — start the FIFO clip writer bridge

Run this first. It creates the FIFO and waits for GRC to write audio into it.

```bash
cd /home/mat/SDR-audio-Transcriber

scripts/start_grc_clip_writer.sh \
  --receiver rx-grc-1 \
  --source MSE-88 \
  --mode nfm \
  --frequency 442.275M \
  --sample-rate 24000 \
  --threshold 60 \
  --hang-ms 1800 \
  --verbose
```

For NOAA weather radio:

```bash
scripts/start_grc_clip_writer.sh \
  --receiver rx-grc-1 \
  --source MSE-88 \
  --mode nfm \
  --frequency 162.4M \
  --sample-rate 24000 \
  --threshold 100 \
  --hang-ms 1800 \
  --verbose
```

The `--sample-rate` value must match the GRC flowgraph's final audio/PCM rate.

### Terminal 2 — run GNU Radio Companion

Open the flowgraph:

```bash
gnuradio-companion grc/sdr_audio_transcriber_nfm_fifo.grc
```

In GRC, check these variables before pressing Run:

```text
center_freq = 442.275e6       # or 162.4e6 for NOAA
audio_rate  = 24000           # must match start_grc_clip_writer.sh --sample-rate
ppm         = your SDR PPM correction, e.g. -41
gain        = tuner gain, e.g. 42
audio_gain  = float audio gain before short conversion
fifo_path   = "runtime/grc_audio.pcm"
```

Then press **Run** in GRC.

The flowgraph gives you:

```text
Osmocom Source
 ├─> QT GUI Frequency Sink
 ├─> QT GUI Waterfall Sink
 └─> NBFM Receive
      ├─> Audio Sink
      └─> Float to Short
           └─> File Sink -> runtime/grc_audio.pcm FIFO
```

### Terminal 3 — transcription worker

Use the normal worker. Start without Qwen cleanup first while testing acquisition:

```bash
cd /home/mat/SDR-audio-Transcriber

.venv/bin/python3 scripts/transcribe_worker.py \
  --whisper-model medium.en \
  --device cpu \
  --compute-type int8 \
  --no-cleanup \
  --enable-classifier
```

With Qwen/LM Studio cleanup:

```bash
.venv/bin/python3 scripts/transcribe_worker.py \
  --whisper-model medium.en \
  --device cpu \
  --compute-type int8 \
  --lmstudio-host 192.168.3.38 \
  --lmstudio-port 1234 \
  --enable-classifier
```

### Terminal 4 — web server

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

## Important notes

The FIFO bridge script only supplies metadata. GRC supplies the actual audio. Make sure these values agree:

```text
GRC center_freq      <-> bridge --frequency
GRC demod/audio mode <-> bridge --mode
GRC audio_rate       <-> bridge --sample-rate
GRC fifo_path        <-> bridge --fifo
```

If the bridge is waiting and GRC is not running, no WAV files will be created.

If GRC is running but the bridge is not running, the GRC File Sink may block while trying to open the FIFO.

## Tuning tips

If you see signal in the GRC waterfall but no WAV clips are created, lower the bridge threshold:

```bash
--threshold 30
```

If it records constant noise or NOAA never closes, raise it:

```bash
--threshold 120
```

If Whisper returns `[no transcript text]`, check the latest WAV:

```bash
WAV="$(ls -t runtime/done/*.wav | head -1)"
soxi "$WAV"
ffmpeg -hide_banner -i "$WAV" -af volumedetect -f null - 2>&1 | grep -E "mean_volume|max_volume"
```

If the audio is too quiet, raise `audio_gain` in GRC or lower the clip threshold. If it is clipped/distorted, lower `audio_gain` or tuner gain.
