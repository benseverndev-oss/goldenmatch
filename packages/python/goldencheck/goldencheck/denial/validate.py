"""Ground-truth g1 validation for candidate denial constraints.

Discovery (evidence + :func:`discover`) works on a *sample* and emits candidate
DCs as predicate conjunctions. This module re-measures each candidate's g1 on the
real Polars frame so a sample artefact never ships as a finding.

A DC ``¬(p1 ∧ … ∧ pm)`` is VIOLATED by an element when ALL of ``p1..pm`` hold:

* **Single-tuple** (every predicate references one row -- kind ``const``/``single``):
  validated EXACTLY over all n rows, O(n). ``g1 = |violating rows| / n`` and the
  exact violating row indices are returned.
* **Cross-tuple** (at least one ``cross`` predicate): full-table validation is
  O(n^2), so we validate on a bounded, seeded row sample and return an ESTIMATED
  g1 over the sample's ordered pairs plus a few representative violating pairs.
"""
from __future__ import annotations

import random

import polars as pl

from goldencheck.denial.constants import VALIDATION_SAMPLE
from goldencheck.denial.models import Predicate
from goldencheck.denial.predicates import encode_columns, predicate_holds

__all__ = ["is_single_tuple", "validate_single_tuple", "validate_cross_tuple"]


def is_single_tuple(preds: list[Predicate]) -> bool:
    """True iff no predicate has kind ``"cross"`` (the DC scopes to one tuple)."""
    return all(p.kind != "cross" for p in preds)


def validate_single_tuple(
    preds: list[Predicate], df: pl.DataFrame
) -> tuple[float, list[int]]:
    """Exact O(n) validation. Returns ``(g1, violating_row_indices)``.

    A row violates when ALL ``preds`` hold on it (every pred is const/single, so
    ``row_b=None``). ``g1 = len(violating) / df.height``.
    """
    n = df.height
    if n == 0:
        return 0.0, []
    enc = encode_columns(df)
    violating = [
        r for r in range(n) if all(predicate_holds(p, enc, r, None) for p in preds)
    ]
    return len(violating) / n, violating


def validate_cross_tuple(
    preds: list[Predicate],
    df: pl.DataFrame,
    *,
    sample: int = VALIDATION_SAMPLE,
    seed: int = 0,
    max_pairs: int = 5,
) -> tuple[float, list[tuple[int, int]]]:
    """Estimated g1 over a bounded, seeded row sample. Returns ``(g1_est, pairs)``.

    Takes ``min(df.height, sample)`` rows (seeded), evaluates every ordered pair
    ``(α, β)`` with ``α != β``. A pair violates when ALL ``preds`` hold: const/single
    preds evaluated on ``α`` (row_b=None), cross preds on ``(α, β)``.
    ``g1_est = violations / (m*(m-1))`` where ``m`` is the sample size. Returns up
    to ``max_pairs`` representative violating ``(α, β)`` index pairs into the SAMPLE.
    """
    n = df.height
    m = min(n, sample)
    if m < 2:
        return 0.0, []

    if n <= sample:
        rows = list(range(n))
    else:
        rng = random.Random(seed)
        rows = rng.sample(range(n), m)

    sub = df[rows]
    enc = encode_columns(sub)

    single = [p for p in preds if p.kind != "cross"]
    cross = [p for p in preds if p.kind == "cross"]

    violations = 0
    examples: list[tuple[int, int]] = []
    for a in range(m):
        if not all(predicate_holds(p, enc, a, None) for p in single):
            continue  # single-tuple part fails on α -> no pair (α,·) can violate
        for b in range(m):
            if a == b:
                continue
            if all(predicate_holds(p, enc, a, b) for p in cross):
                violations += 1
                if len(examples) < max_pairs:
                    examples.append((a, b))

    g1_est = violations / (m * (m - 1))
    return g1_est, examples
