# Speech-first reliability and migration

## Guarantees and boundaries

* A successful ASR result is written atomically and fsynced to a per-clip
  checkpoint before archive movement, classification, cleanup or rendering.
* Completed audio and capture metadata are archived before publishing the
  canonical `done/<stem>.transcript.json` ready marker. Raw text is immutable
  during optional enrichment.
* Pending enrichment is durable. CW/cleanup failures get their own errors, not
  an empty replacement speech transcript. Internal and external CW commands run
  concurrently with independent timeouts.
* JSONL/HTML views are serialized between workers and rebuilt atomically from
  canonical records plus retained historical entries. Renderer failure does not
  move good audio into `failed` or erase the raw transcript.
* Kernel locks reject duplicate consumers. A stopped process releases its lock;
  a PID file alone is never treated as proof that recovery is safe.

These guarantees depend on functioning local storage with working hard links,
fsync and advisory locks. This is not a distributed/NFS queue. Disk exhaustion,
filesystem corruption or power failure beyond the filesystem's durability
contract cannot be repaired by a speech model.

## Upgrade

1. Stop the old receiver/worker before changing code. Preserve `runtime/` and a
   backup; do not run old/new workers together or apply changes on live partial
   recordings.
2. Use the documented supported ASR interpreter. The model/library baseline is
   intentionally retained; a NumPy <2 constraint protects the older runtime.
3. Start speech only and check captured WAV speed/pitch, raw transcripts,
   metadata and processing time against known RF recordings.
4. Enable `--enable-classifier` only when ready to run the separate sidecar.
   Cleanup is now explicitly opt-in with `--enable-cleanup`.

The defaults remain `small.en`, English, beam size 5, VAD on, CPU INT8. No claim
is made that this model is accurate on every receiver, callsign, clipped word,
noise condition or radio vocabulary. Use `verify_asr.py` on manually checked
recordings and measure callsigns/numbers separately as well as overall WER.

## Compatibility changes

`--enable-classifier` queues asynchronous enrichment; it does not launch it.
Run `scripts/enrichment_worker.py` separately. Both processes need matching
`--done` and `--transcripts` and the same external-decoder environment.

`--enable-cleanup` is required to request model cleanup. `--no-cleanup` still
works and overrides it. The optional model cannot inject CW identities into the
speech prompt, and cleanup text is never re-used as station-identification
truth. Automatic label promotion is disabled; old state and historical labels
are retained as history, not revalidated.

The `--audio-rate` launcher option now actually requests that PCM rate from
rtl_fm with `-r`. WBFM defaults to 48 kHz output. The WAV header always matches
the requested output rate, and unsupported upsampling is rejected. Gating still
needs receiver-specific adjustment: audio RMS is not RF signal strength.

## Recording and restart behavior

Capture buffers partial PCM samples and drops at most one incomplete trailing
byte with a diagnostic. EOF, SIGINT and SIGTERM finalize the active recording,
subject to `--min-sec`; SIGKILL cannot run finalizers. Keep orphaned `.wav.part`
files for manual salvage after an ungraceful capture failure.

Hang time is based on audio samples, not replay speed. `--pre-roll-ms` defaults
to 200 ms (100 ms chunk granularity); set it to 0 to restore no pre-roll. Maximum
clip boundaries do not omit samples from a continuously active stream. Invalid
live threshold-control data is reported and ignored rather than crashing capture.

Queue, processing, done and failed directories must be distinct on one local
filesystem. Tmp and queue must share a filesystem too. Archive publication never
overwrites a different existing file. Checksummed duplicate delivery and
checkpoints already published before a crash are reconciled without repeating
ASR or replacing a later enriched record. Malformed or conflicting checkpoints
stop recovery for inspection instead of guessing or deleting evidence.

Canonical records contain absolute archive paths. Do not relocate a runtime or
change its configured directories mid-recovery; validate and migrate paths
explicitly when moving a deployment.

## Verification levels

Offline `unittest` coverage exercises actual PCM capture, OS processes,
process-group deadlines, actual synthetic Morse decoding, receiver argument
construction, archive/checkpoint recovery, concurrent CW scheduling and the real
HTML renderer. Speech persistence tests use an explicit fake ASR model.

`verify_asr.py` performs real faster-whisper inference with no mock and reports
`blocked` when dependencies are missing. Its optional WER gate is a chosen test
criterion, not an RF accuracy guarantee. Named model IDs may download weights;
use a local model directory to record model-file checksums.

The committed CI separates offline tests from a real-model smoke test on
synthesized speech. Live RF demodulation, an external neural CW backend and
real-world CW false-positive rates still require target-host acceptance tests.
