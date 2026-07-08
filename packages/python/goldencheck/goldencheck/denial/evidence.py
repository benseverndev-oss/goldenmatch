"""Pure-Python evidence-set builder for denial-constraint discovery.

This is the correctness / parity ORACLE. A Rust kernel replaces it for speed in
a later task and MUST reproduce these masks byte-for-byte, so the exact bit
layout below is load-bearing.

Given a :class:`~goldencheck.denial.predicates.PredicateSpace`, split its
predicates (in list order) into ``singles`` (kind in ``{"const", "single"}``,
count ``s``) and ``crosses`` (kind ``"cross"``, count ``c``). Two passes build
integer *satisfaction masks* (one u64 each) counted into ``mask -> count``
histograms:

* **Pass 1 -- row-level mask** (one u64 per row): bit ``i`` (``0 <= i < s``) is
  set iff ``singles[i]`` holds on that row (``predicate_holds(singles[i], enc,
  row, None)``). Cross predicates do NOT participate in Pass 1.
* **Pass 2 -- pairwise mask** (one u64 per ORDERED pair ``(alpha, beta)``,
  ``alpha != beta``):

  * bit ``i`` (``0 <= i < s``)      = ``singles[i]`` holds on **alpha**
    (``predicate_holds(singles[i], enc, alpha, None)``)
  * bit ``s + i``                   = ``singles[i]`` holds on **beta**
    (``predicate_holds(singles[i], enc, beta, None)``)
  * bit ``2s + j`` (``0 <= j < c``) = ``crosses[j]`` holds on the pair
    (``predicate_holds(crosses[j], enc, alpha, beta)``)

* **Both orderings:** Pass 2 iterates ALL ordered pairs ``(alpha, beta)`` with
  ``alpha != beta`` over the sample index set -- this covers both
  ``(alpha, beta)`` and ``(beta, alpha)``, which is required so reversed
  cross-column predicates are reachable. For a sample of size ``m`` that is
  ``m * (m - 1)`` ordered pairs.

Intentionally O(n) / O(m^2) pure Python: correctness over speed.
"""
from __future__ import annotations

from goldencheck.denial.predicates import PredicateSpace, predicate_holds

__all__ = ["row_evidence", "pair_evidence"]


def _split(space: PredicateSpace):
    singles = [p for p in space.predicates if p.kind in ("const", "single")]
    crosses = [p for p in space.predicates if p.kind == "cross"]
    assert len(singles) == space.n_single
    assert len(crosses) == space.n_cross
    return singles, crosses


def row_evidence(space: PredicateSpace, n: int) -> dict[int, int]:
    """Pass 1: mask -> row-count over the ``n`` rows.

    Bit ``i`` = ``singles[i]`` holds on the row.
    """
    singles, _ = _split(space)
    enc = space.enc
    hist: dict[int, int] = {}
    for row in range(n):
        mask = 0
        for i, p in enumerate(singles):
            if predicate_holds(p, enc, row, None):
                mask |= 1 << i
        hist[mask] = hist.get(mask, 0) + 1
    return hist


def pair_evidence(space: PredicateSpace, sample_idx: list[int]) -> dict[int, int]:
    """Pass 2: mask -> pair-count over all ordered pairs ``(alpha, beta)``,
    ``alpha != beta``, ``alpha, beta in sample_idx``.

    Bit layout: ``[0..s)`` singles-on-alpha, ``[s..2s)`` singles-on-beta,
    ``[2s..2s+c)`` crosses on ``(alpha, beta)``.
    """
    singles, crosses = _split(space)
    enc = space.enc
    s = len(singles)
    hist: dict[int, int] = {}
    for alpha in sample_idx:
        # singles-on-alpha bits are constant across all beta for this alpha.
        alpha_mask = 0
        for i, p in enumerate(singles):
            if predicate_holds(p, enc, alpha, None):
                alpha_mask |= 1 << i
        for beta in sample_idx:
            if alpha == beta:
                continue
            mask = alpha_mask
            for i, p in enumerate(singles):
                if predicate_holds(p, enc, beta, None):
                    mask |= 1 << (s + i)
            for j, p in enumerate(crosses):
                if predicate_holds(p, enc, alpha, beta):
                    mask |= 1 << (2 * s + j)
            hist[mask] = hist.get(mask, 0) + 1
    return hist
