# Follow-up speech comparison — 2026-10-26

Status: **completed experimental comparison: 164/164 model/clip evaluations**.
Every backend produced 41 terminal results; empty outputs are retained. Three
earlier thermal aborts were recovered without repeating completed files. Final
integrity checks verified 41 original inputs, 41 derivatives and 28 model/runtime
files. Both receiver containers remain running; no live backend was replaced.

## Decision and observed results

**Keep the current v3 deployment unchanged for now. Make v2 ONNX INT8 the next
speed-focused candidate for a human-reviewed A/B test.** It has substantial measured
compute headroom and the least disagreement with the automated review, but no
recovery of the four historical reviewed spoken callsigns. That is not an accuracy
win. A matched-runtime v3 ONNX comparison would help separate model-version effects
from the current v3 C++ implementation's runtime costs.

Do not select **Whisper large-v3 on this CPU profile** for the near-live GUI: it
missed the speed goal, consumed much more memory, and showed no pilot-callsign
advantage. No GPU or LM Studio ASR capability/performance was tested. Vosk is the
lowest-memory option here, but its transcript disagreement and pilot results do
not support selecting it for callsign-focused use.

All four returned text on the same 23/35 review clips and empty output on the
other 12. On the 33 schema-usable automated-review cases, all four agreed on 22
speech and 11 no-speech decisions. **Agreement is not human truth.**

| Model/runtime | Review-set decode wall | RTF including within-clip pauses | Sampled peak process-group RSS |
| --- | ---: | ---: | ---: |
| Parakeet v2 / sherpa ONNX INT8, two allowed CPUs | 93.02 s | 0.099 | 1.92 GiB |
| Vosk small English, two allowed CPUs | 101.82 s | 0.109 | 0.32 GiB |
| Current v3 / NeMo Q8, mixed two-/one-CPU profiles | 1,000.47 s | 1.069 | 1.18 GiB |
| Whisper large-v3 / faster-whisper INT8, one CPU | 1,821.27 s | 1.945 | 5.72 GiB |

The review set contains 936.2 s of audio. Load time, between-clip cooling and
operator time between retries are not in this table. It is **not a controlled
model-version speed ranking**: runtimes, CPU profiles and thermal pacing differ.
The recorded v3 two-CPU subset had approximate unpaused RTF 0.321, while its
one-CPU subset had 0.772. Whisper's approximate unpaused RTF was 1.152. Subtracting
recorded pauses is diagnostic, not a counterfactual measurement without a governor.
Whisper's median review-clip latency was 49.0 s and one clip took 183.8 s.
Its final sampled available-memory minimum was about 945 MiB; the 768 MiB reserve
was maintained. VAD skipped ASR entirely on 15/41 inputs: these are real pipeline
evaluations, not 41 full Whisper decoder executions or proof of speech absence.

| Backend | Legacy canonical-token recovery | Existing-parser whole-candidate recovery | Automated transcript edit disagreement |
| --- | ---: | ---: | ---: |
| Parakeet v2 | 0/4 | 0/4 | 0.428 |
| Current Parakeet v3 | 0/4 | 1/4 | 0.506 |
| Vosk small English | 0/4 | 0/4 | 0.904 |
| Whisper large-v3 | 0/4 | 0/4 | 0.599 |

The last column uses the same 22 nonempty automated transcripts (1,547 normalized
reference tokens), not independent human references. Do not convert these values
into human-accuracy percentages. The callsign views and their limitations are
defined below. **Human WER and representative field callsign accuracy remain null.**

Next: independently review paired disagreements, include confidently reviewed
negatives and weak/strong/boundary examples, then run a bounded short-chunk live
latency/coverage test before changing the operational backend. This run did not
test new live RF, pre-roll behavior, real-time delivery, or the complete optimizer.

Machine-readable aggregate: [speech-comparison-20261026.json](speech-comparison-20261026.json).
The prior [experimental selection](speech-model-selection.md) remains historical
evidence; the new timings must not be substituted into it without their profiles.

## Frozen design

- Processing host: io3, Intel i7-7700, 8 GB RAM, CPU only. Isolated private runtime;
  initially two allowed logical CPUs (2,3), later one CPU for guarded runs as
  detailed below; nice 15, one model at a time. No receiver retune,
  RF capture, service replacement, or hosted audio upload.
- Same immutable 35 blinded review clips (936.2 s), plus a separately reported
  historical six-source pilot (360 s). These are previously used validation audio,
  **not a new held-out dataset** or 41 independent received events.
- SHA-verified PCM16 mono sources, explicit FFmpeg 16 kHz derivatives with source
  sample ranges and checksums. No noise suppression, target hints, expected-station
  prompts, transcript cleanup or hidden callsign substitutions.
  Preprocessor: FFmpeg 4.4.2-0ubuntu0.22.04.1; host kernel 5.15.0-130-generic.
- Models stay loaded between clips. Load time, inference wall time, cumulative
  process-group peak RSS (sampled every two seconds), temperatures, failures and
  real-time factor are distinct.
  These full-clip batch timings do not directly establish GUI streaming latency.
