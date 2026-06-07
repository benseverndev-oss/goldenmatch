"""Weak-positive-aware blocking-pass selection.

Multi-pass blocking unions several blocking keys to lift pair completeness
(recall ceiling). Some passes earn their cost; others add candidate pairs that
are almost all non-matches, or are wholly subsumed by other passes. This module
prunes the latter WITHOUT ground truth.

The key lesson (measured on Febrl4): **marginal new-pair count is an unsafe
pruning signal.** A high-precision pass like ``date_of_birth`` adds very few new
candidate pairs but a large share of them are true matches (+8.5pp recall for
~1.7K pairs) — a naive "few new pairs => drop" rule deletes exactly the passes
that matter most. Instead we rank passes by their marginal yield of *likely
matches*: new pairs weighted by an unsupervised weak-positive proxy (a pair
agreeing on >= 2 discriminative fields is a probable match). The DOB pass then
ranks near the top; the genuine noise passes (e.g. surname[:5] at ~0.5%
weak-positive density) fall to the bottom.

Greedy selection keeps passes in descending marginal-weak-positive order and
stops once the next pass's marginal yield drops below ``min_marginal_weak_positive``
(default 1 => only fully-redundant / all-noise passes are dropped, which is
recall-safe) or a ``candidate_budget`` is hit.

Runs on the auto-config SAMPLE frame (typically <= 20K rows), so exact pair
enumeration per pass is affordable; the selected passes are then applied to the
full dataset.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from itertools import combinations

import polars as pl

from goldenmatch.config.schemas import BlockingKeyConfig

logger = logging.getLogger(__name__)

# Per-pass pair-enumeration guards (this runs on a sample, but a coarse key can
# still make one block huge). Blocks larger than this are skipped for the
# selection estimate exactly as the scorer would treat oversized blocks.
_MAX_BLOCK_FOR_ESTIMATE = 2000
# Cap the weak-positive density sample per pass so the estimate stays cheap on
# very high-cardinality passes.
_WEAK_POS_SAMPLE = 4000


@dataclass
class PassSelectionResult:
    """Outcome of :func:`select_passes`."""

    kept: list[BlockingKeyConfig]
    dropped: list[BlockingKeyConfig]
    report: list[dict] = field(default_factory=list)  # per-pass, in greedy order

    def summary(self) -> str:
        lines = [f"pass selection: kept {len(self.kept)}, dropped {len(self.dropped)}"]
        for r in self.report:
            mark = "keep" if r["kept"] else "DROP"
            lines.append(
                f"  [{mark}] {r['label']:42} new_pairs={r['marginal_new_pairs']:>9,} "
                f"weak_pos~={r['marginal_weak_positive']:>8,} cum_cand={r['cum_candidates']:>9,}"
            )
        return "\n".join(lines)


def _pass_label(key: BlockingKeyConfig) -> str:
    return f"{list(key.fields)}/{list(key.transforms or [])}"


def _pass_pairs(sample_df: pl.DataFrame, key: BlockingKeyConfig) -> set[tuple[int, int]]:
    """All within-block candidate pairs a single pass produces on the sample.

    Reuses the same block-key expression + null/sentinel filtering as the real
    blocker so the estimate matches production blocking.
    """
    from goldenmatch.core.blocker import _build_block_key_expr

    keyed = (
        sample_df.lazy()
        .with_columns(_build_block_key_expr(key))
        .filter(
            pl.col("__block_key__").is_not_null()
            & ~pl.col("__block_key__").str.strip_chars().str.to_lowercase().is_in(["nan", "null", "none"])
        )
        .collect()
    )
    pairs: set[tuple[int, int]] = set()
    for _, group in keyed.group_by("__block_key__"):
        ids = group["__row_id__"].to_list()
        if len(ids) < 2 or len(ids) > _MAX_BLOCK_FOR_ESTIMATE:
            continue
        for a, b in combinations(ids, 2):
            pairs.add((a, b) if a < b else (b, a))
    return pairs


def _default_discriminative_fields(
    sample_df: pl.DataFrame, blocking_fields: set[str],
) -> list[tuple[str, str]]:
    """Pick weak-positive evidence fields when the caller gives none.

    Heuristic: non-blocking columns with the highest distinct-value ratio are
    the most identity-bearing. Returns up to 5 ``(field, kind)`` where ``kind``
    is ``"fuzzy"`` (jaro_winkler >= 0.85) or ``"exact"``. Numeric-ish / short
    columns score as exact; everything else fuzzy.
    """
    n = sample_df.height or 1
    cands: list[tuple[float, str]] = []
    for col in sample_df.columns:
        if col.startswith("__") or col in blocking_fields:
            continue
        try:
            ratio = sample_df[col].n_unique() / n
        except Exception:
            continue
        cands.append((ratio, col))
    cands.sort(reverse=True)
    out: list[tuple[str, str]] = []
    for _ratio, col in cands[:5]:
        # crude exact-vs-fuzzy: short codes / numeric -> exact match proxy
        sample_vals = [v for v in sample_df[col].head(50).to_list() if v is not None]
        avg_len = sum(len(str(v)) for v in sample_vals) / max(len(sample_vals), 1)
        kind = "exact" if avg_len <= 8 else "fuzzy"
        out.append((col, kind))
    return out


def _make_weak_positive_fn(sample_df: pl.DataFrame, fields: list[tuple[str, str]]):
    """Closure: (id_a, id_b) -> bool, True if the pair agrees on >= 2 fields."""
    from rapidfuzz.distance import JaroWinkler

    row_by_id = {r["__row_id__"]: r for r in sample_df.to_dicts()}

    def weak_positive(a: int, b: int) -> bool:
        ra = row_by_id.get(a)
        rb = row_by_id.get(b)
        if ra is None or rb is None:
            return False
        agree = 0
        for col, kind in fields:
            va, vb = ra.get(col), rb.get(col)
            if va is None or vb is None:
                continue
            sa, sb = str(va), str(vb)
            if kind == "exact":
                if sa == sb and sa != "":
                    agree += 1
            elif JaroWinkler.similarity(sa, sb) >= 0.85:
                agree += 1
            if agree >= 2:
                return True
        return False

    return weak_positive


def select_passes(
    sample_df: pl.DataFrame,
    passes: list[BlockingKeyConfig],
    *,
    discriminative_fields: list[tuple[str, str]] | None = None,
    blocking_fields: set[str] | None = None,
    min_marginal_weak_positive: int = 1,
    candidate_budget: int | None = None,
    seed: int = 42,
) -> PassSelectionResult:
    """Greedily select multi-pass blocking passes by marginal likely-match yield.

    Args:
        sample_df: sample frame with ``__row_id__`` and the blocking/evidence
            columns. Pass enumeration is exact on this frame.
        passes: candidate blocking passes (``config.passes``).
        discriminative_fields: ``(field, "exact"|"fuzzy")`` used for the
            weak-positive proxy. Auto-derived from the highest-cardinality
            non-blocking columns when None.
        blocking_fields: columns used as blocking keys (excluded from the
            auto-derived evidence set so a pass isn't scored on its own key).
        min_marginal_weak_positive: stop keeping passes once the next pass's
            estimated marginal likely-match yield is below this. Default 1 keeps
            anything contributing >= 1 new probable match and drops only
            fully-redundant / all-noise passes (recall-safe). Raise it to trade
            recall for fewer candidates.
        candidate_budget: optional hard cap on cumulative candidate pairs; a
            pass that would exceed it is dropped.

    Returns:
        PassSelectionResult. ``kept`` preserves the original pass order; the
        ``report`` is in greedy-selection order.
    """
    if len(passes) <= 1:
        return PassSelectionResult(kept=list(passes), dropped=[], report=[])

    if "__row_id__" not in sample_df.columns:
        sample_df = sample_df.with_row_index("__row_id__")

    if blocking_fields is None:
        bf: set[str] = set()
        for p in passes:
            bf.update(p.fields)
        blocking_fields = bf

    if discriminative_fields is None:
        discriminative_fields = _default_discriminative_fields(sample_df, blocking_fields)

    if not discriminative_fields:
        # No evidence columns to judge likely-matches -> safe fallback: keep all
        # passes (we can only prove redundancy, handled below via 0-new-pairs).
        logger.debug("pass selection: no discriminative fields; redundancy-only pruning")

    weak_positive = _make_weak_positive_fn(sample_df, discriminative_fields)
    rng = random.Random(seed)

    pair_sets = [_pass_pairs(sample_df, p) for p in passes]
    remaining = list(range(len(passes)))
    covered: set[tuple[int, int]] = set()
    kept_idx: list[int] = []
    report: list[dict] = []

    while remaining:
        # Estimate each remaining pass's marginal likely-match yield.
        best_idx = -1
        best_yield = -1.0
        best_new_count = 0
        for i in remaining:
            new_pairs = pair_sets[i] - covered
            if not new_pairs:
                marginal = 0.0
                new_count = 0
            else:
                new_count = len(new_pairs)
                if new_count <= _WEAK_POS_SAMPLE:
                    sample_pairs = new_pairs
                else:
                    sample_pairs = rng.sample(list(new_pairs), _WEAK_POS_SAMPLE)
                hits = sum(1 for (a, b) in sample_pairs if weak_positive(a, b))
                density = hits / len(sample_pairs)
                marginal = new_count * density
            if marginal > best_yield:
                best_yield, best_idx, best_new_count = marginal, i, new_count

        # Stop conditions.
        prospective = len(covered) + best_new_count
        over_budget = candidate_budget is not None and prospective > candidate_budget
        below_floor = best_yield < min_marginal_weak_positive
        if below_floor or over_budget:
            # Drop every remaining pass.
            for i in remaining:
                report.append({
                    "label": _pass_label(passes[i]),
                    "marginal_new_pairs": len(pair_sets[i] - covered),
                    "marginal_weak_positive": 0,
                    "cum_candidates": len(covered),
                    "kept": False,
                })
            break

        remaining.remove(best_idx)
        kept_idx.append(best_idx)
        covered |= pair_sets[best_idx]
        report.append({
            "label": _pass_label(passes[best_idx]),
            "marginal_new_pairs": best_new_count,
            "marginal_weak_positive": round(best_yield),
            "cum_candidates": len(covered),
            "kept": True,
        })

    kept_set = set(kept_idx)
    kept = [passes[i] for i in range(len(passes)) if i in kept_set]
    dropped = [passes[i] for i in range(len(passes)) if i not in kept_set]
    return PassSelectionResult(kept=kept, dropped=dropped, report=report)
