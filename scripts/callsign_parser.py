#!/usr/bin/env python3
"""Conservative callsign extraction from raw speech transcripts.

This module contains no station whitelist and receives no expected identity.  It
recognizes compact callsigns and contiguous NATO/spelled-letter sequences with a
single digit.  Ambiguous tokens remain alternatives instead of being repaired
from context.
"""
from __future__ import annotations

import argparse
from itertools import product
import json
import re
from typing import Any, Iterable


PHONETIC = {
    "alfa": ("A",), "alpha": ("A",), "bravo": ("B",),
    "charlie": ("C",), "delta": ("D",), "echo": ("E",),
    "foxtrot": ("F",), "golf": ("G",), "gulf": ("G",), "hotel": ("H",),
    "india": ("I",), "juliett": ("J",), "juliet": ("J",),
    "kilo": ("K",), "lima": ("L",), "mike": ("M",),
    "november": ("N",), "oscar": ("O",), "papa": ("P",),
    "quebec": ("Q",), "romeo": ("R",), "sierra": ("S",),
    "tango": ("T",), "uniform": ("U",), "victor": ("V",),
    "whiskey": ("W",), "whisky": ("W",), "xray": ("X",),
    "yankee": ("Y",), "zulu": ("Z",), "zed": ("Z",),
}
DIGITS = {
    "zero": ("0",), "one": ("1",), "two": ("2",),
    "three": ("3",), "four": ("4",), "five": ("5",),
    "six": ("6",), "seven": ("7",), "eight": ("8",),
    "nine": ("9",),
}
AMBIGUOUS = {"oh": ("O", "0")}
CALLSIGN_RE = re.compile(r"[A-Z]{1,2}[0-9][A-Z]{1,4}(?:/[A-Z0-9]+)?")
TOKEN_RE = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)?|[0-9]+")


def _values(word: str) -> tuple[str, ...] | None:
    original = word.replace("-", "")
    value = original.lower()
    if value in PHONETIC:
        return PHONETIC[value]
    if value in DIGITS:
        return DIGITS[value]
    if value in AMBIGUOUS:
        return AMBIGUOUS[value]
    if len(value) == 1 and value.isalpha():
        return (value.upper(),)
    if len(value) == 1 and value.isdigit():
        return (value,)
    # ASR commonly emits a short spelled prefix as one uppercase token ("KJ").
    # Do not accept ordinary lowercase words or longer initialisms this way.
    if 1 < len(original) <= 2 and original.isalpha() and original.isupper():
        return (original,)
    return None


def _tokens(text: str) -> list[dict[str, Any]]:
    return [{"word": m.group(0), "char_start": m.start(), "char_end": m.end()}
            for m in TOKEN_RE.finditer(text)]


