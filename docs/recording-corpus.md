# Collect recordings and measure decoder changes

This is the first implementation of [the development plan](../DEVELOPMENT_PLAN.md).
It adds an operator-started collector and offline evaluation tools. It does not
retune an SDR, deploy services, train a model, upload audio or delete recordings.
Use the speech-first prerequisite branch/PR before its decoder entry points.

## 1. Choose a private corpus and an acquisition session

Run from the repository root. Collection uses Python's standard library; actual
speech decoding uses the existing ASR virtual environment. The current evaluator
uses Unix subprocess supervision from `safe_runtime.py`.

The default corpus is `runtime/corpus`. Keep it outside the HTTP transcript server's
root. New corpus directories are owner-only, and the collector writes a `.gitignore`
guard. Do not rely on that guard instead of reviewing Git changes before committing.
Custom roots and existing directories need their own access-control review.

Choose a session ID and dataset partition before collecting. Use a new session
when receiver settings or acquisition conditions change. Keep all clips from the
same source session and all variants of one parent I/Q recording in one split.
Settings are operator-supplied provenance, not automatic measurements.

For example, create a private `runtime/capture-settings.json` containing the actual
receiver, gain, filter, frequency-correction and squelch settings. Do not copy
example numbers as if they were measured. The optional settings file must be a
JSON object, and credentials should never be included.

Start the existing receiver and speech worker using their documented commands on
the deployment host. In a separate terminal, collect completed output:

```bash
python3 scripts/recording_corpus.py --root runtime/corpus collect runtime/done \
  --session rx1-baseline-session01 --split validation --origin rf \
  --settings runtime/capture-settings.json --watch
```

Omit `--settings` when unavailable; unknown metadata remains unknown. One-shot
collection is the same command without `--watch`. Import valid completed WAVs from
`runtime/failed` separately with the same session settings; corrupted/partial WAVs
are rejected, not silently repaired or counted as successes.

The collector copies mono 16-bit WAV files, waits two seconds after modification,
checks the full sample count, hashes the file and preserves its JSON sidecar. It
never moves originals or consumes `queue`, `processing` or `tmp`. It supports only
finalized `.wav` inputs in this increment, not I/Q, stereo, floats or other codecs.

Audio is stored once per exact WAV checksum, with separate session observations.
Reimporting is idempotent. A checksum cannot belong to multiple partitions; a
session's split, origin and settings cannot be reassigned. Quarantine is the default
for unknown provenance, but there is no quarantine migration command yet: decide
known collection splits up front. Changing settings requires a new session ID.

Watch mode skips unchanged successfully imported files in memory; a restart checks
them again. This is a small-corpus pilot collector, not a scalable archival service.
Monitor disk space and stop collection before exhaustion. No scheduled cleanup is
installed. If interrupted during import, hidden temporary/orphaned files may remain
for inspection; originals are never deleted.

View recording IDs, quality, sessions, review IDs and run IDs:

```bash
python3 scripts/recording_corpus.py --root runtime/corpus inventory
```

RMS, peak, near-full-scale and zero-sample fractions are PCM diagnostics only.
`snr_db: null` is intentional: these measurements do not establish RF SNR or
transcription accuracy. A sidecar/header rate disagreement is rejected, but matching
headers alone cannot prove the receiver actually produced that sample rate.

## 2. Add independently checked references

Listen to the original recording and write a private reference JSON. For example,
for a human-checked CW-only recording:

```json
{
  "provenance": "human",
  "speech": "",
  "cw": "DE KJ6DZB",
  "callsigns": ["KJ6DZB"],
  "protected": true
}
```

Use actual heard text, not this example unless it matches the audio. `""` means
reviewed absence (a negative control); `null` means the target is unreviewed. Both
`speech` and `cw` keys are required. Mixed clips can have text for both. Synthetic
fixtures must use `provenance: "synthetic"` and a synthetic capture origin; do not
present them as human-reviewed RF. Optional `required_tokens` checks important
speech strings without inventing phonetic or number normalization.

```bash
python3 scripts/recording_corpus.py --root runtime/corpus annotate ASSET_ID \
  --reviewer 'operator' --reference runtime/reference.json
```

Replace `ASSET_ID` with the 64-character ID returned by collection/inventory. Reviews
are append-only. Each test run freezes the selected review IDs before any decoder
starts, so later corrections do not rewrite old scores. References are never
passed to a decoder as prompts or context. RF acceptance requires human-reviewed
references; the tool does not certify that a claimed reviewer actually listened.

