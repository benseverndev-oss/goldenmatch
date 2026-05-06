"""Attach a one-line natural language explanation to lineage pair records.

``goldenmatch.core.explain.explain_pair_nl`` builds a template-based summary
from the field-level breakdown (no LLM, zero cost). The web layer surfaces
that string above each pair's field table so reviewers don't have to read
the diff to understand why a pair matched.

The engine's signature accepts ``row_a`` / ``row_b`` dicts but the current
implementation doesn't reference them — only the field scores and overall
score drive the prose. Passing ``{}`` keeps that contract explicit and
avoids round-tripping the source rows just to throw them away here.
"""
from __future__ import annotations

from goldenmatch.core.explain import explain_pair_nl


def prose_for_pair(pair: dict) -> str:
    return explain_pair_nl(
        row_a={},
        row_b={},
        field_scores=pair.get("fields") or [],
        overall_score=float(pair.get("score") or 0.0),
    )


def with_prose(pair: dict) -> dict:
    """Return a shallow copy of ``pair`` with a ``prose`` key added.

    Skips re-computation when ``prose`` is already present (idempotent if
    the same pair flows through multiple enrichment sites).
    """
    if "prose" in pair:
        return pair
    return {**pair, "prose": prose_for_pair(pair)}


def enrich_pairs(pairs: list[dict]) -> list[dict]:
    return [with_prose(p) for p in pairs]
