# Local and remote model providers

Status: design note; provider abstraction and optimizer are not yet implemented.

## Measured single-channel throughput

Real-time factor (RTF) is decoder wall time divided by audio duration. An RTF below
1.0 means a decoder processed audio faster than it arrived. On the identical
35-clip, 936.2-second CPU evaluation:

| Backend | Outer RTF | Processing speed | Linearized processing time for 60 s audio |
| --- | ---: | ---: | ---: |
| Parakeet TDT 0.6B v3 Q8 | 0.588 | 1.70x real time | 35.3 s |
| faster-whisper `small.en` | 0.202 | 4.95x real time | 12.1 s |

Those historical timings showed compute headroom, not validated continuous
capacity. These measurements include
cold per-clip evaluator overhead and are not a warmed multi-channel benchmark.
The standalone companion now uses a persistent local Parakeet service; the older
one-shot evaluator/adapter remains a distinct path.

The [2026-10-26 follow-up](speech-comparison-20261026.md) completed 41 files on each
of v2 ONNX INT8, current v3 Q8, Vosk small and Whisper large-v3 INT8. V2's review-set
RTF was 0.099 and Vosk's 0.109. Current v3 needed thermal stops and changed CPU
profiles; large-v3's guarded single-CPU RTF was 1.945 with 5.72 GiB sampled peak RSS.
These profiles differ, and neither short compute bursts nor subtracting thermal
pauses proves sustained GUI capacity. Test-only thermal pacing is not a deployed
hardware optimizer. No remote/GPU provider was benchmarked or implicitly selected.

Throughput is not end-to-end latency. The current pipeline starts transcription
after a finalized file closes in the file-based path. The companion instead uses
short pause-aware chunks. In either case, visible latency includes buffering plus
queueing and inference. A true incremental streaming decoder is distinct from
this chunked live display.

For planning only, aggregate decoder utilization can be estimated as:

```text
sum(channel duty cycle * measured backend RTF)
```

Target less than 0.7 until a real concurrent-channel benchmark establishes a safe
limit. The margin is for capture priority, bursts, model startup, thermals, and
queue recovery; it is not a validated capacity guarantee.

## Provider contract

All speech implementations should sit behind one normalized provider contract.
Every completed or failed attempt must preserve:

- provider type and endpoint class;
- exact engine/model identity, revision, and available artifact hashes;
- raw transcript, status, word/segment timestamps, and backend diagnostics;
- source-audio checksum and immutable recording reference;
- queue, network, model-load, inference, and total wall time where available;
- device/compute configuration and resource impact;
- failure reason, timeout, retry identity, and fallback history;
- remote-retention request and privacy policy when a clip leaves the host.

Initial provider types:

1. **Local embedded** -- a loaded in-process model such as faster-whisper.
2. **Local command** -- a trusted bounded adapter such as the current Parakeet path.
3. **Local service** -- a persistent model server on the receiver or a LAN GPU host.
4. **Remote ASR** -- an explicitly configured HTTP audio-transcription provider.

DeepCW remains an independent CW branch; speech-provider routing must never gate
or replace its preserved-audio input.

## LM Studio and remote providers

LM Studio is not automatically an ASR provider or a performance improvement. The
configured model must explicitly accept audio and expose a compatible
transcription endpoint. A text/vision model used for transcript cleanup is not an
audio decoder and must not be reported as one.

Remote performance includes upload, server queue, inference, download, retries,
and rate limits. A remote GPU may improve inference while still losing on total
latency. Sending RF audio off-host is an external disclosure and requires explicit
configuration and authorization; local-only operation must remain available.

The adapter should negotiate or validate capabilities before use: audio modality,
endpoint, accepted formats and limits, timestamp support, model identifier,
retention controls, authentication requirements, and error schema. Unsupported
capabilities are **Blocked or failed**, not silently emulated by transcript cleanup.

## Hardware optimizer

The optimizer should benchmark eligible provider profiles on a fixed local
calibration corpus, then choose only among profiles that satisfy operator-defined
constraints. Cache results by hardware fingerprint, provider/engine version, model
revision/hash, preprocessing, quantization, thread count, and chunk policy.

Measure at least:

- cold and warm RTF plus p50/p95 completion latency;
- sustained queue depth and recovery after bursts;
- CPU, RAM, GPU/VRAM, temperature, and power when available;
- network latency, reliability, rate limits, and monetary cost for remote routes;
- exact callsign recovery, false candidates, WER/CER, and reviewed coverage when
  independent human references exist.

Speed/resource tuning may be automatic within explicit bounds. Accuracy ranking
must use independently reviewed references; decoder confidence and automated
model agreement are not ground truth. A routing decision must be recorded on every
result so fallback never hides which provider produced the transcript.

Suggested constraints include maximum p95 RTF/latency, minimum free memory,
temperature ceiling, maximum remote cost, local-only privacy mode, and a reviewed
accuracy floor. If no profile satisfies all required constraints, report the exact
blocker instead of selecting the least-bad provider silently.

## Implementation order

1. Define the normalized provider request/result schema.
2. Convert Parakeet to an optional persistent local service while retaining the
   bounded command adapter.
3. Add a warm/cold hardware probe and reproducible benchmark command.
4. Add a generic remote audio-transcription adapter with capability validation.
5. Add constraint-based routing, explicit fallback provenance, and local-only mode.
6. Run a capture-safe concurrent-channel benchmark before enabling automatic
   multi-channel placement.

Model selection evidence and its limits remain in
[`speech-model-selection.md`](speech-model-selection.md).