def _timed_tokens(text: str, words: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    transcript_tokens = _tokens(text)
    transcript_index = 0
    result = []
    for item in words:
        word = item.get("word", item.get("text", ""))
        if not isinstance(word, str):
            continue
        for match in TOKEN_RE.finditer(word):
            token = {"word": match.group(0), "char_start": None, "char_end": None}
            # Align backend word records to the raw transcript without changing
            # either. If alignment fails, timestamps and reconstructed raw text
            # remain available while character offsets stay explicitly unknown.
            while transcript_index < len(transcript_tokens):
                candidate = transcript_tokens[transcript_index]
                transcript_index += 1
                if candidate["word"].casefold() == token["word"].casefold():
                    token["char_start"] = candidate["char_start"]
                    token["char_end"] = candidate["char_end"]
                    break
            for key in ("start", "end"):
                value = item.get(key)
                if isinstance(value, (int, float)):
                    token[key] = round(float(value), 3)
            result.append(token)
    return result


def parse_callsigns(text: str, words: Iterable[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return complete candidates and their raw supporting spans.

    When word timestamps are supplied they are retained.  No fuzzy edit,
    homophone repair, station lookup, or expected-identity matching is done.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for match in re.finditer(r"(?<![A-Z0-9])[A-Z]{1,2}[0-9][A-Z]{1,4}(?:/[A-Z0-9]+)?(?![A-Z0-9])",
                             text.upper()):
        key = (match.group(0), match.start(), match.end())
        seen.add(key)
        candidates.append({"candidate": match.group(0), "alternatives": [],
                           "complete": True, "source": "compact",
                           "raw_span": text[match.start():match.end()],
                           "char_start": match.start(), "char_end": match.end(),
                           "uncertainty": []})

    tokens = _timed_tokens(text, words) if words is not None else _tokens(text)
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for token in tokens:
        values = _values(token["word"])
        if values is None:
            if current:
                runs.append(current)
                current = []
            continue
        current.append({**token, "values": values})
    if current:
        runs.append(current)

    for run in runs:
        # A valid amateur callsign represented this way is 3-7 tokens. Examine
        # all contiguous subranges so surrounding spelled prose is not absorbed.
        run_candidates = []
        for left in range(len(run)):
            for right in range(left + 3, min(len(run), left + 7) + 1):
                part = run[left:right]
                alternatives = []
                for values in product(*(item["values"] for item in part)):
                    value = "".join(values)
                    if CALLSIGN_RE.fullmatch(value):
                        alternatives.append(value)
                alternatives = sorted(set(alternatives))
                if not alternatives:
                    continue
                primary = alternatives[0]
                char_start, char_end = part[0].get("char_start"), part[-1].get("char_end")
                raw = (text[char_start:char_end] if isinstance(char_start, int) and
                       isinstance(char_end, int) else " ".join(x["word"] for x in part))
                key = (primary, char_start, char_end, raw)
                if key in seen:
                    continue
                seen.add(key)
                item: dict[str, Any] = {
                    "candidate": primary, "alternatives": alternatives[1:],
                    "complete": True, "source": "phonetic_or_spelled",
                    "raw_span": raw, "char_start": char_start, "char_end": char_end,
                    "uncertainty": (["ambiguous_token"] if len(alternatives) > 1 else []),
                }
                if "start" in part[0]:
                    item["start"] = part[0]["start"]
                if "end" in part[-1]:
                    item["end"] = part[-1]["end"]
                run_candidates.append((left, right, item))
        emitted = False
        for left, right, item in run_candidates:
            if any(other_left <= left and other_right >= right and
                   (other_left, other_right) != (left, right)
                   for other_left, other_right, _ in run_candidates):
                continue
            if any(len(token["values"]) > 1 for token in run[left:right]):
                item["uncertainty"] = ["ambiguous_token"]
            candidates.append(item)
            emitted = True
        if not emitted and len(run) >= 3:
            # Preserve a contiguous phonetic/spelled sequence which looks like
            # identifier material but lacks a valid callsign structure. It is
            # never included in exact-call scoring because complete is false.
            values = [item["values"][0] for item in run]
            char_start, char_end = run[0].get("char_start"), run[-1].get("char_end")
            raw = (text[char_start:char_end] if isinstance(char_start, int) and
                   isinstance(char_end, int) else " ".join(x["word"] for x in run))
            item = {"candidate": "".join(values), "alternatives": [],
                    "complete": False, "source": "phonetic_or_spelled",
                    "raw_span": raw, "char_start": char_start, "char_end": char_end,
                    "uncertainty": ["incomplete_structure"]}
            if "start" in run[0]:
                item["start"] = run[0]["start"]
            if "end" in run[-1]:
                item["end"] = run[-1]["end"]
            candidates.append(item)

    # Prefer the longest support when nested sequences yield the same candidate.
    candidates.sort(key=lambda x: (x["char_start"] is None,
                                   x["char_start"] if x["char_start"] is not None else 0,
                                   -(len(x["raw_span"])), x["candidate"]))
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", nargs="?")
    args = parser.parse_args()
    print(json.dumps(parse_callsigns(args.text or ""), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
