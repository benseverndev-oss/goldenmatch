"""Project a sample's block-size distribution to full-frame cost.

One authority for "how big does this blocking pass get at full N?", shared by
auto-config's static-pass gate (``_projected_pass_cost``) and learned blocking's
rule selector. Both need the same answer and must not drift apart.

**Polars/numpy-free by construction** (the D6 zero-polars invariant that
``_projected_pass_cost`` already carried): the input is a plain mapping of block
key -> sample row count.
"""
from __future__ import annotations

import math
from collections.abc import Iterable

__all__ = ["project_block_counts", "projected_pair_count"]


def project_block_counts(
    counts: Iterable[int], sample_n: int, full_n: int
) -> tuple[int, int]:
    """``(max_block_rows, candidate_pairs)`` the pass emits at ``full_n``.

    ``counts`` are the per-block row counts measured on a ``sample_n``-row
    sample. ``candidate_pairs`` is ``sum C(block, 2)`` -- the axis scoring memory
    and wall-clock actually scale on, which a block-ROW ceiling alone misses (a
    15k-row birth-year block clears a 25k-row ceiling yet is 110M pairs).

    Growth is SATURATION-AWARE. Extrapolating each block's size by the plain row
    ratio is only correct for a saturated low-cardinality key -- one whose
    distinct values are all already in the sample, so a bigger frame just grows
    each block. A NEAR-UNIQUE key instead keeps producing NEW values as N grows:
    its blocks stay ~constant size and the block COUNT grows. Growing a
    near-unique key's SIZE invents ~C(ratio, 2) PHANTOM pairs per sample
    singleton, which is what made a near-unique compound like ``(zip, email)``
    project ~2.2B pairs at 30M, get dropped by the pair gate, and collapse
    blocking to a single pass. So size grows only by the key's sample COLLISION
    headroom ``1 - distinct/sample_n``: a fully saturated key (d -> 0) still
    grows by the full ratio, a near-unique key (d -> 1) barely grows so its
    singletons stay singletons and contribute no pairs.

    Block COUNT growth is implicit and deliberately NOT modelled, which means a
    near-unique key is UNDER-projected. That is the safe direction for a cost
    gate: under-projection can only let a cheap key through, and near-unique keys
    are cheap by construction. Coarse keys -- the ones a gate exists to catch --
    are saturated, where the projection is accurate (measured within 2% on a
    65-block ``birth_year`` pass at 2M).
    """
    sizes = [c for c in counts if c > 0]
    if not sizes:
        return (0, 0)
    if full_n == sample_n or sample_n <= 0:
        growth = 1.0
    else:
        ratio = full_n / sample_n
        d = len(sizes) / sample_n
        growth = 1.0 + (ratio - 1.0) * (1.0 - d)

    max_block = 0
    pairs = 0
    for cnt in sizes:
        b = math.ceil(cnt * growth) if growth != 1.0 else cnt
        if b > max_block:
            max_block = b
        pairs += b * (b - 1) // 2
    return (max_block, pairs)


def projected_pair_count(counts: Iterable[int], sample_n: int, full_n: int) -> int:
    """``project_block_counts(...)[1]`` -- the candidate-pair term alone."""
    return project_block_counts(counts, sample_n, full_n)[1]
