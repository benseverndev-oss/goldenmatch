#!/usr/bin/env python
"""Generate the sketch-core golden-vector fixture from the Python reference.

This is the SINGLE source of golden vectors: it imports ``goldenmatch.core.sketch``
(the authoritative reference) and writes ``tests/fixtures/sketch_golden.json``.
The Rust crate, the native binding, and the TS port all assert against the same
file. Re-run after any deliberate algorithm change (which must be rare and
accompanied by a parity-contract update):

    python scripts/gen_sketch_golden.py

u64 values are serialized as DECIMAL STRINGS (JSON cannot represent u64 exactly).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make goldenmatch importable without installing the worktree package.
_PKG = Path(__file__).resolve().parents[1] / "packages" / "python" / "goldenmatch"
sys.path.insert(0, str(_PKG))

from goldenmatch.core import sketch  # noqa: E402

_FIXTURE = _PKG / "tests" / "fixtures" / "sketch_golden.json"

# (text, mode, k, num_perms, num_bands, seed) — chosen to exercise every edge.
_CASES = [
    ("hello world", "char", 3, 8, 4, 42),
    ("hello world", "word", 2, 16, 8, 7),
    ("", "char", 3, 8, 4, 0),  # empty -> all-MAX signature
    ("   \t\n", "word", 2, 8, 4, 0),  # whitespace-only -> zero tokens -> empty set
    ("ab", "char", 5, 8, 4, 1),  # short input (n < k) -> single shingle
    ("x", "word", 3, 8, 4, 1),  # short word input
    ("a b c", "word", 1, 8, 4, 3),  # NBSP is NOT a separator; ASCII space is
    ("hello", "char", 1, 8, 4, 9),  # single-char shingles
    ("héllo wörld", "char", 3, 16, 8, 2),  # multibyte / accented
    ("東京タワー", "char", 2, 16, 4, 5),  # CJK code points
    ("foo foo foo bar bar", "word", 2, 16, 8, 11),  # repeated tokens (dedup)
    (
        "the quick brown fox jumps over the lazy dog " * 8,
        "word",
        3,
        32,
        16,
        13,
    ),  # long text, larger params
    ("the quick brown fox", "char", 4, 64, 8, 0),  # bigger num_perms
]


def _to_strs(xs: list[int]) -> list[str]:
    return [str(x) for x in xs]


def main() -> None:
    out = []
    for text, mode, k, num_perms, num_bands, seed in _CASES:
        sh = sketch.shingle(text, mode, k)
        sig = sketch.signature(sh, num_perms, seed)
        bands = sketch.band_hashes(sig, num_bands)
        out.append(
            {
                "text": text,
                "mode": mode,
                "k": k,
                "num_perms": num_perms,
                "num_bands": num_bands,
                "seed": seed,
                "shingles": _to_strs(sh),
                "signature": _to_strs(sig),
                "band_hashes": _to_strs(bands),
            }
        )
    _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    _FIXTURE.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(out)} cases to {_FIXTURE}")


if __name__ == "__main__":
    main()
