# Parallel CW Decoder Workflow

This note captures the next SDR audio transcription architecture for testing CW decoding without breaking the existing speech transcription pipeline.

## Decision

The SDR audio transcription workflow stays centered on audio capture, Whisper transcription, cleanup, classification, and web output.

MorseAngel, or another CW-specific model/decoder, should **not** become the main pipeline. It should replace only the current self-made Python DSP CW decoder path.

The same completed WAV clip should be processed in parallel:

```text
SDR / rtl_fm / GNU Radio audio input
        |
        v
scripts/clip_writer.py
        |
        v
runtime/queue/*.wav
        |
        +------------------------------+
        |                              |
        v                              v
Whisper / faster-whisper              CW decoder sidecar
speech transcription                   MorseAngel / future CW model
        |                              |
        v                              v
speech transcript event                CW decoded text event
        |                              |
        +---------------+--------------+
                        |
                        v
          classifier / label promotion model
                        |
                        v
        runtime/transcripts/*.html + JSON evidence
```

## What this replaces

Replace:

```text
scripts/cw_decode.py as the primary CW decoding engine
```

Keep:

```text
scripts/clip_writer.py
scripts/transcribe_worker.py
scripts/clip_classifier.py
runtime queue / processing / done folders
Whisper / faster-whisper transcription
LM Studio / Qwen cleanup
classification_state.json evidence accumulation
static web transcript pages
```

The existing in-repo DSP decoder can remain as a baseline/fallback during testing, but it should no longer be treated as the long-term CW decoding solution.

## Target worker behavior

For each completed WAV clip, the worker should allow two independent decoder branches:

1. Speech branch
   - Input: completed WAV clip
   - Engine: faster-whisper
   - Output: raw speech text, optional cleanup text

2. CW branch
   - Input: the same completed WAV clip
   - Engine: external CW command, initially a MorseAngel adapter or wrapper
   - Output: normalized CW text JSON

The classifier should receive a merged object containing whatever results are available. If CW decoding fails or times out, the speech pipeline should still complete.

## Normalized event shape

```json
{
  "timestamp_start": "2026-05-14T12:01:22.000Z",
  "timestamp_end": "2026-05-14T12:01:32.000Z",
  "source": "MSE-88",
  "receiver": "rx-1",
  "frequency_hz": 442275000,
  "mode": "nfm",
  "audio_file": "runtime/done/example.wav",
  "results": {
    "speech": {
      "engine": "faster-whisper",
      "text": "...",
      "confidence": null
    },
    "cw": {
      "engine": "morseangel-adapter",
      "text": "CQ CQ DE KJ6DZB",
      "callsigns": ["KJ6DZB"],
      "wpm": null,
      "confidence": null
    }
  }
}
```

## Existing hook to use first

`clip_classifier.py` already has an external decoder hook:

```bash
.venv/bin/python3 scripts/clip_classifier.py \
  runtime/done/some_repeater_clip.wav \
  --cw-external-command "some-cw-decoder --input {wav}" \
  --pretty
```

For testing MorseAngel or a later CW model, build a small adapter that accepts a WAV file and prints decoded CW text to stdout. The classifier currently reads stdout from the external command and extracts callsigns from that text.

A future adapter should normalize richer JSON, but the first test can be simple stdout text.

## MorseAngel adapter target

Desired command shape:

```bash
.venv/bin/python3 scripts/morseangel_adapter.py \
  --input runtime/done/some_repeater_clip.wav \
  --output-json runtime/done/some_repeater_clip.cw.json \
  --timeout 20
```

Minimum stdout for compatibility with the current classifier:

```text
CQ CQ DE KJ6DZB
```

Better JSON output for future classifier work:

```json
{
  "engine": "morseangel",
  "decoded": true,
  "text": "CQ CQ DE KJ6DZB",
  "callsigns": ["KJ6DZB"],
  "confidence": null,
  "wpm": null,
  "error": null
}
```

## Testing plan

1. Capture several WAV clips with known CW ID audio.
2. Run the current internal decoder and save results.
3. Run the external CW command hook against the same WAV clips.
4. Compare decoded text, callsign extraction, and classifier label candidates.
5. Keep failed/uncertain clips for reprocessing as the CW model improves.

Example baseline command:

```bash
.venv/bin/python3 scripts/clip_classifier.py \
  runtime/done/some_repeater_clip.wav \
  --pretty
```

Example external CW test command:

```bash
.venv/bin/python3 scripts/clip_classifier.py \
  runtime/done/some_repeater_clip.wav \
  --cw-external-command ".venv/bin/python3 scripts/morseangel_adapter.py --input {wav}" \
  --cw-external-timeout 30 \
  --pretty
```

## Failure rules

- CW decoder timeout should not fail the full transcription job.
- CW decoder empty output should be marked as no CW evidence, not as a fatal error.
- Speech transcript should still be written when the CW branch fails.
- CW text should be stored as evidence, not final truth.
- The classification model decides how much to trust speech, CW, tone, and callsign evidence together.

## Implementation notes

Near-term work:

- Add `scripts/morseangel_adapter.py` or equivalent once the MorseAngel invocation is confirmed.
- Add a worker option such as `--cw-external-command` to the live transcription path if it is not already exposed there.
- Preserve the existing classifier state format while adding the external CW evidence source.
- Keep the internal DSP decoder available as `internal_cw_decoder` for A/B comparison until the external decoder is proven better.
