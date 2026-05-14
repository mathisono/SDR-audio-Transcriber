# Testing the External CW Adapter

This guide is for testing the parallel CW path without disrupting the normal Whisper transcription workflow.

## Current status

The live worker already supports an external CW decoder through:

```bash
--cw-external-command
--cw-external-timeout
```

The committed adapter shim is:

```text
scripts/morseangel_adapter.py
```

It is a stable command target for the pipeline. It does **not** assume a confirmed MorseAngel command-line interface yet. Instead, it can wrap any external decoder command once that command is known.

## Safe wiring test

This command verifies that the adapter exists and returns normalized JSON. It will not create false CW text because no decoder command is configured:

```bash
.venv/bin/python3 scripts/morseangel_adapter.py \
  --input runtime/done/some_repeater_clip.wav \
  --json \
  --pretty
```

Expected behavior when no command is configured:

```json
{
  "engine": "morseangel-adapter",
  "decoded": false,
  "text": "",
  "callsigns": [],
  "error": "no MorseAngel command configured; pass --command or set MORSEANGEL_COMMAND"
}
```

The adapter exits non-zero in this case, but prints no decoded CW text unless a decoder actually succeeds. That prevents accidental bad classifier evidence.

## Test the classifier hook

Run the existing classifier with the adapter as an external decoder:

```bash
.venv/bin/python3 scripts/clip_classifier.py \
  runtime/done/some_repeater_clip.wav \
  --cw-external-command ".venv/bin/python3 scripts/morseangel_adapter.py --input {wav}" \
  --cw-external-timeout 30 \
  --pretty
```

Expected result before MorseAngel is configured:

- Internal DSP CW decoder still runs as the baseline.
- External decoder section should show an error or no external text.
- No fake external CW label candidates should be added.

## Test the full live worker

Use the normal worker command, but add the external CW adapter hook:

```bash
.venv/bin/python3 scripts/transcribe_worker.py \
  --whisper-model small.en \
  --device cpu \
  --compute-type int8 \
  --no-cleanup \
  --enable-classifier \
  --classify-modes nfm \
  --cw-external-command ".venv/bin/python3 scripts/morseangel_adapter.py --input {wav}" \
  --cw-external-timeout 30
```

This keeps the normal flow:

```text
completed WAV clip
  -> faster-whisper speech transcription
  -> internal classifier baseline
  -> external CW adapter hook
  -> merged label candidates
  -> classification_state.json
  -> transcript pages
```

## Configure an actual CW decoder command

Once the MorseAngel or CW model invocation is confirmed, use either `--command` inside the adapter command:

```bash
.venv/bin/python3 scripts/clip_classifier.py \
  runtime/done/some_repeater_clip.wav \
  --cw-external-command ".venv/bin/python3 scripts/morseangel_adapter.py --input {wav} --command 'ACTUAL_DECODER_COMMAND --input {wav}'" \
  --cw-external-timeout 30 \
  --pretty
```

Or set an environment variable before starting the worker:

```bash
export MORSEANGEL_COMMAND='ACTUAL_DECODER_COMMAND --input {wav}'

.venv/bin/python3 scripts/transcribe_worker.py \
  --whisper-model small.en \
  --device cpu \
  --compute-type int8 \
  --no-cleanup \
  --enable-classifier \
  --classify-modes nfm \
  --cw-external-command ".venv/bin/python3 scripts/morseangel_adapter.py --input {wav}" \
  --cw-external-timeout 30
```

The adapter accepts either plain decoded text on stdout:

```text
CQ CQ DE KJ6DZB
```

or JSON stdout:

```json
{
  "text": "CQ CQ DE KJ6DZB",
  "confidence": 0.82,
  "wpm": 18
}
```

## Save normalized CW JSON sidecars

For A/B testing, write normalized CW output next to the clip:

```bash
.venv/bin/python3 scripts/morseangel_adapter.py \
  --input runtime/done/some_repeater_clip.wav \
  --command 'ACTUAL_DECODER_COMMAND --input {wav}' \
  --output-json runtime/done/some_repeater_clip.cw.json \
  --json \
  --pretty
```

## What to compare

For each known CW clip, compare:

```text
internal DSP decoder text
external CW adapter text
callsigns extracted
label_candidates added
stable label promoted over time
```

Good test clips:

- Clear repeater CW ID.
- Weak/noisy CW ID.
- Speech-only repeater traffic.
- Tone-only clip.
- Mixed speech plus CW ID.
- Empty/noise clip.

## Important failure rules

- A failed CW decoder should not stop Whisper transcription.
- Empty CW output should not add label candidates.
- CW text remains evidence, not final truth.
- The classifier decides how to merge speech callsigns, CW callsigns, and tone evidence.
