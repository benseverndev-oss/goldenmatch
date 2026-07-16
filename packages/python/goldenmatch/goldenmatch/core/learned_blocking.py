"""Learned blocking -- data-driven blocking predicate selection.

Replaces manual blocking key choice with automatic predicate learning:
1. Sample run with conservative static blocking generates training pairs
2. Predicate library generates candidate blocking rules
3. Rules evaluated by recall (% true matches in same block) vs reduction ratio
4. Best rules selected and applied

Usage in config:
    blocking:
      strategy: learned
      learned:
        sample_size: 5000
        min_recall: 0.95
        min_reduction: 0.90
        predicate_depth: 2
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import TYPE_CHECKING

from goldenmatch._polars_lazy import pl

if TYPE_CHECKING:
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig

logger = logging.getLogger(__name__)


@dataclass
class BlockingPredicate:
    """A single blocking predicate: transform applied to a field."""

    field: str
    transform: str  # "exact", "first_3", "first_5", "soundex", "first_token", "digits_only"

    def key(self) -> str:
        return f"{self.field}:{self.transform}"


@dataclass
class BlockingRule:
    """A conjunction of predicates forming a blocking rule."""

    predicates: list[BlockingPredicate]
    recall: float = 0.0
    reduction_ratio: float = 0.0
    n_blocks: int = 0

    def key(self) -> str:
        return " AND ".join(p.key() for p in sorted(self.predicates, key=lambda p: p.key()))


# ── Predicate Library ─────────────────────────────────────────────────────

_TRANSFORM_MAP = {
    "exact": lambda v: str(v).strip().lower() if v else "",
    "first_3": lambda v: str(v).strip().lower()[:3] if v else "",
    "first_5": lambda v: str(v).strip().lower()[:5] if v else "",
    "soundex": lambda v: _safe_soundex(str(v)) if v else "",
    "first_token": lambda v: str(v).strip().lower().split()[0] if v and str(v).strip() else "",
    "digits_only": lambda v: "".join(c for c in str(v) if c.isdigit()) if v else "",
}


def _safe_soundex(val: str) -> str:
    try:
        import jellyfish
        return jellyfish.soundex(val)
    except Exception:
        return val[:4].upper()


def generate_predicates(columns: list[str]) -> list[BlockingPredicate]:
    """Generate candidate predicates for all columns."""
    predicates = []
    for col in columns:
        for transform_name in _TRANSFORM_MAP:
            predicates.append(BlockingPredicate(field=col, transform=transform_name))
    return predicates


def _apply_predicate(value: object, predicate: BlockingPredicate) -> str:
    """Apply a predicate transform to a value."""
    fn = _TRANSFORM_MAP.get(predicate.transform, _TRANSFORM_MAP["exact"])
    return fn(value)


def _compute_block_key(row: dict, predicates: list[BlockingPredicate]) -> str:
    """Compute a block key from multiple predicates (conjunction)."""
    parts = []
    for p in predicates:
        val = row.get(p.field)
        parts.append(_apply_predicate(val, p))
    return "||".join(parts)


# ── Evaluation ────────────────────────────────────────────────────────────


def evaluate_rule(
    df: pl.DataFrame,
    rule: BlockingRule,
    true_pairs: set[tuple[int, int]],
) -> tuple[float, float, int]:
    """Evaluate a blocking rule by recall and reduction ratio.

    Returns (recall, reduction_ratio, n_blocks).
    """
    if not true_pairs:
        return 0.0, 1.0, 0

    # Assign block keys + record each row's block for later recall lookup.
    rows = df.select(["__row_id__"] + [p.field for p in rule.predicates]).to_dicts()
    blocks: dict[str, list[int]] = {}
    block_of: dict[int, str] = {}
    for row in rows:
        key = _compute_block_key(row, rule.predicates)
        if key:
            rid = row["__row_id__"]
            blocks.setdefault(key, []).append(rid)
            block_of[rid] = key

    # Count blocked pairs WITHOUT materialising them. At 1M rows with a
    # low-cardinality candidate key, the old code's `combinations(members, 2)`
    # set blew up — a single 200K-member block enumerates 20 billion tuples
    # (≈ 600 GB of Python set memory) and crashed `evaluate_rule` mid-eval
    # during auto-config's learned-blocking sample run (PR #173, scale audit).
    #
    # We only need len() of the pair set and len(true_pairs & blocked_pairs).
    # Both are derivable from block sizes + a row→block lookup; the explicit
    # set is never used elsewhere.
    n_blocked_pairs = sum(
        len(members) * (len(members) - 1) // 2
        for members in blocks.values()
        if len(members) > 1
    )

    # Total possible pairs
    n = df.height
    total_pairs = n * (n - 1) // 2

    # Recall: fraction of true pairs that land in the same block. Constant
    # work per true pair (one dict lookup + equality check), versus the old
    # O(|true_pairs|) set-intersection cost AFTER an O(blocked_pairs)-sized
    # set build.
    if true_pairs:
        hits = sum(
            1 for a, b in true_pairs
            if block_of.get(a) is not None and block_of.get(a) == block_of.get(b)
        )
        recall = hits / len(true_pairs)
    else:
        recall = 0.0

    # Reduction ratio: fraction of pairs eliminated
    if total_pairs > 0:
        reduction = 1 - (n_blocked_pairs / total_pairs)
    else:
        reduction = 1.0

    return recall, reduction, len(blocks)


# ── Rule Learning ─────────────────────────────────────────────────────────


def learn_blocking_rules(
    df: pl.DataFrame,
    scored_pairs: list[tuple[int, int, float]],
    columns: list[str] | None = None,
    min_recall: float = 0.95,
    min_reduction: float = 0.90,
    predicate_depth: int = 2,
    threshold: float = 0.7,
) -> list[BlockingRule]:
    """Learn blocking rules from scored pairs.

    Args:
        df: DataFrame with __row_id__ and data columns.
        scored_pairs: Pairs from a sample run (row_id_a, row_id_b, score).
        columns: Columns to consider for predicates. Defaults to all non-internal.
        min_recall: Minimum recall requirement for selected rules.
        min_reduction: Minimum reduction ratio requirement.
        predicate_depth: Max predicates per rule (conjunction depth).
        threshold: Score threshold for true positive pairs.

    Returns:
        List of blocking rules meeting the constraints, best first.
    """
    if columns is None:
        columns = [c for c in df.columns if not c.startswith("__")]

    # True pairs from scored pairs above threshold
    true_pairs = {
        (min(a, b), max(a, b))
        for a, b, s in scored_pairs
        if s >= threshold
    }

    if not true_pairs:
        logger.warning("No true pairs found above threshold %.2f. Using first column as fallback.", threshold)
        return [BlockingRule(
            predicates=[BlockingPredicate(field=columns[0], transform="first_5")],
            recall=0.0, reduction_ratio=0.0,
        )]

    logger.info("Learning blocking rules from %d true pairs, %d columns", len(true_pairs), len(columns))

    # Generate single predicates
    all_predicates = generate_predicates(columns)

    # Evaluate single predicates
    single_rules: list[BlockingRule] = []
    for pred in all_predicates:
        rule = BlockingRule(predicates=[pred])
        recall, reduction, n_blocks = evaluate_rule(df, rule, true_pairs)
        rule.recall = recall
        rule.reduction_ratio = reduction
        rule.n_blocks = n_blocks
        single_rules.append(rule)

    # Filter to predicates with reasonable recall
    good_singles = [r for r in single_rules if r.recall >= min_recall * 0.5]  # relaxed for combination
    good_singles.sort(key=lambda r: r.recall, reverse=True)
    good_singles = good_singles[:20]  # limit for combinatorial explosion

    # Check if any single predicate meets both constraints
    passing_rules = [
        r for r in single_rules
        if r.recall >= min_recall and r.reduction_ratio >= min_reduction
    ]

    # Try depth-2 combinations if no single predicate is sufficient
    if not passing_rules and predicate_depth >= 2 and len(good_singles) >= 2:
        for r1, r2 in combinations(good_singles, 2):
            p1 = r1.predicates[0]
            p2 = r2.predicates[0]
            # Skip if same field+transform
            if p1.key() == p2.key():
                continue
            combo = BlockingRule(predicates=[p1, p2])
            recall, reduction, n_blocks = evaluate_rule(df, combo, true_pairs)
            combo.recall = recall
            combo.reduction_ratio = reduction
            combo.n_blocks = n_blocks
            if recall >= min_recall and reduction >= min_reduction:
                passing_rules.append(combo)

    # Sort by recall (highest first), then reduction ratio
    passing_rules.sort(key=lambda r: (r.recall, r.reduction_ratio), reverse=True)

    if not passing_rules:
        # Fallback: pick the single rule with best recall
        best = max(single_rules, key=lambda r: r.recall) if single_rules else None
        if best:
            logger.warning(
                "No rule meets constraints (min_recall=%.2f, min_reduction=%.2f). "
                "Best: recall=%.2f, reduction=%.2f",
                min_recall, min_reduction, best.recall, best.reduction_ratio,
            )
            passing_rules = [best]

    logger.info(
        "Learned %d blocking rules. Best: recall=%.3f, reduction=%.3f",
        len(passing_rules),
        passing_rules[0].recall if passing_rules else 0,
        passing_rules[0].reduction_ratio if passing_rules else 0,
    )

    return passing_rules


# ── Apply Learned Blocks ──────────────────────────────────────────────────


def apply_learned_blocks(
    lf: pl.LazyFrame,
    rules: list[BlockingRule],
    max_block_size: int = 5000,
) -> list:
    """Apply learned blocking rules to produce BlockResult list.

    Uses union of all rules (multi-pass style) for maximum recall.
    """
    from goldenmatch.core.blocker import BlockResult

    df = lf.collect()
    all_blocks: list = []

    for rule in rules[:3]:  # limit to top 3 rules
        rows = df.select(
            ["__row_id__"] + list({p.field for p in rule.predicates})
        ).to_dicts()

        # Build (block_key -> positions) instead of (block_key -> __row_id__ values).
        # Direct positional indexing into `df` is O(K) per block; filter+is_in
        # was O(N) per block, which dominated wall at 1M (cProfile Round 5).
        # We enumerate `rows` so positions track df's current ordering.
        blocks: dict[str, list[int]] = {}
        for pos, row in enumerate(rows):
            key = _compute_block_key(row, rule.predicates)
            if key:
                blocks.setdefault(key, []).append(pos)

        for block_key, member_positions in blocks.items():
            if len(member_positions) < 2:
                continue
            if len(member_positions) > max_block_size:
                continue
            block_lf = df[sorted(member_positions)].lazy()
            all_blocks.append(BlockResult(
                block_key=f"learned:{rule.key()}:{block_key}",
                df=block_lf,
                strategy="learned",
            ))

    # Deduplicate blocks by member set
    seen: set[frozenset[int]] = set()
    deduped: list = []
    for block in all_blocks:
        block_df = block.materialize().native
        members = frozenset(block_df["__row_id__"].to_list())
        if members not in seen:
            seen.add(members)
            deduped.append(block)

    logger.info("Learned blocking produced %d blocks from %d rules", len(deduped), min(len(rules), 3))
    return deduped


# ── Lowering to a bucket-eligible config (#1839) ──────────────────────────
#
# WHY: `_use_bucket_scorer` refuses `strategy="learned"`, so every zero-config
# run >= 50K rows forfeits the bucket scorer and pays the legacy per-block path.
# That is NOT a semantic parity gap -- it is a REPRESENTATION gap. Bucket derives
# block keys from `blocking.passes/keys`, and a learned config carries neither,
# so bucket has nothing to bucket on. (Which is also why "just relax the gate"
# is not a fix: bucket would have no keys at all.)
#
# A learned rule is a conjunction of (field, transform) predicates -- exactly the
# shape `BlockingKeyConfig.field_transforms` expresses (#1826, built to map mixed
# Splink rules per-field instead of widening every field). Lowering learned rules
# into multi_pass passes closes the gap using machinery that already exists;
# `score_buckets.py` already honors `field_transforms`.
#
# NOTHING HERE IS WIRED UP YET. This is the compiler + a differential harness to
# ANSWER the gating question with data, not to flip any default. See
# `scripts/learned_lowering_diff.py`.

# Each learned transform -> a registry chain that is value-identical to the
# learned lambda (pinned across edge cases in test_learned_lowering_parity.py).
# 4 of 6 are natively vectorizable, so a lowered config additionally escapes
# map_elements for those -- a second, independent win.
_LOWERED_CHAIN: dict[str, list[str]] = {
    "exact": ["strip", "lowercase"],
    "first_3": ["strip", "lowercase", "substring:0:3"],
    "first_5": ["strip", "lowercase", "substring:0:5"],
    "first_token": ["strip", "lowercase", "first_token"],
    "soundex": ["soundex"],
    "digits_only": ["digits_only"],
}


class LoweringUnsupportedError(ValueError):
    """A learned rule has no exact multi_pass equivalent.

    Raised rather than lowering approximately. An approximate lowering would
    silently change which pairs are generated -- the exact failure mode (silent,
    recall-only, precision stays 1.0) that #1800 / #1837 / #1839 all were.
    """


def lower_rule_to_key(rule: BlockingRule) -> BlockingKeyConfig:
    """Lower ONE learned rule to a BlockingKeyConfig pass.

    Raises LoweringUnsupportedError when the rule cannot be expressed exactly:

    * an unknown transform (no registry chain), or
    * two predicates on the SAME field (e.g. ``last:exact AND last:soundex``).
      ``field_transforms`` is keyed by field, so it cannot hold two chains for
      one field. `learn_blocking_rules` CAN generate these: its dedup guard is
      ``p1.key() == p2.key()``, which compares field+transform, not field alone.
      Collapsing them would widen the key and mega-block it (the #1826 footgun).
    """
    from goldenmatch.config.schemas import BlockingKeyConfig

    fields = [p.field for p in rule.predicates]
    dupes = {f for f in fields if fields.count(f) > 1}
    if dupes:
        raise LoweringUnsupportedError(
            f"rule {rule.key()!r} has multiple predicates on field(s) {sorted(dupes)}; "
            f"field_transforms cannot express two chains for one field"
        )
    unknown = [p.transform for p in rule.predicates if p.transform not in _LOWERED_CHAIN]
    if unknown:
        raise LoweringUnsupportedError(
            f"rule {rule.key()!r} uses transform(s) {unknown} with no registry chain"
        )
    return BlockingKeyConfig(
        fields=fields,
        field_transforms={p.field: list(_LOWERED_CHAIN[p.transform]) for p in rule.predicates},
    )


def lower_rules_to_blocking_config(
    rules: list[BlockingRule],
    *,
    max_block_size: int = 5000,
    skip_oversized: bool = True,
) -> BlockingConfig:
    """Lower learned rules to a multi_pass BlockingConfig that bucket accepts.

    Mirrors ``apply_learned_blocks``: same ``rules[:3]`` cap, union semantics.
    Raises LoweringUnsupportedError if ANY of those rules cannot be lowered
    exactly -- a partial lowering would silently drop that rule's candidates.
    """
    from goldenmatch.config.schemas import BlockingConfig

    if not rules:
        raise LoweringUnsupportedError("no rules to lower")
    return BlockingConfig(
        strategy="multi_pass",
        passes=[lower_rule_to_key(r) for r in rules[:3]],
        union_mode=True,
        max_block_size=max_block_size,
        skip_oversized=skip_oversized,
    )


# ── Cache ─────────────────────────────────────────────────────────────────


def save_learned_rules(rules: list[BlockingRule], path: str | Path) -> None:
    """Save learned rules to JSON for reuse."""
    data = [
        {
            "predicates": [{"field": p.field, "transform": p.transform} for p in r.predicates],
            "recall": r.recall,
            "reduction_ratio": r.reduction_ratio,
            "n_blocks": r.n_blocks,
        }
        for r in rules
    ]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_learned_rules(path: str | Path) -> list[BlockingRule] | None:
    """Load cached learned rules. Returns None if file doesn't exist."""
    p = Path(path)
    if not p.exists():
        return None
    with open(p) as f:
        data = json.load(f)
    return [
        BlockingRule(
            predicates=[BlockingPredicate(**pred) for pred in item["predicates"]],
            recall=item["recall"],
            reduction_ratio=item["reduction_ratio"],
            n_blocks=item.get("n_blocks", 0),
        )
        for item in data
    ]
