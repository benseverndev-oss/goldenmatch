#!/usr/bin/env python3
"""Author the fs-wasm cross-surface parity fixture from the Python NATIVE kernel.

The native `score_block_pairs_fs` and the TS `fs-wasm` binding both call the SAME
`goldenmatch-fs-core::score_fs_pair`, so their output is byte-identical by
construction. This emits a small zero-config block scored by the native kernel as
the ORACLE; `tests/parity/fs-wasm.parity.test.ts` feeds the identical inputs to
fs-wasm and asserts the same pairs.

Run with the native ext available (scripts/build_native.py):
    python scripts/emit_fs_wasm_fixture.py
"""
from __future__ import annotations

import json
import math
import pathlib

from goldenmatch.core._native_loader import native_module


def _weight_range(match_weights: list[list[float]]) -> tuple[float, float]:
    field_mins = [min(w) for w in match_weights]
    field_maxs = [max(w) for w in match_weights]
    regular_min = sum(field_mins)
    regular_max = sum(field_maxs)
    return regular_min, regular_max - regular_min


def main() -> None:
    mod = native_module()

    # 2 fields: jaro_winkler (id 0), exact (id 3). 6 rows, one block.
    field0 = ["robert", "robert", "william", "willyam", "bob", "xyzzy"]
    field1 = ["smith", "smith", "jones", "jones", "brown", "zzz"]
    field_values = [field0, field1]
    row_ids = list(range(len(field0)))
    block_sizes = [len(field0)]
    scorer_ids = [0, 3]
    levels = [3, 2]
    partial_thresholds = [0.8, 0.9]
    # 3-level jw weights, 2-level exact weights (disagree<partial<agree).
    match_weights = [[-2.0, 0.5, 3.0], [-1.5, 2.5]]
    calibrated = False
    prior_w = 0.0
    threshold = 0.4
    min_weight, weight_range = _weight_range(match_weights)

    pairs = mod.score_block_pairs_fs(
        row_ids,
        block_sizes,
        field_values,
        scorer_ids,
        levels,
        partial_thresholds,
        match_weights,
        calibrated,
        prior_w,
        min_weight,
        weight_range,
        threshold,
        [],  # exclude
    )
    expected = [[int(a), int(b), round(float(s), 6)] for a, b, s in pairs]
    assert expected, "fixture must not be vacuous"
    assert all(math.isfinite(s) for _, _, s in expected)

    fixture = {
        "_comment": (
            "AUTHORED by the Python native score_block_pairs_fs (the oracle). "
            "fs-wasm calls the SAME fs-core::score_fs_pair -> byte-identical. "
            "Regenerate: python scripts/emit_fs_wasm_fixture.py"
        ),
        "field_values": field_values,  # [field][row]; null encoded as JSON null
        "row_ids": row_ids,
        "block_sizes": block_sizes,
        "scorer_ids": scorer_ids,
        "levels": levels,
        "partial_thresholds": partial_thresholds,
        "match_weights": match_weights,
        "calibrated": calibrated,
        "prior_w": prior_w,
        "min_weight": min_weight,
        "weight_range": weight_range,
        "threshold": threshold,
        "expected_pairs": expected,
    }

    out = (
        pathlib.Path(__file__).resolve().parents[4]
        / "packages/typescript/goldenmatch/tests/parity/fixtures/fs/fs_block_scoring.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, indent=2) + "\n")
    print(f"wrote {out} ({len(expected)} pairs)")


if __name__ == "__main__":
    main()
