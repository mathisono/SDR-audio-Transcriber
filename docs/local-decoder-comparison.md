# Local speech and CW decoder comparison

Status: **experimental pilot, 2026-09-28**. No production decoder was changed.
Private recordings, references, raw transcripts, model weights and full reports
remain outside Git.

This runbook compares speech and CW as separate tasks. Speech backends feed the
rule-based callsign parser only after raw ASR is frozen. CW backends consume the
preserved channel WAV independently of speech VAD. Expected identities are used
only by the scorer after decoding.

## Backend identities and licenses

| Backend | Pinned upstream/model revision | Kind | Upstream license |
| --- | --- | --- | --- |
| DeepCW | `e04/deepcw-engine@8e264d243bbd4467bd19f3f28292219405b47e0e` | ONNX neural CW | AGPL-3.0-only |
| multimon-ng | `EliasOenal/multimon-ng@0722194b7739748e49f18ac1fc76f236d4ca390d` | Conventional DSP CW | GPL-2.0 |
| internal `cw_decode_dsp_v5.5` | repository commit under test | Project DSP CW | repository license |
| Parakeet TDT 0.6B v3 | `nvidia/parakeet-tdt-0.6b-v3@541d1f99c6b0c3cd0b11a95167540bb8edefd82b` | Native local ASR, Q8 GGUF | CC-BY-4.0 |
| Qwen3-ASR 0.6B | `Qwen/Qwen3-ASR-0.6B@5eb144179a02acc5e5ba31e748d22b0cf3e303b0` | Official local ASR package | Apache-2.0 |

The adapters invoke local tools; they do not bundle or relicense upstream code or
weights. Review every upstream license before redistributing a runtime or model.
No model weights are committed by this project.

## Isolated installation

Set private paths outside the repository and pin the checked revisions. These
commands do not alter the capture service environment.

```bash
git clone https://github.com/e04/deepcw-engine "$RUNTIMES/deepcw-engine"
git -C "$RUNTIMES/deepcw-engine" checkout 8e264d243bbd4467bd19f3f28292219405b47e0e
python3 -m venv "$RUNTIMES/deepcw-venv"
"$RUNTIMES/deepcw-venv/bin/pip" install -r \
  "$RUNTIMES/deepcw-engine/examples/python/requirements.txt"

git clone https://github.com/EliasOenal/multimon-ng "$RUNTIMES/multimon-ng"
git -C "$RUNTIMES/multimon-ng" checkout 0722194b7739748e49f18ac1fc76f236d4ca390d
cmake -S "$RUNTIMES/multimon-ng" -B "$RUNTIMES/multimon-build" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$RUNTIMES/multimon"
cmake --build "$RUNTIMES/multimon-build" --parallel 2
cmake --install "$RUNTIMES/multimon-build"

git clone https://github.com/NVIDIA/NeMo-Speech.cpp "$RUNTIMES/NeMo-Speech.cpp"
git -C "$RUNTIMES/NeMo-Speech.cpp" checkout 97a15afa5caa9bce5baaa86c1184103877af4101
bash "$RUNTIMES/NeMo-Speech.cpp/scripts/install.sh" --version 0.1.0 \
  --prefix "$RUNTIMES/nemo-speech" --backend cpu --binary-only --no-modify-path
"$RUNTIMES/nemo-speech/bin/nemo-speech" pull parakeet-tdt

python3.12 -m venv "$RUNTIMES/qwen3-asr-venv"
"$RUNTIMES/qwen3-asr-venv/bin/pip" install 'qwen-asr==0.0.6'
HF_HOME="$RUNTIMES/hf-cache" "$RUNTIMES/qwen3-asr-venv/bin/huggingface-cli" \
  download Qwen/Qwen3-ASR-0.6B --revision 5eb144179a02acc5e5ba31e748d22b0cf3e303b0 \
  --local-dir "$RUNTIMES/qwen3-asr-0.6b"
```

The pilot used NeMo-Speech.cpp 0.1.0 and multimon-ng 1.6.2. Qwen used
`qwen-asr` 0.0.6, PyTorch 2.14.0+cpu and Transformers 4.57.6. The larger Qwen
1.7B checkpoint was not attempted: the available host had no GPU, 15 GiB RAM and
insufficient free persistent storage for another large isolated environment.

DeepCW metadata was inspected rather than inferred. At the pinned revision it
requires 3,200 Hz mono input, 5--20 second windows, `[batch, channel, time,
frequency]` float32 spectrograms, and a 400--1,200 Hz spectrogram band. The
checkpoint and metadata SHA-256 values in the pilot were respectively
`ef120799457bca042d4690944f0faf93268eb4654e7f50f28784ad63bdc1fe02` and
`b4342157b90229ee7380e165f3d8036179b80d1e6ae02f2557388b7fcd558c01`.

