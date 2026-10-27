# Speech transcriber selection

Decision date: 2026-10-24 UTC

## Decision

**Select Parakeet TDT 0.6B v3 Q8 as the primary experimental local speech backend.**

- Keep faster-whisper `small.en` as the faster baseline and operational fallback.
- Keep model outputs, timestamps, callsign candidates, and provenance verbatim; do not autocorrect candidate callsigns.
- Treat this as an implementation selection, not a production-accuracy claim. The same-clip 35-item comparison has no independent human transcript reference, so WER and field callsign accuracy remain null.

The selected Parakeet model revision is `541d1f99c6b0c3cd0b11a95167540bb8edefd82b`; the evaluated Q8 GGUF SHA-256 is `e3880d0aaaaf2c308ea2c35016b2b895c423eb3fda924c1b463d1c19b7f4d32e`.

## Same-clip evaluation

Both backends processed the same checksum-addressed 35-clip corpus (936.2 seconds) sequentially on CPU with bounded timeouts, CPU affinity 0--3, and nice level 15. No network or hosted API was used for these local runs.

| Backend | Run ID | Text | No decode | Error | Wall / outer RTF | Peak RSS |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Parakeet TDT 0.6B v3 Q8 | `8ee6fab4310343debc9a44127107f666` | 23 | 12 | 0 | 550.99 s / 0.588 | 1,167,224 KiB |
| faster-whisper `small.en` | `051e8eda54fd442b9f08a3f5e6b19e07` | 23 | 12 | 0 | 189.36 s / 0.202 | 920,244 KiB |

Both local backends agreed with all 33 schema-usable blind audio-model speech-presence decisions: 22 speech and 11 no speech. On the 22 clips where both branches returned text, Parakeet's micro word-edit disagreement with the automated review transcript was 0.510, versus 0.637 for Whisper. These are automated model-to-model disagreement rates, not human WER.

The callsign-candidate comparison also remains automated and unverified:

| Backend | Local candidate clips | Both candidate | Local only | Model only | Neither | Exact string agreement |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Parakeet | 13 | 5 | 8 | 2 | 18 | 1 (`KJ6DZB`) |
| Whisper | 6 | 4 | 2 | 3 | 24 | 1 (`BLAST`, a spelled word rather than a valid amateur callsign) |

Parakeet's eight local-only candidate clips make callsign-parser false positives an explicit review target.

## Human evidence and limits

The earlier fixed six-source human-reviewed pilot remains the only scored speech-callsign subset: Parakeet recovered 1/4 source-linked spoken callsigns, while Whisper recovered 0/4. That small, candidate-enriched pilot supports choosing an implementation lane but does not establish population-level accuracy. No full human transcript exists for the 35-clip package, so general WER/CER and verified field callsign precision/recall remain null.

## Transcriber integration

The worker now accepts `--speech-backend parakeet`, a required trusted `--parakeet-command`, and a bounded `--speech-timeout`. It validates a strict one-shot JSON result, preserves raw text and model provenance in the durable checkpoint, and archives failures without deleting source audio. Whisper remains the default so existing deployments do not change implicitly.

A real one-clip worker smoke test completed successfully on `review-014.wav`: the audio SHA-256 matched the corpus asset, the final record carried the pinned model revision and hash, and 32 timestamped word segments were preserved. This verifies integration behavior, not transcript accuracy.

Final repository verification passed: 116 tests succeeded, 2 dependency-gated DeepCW preprocessing tests were skipped, Python compilation and shell syntax checks passed, and `git diff --check` was clean.

## Next gate

Freeze session-balanced human references for complete speech transcripts, spoken callsigns, confident absences, and unclear cases. Score WER/CER and exact callsign precision/recall without decoder hints, with local-only callsign candidates prioritized for false-positive review. Keep the backend experimental until that gate passes.

Machine-readable evidence: [`speech-model-reconciliation-20261024.json`](speech-model-reconciliation-20261024.json).
