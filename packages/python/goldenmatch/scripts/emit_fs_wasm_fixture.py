#!/usr/bin/env python3
"""Author the fs-wasm cross-surface parity fixtures from the Python NATIVE kernel.

The native `score_block_pairs_fs` and the TS `fs-wasm` binding both call the SAME
`goldenmatch-fs-core::score_fs_pair`, so their output is byte-identical by
construction. This emits small blocks scored by the native kernel as the ORACLE;
`tests/parity/fs-wasm.parity.test.ts` feeds the identical inputs to fs-wasm and
asserts the same pairs.

Two fixtures:
  * `fs_block_scoring.json` — the original zero-config block (no NE, no banding).
  * `fs_block_scoring_ne_banding.json` — the fs-default-ts-path PR2 GATE: negative
    evidence + custom level-banding + a partial-MISSING (null) field, the cases
    where the reroute's FIXED full-field normalization differs from the pure-TS
    per-pair shrinking range. Authored WITHOUT `require_positive_evidence` /
    `missing_disagree` (both default off in the pyo3 kernel), matching the fs-wasm
    entry's capabilities exactly, so TS-kernel == Python-native by construction.

Run with the native ext available (scripts/build_native.py):
    python scripts/emit_fs_wasm_fixture.py
"""
from __future__ import annotations

import json
import math
import pathlib

from goldenmatch.core._native_loader import native_module

_FIXTURE_DIR = (
    pathlib.Path(__file__).resolve().parents[4]
    / "packages/typescript/goldenmatch/tests/parity/fixtures/fs"
)


def _weight_range(match_weights: list[list[float]]) -> tuple[float, float]:
    """Regular-field-only FIXED range (min sum, span)."""
    field_mins = [min(w) for w in match_weights]
    field_maxs = [max(w) for w in match_weights]
    regular_min = sum(field_mins)
    regular_max = sum(field_maxs)
    return regular_min, regular_max - regular_min


def _weight_range_with_ne(
    match_weights: list[list[float]], ne_weights: list[float]
) -> tuple[float, float]:
    """FIXED full-field range INCLUDING negative evidence — mirrors Python
    ``fs_weight_range``: regular sum(min)/sum(max) plus, per NE field, the
    ``(min(w_fired, 0), max(w_fired, 0))`` envelope. Returns ``(min, span)``."""
    reg_min = sum(min(w) for w in match_weights)
    reg_max = sum(max(w) for w in match_weights)
    ne_min = sum(min(w, 0.0) for w in ne_weights)
    ne_max = sum(max(w, 0.0) for w in ne_weights)
    total_min = reg_min + ne_min
    total_max = reg_max + ne_max
    return total_min, total_max - total_min


def emit_basic(mod) -> None:
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

    out = _FIXTURE_DIR / "fs_block_scoring.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, indent=2) + "\n")
    print(f"wrote {out} ({len(expected)} pairs)")


def emit_ne_banding(mod) -> None:
    """The PR2 GATE fixture: NE + custom banding + a partial-missing field.

    3 regular fields + 1 negative-evidence field over 5 rows, one block:
      * name  (jaro_winkler id 0, 3-level CUSTOM banding: level_thresholds
        [0.95, 0.3] -> level = count of satisfied cutoffs, `>=` inclusive)
      * code  (exact id 3, default 2-level banding)
      * city  (jaro_winkler id 0, default 2-level banding) — row 2 is NULL
        (MISSING: contributes no evidence, normalized against the FIXED range)
      * dob   (NEGATIVE EVIDENCE, exact id 3, fires when sim < 0.5) — row 2
        disagrees, so the NE fires against every pair involving row 2.

    A wide-negative `threshold` emits every within-block pair so the fixture is a
    thorough oracle. Authored with the level_thresholds + ne_* kwargs ONLY (no
    require_positive_evidence / missing_disagree — both default off), matching the
    fs-wasm entry.
    """
    name = ["robert", "robert", "rupert", "robert", "xyzzy"]
    code = ["A1", "A1", "A1", "B2", "Z9"]
    city = ["paris", "paris", None, "london", "tokyo"]  # row 2 missing
    field_values = [name, code, city]
    row_ids = list(range(5))
    block_sizes = [5]

    scorer_ids = [0, 3, 0]
    levels = [3, 2, 2]
    partial_thresholds = [0.8, 0.9, 0.8]
    # name is 3-level (2 custom thresholds -> 3 weights); code/city 2-level.
    match_weights = [[-2.0, 0.5, 3.0], [-1.5, 2.5], [-1.0, 2.0]]
    # Custom banding for name only; None => default banding for code/city.
    level_thresholds = [[0.95, 0.3], None, None]

    # Negative evidence: dob (exact, fires when values disagree i.e. sim < 0.5).
    dob = ["1990", "1990", "1985", "1990", "1971"]
    ne_values = [dob]
    ne_scorer_ids = [3]
    ne_thresholds = [0.5]
    ne_weights = [-3.0]  # EM-learned-style fired weight (negative)

    calibrated = False
    prior_w = 0.0
    threshold = -1.0e9  # emit every within-block pair
    min_weight, weight_range = _weight_range_with_ne(match_weights, ne_weights)

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
        level_thresholds=level_thresholds,
        ne_values=ne_values,
        ne_scorer_ids=ne_scorer_ids,
        ne_thresholds=ne_thresholds,
        ne_weights=ne_weights,
    )
    expected = [[int(a), int(b), round(float(s), 6)] for a, b, s in pairs]
    assert expected, "NE/banding fixture must not be vacuous"
    assert all(math.isfinite(s) for _, _, s in expected)
    # Sanity: every within-block pair is emitted at the wide-negative threshold.
    assert len(expected) == 5 * (5 - 1) // 2, expected

    fixture = {
        "_comment": (
            "AUTHORED by the Python native score_block_pairs_fs (the oracle) with "
            "negative evidence + custom level-banding + a partial-MISSING field. "
            "fs-wasm calls the SAME fs-core::score_fs_pair -> byte-identical. This "
            "is the fs-default-ts-path PR2 cross-language GATE. No "
            "require_positive_evidence / missing_disagree (both default off), "
            "matching the fs-wasm entry. Regenerate: python "
            "scripts/emit_fs_wasm_fixture.py"
        ),
        "field_values": field_values,
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
        "level_thresholds": level_thresholds,
        "ne_values": ne_values,
        "ne_scorer_ids": ne_scorer_ids,
        "ne_thresholds": ne_thresholds,
        "ne_weights": ne_weights,
        "expected_pairs": expected,
    }

    out = _FIXTURE_DIR / "fs_block_scoring_ne_banding.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, indent=2) + "\n")
    print(f"wrote {out} ({len(expected)} pairs)")


def main() -> None:
    mod = native_module()
    emit_basic(mod)
    emit_ne_banding(mod)


if __name__ == "__main__":
    main()
