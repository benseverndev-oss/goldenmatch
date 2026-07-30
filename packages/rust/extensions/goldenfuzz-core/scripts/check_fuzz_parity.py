#!/usr/bin/env python3
"""Cross-check goldenfuzz-core's fuzz.* scorers against Python `rapidfuzz.fuzz`.

The rapidfuzz Rust crate 0.5.0 exposes only `fuzz::ratio`, so the composite
family (WRatio / QRatio / token_* / partial_*) is proven against the Python
oracle: the `emit_fuzz_corpus` Rust test writes
`target/goldenfuzz_fuzz_corpus.jsonl` (each line = the two inputs plus our ten
scores). This script recomputes each score with `rapidfuzz.fuzz` on the exact
same inputs and asserts equality within 1e-9 (absolute).

Note: rapidfuzz's default `fuzz` (C++) is byte-identical to its pure-Python
`fuzz_py` reference (verified: worst abs diff 0.0), so either is a valid oracle.

Run:
    cargo test -p goldenfuzz-core emit_fuzz_corpus   # regenerate the corpus
    uv run --no-project --with rapidfuzz --python 3.12 python \
        packages/rust/extensions/goldenfuzz-core/scripts/check_fuzz_parity.py

Exit 0 = all pairs matched; exit 1 = mismatches (or corpus missing/empty).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import rapidfuzz.fuzz as fuzz

# Windows consoles default to cp1252; the corpus is full of non-ASCII/whitespace.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except (AttributeError, ValueError):
    pass

FIELDS = [
    ("ratio", fuzz.ratio),
    ("partial_ratio", fuzz.partial_ratio),
    ("token_sort_ratio", fuzz.token_sort_ratio),
    ("token_set_ratio", fuzz.token_set_ratio),
    ("token_ratio", fuzz.token_ratio),
    ("partial_token_sort_ratio", fuzz.partial_token_sort_ratio),
    ("partial_token_set_ratio", fuzz.partial_token_set_ratio),
    ("partial_token_ratio", fuzz.partial_token_ratio),
    ("WRatio", fuzz.WRatio),
    ("QRatio", fuzz.QRatio),
]

TOL = 1e-9


def main() -> int:
    corpus_path = (
        Path(__file__).resolve().parent.parent / "target" / "goldenfuzz_fuzz_corpus.jsonl"
    )
    if len(sys.argv) > 1:
        corpus_path = Path(sys.argv[1])
    if not corpus_path.exists():
        print(f"corpus not found: {corpus_path}", file=sys.stderr)
        print("run `cargo test -p goldenfuzz-core emit_fuzz_corpus` first", file=sys.stderr)
        return 1

    lines = corpus_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        print("corpus is empty", file=sys.stderr)
        return 1

    n = 0
    mismatches: list[str] = []
    worst = 0.0
    worst_where = ""
    for line in lines:
        rec = json.loads(line)
        s1 = rec["s1"]
        s2 = rec["s2"]
        n += 1
        for name, fn in FIELDS:
            theirs = fn(s1, s2)
            mine = rec[name]
            diff = abs(theirs - mine)
            if diff > worst:
                worst = diff
                worst_where = f"{name} ({s1!r},{s2!r})"
            if diff > TOL:
                mismatches.append(
                    f"{name} ({s1!r},{s2!r}): goldenfuzz={mine!r} rapidfuzz={theirs!r} diff={diff}"
                )

    print(f"pairs fuzzed: {n}")
    print(f"scorers per pair: {len(FIELDS)}  (comparisons: {n * len(FIELDS)})")
    print(f"worst abs diff: {worst:.3e}  at {worst_where}")
    if mismatches:
        print(f"MISMATCHES: {len(mismatches)}")
        for m in mismatches[:50]:
            print("  " + m)
        return 1
    print("OK: all scorers within 1e-9 of rapidfuzz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
