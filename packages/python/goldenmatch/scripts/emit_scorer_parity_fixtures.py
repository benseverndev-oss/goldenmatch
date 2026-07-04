#!/usr/bin/env python3
"""Emit rapidfuzz-sourced scorer parity goldens for the TS port.

Writes tests/parity/fixtures/scorer-rapidfuzz.json: rows of
[scorer, a, b, expected]. `expected` is rapidfuzz's normalized_similarity
(jaro/jaro_winkler/levenshtein), the token_sort base (normalize + Indel), or
exact 1/0. This is the BINDING oracle the pure-TS scorers must match to 4dp.

rapidfuzz 3.14.5. Deterministic (seeded). Imports rapidfuzz ONLY (no goldenmatch).
Run: PYTHONIOENCODING=utf-8 /d/show_case/goldenmatch/.venv/Scripts/python \
        packages/python/goldenmatch/scripts/emit_scorer_parity_fixtures.py
"""
import json
import random
import re
from pathlib import Path

from rapidfuzz.distance import Indel, Jaro, JaroWinkler, Levenshtein

OUT = (
    Path(__file__).resolve().parents[3]
    / "typescript/goldenmatch/tests/parity/fixtures/scorer-rapidfuzz.json"
)


def _token_sort_norm(s: str) -> str:
    toks = sorted(t for t in re.sub(r"[^a-z0-9\s]", " ", s.lower()).split() if t)
    return " ".join(toks)


def _score(scorer: str, a: str, b: str) -> float:
    if scorer == "jaro":
        return Jaro.normalized_similarity(a, b)
    if scorer == "jaro_winkler":
        return JaroWinkler.normalized_similarity(a, b)
    if scorer == "levenshtein":
        return Levenshtein.normalized_similarity(a, b)
    if scorer == "token_sort":
        return Indel.normalized_similarity(_token_sort_norm(a), _token_sort_norm(b))
    if scorer == "exact":
        return 1.0 if a == b else 0.0
    raise ValueError(scorer)


EMOJI = "\U0001F600"  # grinning face (one codepoint, two UTF-16 code units)

# Named anchors that MUST appear (the divergence red->green targets + canon).
ANCHORS = [
    ("jaro", "dabaeb", "dbea"),                   # transposition floor target -> 0.8056
    ("jaro_winkler", "ad", "abaed"),              # boost-threshold target -> 0.5667
    ("jaro", EMOJI + "ab", EMOJI + "ac"),         # non-BMP jaro -> 0.7778
    ("jaro_winkler", EMOJI + "ab", EMOJI + "ac"),  # non-BMP jw -> 0.8222
    ("levenshtein", EMOJI + "ab", EMOJI + "ac"),
    ("jaro_winkler", "café", "cafe"),
    ("levenshtein", "café", "cafe"),
    ("token_sort", "Café Bar", "bar café"),
    # canonical references (must stay byte-stable)
    ("jaro_winkler", "MARTHA", "MARHTA"),
    ("jaro_winkler", "DIXON", "DICKSONX"),
    ("jaro_winkler", "DWAYNE", "DUANE"),
    ("jaro_winkler", "John", "Jon"),
    ("jaro", "MARTHA", "MARHTA"),
    ("levenshtein", "kitten", "sitting"),
    ("token_sort", "John Smith", "Smith Johnson"),
    ("exact", "abc", "abc"),
    ("exact", "abc", "xyz"),
    ("jaro_winkler", "", ""),
    ("jaro_winkler", "abc", ""),
]

random.seed(2026)
POOLS = ["abcde", "abcdefghijklmnop", EMOJI + "\U0001F601ab", "éüname"]

rows: list[list] = [[s, a, b, round(_score(s, a, b), 6)] for s, a, b in ANCHORS]
for _ in range(120):
    pool = random.choice(POOLS)
    a = "".join(random.choice(pool) for _ in range(random.randint(0, 9)))
    b = "".join(random.choice(pool) for _ in range(random.randint(0, 9)))
    for s in ("jaro", "jaro_winkler", "levenshtein"):
        rows.append([s, a, b, round(_score(s, a, b), 6)])

# Real-name near-pairs — the JW prefix-boost / transposition surface on the
# inputs users actually feed. (A 2005-pair rapidfuzz sweep over this shape +
# multi-token phrases found max abs error 5.6e-17 vs the pure-TS scorers; these
# lock a representative slice into the committed gate so it can't regress.)
NAMES = [
    "martha", "marhta", "dixon", "dickson", "jellyfish", "smellyfish",
    "dwayne", "duane", "john", "jon", "smith", "smyth", "robert", "rupert",
    "saturday", "sunday", "kitten", "sitting", "william", "andrew", "andre",
    "taylor", "tyler", "moore", "more", "christopher", "kristopher",
    "elizabeth", "elisabeth", "catherine", "katherine", "stephen", "steven",
]
for _ in range(80):
    a = random.choice(NAMES)
    b = random.choice(NAMES)
    for s in ("jaro", "jaro_winkler", "levenshtein"):
        rows.append([s, a, b, round(_score(s, a, b), 6)])

# Multi-token phrases — token_sort's preprocessing surface (lowercase + strip
# non-alnum + split / sort / join), including reorderings, one-token swaps,
# punctuation, and case shifts. token_sort had the thinnest committed coverage.
_WORDS = ["acme", "corp", "new", "york", "mets", "the", "quick", "brown",
          "fox", "john", "smith", "st", "main", "co", "ltd", "inc"]
for _ in range(80):
    toks_a = [random.choice(_WORDS) for _ in range(random.randint(1, 4))]
    toks_b = toks_a[:]
    random.shuffle(toks_b)
    if random.random() < 0.4 and toks_b:
        toks_b[random.randrange(len(toks_b))] = random.choice(_WORDS)
    a = " ".join(toks_a)
    b = " ".join(toks_b)
    if random.random() < 0.3:
        a = a.upper()
    if random.random() < 0.3:
        b = b.replace(" ", ", ", 1) + "!"
    rows.append(["token_sort", a, b, round(_score("token_sort", a, b), 6)])

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(
    json.dumps({"_rapidfuzz_version": "3.14.5", "cases": rows}, ensure_ascii=False, indent=1),
    encoding="utf-8",
)
print(f"wrote {len(rows)} cases -> {OUT}")
