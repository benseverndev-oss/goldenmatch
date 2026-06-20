#!/usr/bin/env python
"""Generate the SimHash golden-vector fixture from the Python reference (#1082).

This is the SINGLE source of SimHash golden vectors: it imports
``goldenmatch.core.sketch`` (the authoritative reference) and writes
``tests/fixtures/sketch_simhash_golden.json``. The Rust crate, the native
binding, and the TS port all assert against the same file. Re-run after any
deliberate algorithm change (which must be rare and accompanied by a
parity-contract update):

    python scripts/gen_simhash_golden.py

Signatures serialize as ``list[int]`` of 0/1; band_hashes as DECIMAL STRINGS
(JSON cannot represent u64 exactly).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make goldenmatch importable without installing the worktree package.
_PKG = Path(__file__).resolve().parents[1] / "packages" / "python" / "goldenmatch"
sys.path.insert(0, str(_PKG))

from goldenmatch.core import sketch  # noqa: E402

_FIXTURE = _PKG / "tests" / "fixtures" / "sketch_simhash_golden.json"

_V8 = [0.5, -0.3, 0.8, 0.1, -0.9, 0.4, -0.2, 0.7]
_V16 = [0.5, -0.3, 0.8, 0.1, -0.9, 0.4, -0.2, 0.7, 1.1, -1.4, 0.05, -0.6, 0.9, 0.2, -0.7, 0.33]
_ZERO8 = [0.0] * 8
_NEG8 = [-1.0, -2.0, -3.0, -4.0, -5.0, -6.0, -7.0, -8.0]
_MIXED8 = [-1.0, 2.0, -3.0, 4.0, -5.0, 6.0, -7.0, 8.0]


def _vec64(seed: int) -> list[float]:
    # Deterministic 64-dim vector from the reference splitmix64 stream, mapped to
    # a small symmetric float range — exercises a larger dim without a data dep.
    out: list[float] = []
    state = seed
    for _ in range(64):
        v, state = sketch.splitmix64(state)
        out.append((v % 2001) / 1000.0 - 1.0)  # in [-1.0, 1.0]
    return out


# (label, vector, num_planes, num_bands, seed) — chosen to exercise every edge.
_CASES = [
    ("V8 / planes=8 seed=42", _V8, 8, 4, 42),
    ("V8 / planes=16 seed=7", _V8, 16, 4, 7),
    ("V16 / planes=16 seed=7", _V16, 16, 4, 7),
    ("V16 / planes=8 seed=3", _V16, 8, 2, 3),
    ("zero8 / planes=8 seed=42 (all ties)", _ZERO8, 8, 4, 42),
    ("neg8 / planes=8 seed=1", _NEG8, 8, 4, 1),
    ("mixed8 / planes=16 seed=11", _MIXED8, 16, 8, 11),
    ("dim64 / planes=16 seed=5", _vec64(5), 16, 4, 5),
    ("dim64 / planes=32 seed=13", _vec64(13), 32, 8, 13),
    ("dim64 / planes=64 seed=0", _vec64(0), 64, 16, 0),
    ("empty / planes=4 seed=1", [], 4, 2, 1),
]


def main() -> None:
    out = []
    for label, vector, num_planes, num_bands, seed in _CASES:
        sig = sketch.simhash_signature(vector, num_planes, seed)
        bands = sketch.simhash_band_hashes(sig, num_bands)
        out.append(
            {
                "label": label,
                "vector": vector,
                "num_planes": num_planes,
                "num_bands": num_bands,
                "seed": seed,
                "signature": sig,  # list[int] of 0/1
                "band_hashes": [str(x) for x in bands],  # u64 decimal strings
            }
        )
    _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    _FIXTURE.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(out)} cases to {_FIXTURE}")


if __name__ == "__main__":
    main()
