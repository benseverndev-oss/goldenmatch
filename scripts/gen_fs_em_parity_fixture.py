#!/usr/bin/env python3
"""Emit the Python-side anchors for the Rust FS EM parity gate.

The Rust `score-core/src/em_core.rs` is the designated single source of truth
for Fellegi-Sunter training math (see
`docs/superpowers/specs/2026-08-13-fs-em-rust-single-source-design.md`), but the
behaviour it must reproduce is defined by
`goldenmatch.core.probabilistic`. This script writes that behaviour down as data
so a Rust test can assert against it without embedding Python.

**Why a committed emitter and not pasted numbers.** The existing anchors in
`em_core.rs` came from "the C1 commit message / scratch script" -- a script that
was never committed. Nobody can re-derive those numbers, so nobody can tell a
Python behaviour change from a bad port: the test just goes red with no way to
decide which side moved. This script is the harness, and re-running it is the
whole diff.

Regenerate:

    PYTHONPATH=packages/python/goldenmatch \\
      python scripts/gen_fs_em_parity_fixture.py

Then `git diff` on the fixture shows any drift. A change there is a change to
trained models and must be justified in the PR, not waved through.
"""
from __future__ import annotations

import json
import pathlib
import sys

OUT = (
    pathlib.Path(__file__).resolve().parent.parent
    / "packages/rust/extensions/score-core/tests/fixtures/em_counts_parity.json"
)


def _mk(fields):
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    return MatchkeyConfig(
        name="fs",
        type="probabilistic",
        fields=[
            MatchkeyField(
                field=name,
                scorer="jaro_winkler",
                levels=levels,
                partial_threshold=0.8,
            )
            for name, levels in fields
        ],
    )


#: Each case is (name, fields, patterns, u_probs, conditioned, why).
#:
#: These are chosen for the ways the port can be wrong while still returning a
#: valid model, not for realism.
CASES = [
    (
        "two_level_learnable_only",
        [("first", 2), ("last", 2)],
        [[[1, 1], 500], [[0, 1], 300], [[1, 0], 150], [[0, 0], 50]],
        {"first": [0.9, 0.1], "last": [0.85, 0.15]},
        [],
        "the base case: both fields free, so every weight is log2(m/u)",
    ),
    (
        "near_unique_blocking_field_1836",
        [("first", 2), ("zip", 2)],
        # Blocked ON zip, so every candidate agrees on it: level 1 throughout.
        [[[1, 1], 500], [[0, 1], 300]],
        {"first": [0.9, 0.1], "zip": [0.999, 0.001]},
        ["zip"],
        "THE #1836 case. `zip` is near-unique, so a learned u collapses toward "
        "the smoothing floor and log2(m/u) explodes past 20 bits, letting one "
        "field dominate every other. It must come back neutral-u with the "
        "bounded -3..+3 ramp instead. This is the case that fails silently.",
    ),
    (
        "three_level_conditioned",
        [("first", 2), ("city", 3)],
        [[[1, 2], 400], [[0, 2], 300], [[1, 0], 200], [[0, 1], 100]],
        {"first": [0.9, 0.1], "city": [0.8, 0.15, 0.05]},
        ["city"],
        "a 3-level conditioned field must ramp -3, 0, +3 across ITS levels; a "
        "hard-coded two-element ramp would index wrong at scoring time",
    ),
    (
        "unobserved_entries",
        [("first", 2), ("last", 2)],
        [[[1, -1], 400], [[-1, 1], 300], [[1, 1], 200], [[0, 0], 100]],
        {"first": [0.9, 0.1], "last": [0.85, 0.15]},
        [],
        "-1 is UNOBSERVED: it must carry no evidence and stay out of the "
        "denominator, not be read as level -1 or as a disagreement",
    ),
    (
        "weights_are_counts_not_proportions",
        [("first", 2), ("last", 2)],
        [[[1, 1], 5], [[0, 1], 3], [[1, 0], 1], [[0, 0], 1]],
        {"first": [0.9, 0.1], "last": [0.85, 0.15]},
        [],
        "the same shape as case 1 at 1/100th the counts. The 1e-6 smoothing is "
        "ADDITIVE, so this must NOT equal case 1 -- if a port normalises the "
        "weights it will, and the difference lands in the low-probability "
        "cells where FS weights are largest",
    ),
]


def main() -> int:
    from goldenmatch.core.probabilistic import (
        estimate_u_from_counts,
        train_em_from_counts,
    )

    out = {
        "_generated_by": "scripts/gen_fs_em_parity_fixture.py",
        "_reference": "goldenmatch.core.probabilistic",
        "_tolerances": {"probabilities": 1e-9, "match_weights": 1e-7},
        "_tolerance_reason": (
            "libm ln/log2/exp differ from CPython in the low mantissa bits, so "
            "parity is decision-level rather than bitwise. Weights get the "
            "looser bound because they amplify small m near the 1e-10 floor."
        ),
        "cases": [],
    }

    for name, fields, patterns, u_probs, conditioned, why in CASES:
        mk = _mk(fields)
        pattern_counts = [(tuple(vec), int(c)) for vec, c in patterns]
        em = train_em_from_counts(
            mk, pattern_counts, u_probs, conditioned_fields=tuple(conditioned)
        )
        # u-from-counts is exercised on the SAME patterns. It is not what the
        # trainer was handed (that is `u_probs`, from random pairs) -- this pins
        # the estimator itself, which has no other gate.
        u_est = estimate_u_from_counts(mk, pattern_counts)

        out["cases"].append(
            {
                "name": name,
                "why": why,
                "fields": [{"name": n, "n_levels": lv} for n, lv in fields],
                "patterns": [vec for vec, _ in patterns],
                "counts": [c for _, c in patterns],
                "u_probs_in": [u_probs[n] for n, _ in fields],
                "conditioned": [n in conditioned for n, _ in fields],
                "expect": {
                    "m_probs": [list(em.m_probs[n]) for n, _ in fields],
                    "u_probs": [list(em.u_probs[n]) for n, _ in fields],
                    "match_weights": [list(em.match_weights[n]) for n, _ in fields],
                    "proportion_matched": em.proportion_matched,
                    "converged": em.converged,
                    "iterations": em.iterations,
                },
                "expect_u_from_counts": [list(u_est[n]) for n, _ in fields],
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(out['cases'])} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
