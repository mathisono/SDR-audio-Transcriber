# SDR Audio Transcriber

Small SDR capture/transcription pipeline.

## Main path

```text
rtl_fm -> clip_writer -> runtime/queue/*.wav -> transcribe_worker -> runtime/transcripts/*.html
```

## Quick start

### Receiver

```bash
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

### Worker

```bash
.venv/bin/python3 scripts/transcribe_worker.py \
  --whisper-model small.en \
  --device cpu \
  --compute-type int8 \
  --no-cleanup \
  --enable-classifier \
  --classify-modes nfm
```

### Web server

```bash
cd runtime/transcripts
python3 -m http.server 8090 --bind 0.0.0.0
```

## Notes

- `rtl_fm` is the preferred ingest path.
- Whisper/faster-whisper does the transcription.
- Optional LM Studio cleanup can be enabled if desired.
- Runtime output lives under `runtime/`.
- Generated transcripts are in `runtime/transcripts/`.
