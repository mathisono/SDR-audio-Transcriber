# Parallel CW decoder workflow

Implemented by `transcribe_worker.py`, `enrichment_worker.py` and
`clip_classifier.py`. Speech remains primary; the same archived audio is used
for both optional CW branches. Do not start two consumers on the speech queue.

```text
queue WAV -> speech worker -> durable raw checkpoint -> archive WAV + metadata
                                                         |
                                                 publish transcript JSON
                                                         |
                           +-----------------------------+-----------------+
                           v                                               v
                     raw JSONL / HTML                           enrichment worker
                                                                    /         \
                                                     internal CW process   external CW process
                                                       own deadline          own deadline
                                                                    \         /
                                                             independent evidence
```

The sidecar runs the two decoder subprocesses concurrently. A missing,
crashing, timed-out, or malformed backend does not prevent the other from
running. Both use the exact same WAV, checked against its stored SHA-256.
Optional LM Studio cleanup is also independent of speech publication and has a
process-level deadline as well as an HTTP timeout.

## Start

```bash
.venv/bin/python3 scripts/transcribe_worker.py --no-cleanup --enable-classifier
# Separate terminal/service:
.venv/bin/python3 scripts/enrichment_worker.py
```

`--enable-classifier` schedules work only. An absent sidecar leaves a visible
`enrichment.status: pending`; it does not block speech. For non-default paths,
match `--done` and `--transcripts` in both processes.

Pending work lives in the authoritative per-clip transcript JSON, not a
volatile thread queue. A sidecar crash before publication leaves it pending for
restart. Finished jobs contain `complete` or `complete_with_errors`, independent
branch results and error details. Failed jobs are not retried in a tight loop;
retain the audio and use the standalone classifier for decoder experiments.

## Evidence, not station identity

No automatic label promotion is performed by the new pipeline. Existing
`classification_state.json` is left untouched, and historical log records are
retained. New `label_candidates` are empty. CW callsign strings remain inside
per-engine evidence with `verified: false` and no calibrated confidence.
Internal heuristic scores and externally reported scores are retained as
separate diagnostic fields. They must not be interpreted as accuracy odds.

The DSP scans tone power over the full clip, so silence in the first 15 seconds
no longer prevents a later ID from being found. Reused tone envelopes reduce
repeated computation during parameter search. These changes do not establish
reliable decoding of arbitrary speech/noise or weak/mixed RF signals.

The MorseAngel adapter is a shim requiring an actual configured decoder. No
neural Morse engine is bundled or claimed to have been verified.
