#!/usr/bin/env python3
"""Cross-check goldenphonetic-core's phonetic encoders against Python `jellyfish`.

`jellyfish` (>= 1.0) is itself the Rust reference these functions port; this script
proves goldenphonetic-core is byte-for-byte identical to it. The Rust
`emit_phonetic_corpus` test writes `target/goldenphonetic_corpus.jsonl`; each line
is one record:

  {"kind":"u","s":..,"soundex":..,"metaphone":..,"nysiis":..,"mrc":<str|null>}
  {"kind":"p","s1":..,"s2":..,"mrcmp":<true|false|null>}

For unary records this recomputes soundex / metaphone / nysiis with jellyfish and
asserts EXACT string equality, and for the MRA codex asserts:
  * mrc == null  <=>  jellyfish.match_rating_codex(s) RAISES, and
  * mrc == <str> <=>  jellyfish.match_rating_codex(s) == that string (no raise).
For pair records it asserts jellyfish.match_rating_comparison(s1,s2) equals our
Option<bool> (None <-> null), which jellyfish itself returns (it never raises).

Run:
    cargo test -p goldenphonetic-core emit_phonetic_corpus   # regenerate the corpus
    uv run --no-project --with jellyfish --python 3.12 python \
        packages/rust/extensions/goldenphonetic-core/scripts/check_phonetic_parity.py

Exit 0 = all records matched; exit 1 = any mismatch (or corpus missing/empty).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import jellyfish

# Windows consoles default to cp1252; the corpus is full of non-ASCII.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except (AttributeError, ValueError):
    pass


def codex_or_raise(s: str):
    """Return the jellyfish codex string, or the sentinel RAISED on ValueError."""
    try:
        return jellyfish.match_rating_codex(s)
    except ValueError:
        return RAISED


RAISED = object()


def main() -> int:
    corpus_path = (
        Path(__file__).resolve().parent.parent / "target" / "goldenphonetic_corpus.jsonl"
    )
    if len(sys.argv) > 1:
        corpus_path = Path(sys.argv[1])
    if not corpus_path.exists():
        print(f"corpus not found: {corpus_path}", file=sys.stderr)
        print("run `cargo test -p goldenphonetic-core emit_phonetic_corpus` first", file=sys.stderr)
        return 1

    lines = corpus_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        print("corpus is empty", file=sys.stderr)
        return 1

    counts = {
        "soundex": 0,
        "metaphone": 0,
        "nysiis": 0,
        "match_rating_codex": 0,
        "match_rating_comparison": 0,
    }
    mismatches: list[str] = []
    n_unary = 0
    n_pairs = 0

    for line in lines:
        rec = json.loads(line)
        if rec["kind"] == "u":
            n_unary += 1
            s = rec["s"]

            for name in ("soundex", "metaphone", "nysiis"):
                theirs = getattr(jellyfish, name)(s)
                mine = rec[name]
                counts[name] += 1
                if theirs != mine:
                    mismatches.append(
                        f"{name}({s!r}): goldenphonetic={mine!r} jellyfish={theirs!r}"
                    )

            # match_rating_codex: null <-> jellyfish raises; else string equality.
            counts["match_rating_codex"] += 1
            theirs = codex_or_raise(s)
            mine = rec["mrc"]  # None (JSON null) or a string
            if mine is None:
                if theirs is not RAISED:
                    mismatches.append(
                        f"match_rating_codex({s!r}): goldenphonetic=Err(null) "
                        f"jellyfish={theirs!r} (did NOT raise)"
                    )
            else:
                if theirs is RAISED:
                    mismatches.append(
                        f"match_rating_codex({s!r}): goldenphonetic={mine!r} "
                        f"jellyfish RAISED ValueError"
                    )
                elif theirs != mine:
                    mismatches.append(
                        f"match_rating_codex({s!r}): goldenphonetic={mine!r} "
                        f"jellyfish={theirs!r}"
                    )
        else:  # pair
            n_pairs += 1
            s1, s2 = rec["s1"], rec["s2"]
            counts["match_rating_comparison"] += 1
            theirs = jellyfish.match_rating_comparison(s1, s2)  # True/False/None
            mine = rec["mrcmp"]  # True/False/None
            if theirs != mine:
                mismatches.append(
                    f"match_rating_comparison({s1!r},{s2!r}): "
                    f"goldenphonetic={mine!r} jellyfish={theirs!r}"
                )

    print(f"unary inputs fuzzed:  {n_unary}")
    print(f"pair inputs fuzzed:   {n_pairs}")
    print("per-function comparisons:")
    for name, c in counts.items():
        print(f"  {name:<26} {c}")
    total = sum(counts.values())
    print(f"total comparisons:    {total}")

    if mismatches:
        print(f"\nMISMATCHES: {len(mismatches)}")
        for m in mismatches[:80]:
            print("  " + m)
        return 1
    print("\nOK: all phonetic outputs byte-identical to jellyfish")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