- Stop/defer at 85 C, less than 768 MiB available RAM, less than 8 GiB free disk,
  300 s per clip/load, or one hour per model. Preserve partial/failed results;
  never silently replay an uncertain completed request.

### Thermal pacing amendment (after initial guarded runs)

Vosk completed 41/41. Parakeet v2 completed the 35-clip set but the 85 C hard guard
stopped its first pilot source; the missing six sources were resumed after verifying
the old worker had stopped. Parakeet v3 then hit the same guard after four completed
clips. Original results/logs remain; completed clips are not replayed.

For the second v3 attempt, the test supervisor paused only its own isolated process
group at 75 C and resumes at 65 C, with a maximum 120-second cooling pause. The 85 C
hard abort remains unchanged. Both inference-wall time including pauses and the
approximate unpaused interval are reported; between-clip cooling and restart/load
overhead are retained separately. Initial resource samples were two seconds apart;
the thermal governor samples every half second. This is test-only pacing, not a
deployed production optimizer or proof of uninterrupted live performance.

The initial termination path allowed a warm-server child to drain briefly after
its worker exited. Its exit was checked before resuming; the hard-stop path now
kills the entire test process group so descendants cannot keep heating the host.
No unrelated processes or live services are targeted.

V3's second attempt nevertheless hit an explicitly recorded 85 C at review-015,
after 14 saved results. Its final bounded retry used one allowed CPU (2), one
thread setting, 65/55 C pause/resume thresholds and 0.1-second resource sampling.
Only missing outputs were processed. These mixed execution profiles must be shown
separately in timing reports; they are not an apples-to-apples model speed ranking.
The same waveform/model settings were retained for recognition comparison. The
third attempt completed all remaining inputs. Whisper then completed all 41 inputs
under the same one-CPU, 65/55 C pacing profile without a hard stop. NeMo's thread
argument controls HTTP workers, not a verified ASR thread count; enforced CPU
affinity is the reliable compute-budget constraint here.

## Candidates

| Candidate | Runtime/configuration | Provenance |
| --- | --- | --- |
| Parakeet TDT 0.6B v2 | sherpa-onnx 1.12.26, ONNX INT8, greedy TDT | `csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8@1ab9323565ddb038682214b292f588070a538ce2` |
| Current Parakeet v3 | NeMo-Speech.cpp 0.1.0, Q8 GGUF, warm local server | NVIDIA revision `541d1f99c6b0c3cd0b11a95167540bb8edefd82b`; SHA `e3880d0aaaaf2c308ea2c35016b2b895c423eb3fda924c1b463d1c19b7f4d32e` |
| Whisper large-v3 | faster-whisper 1.2.1 / CTranslate2 4.8.4, CPU INT8; repository English/beam-5/VAD-on path | `Systran/faster-whisper-large-v3@edaa852ec7e145841d8ffdb056a99866b5f0a478` |
| Vosk small English | Vosk 0.3.45, small-en-us-0.15, unconstrained vocabulary | Official Alpha Cephei archive; archive and extracted hashes retained |

V2/v3 runtime and quantization differ. This compares deployable combinations,
not an isolated causal effect of model version. Vosk's existing streaming API is
fed the same finalized clips; no claim of measured live streaming latency follows.
Word/token timestamps remain native backend outputs, not interchangeable confidence.

## Accuracy contract

The 35-clip package has no full independently reviewed transcripts. Automated
audio-model references may measure disagreement, **not human WER or truth**.
The historical pilot has four human-confirmed spoken callsign occurrences; score
source-linked recovery separately with the pre-existing canonical NATO/number
equivalence rule, no fuzzy matching. The reviews were not fully blinded and the
pilot is candidate-enriched. Do not infer precision from unreviewed extra text or
treat repeated appearances of one event as independent occurrences.

Keep verbatim hypotheses separate from interpreted callsigns. New field WER,
false-positive rates on confidently reviewed noise, event-balanced performance,
boundary/pre-roll coverage and short live-chunk accuracy remain open. Independent
DeepCW capture is unchanged; speech VAD must never gate Morse input.

Two callsign scoring views are retained. The historical scorer accepts canonical
NATO tokens and looks for target substrings in normalized runs; it is not a
measurement of exact-call precision. A separate post-decode diagnostic requires a
whole unambiguous candidate match from the repository's already-existing,
unchanged callsign parser, which includes fixed variants such as `Gulf` → `G`.
That distinction affects v3's pilot result; it is not a new model decode or a
target-driven correction. Preserve both scores, raw text and derived candidates;
do not relabel the broader parser's output as canonical-token recovery. This
additional diagnostic was selected after inspecting the v3 scoring discrepancy,
not preregistered as the primary metric. It does not enlarge the reviewed sample.

Private artifacts live on io3 under `/home/w6bb/rf-model-comparison-20261026`.
No private audio, model weights or raw transcripts belong in Git.
