# Testing an external CW decoder

The adapter is **not** MorseAngel inference. It accepts an actual one-shot
command supplied with `--command` or `MORSEANGEL_COMMAND`. Without one, it returns
`decoded: false`, empty text/callsigns, and a configuration error.

## Wiring check (not a decode-accuracy test)

```bash
.venv/bin/python3 scripts/morseangel_adapter.py \
  --input /path/to/known-id.wav --json --pretty
```

An unconfigured adapter exits 2. Diagnostic JSON, filenames and `message`
fields never become decoded text.

## Use a configured backend

Substitute your backend's documented invocation; `ACTUAL_DECODER_COMMAND` below
is a placeholder, not an installed program:

```bash
export MORSEANGEL_COMMAND='ACTUAL_DECODER_COMMAND --input {wav}'
.venv/bin/python3 scripts/clip_classifier.py /path/to/known-id.wav \
  --cw-external-command '.venv/bin/python3 scripts/morseangel_adapter.py --input {wav} --json' \
  --cw-internal-timeout 20 --cw-external-timeout 30 --pretty
```

For the live speech worker, pass the same `--cw-external-command` and enable
`--enable-classifier`. Run `enrichment_worker.py` in a separate process from the
same repository root and environment. If the adapter uses an environment
variable, the sidecar must inherit it too.

## Output contract

A successful command must exit **0** and write only decoded text to stdout:

```text
DE KJ6DZB
```

Or a JSON object:

```json
{"decoded": true, "text": "DE KJ6DZB", "confidence": 0.82, "wpm": 18}
```

`decoded: false`, a nonempty `error`, or a nonzero exit suppresses all decoded
text/callsigns. Only `text` or `decoded_text` is accepted from JSON; diagnostic
`message`, `input`, paths and stderr are not evidence. JSON arrays, malformed
JSON and non-string text are rejected. Plain-text stdout is trusted only after
exit 0, so backends must send logs to stderr, never stdout. A backend score is
preserved as an uncalibrated diagnostic, not a station-identification probability.

Commands are tokenized before `{wav}` substitution, preserving spaces in paths.
They run without a shell, with positive finite deadlines and bounded captured
output. Timeout cleanup terminates their process group, including nested
repository adapters. This is not a sandbox: use trusted one-shot commands that
do not daemonize or start independent sessions.

## Acceptance work

Use held-out real receiver recordings with manually checked references: clear
IDs, late IDs, weak/noisy IDs, speech, silence, steady tones and mixed traffic.
Compare exact text, missed callsigns and unexpected callsigns for each backend.
Offline synthetic tests only establish limited controlled behavior and failure
isolation; they do not certify a neural backend or RF recognition accuracy.

Inspect `runtime/transcripts/evidence.html` for pending status, independent CW
results and errors, while `raw.html` continues displaying original speech.