## 3. Run actual decoders separately

Speech uses the repository's actual `transcribe_file` function, not an LLM-generated
substitute. Run with the interpreter containing the ASR dependencies:

```bash
.venv/bin/python3 scripts/recording_corpus.py --root runtime/corpus run \
  --engine speech --split validation --origin rf \
  --model small.en --device cpu --compute-type int8 --timeout 180
```

Known model identifiers may download public weights through faster-whisper. Prefer
a versioned local model snapshot for acceptance runs and record its file hashes
separately; a model name is not a weight revision. Model downloads/loads must fit the
timeout, or preload the model on the host. This command does not install packages.

Run internal DSP CW independently:

```bash
python3 scripts/recording_corpus.py --root runtime/corpus run \
  --engine cw --split validation --origin rf --timeout 60
```

For an actual installed external CW engine, supply its verified command with a
`{wav}` placeholder through `--engine external-cw --command '...'`. Commands run
without a shell, with bounded output/time and process-group cleanup. They must be
trusted one-shot programs that do not daemonize. Valid plain decoded text or the
supported decoded-text JSON is accepted. Failures/diagnostics are not station IDs.
Without a configured command, the result is `blocked`, never a decoder success.

Each run returns a run ID. Code 0 means the execution completed as measured, **not**
that accuracy passed. Blocked/error results return 2. Ctrl-C returns 130, and all
selected but unfinished recordings remain `not_completed` in the database rather
than disappearing from the denominator. There is no automatic resume of that run;
start a new run while retaining the interrupted one.

The pilot evaluator cold-loads a model for each clip. `wall_seconds` and
`cold_wall_real_time_factor` include startup; speech also reports model-load and
inference times. This is not a warmed, multi-channel throughput benchmark.

## 4. Review results and compare like with like

```bash
python3 scripts/recording_corpus.py --root runtime/corpus report RUN_ID
python3 scripts/recording_corpus.py --root runtime/corpus compare BASELINE_ID CANDIDATE_ID
```

Run a candidate with `--repo /path/to/candidate-checkout` to test a different source
version, or change a model setting while keeping original audio/references fixed.
The target checkout must contain the corresponding decoder entry point. The active
interpreter supplies dependencies; changing `--repo` does not create another venv.

Compare uses only matching recording and reference IDs of the same engine type.
It lists unmatched, unreviewed or failed pairs as excluded. Lower paired error
counts are improvement on those recordings, not proof that acquisition improved.
Changing receivers/settings while also changing the model is not a controlled A/B.

Reports preserve status counts, per-clip output, provenance, WER/CER and negative
controls. Errors/blocked outputs are not scored as correct silence. WER can exceed
1. Unreviewed targets have no score. CW `?` remains an error symbol. Callsign
extraction is a heuristic for comparison, not an authoritative worldwide callsign
validator. RF and synthetic subsets must be reported separately.

For holdout acceptance, predeclare error limits and minimum sample/session coverage
before looking at the results. The tool intentionally has no universal accuracy
threshold and does not treat model confidence as calibrated correctness.

## 5. Retention and backup

```bash
python3 scripts/recording_corpus.py --root runtime/corpus retention-plan
```

This always returns `deletion_enabled: false`, `metadata_action: keep` and per-file
keep reasons. There is no `--apply` or deletion command. High decoder scores do not
change it. Protected references, validation/test recordings and historical failures
are explicitly marked. Implementing any future expiry needs a separately reviewed
policy and independent accuracy evidence described in M6 of the development plan.

Back up the immutable `audio/` directory and take a consistent SQLite snapshot with
the SQLite backup API (or stop all corpus processes and perform a complete backup).
Do not copy only the live `.sqlite3` file while ignoring its WAL. Before restoring,
check every referenced audio checksum. The private database includes text, settings
and receiver/session information and must receive the same protection as recordings.

## Verification scope

```bash
python3 -m unittest discover -s tests -p 'test_recording_corpus.py' -v
```

The new suite tests copying, provenance, partition guards, frozen references,
negative controls, metrics, comparison, interruption, supervised subprocess results
and keep-all retention. Injected outputs and fake external commands are explicitly
plumbing tests, not recognition accuracy. Real internal CW fixtures and the existing
ASR smoke test are separate CI checks. Actual RF recording and a configured neural
CW engine still require deployment-host testing.
