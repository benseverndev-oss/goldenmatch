"""Tests for the pure-Python evidence-set builder (the parity oracle)."""
from __future__ import annotations

import polars as pl
from goldencheck.denial.evidence import pair_evidence, row_evidence
from goldencheck.denial.predicates import build_predicate_space, predicate_holds


def test_row_evidence_counts_match_manual():
    # status ∈ {shipped, pending}; ship vs order per-tuple comparison
    df = pl.DataFrame(
        {
            "status": ["shipped", "shipped", "pending", "shipped"],
            "ship": [5, 1, 9, 5],
            "order": [3, 2, 1, 5],  # row0 ship>order, row1 ship<order, row3 ship==order
        }
    )
    space = build_predicate_space(df)
    ev = row_evidence(space, df.height)
    assert sum(ev.values()) == df.height  # every row contributes exactly one mask

    singles = [p for p in space.predicates if p.kind in ("const", "single")]
    idx = next(
        i
        for i, p in enumerate(singles)
        if p.kind == "const" and p.literal == "shipped"
    )
    shipped_rows = sum(cnt for mask, cnt in ev.items() if mask & (1 << idx))
    assert shipped_rows == 3


def test_row_evidence_masks_equal_oracle():
    # Independently recompute each row's mask via predicate_holds and compare
    # the mask->count histogram against row_evidence.
    df = pl.DataFrame(
        {
            "status": ["shipped", "shipped", "pending", "shipped"],
            "ship": [5, 1, 9, 5],
            "order": [3, 2, 1, 5],
        }
    )
    space = build_predicate_space(df)
    singles = [p for p in space.predicates if p.kind in ("const", "single")]
    expected: dict[int, int] = {}
    for row in range(df.height):
        mask = 0
        for i, p in enumerate(singles):
            if predicate_holds(p, space.enc, row, None):
                mask |= 1 << i
        expected[mask] = expected.get(mask, 0) + 1
    assert row_evidence(space, df.height) == expected


def test_pair_evidence_ordered_pairs_total():
    df = pl.DataFrame({"a": [1, 2, 3], "b": [3, 2, 1]})
    space = build_predicate_space(df)
    ev = pair_evidence(space, [0, 1, 2])
    assert sum(ev.values()) == 3 * 2  # ordered pairs α≠β = m(m-1)


def test_pair_evidence_masks_equal_oracle():
    # Full mask-histogram oracle: [0..s) singles-on-α, [s..2s) singles-on-β,
    # [2s..2s+c) crosses on (α,β). Recompute independently and compare.
    df = pl.DataFrame({"a": [1, 5, 3], "b": [3, 2, 9]})
    space = build_predicate_space(df)
    singles = [p for p in space.predicates if p.kind in ("const", "single")]
    crosses = [p for p in space.predicates if p.kind == "cross"]
    s = len(singles)
    sample = [0, 1, 2]
    expected: dict[int, int] = {}
    for a in sample:
        for b in sample:
            if a == b:
                continue
            mask = 0
            for i, p in enumerate(singles):
                if predicate_holds(p, space.enc, a, None):
                    mask |= 1 << i
                if predicate_holds(p, space.enc, b, None):
                    mask |= 1 << (s + i)
            for j, p in enumerate(crosses):
                if predicate_holds(p, space.enc, a, b):
                    mask |= 1 << (2 * s + j)
            expected[mask] = expected.get(mask, 0) + 1
    assert pair_evidence(space, sample) == expected


def test_pair_evidence_bit_layout_alpha_beta():
    # A cross '<' predicate on the SAME column must fire for (α,β) and its
    # reverse '>' must fire for (β,α): reversed cross predicates are reachable
    # only because Pass 2 visits both orderings.
    df = pl.DataFrame({"a": [1, 5]})
    space = build_predicate_space(df)
    singles = [p for p in space.predicates if p.kind in ("const", "single")]
    crosses = [p for p in space.predicates if p.kind == "cross"]
    s = len(singles)
    lt_j = next(
        j for j, p in enumerate(crosses) if p.col_a == "a" and p.col_b == "a" and p.op.value == "<"
    )
    gt_j = next(
        j for j, p in enumerate(crosses) if p.col_a == "a" and p.col_b == "a" and p.op.value == ">"
    )
    ev = pair_evidence(space, [0, 1])
    assert len(ev) >= 1
    # exactly one ordered pair sets '<'; the other sets '>'
    lt_hits = sum(cnt for mask, cnt in ev.items() if mask & (1 << (2 * s + lt_j)))
    gt_hits = sum(cnt for mask, cnt in ev.items() if mask & (1 << (2 * s + gt_j)))
    assert lt_hits == 1
    assert gt_hits == 1
