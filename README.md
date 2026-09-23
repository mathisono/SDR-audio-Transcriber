# SDR Audio Transcriber

Capture radio audio, preserve the original recording, and publish raw
faster-whisper transcripts. **Speech is primary; CW and model cleanup are optional
sidecar jobs and cannot prevent a successful speech result from being saved.**

```text
rtl_fm -> clip_writer -> queue/*.wav -> transcribe_worker
                                      |
                                      v
                             done/*.wav + *.transcript.json
                                      |
                        +-------------+----------------+
                        v                              v
                 JSONL / HTML log          optional enrichment_worker
                                            |                    |
                                     internal CW DSP      external CW command
                                            +---------+----------+
                                                      v
                                            unverified evidence
```

## Install

The speech baseline remains **faster-whisper 0.10.1, small.en, CPU INT8**, with
English, beam size 5 and Silero VAD. The legacy ASR dependencies require Python
3.8-3.11; use an installed Python 3.11 interpreter for a fresh environment:

```bash
PYTHON=python3.11 bash install.sh
```

The selected interpreter needs its matching `venv`/development packages. The
installer refuses unsupported ASR interpreters rather than silently changing
models. Capture/CW/offline tests do not require faster-whisper; the writer no
longer depends on the removed `audioop` module.

## Start with speech only

From the repository root, start the receiver:

```bash
bash scripts/start_rtl_fm_receiver.sh \
  --receiver rx-1 --source MSE-88 --mode nfm --frequency 162.4M \
  --sample-rate 48000 --audio-rate 16000 \
  --no-calibrate --verbose
```

Listen to the captured WAVs and adjust `--threshold` for your received audio and
noise. RMS gating is not a calibrated RF squelch or a speech detector. The
launcher now sends **`-r AUDIO_RATE` to rtl_fm**, and writes that same rate into
the WAV header. It rejects unsupported upsampling. WBFM defaults to 48 kHz PCM;
NFM defaults to its demodulation rate unless overridden.

In another terminal:

```bash
.venv/bin/python3 scripts/transcribe_worker.py \
  --whisper-model small.en --device cpu --compute-type int8 --no-cleanup
```

Serve the log locally:

```bash
cd runtime/transcripts
python3 -m http.server 8090 --bind 127.0.0.1
```

The server has no authentication. Expose it to another host only on a trusted
network or behind an authenticated proxy. Existing dashboard/raw/processed
pages remain; `evidence.html` shows optional decoder status and diagnostics.

## Optional CW and cleanup

**`--enable-classifier` now queues work, rather than decoding inline. You must
also run the enrichment worker.** Start the speech worker with that flag:

```bash
.venv/bin/python3 scripts/transcribe_worker.py \
  --whisper-model small.en --device cpu --compute-type int8 \
  --no-cleanup --enable-classifier --classify-modes nfm
```

Then start the independent sidecar:

```bash
.venv/bin/python3 scripts/enrichment_worker.py
```

Both CW branches receive the same immutable archived audio and start
concurrently, with independent deadlines (`--cw-internal-timeout` and
`--cw-external-timeout`, 20 seconds each by default). An empty external command
means the external branch is disabled. The internal decoder is experimental;
its scores are **not** accuracy probabilities, and automatic station-label
promotion is disabled. Speech/noise can still produce Morse-like strings.

`morseangel_adapter.py` is only a command wrapper, **not a bundled neural CW
model**. To use a real external engine, configure `--cw-external-command` on the
speech worker. See [external decoder tests](docs/testing-external-cw-adapter.md).

Cleanup now requires **`--enable-cleanup`**; `--no-cleanup` overrides it. Existing
`--lmstudio-host`, `--lmstudio-url`, `--cleanup-model` and timeout options still
configure that job. Cleanup runs in the sidecar and never changes `raw_text`.

For custom paths, pass the same `--done` and `--transcripts` directories to both
workers. They do not compete for `queue/*.wav`. `--once` drains available work
and exits, which is useful for controlled replay tests.

## Verify

Dependency-free regression checks (real synthetic CW and real file/process
handling; **mock speech model**, no SDR hardware):

```bash
python3 -m unittest discover -s tests -v
```

Real recognition check, using a known WAV and manually checked reference text:

```bash
.venv/bin/python3 scripts/verify_asr.py \
  --wav /path/to/known-recording.wav \
  --reference-file /path/to/reference.txt \
  --output runtime/asr-check.json \
  --model small.en --device cpu --compute-type int8
```

This runs the real repository speech function and records WER, timing, library
versions, audio/code checksums and model-file checksums when a local model
directory is supplied. A missing runtime is reported as **blocked**, never a
pass. A named model can trigger a download. `--max-wer` sets an optional,
operator-chosen acceptance threshold; it is not an RF accuracy guarantee.

The CI workflow runs offline checks separately from a real-model smoke test on
synthesized speech. Neither substitutes for recordings from your actual RF path.

## Recovery and compatibility

Raw speech is fsynced to a processing checkpoint before any optional work.
Audio is then archived before its transcript becomes visible to the sidecar.
Per-clip `done/*.transcript.json` files are authoritative; JSONL and HTML are
recoverable views. The original JSONL is backed up before migration, and old
entries without per-clip JSON are retained.

Stop the old worker before upgrading. The new worker uses kernel locks to
reject duplicate consumers and safely resumes its own interrupted jobs. Keep
`queue`, `processing`, `done`, and `failed` as distinct directories on the same
local filesystem; `tmp` and `queue` must also share a filesystem. Do not run old
and new workers against the same runtime or change runtime paths mid-recovery.

See [reliability and migration notes](docs/speech-first-reliability.md) and the
[parallel CW architecture](docs/parallel-cw-decoder-workflow.md).
