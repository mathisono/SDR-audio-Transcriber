# Development plan: evidence-first RF transcription

Status: active roadmap; first corpus increment, 2026-09-24. Update this document
in each milestone PR with the tested commit, dataset revision and remaining gates.

## Goal and scope

Produce dependable speech transcripts from received RF, then turn a web receiver's
waterfall into a time-aligned, searchable transmission history. Monitor the usable
bandwidth of one shared I/Q capture, discover analog narrowband-FM channels, manage
bounded receiver chains, transcribe with faster-whisper, and associate repeater
bookmarks with independently supported CW-ID evidence.

Speech is the primary deliverable. CW, cleanup, learned detection and UI enrichment
must not block capture or erase raw speech. "Entire baseband" means the currently
captured usable bandwidth, not every frequency the hardware can tune.

OpenWebRX+ is an integration candidate, **not a selected dependency**. Choose the
actual web receiver and supported server/browser interfaces at the UI milestone.
Do not claim a complete plugin or automatic multi-channel receiver already exists.

## Current implementation boundary

[PR #1](https://github.com/mathisono/SDR-audio-Transcriber/pull/1) contains the
speech-first capture/recovery and independent enrichment changes. The initial
corpus work is based on its head `87c87e7a141580d3e067ba1b9da947d86c35f3bb`, not the
older `main` implementation. Merge/rebase dependencies deliberately; do not run
old and new consumers against the same live runtime directories.

This increment adds `scripts/recording_corpus.py`, `scripts/corpus_decode.py` and
regression tests. It copies finalized recordings into a private local corpus,
records acquisition settings and PCM diagnostics, stores append-only reviewed
references, runs actual decoder commands when available, and compares predictions
against fixed references. See [the collection/testing runbook](docs/recording-corpus.md).

It does **not** start a receiver, change live settings, train a model, install a
neural CW backend, demonstrate live-RF accuracy, export SigMF, or delete data.
The retention command is audit-only and always keeps recordings and metadata.

## Architectural invariants

- One coordinated hardware owner supplies timestamped I/Q to receiver chains and
  the display; independent clients must not fight over tuning or SDR handles.
- Preserve original audio before optional processing. Where I/Q is retained, link
  derived audio to its parent capture, sample range, rate conversion and delay.
- Speech and CW receive the same immutable evidence independently. Speech VAD
  must not discard the tones needed by CW; speech and CW regions may overlap.
- Raw transcripts, cleaned text, decoder hypotheses and human references are
  distinct records. A successful command is not proof of a correct transcript.
- Queue admission, storage use, dropped samples, gaps and processing delays must
  be visible. Do not silently imply full coverage when capacity is exceeded.
- Recording retention and public access are explicit operator decisions. No
  recordings, transcripts, credentials or private capture settings belong in Git.

## Milestones and acceptance gates

### M0 — Reliable single-channel acquisition and speech

Complete the speech-first prerequisite and verify the target host, not only mocks.
Check actual rtl_fm output rate against WAV headers, known-tone pitch/duration,
pre-roll and first/last words, EOF/signals, continuous-traffic clip splits,
interrupted jobs and original-audio preservation. Run actual `small.en`, English,
CPU INT8, beam size 5 and VAD as the initial baseline, without cleanup.

Gate: repeatable end-to-end recordings and real inference with documented versions,
correct sample/timing behavior, and no loss of successful speech when optional
components fail. Report RF decoding and synthetic/mocked tests separately.

The inherited CI failed before inference because PyAV's isolated build used newer
Cython. Keep the runtime/model pins; apply the Cython constraint to modern pip's
build environment as well as older pip. A green installation step alone is not a
green inference or RF-accuracy result.

### M1 — Accumulate a representative, reviewable corpus (this increment)

Collect finalized `done` recordings, and separately inspect/import valid finalized
WAVs from `failed`. Do not consume live queue or partial files. Capture before
changing gain, filters, squelch or model settings, then start a new labeled session
for each acquisition change. Preserve weak, clipped, noisy, mixed speech/CW,
short, long, ID-only, speech-only, silence and interfering-signal examples.

Allocate complete sessions to train, validation or test **before importing**.
Adjacent clips, alternate demodulations of one I/Q recording, and duplicates must
remain in one partition. The current tool rejects exact cross-partition duplicate
WAVs and session reassignment; near-duplicate and I/Q-parent grouping checks remain
future work. Quarantine is the default when assignment is unknown; automatic
quarantine reassignment is not implemented.

Human-review both positive and negative examples. Empty text means confirmed
absence; null means unreviewed. Version corrections instead of rewriting history.
A decoder's output must never silently become reference text.

Gate: inventory, checksums, session settings, review provenance, frozen run
membership/references, failure counts and reproducible report/compare commands.
The first collection campaign should span multiple sessions, receivers/settings
where available, signal qualities and speakers; a convenient small sample is not
an acceptance dataset. Choose numerical acceptance thresholds after the pilot,
before evaluating the final held-out set.

### M2 — Single-chain I/Q replay and acquisition experiments

Add optional SigMF-compatible I/Q retention and replay, starting with one known FM
channel. Record datatype, sample rate, tuning, capture time, sample offsets,
software/configuration versions and checksums. Preserve the relationship between
I/Q samples and demodulated audio, including resampling and filter delay. Keep
project-specific fields in a documented extension, not invented `core:` keys.

Use bounded I/Q buffers and operator-selected retention rather than mandatory
indefinite wideband recording. Validate tune offsets and audio reconstruction with
known signals and real received examples.

Separate experiments:

1. **Decoder A/B:** identical audio and fixed reference; change one decoder/model
   configuration. The corpus compare command supports this paired design.
2. **Acquisition A/B:** fixed decoder; compare alternate demodulations of identical
   retained I/Q, or controlled repeated source material through capture settings.
   Independent live conversations are not paired evidence of acquisition gains.

Gate: reproducible I/Q-to-text timing and measured word/CW errors, not merely louder
recordings or higher model confidence. The current PCM RMS/clipping diagnostics
are not RF SNR or intelligibility measurements.

### M3 — One-channel waterfall integration

Select the web SDR host and implement an adapter, not a new browser-only decoder.
Publish receive-time/frequency/sample-based events with stable recording/channel
IDs, status, transcript revision and audio references. Processing completion time
is separate from receive time. A clickable transmission marker opens text and
original audio while ordinary waterfall clicks retain tuning behavior.

Start with finalized clips. Provisional streaming text is a later revisioned UI
feature. Keep access controls and transcript-history retention explicit.

Gate: annotations remain attached to the correct transmission during scrolling,
zooming and delayed processing; raw text survives UI/network failure.

### M4 — Multiple configured channels, then automatic FM discovery

First run several known channels concurrently using one I/Q stream and a bounded
shared pool of loaded transcription models. Do not load a full Whisper model per
frequency. Benchmark aggregate channel traffic on the target hardware.

Then add detection, modulation checking, channel tracking and a receiver lifecycle:
detected -> checking -> receiving -> holding -> idle. Detection proposes a signal;
it does not establish FM speech. Keep unknown/unsupported outcomes and hysteresis
so gaps do not repeatedly destroy/recreate a receiver. Use pre-detection I/Q
buffering to recover onsets. Bound active chains, queues, disk and CPU/GPU use.

Gate: simultaneous adjacent/weak/strong transmissions stay separate; missed
transmissions, false activations, latency and overload are measurable. Learned
spectrogram detection is optional and must beat a simple baseline on held-out RF.

### M5 — Evidence-backed CW bookmarks

Continue independent internal DSP and configured external CW decoding. Measure
character errors, full-ID accuracy, false callsigns and negative controls. Preserve
unknown symbols and disagreements; heuristic decoder confidence is not a calibrated
probability. A wrapper with no backend is blocked, not a working neural decoder.

Accumulate repeater identification from separate received ID events, not repeated
runs of the same recording. Keep spoken operator callsigns distinct from repeater
identity. Initially require operator confirmation; later automatic bookmarks are
opt-in and require validated evidence criteria. Preserve conflicting IDs and allow
correction. Do not infer repeater location, offset or access tone from a callsign.

Gate: an independently checked identification dataset demonstrates acceptable false
bookmark and missed-ID rates before automatic promotion is enabled.

### M6 — Calibrated, reversible retention (future; disabled now)

Only consider reducing routine recording retention after acquisition and decoding
meet predeclared error limits on an independent, human-reviewed, representative
holdout. Keep results stratified by source, acquisition profile, mode, signal
quality and decoder/model revision. Require sufficient independent sessions and
uncertainty bounds; a small zero-error sample is not proof of reliability.

Later eligibility needs all of: minimum age, completed processing, validated
calibration for the exact deployed configuration/domain, no disagreement or
integrity fault, no hold/pin, and coverage by an ongoing random audit sample.
Predicted per-clip certainty must be calibrated against human truth. Neither a
Whisper language probability nor a CW score of 0.99 authorizes expiry.

Always retain the golden/holdout corpus, failures, difficult/rare cases, newly
encountered conditions, operator pins and an unbiased sample of easy cases.
Detection or acquisition changes invalidate prior calibration until rechecked.
Drift must automatically return policy to keep-all, with an operator-visible alert.

Implement dry-run -> reviewed quarantine -> delayed removal, with audit trail,
path containment, active-job checks and restoration tests. Approve recording and
metadata schedules separately. Large raw I/Q, channel audio, detailed predictions,
transcripts and small provenance summaries need different policies. Metadata may
also contain sensitive content: retain only the minimum needed for reproducibility
and explicit operator requirements, not an unbounded searchable personal archive.
Document tombstones/checksums/policy versions when content is removed. Never purge
the reference needed to audit the claimed accuracy.

Gate: review and approve a separate retention PR; replay/audit/restore tests pass,
and the operator explicitly enables it. This commit has no deletion path.

## Measurement and decision record

Report attempted, measured, blocked, failed, incomplete and unreviewed counts.
Calculate WER/CER only on reviewed, measured targets and display the coverage beside
them. WER may exceed 1. Use separate false-text rates on negative controls and
callsign/number error counts. Never count a blocked decoder as a successful empty
result. Keep RF and synthetic datasets distinct.

Track recording quality separately: verified rate and sample count, clipping,
discontinuities, frequency correction, gain/filter/squelch settings and reviewed
intelligibility. Measure true RF SNR only using an explicit, validated estimator.
Timing measurements must identify cold model loading versus warm inference.

Each milestone PR should include code/configuration and dataset/reference hashes,
exact commands, test environment, per-condition metrics, regressions, unperformed
checks, and a decision to proceed or gather more evidence. Do not lower thresholds
post hoc or filter out failures to manufacture progress.

## References

- [Collection and decoder testing runbook](docs/recording-corpus.md)
- [Speech-first reliability](docs/speech-first-reliability.md)
- [SigMF specification](https://sigmf.org/): capture/annotation and extension rules.
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper): actual inference API.
- [pip build constraints](https://pip.pypa.io/en/stable/user_guide/#build-constraints):
  modern isolated-build constraints differ from runtime constraints.