## Blind evaluation commands

Use checksum-addressed corpus paths, not descriptive filenames. `{wav}` is
expanded as one argument after command tokenization; no shell evaluates it.

```bash
python3 scripts/recording_corpus.py --root "$CORPUS" run \
  --engine deepcw --split validation --origin rf --timeout 180 \
  --command "$DEEPCW_PY scripts/deepcw_adapter.py {wav} \
    --python $DEEPCW_PY --decoder-script $DEEPCW/examples/python/decode_morse.py \
    --model $DEEPCW/model.onnx --metadata $DEEPCW/model.onnx.json \
    --upstream-revision 8e264d243bbd4467bd19f3f28292219405b47e0e \
    --derived-dir $DERIVED/deepcw --timeout 90"

python3 scripts/recording_corpus.py --root "$CORPUS" run \
  --engine multimon-cw --split validation --origin rf --timeout 90 \
  --command "python3 scripts/multimon_cw_adapter.py {wav} \
    --binary $MULTIMON/bin/multimon-ng \
    --upstream-revision 0722194b7739748e49f18ac1fc76f236d4ca390d \
    --derived-dir $DERIVED/multimon --timeout 60"

python3 scripts/recording_corpus.py --root "$CORPUS" run \
  --engine cw-internal --split validation --origin rf --timeout 90

python3 scripts/recording_corpus.py --root "$CORPUS" run \
  --engine parakeet --split validation --origin rf --timeout 210 \
  --model nvidia/parakeet-tdt-0.6b-v3-q8_0 --device cpu --compute-type q8_0 \
  --command "python3 scripts/nemo_speech_adapter.py {wav} \
    --binary $NEMO/bin/nemo-speech --model $PARAKEET_GGUF \
    --model-revision 541d1f99c6b0c3cd0b11a95167540bb8edefd82b \
    --device cpu --timeout 180"

python3 scripts/recording_corpus.py --root "$CORPUS" run \
  --engine qwen3-asr --split validation --origin rf --timeout 360 \
  --model Qwen/Qwen3-ASR-0.6B --device cpu --compute-type bfloat16 \
  --command "nice -n 10 env HF_HOME=$QWEN_CACHE $QWEN_PY \
    scripts/qwen_asr_adapter.py {wav} --model $QWEN_MODEL \
    --model-revision 5eb144179a02acc5e5ba31e748d22b0cf3e303b0 \
    --device cpu --dtype bfloat16"
```

The Whisper control remains the repository's real `small.en`, English, CPU INT8,
beam 5, repository VAD and no cleanup. The comparison run used faster-whisper
1.2.1/CTranslate2 4.8.2 because the legacy 0.10.1 environment was not compatible
with the available Python. Record this discrepancy; do not call the two runtimes
equivalent.

After a parser change, preserve raw ASR and frozen review IDs by creating a child
run rather than overwriting results:

```bash
python3 scripts/recording_corpus.py --root "$CORPUS" \
  rescore-callsigns PARENT_SPEECH_RUN --repo .
```

## Small real-recording pilot

The fixed candidate-enriched pilot contains six separate preserved 60-second RF
source WAVs (360 seconds total). Three independently reviewed CW-ID events and four
previously reviewed spoken callsign occurrences are linked to those sources. The
new corpus-native callsign fields currently cover two of the four speech
occurrences. Three additional RF clips were retained as ordinary/mixed controls,
but they lack independently reviewed absence labels and are therefore unscored.
Three separately reviewed 45-second manual CW intervals were evaluated in a
different corpus and are never pooled with the full-source result.

### CW results

| Backend | Full 60-second sources | Manual 45-second intervals | Full-set wall / RTF | Peak RSS |
| --- | ---: | ---: | ---: | ---: |
| DeepCW | 3/3 exact IDs, 0 extra reviewed callsigns | 2/3, 0 extra | 33.55 s / 0.093 | 204,164 KiB |
| multimon-ng MORSE_CW | 2/3, 0 extra | 2/3, 0 extra | 18.10 s / 0.050 | 98,224 KiB |
| internal DSP | 1/3, 3 extra | 2/3, 5 extra | 40.43 s / 0.112 | 95,824 KiB |

The interval result did not uniformly improve recovery: one event became a fused
non-exact token for DeepCW and multimon-ng. That is why interval and end-to-end
coverage stay separate. DeepCW measured the reviewed ID tone at 900 Hz, inside its
400--1,200 Hz band, so this pilot applied real resampling but no frequency shift.
The adapter records an explicit shift and derived checksum if a future measured
tone lies outside the supported band.

