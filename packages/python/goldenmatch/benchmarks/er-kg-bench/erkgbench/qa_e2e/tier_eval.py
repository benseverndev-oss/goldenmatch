"""Slice 4b gate: resolution-recall per tier over the engineered universe.

Feed each concept's DISTINCT surfaces (deduped -- the dataset plants `cross_document_exact` variants
byte-identical to the concept) as Mentions to `resolver_for_tier(tier)`, and measure the fraction of
same-concept DISTINCT-surface PAIRS merged into one resolved group. FUZZY merges string-close variants
EXACT cannot. Needs goldenmatch (dedupe_df via the FUZZY resolver) -> goldengraph-pipeline lane.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from goldengraph.unified import ResolutionTier, resolver_for_tier

MARGIN = 0.10  # FUZZY_recall - EXACT_recall must be >= this (frozen from the measured run)


def _concept_surface_mentions():
    """(mentions, gold): one Mention per DISTINCT surface per concept (typ = entity_type); gold[i] =
    that mention's canonical_id."""
    bench_root = Path(__file__).resolve().parents[2]
    if str(bench_root) not in sys.path:
        sys.path.insert(0, str(bench_root))
    from dataset.concepts_loader import load_concepts  # type: ignore
    from goldengraph.extract import Mention

    concepts = load_concepts(bench_root / "dataset" / "concepts.jsonl")
    mentions, gold = [], []
    for c in concepts:
        surfaces = list(dict.fromkeys([c.concept] + [v.surface for v in c.variants]))
        for s in surfaces:
            mentions.append(Mention(s, c.entity_type))
            gold.append(c.canonical_id)
    return mentions, gold


def resolution_recall(tier: ResolutionTier) -> tuple[float, int]:
    """Fraction of same-concept distinct-surface pairs the tier's resolver places in one group.
    Returns (recall, n_pairs)."""
    mentions, gold = _concept_surface_mentions()
    ents = resolver_for_tier(tier)(mentions)
    group_of: dict[int, int] = {}
    for g, e in enumerate(ents):
        for i in e.member_idx:
            group_of[i] = g
    by_concept: dict[str, list[int]] = {}
    for i, c in enumerate(gold):
        by_concept.setdefault(c, []).append(i)
    merged = total = 0
    for idxs in by_concept.values():
        for a, b in combinations(idxs, 2):
            total += 1
            if group_of.get(a) == group_of.get(b):
                merged += 1
    return (merged / total if total else 0.0), total


@dataclass
class TierResult:
    fuzzy_recall: float
    exact_recall: float
    n_pairs: int


def evaluate_assertions(res: TierResult):
    """Returns [(label, ok, hard)]; `hard` rows fail the gate."""
    gap = res.fuzzy_recall - res.exact_recall
    return [
        (
            f"FUZZY out-resolves EXACT on the universe (fuzzy {res.fuzzy_recall:.3f} - exact "
            f"{res.exact_recall:.3f} = {gap:.3f} >= {MARGIN}; {res.n_pairs} pairs)",
            gap >= MARGIN,
            True,
        ),
    ]


def gate_exit_code(res: TierResult) -> int:
    return 1 if any(hard and not ok for _l, ok, hard in evaluate_assertions(res)) else 0


def run_tier_deterministic() -> TierResult:
    fr, n = resolution_recall(ResolutionTier.FUZZY)
    er, _ = resolution_recall(ResolutionTier.EXACT)
    return TierResult(fuzzy_recall=fr, exact_recall=er, n_pairs=n)


def render_tier_md(res: TierResult) -> str:
    lines = [
        "# GoldenGraph tier-resolver gate (slice 4b, no LLM)",
        "",
        "4b makes 4a's UnifiedPlan executable: resolution_tier -> the resolver ingest uses. This gate",
        "proves the tiers resolve DIFFERENTLY -- FUZZY merges variant surfaces EXACT cannot (same-",
        "concept distinct-surface merge recall on the engineered universe). The build->capability link",
        "is slice-D's dial scorecard + 4a (reused).",
        "",
        f"- FUZZY resolution-recall: {res.fuzzy_recall:.3f}",
        f"- EXACT resolution-recall: {res.exact_recall:.3f}  (distinct surfaces never exact-merge -> ~0)",
        f"- same-concept distinct-surface pairs: {res.n_pairs}",
        "",
        "## verdicts",
        "",
    ]
    for label, ok, _h in evaluate_assertions(res):
        lines.append(f"- [{'PASS' if ok else 'FAIL'}] {label}")
    return "\n".join(lines) + "\n"