DeepCW has earned a larger trial; three candidate-enriched IDs are not enough for
deployment. multimon-ng remains a useful lightweight conventional comparison.
The internal decoder remains valuable as a third branch but its extra callsign-like
output needs stronger false-positive controls.

### Speech results

| Backend | Blind exact callsign recovery | Completion | Timing |
| --- | ---: | --- | --- |
| Whisper `small.en` control | 0/4 previously reviewed occurrences | 6/6 | 8.60 s inference / 360 s audio; 1,086,724 KiB peak |
| Parakeet TDT 0.6B v3 Q8 | 1/4 source-linked reviews; corpus-native 1/2 | 6/6 (3 text, 3 no-decode) | 236.67 s cold-process wall / RTF 0.657; 1,167,272 KiB peak |
| Qwen3-ASR 0.6B | 0/1 reviewed completed clip | interrupted: 1 text, 1 no-decode, 1 process error, 3 not completed | completed clip: 1.06 s load, 176.52 s inference, 185.55 s outer wall / RTF 3.09; 2,750,536 KiB peak in smoke run |

Parakeet's raw output contained one complete phonetic callsign sequence and earned
a larger, session-balanced speech trial. It did not recover the other reviewed
callsigns, and its general WER is unreportable beyond one verbatim reviewed clip.
Qwen's third process exited with signal-derived code `-9`; the interrupted run
keeps all remaining files as `not_completed`. On this CPU-only host it has not
earned a larger trial. The exact cause of `-9` was not established, so resource
exhaustion remains a hypothesis, not a finding.

## Coverage limits and safety result

- Pilot goals remain short: 3 reviewed CW events versus 20--30 requested, 4
  reviewed speech callsigns versus 30--50 requested, and no held-out independent
  session comparison.
- Real silence/noise/steady-tone controls still await independent review. Decoder
  text on unreviewed controls is preserved but is not scored as a false positive.
  Synthetic silence/noise tests establish plumbing only.
- WER is null except for the single verbatim reviewed speech clip. CW CER is null
  because the reviews confirm identities, not full Morse character streams.
- Speech/CW overlap and transmission-boundary membership need explicit event-level
  annotations before a larger trial. Adjacent crops are not independent events.
- MorseAngel remains **blocked**: the repository contains a supervised adapter but
  no verified local backend command. A GUI-only or absent backend was not counted
  as a decoder and did not delay the first comparison.
- The MSE-88 campaign recorder was inactive during this comparison. Its existing
  OpenWebRX SDR owner remained running; no retune, restart, second SDR consumer,
  service replacement, deletion, automatic bookmark or production switch occurred.

Run the full regression suite and source checks before comparing another backend:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/*.py tests/*.py
git diff --check
```

## 2026-10-24 speech implementation selection

A direct same-input follow-up imported the frozen 35-clip blind-review package
(936.2 seconds) into a new private checksum-addressed corpus. Parakeet and Whisper
each completed 35/35 clips with identical 23 text / 12 no-decode status counts and
no errors.

| Backend | Wall / outer RTF | Peak RSS | Blind audio-model transcript disagreement* |
| --- | ---: | ---: | ---: |
| Parakeet TDT 0.6B v3 Q8 | 550.99 s / 0.588 | 1,167,224 KiB | 0.510 |
| faster-whisper `small.en` | 189.36 s / 0.202 | 920,244 KiB | 0.637 |

\*Micro word edits divided by the automated review transcript's word count across
22 clips where both branches returned text. This is model-to-model agreement, not
human WER or accuracy.

Both local backends matched the blind audio model's speech-presence decision on
all 33 schema-conforming/usable reviews (22 speech, 11 no speech). Parakeet had one
exact callsign-string agreement (`KJ6DZB`); Whisper's one exact string agreement
was `BLAST`, a spelled word rather than a valid amateur callsign. Neither agreement
is human ground truth. Parakeet also produced callsign-shaped candidates on eight
clips where the audio model returned no spoken callsign, so parser false-positive
behavior remains an explicit review target.

**Selection:** use Parakeet as the primary experimental speech backend for the
transcriber because it has the only human-reviewed exact callsign recovery in the
pilot (1/4 versus Whisper 0/4) and the lower automated transcript-disagreement
rate. Keep faster-whisper `small.en` as the faster baseline/fallback. Do not claim
population WER or production accuracy until session-balanced human transcripts,
spoken callsigns, and negative controls are reviewed.
