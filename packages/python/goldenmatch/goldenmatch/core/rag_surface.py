"""Entity-aware RAG surface -- collapse retrieved records into canonical entities (#1092).

Composes the read side (semantic retrieval, #1089) with the write side
(LLM/deterministic canonicalization, #1091) into one call an agent or RAG
pipeline can reach for:

    retrieve  ->  resolve to entities (dedupe)  ->  conflict-aware fact merge

Instead of handing the LLM a long list of duplicate / contradictory raw chunks,
``entity_aware_retrieve`` returns a SHORT list of distinct entities -- each one a
single canonical record reconciled from its duplicates, with per-cell provenance
(which retrieved record each field came from). Fewer, cleaner, non-contradictory
entities reach the model's context window.

Built entirely on existing primitives -- ``retrieve_similar_records`` (#1089),
``dedupe_df`` (the core resolver), and ``canonicalize_cluster`` (#1091) -- so it
adds no new dependency and has zero impact on the dedupe/blocking pipeline.

Zero cloud by default: the in-house embedder + deterministic most-complete
canonicalization need no network or torch. Supply ``llm_call`` / ``budget`` to
let an LLM reconcile borderline fields, and ``exact`` / ``fuzzy`` / ``blocking``
to steer how retrieved records resolve into entities (zero-config when omitted).

The resolve step never raises: if dedupe degrades on a pathological tiny frame,
each retrieved record falls back to its own entity (an honest no-op), so the
surface is always total -- safe to wire into a pipeline.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from goldenmatch.core.llm_canonicalize import CanonicalRecord, canonicalize_cluster
from goldenmatch.core.retrieval import RetrievedRecord, retrieve_similar_records

logger = logging.getLogger(__name__)


@dataclass
class Entity:
    """One resolved entity: a canonical record + the retrieved records it absorbed."""

    entity_id: int
    canonical: CanonicalRecord
    members: list[RetrievedRecord] = field(default_factory=list)
    score: float = 0.0  # best (max) retrieval similarity among the members

    @property
    def size(self) -> int:
        """How many retrieved records collapsed into this entity."""
        return len(self.members)

    @property
    def record(self) -> dict[str, Any]:
        """The canonical (conflict-merged) record for the entity."""
        return self.canonical.record

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "score": round(self.score, 4),
            "size": self.size,
            "canonical": self.canonical.as_dict(),
            "members": [m.as_dict() for m in self.members],
        }


@dataclass
class EntityRetrievalResult:
    """The entity-aware retrieval result: distinct entities + the dedup headline.

    Iterable and sized over its ``entities`` for ergonomic use
    (``for e in result`` / ``len(result)``).
    """

    entities: list[Entity] = field(default_factory=list)
    retrieved: int = 0  # raw records retrieved before resolution
    n_entities: int = 0  # distinct entities after resolution
    collapsed: int = 0  # retrieved - n_entities (duplicates removed from context)
    method: str = "deterministic"  # "llm" if any entity was canonicalized by an LLM

    def __iter__(self):
        return iter(self.entities)

    def __len__(self) -> int:
        return len(self.entities)

    def as_dict(self) -> dict[str, Any]:
        return {
            "retrieved": self.retrieved,
            "n_entities": self.n_entities,
            "collapsed": self.collapsed,
            "method": self.method,
            "entities": [e.as_dict() for e in self.entities],
        }


def _resolve_hits(
    hits: list[RetrievedRecord],
    *,
    exact: list[str] | None,
    fuzzy: dict[str, float] | None,
    blocking: list[str] | None,
    dedupe_threshold: float | None,
    config: Any | None,
) -> list[list[RetrievedRecord]]:
    """Group retrieved records into entities by running the core resolver.

    Returns one list of member ``RetrievedRecord`` per entity. Degrades to one
    entity per record if dedupe can't run (so the surface never raises).
    """
    if len(hits) <= 1:
        return [[h] for h in hits]

    # Build a frame from the retrieved records; the row position is the id the
    # resolver reports back in each cluster's members.
    rows = [dict(h.record) for h in hits]
    frame = pl.DataFrame(rows).with_columns(
        pl.Series("__row_id__", list(range(len(hits))), dtype=pl.Int64)
    )

    try:
        from goldenmatch import dedupe_df

        result = dedupe_df(
            frame,
            config=config,
            exact=exact,
            fuzzy=fuzzy,
            blocking=blocking,
            threshold=dedupe_threshold,
        )
        clusters = result.clusters or {}
    except Exception:
        logger.warning(
            "entity_aware_retrieve: resolve step failed; each record is its own "
            "entity",
            exc_info=True,
        )
        return [[h] for h in hits]

    groups: list[list[RetrievedRecord]] = []
    covered: set[int] = set()
    for info in clusters.values():
        members = info.get("members") if isinstance(info, dict) else None
        if not members:
            continue
        idxs = sorted({int(m) for m in members if 0 <= int(m) < len(hits)})
        if not idxs:
            continue
        covered.update(idxs)
        groups.append([hits[i] for i in idxs])

    # Any retrieved record not in a multi-member cluster is its own entity.
    for i, h in enumerate(hits):
        if i not in covered:
            groups.append([h])
    return groups


def entity_aware_retrieve(
    df: pl.DataFrame,
    query: str,
    column: str,
    *,
    k: int = 20,
    model: str = "inhouse",
    threshold: float = 0.0,
    filters: dict[str, Any] | None = None,
    embedder: Any = None,
    exact: list[str] | None = None,
    fuzzy: dict[str, float] | None = None,
    blocking: list[str] | None = None,
    dedupe_threshold: float | None = None,
    config: Any | None = None,
    fields: list[str] | None = None,
    llm_call: Any = None,
    budget: Any = None,
    canon_model: str = "gpt-4o-mini",
) -> EntityRetrievalResult:
    """Retrieve, resolve, and canonicalize -- the entity-aware RAG surface.

    Semantically retrieves the top-``k`` records similar to ``query``, resolves
    them into distinct entities (dedupe), and merges each entity's facts into one
    canonical record with provenance. Returns fewer, conflict-reconciled entities
    than raw retrieval would -- the records an LLM should actually see.

    Args:
        df: the corpus frame.
        query: free-text query to embed and search for.
        column: the column of ``df`` to embed + the text to resolve on.
        k: max records to retrieve before resolution.
        model: embedder id for retrieval (default ``"inhouse"`` -- local,
            deterministic, no cloud/torch).
        threshold: minimum cosine similarity a retrieved record must reach.
        filters: optional ``{column: value}`` equality pre-filter (metadata),
            applied before embedding.
        embedder: explicit embedder object (overrides ``model``; for tests).
        exact: exact-match columns for the resolve step (passed to ``dedupe_df``).
        fuzzy: ``{column: threshold}`` fuzzy-match config for the resolve step.
        blocking: blocking columns for the resolve step.
        dedupe_threshold: override fuzzy threshold for the resolve step.
        config: an explicit ``GoldenMatchConfig`` for the resolve step (wins over
            ``exact`` / ``fuzzy``). When all resolve config is omitted, dedupe is
            zero-config (auto-configured).
        fields: which fields to canonicalize per entity (default: all non-internal).
        llm_call: optional ``llm_call(prompt) -> (text, in_tok, out_tok)`` to let
            an LLM reconcile each entity's fields; deterministic most-complete
            merge when omitted.
        budget: optional ``BudgetTracker`` gating the LLM canonicalization.
        canon_model: LLM model id for canonicalization + budget accounting.

    Returns:
        An ``EntityRetrievalResult`` -- the distinct ``entities`` (ranked by best
        member similarity) plus ``retrieved`` / ``n_entities`` / ``collapsed``
        counts. Never raises on valid input.
    """
    hits = retrieve_similar_records(
        df,
        query,
        column,
        k=k,
        model=model,
        threshold=threshold,
        filters=filters,
        embedder=embedder,
    )
    if not hits:
        return EntityRetrievalResult(entities=[], retrieved=0, n_entities=0, collapsed=0)

    groups = _resolve_hits(
        hits,
        exact=exact,
        fuzzy=fuzzy,
        blocking=blocking,
        dedupe_threshold=dedupe_threshold,
        config=config,
    )

    entities: list[Entity] = []
    any_llm = False
    for group in groups:
        canonical = canonicalize_cluster(
            [g.record for g in group],
            fields=fields,
            llm_call=llm_call,
            budget=budget,
            model=canon_model,
        )
        any_llm = any_llm or canonical.method == "llm"
        best = max((g.score for g in group), default=0.0)
        entities.append(Entity(entity_id=-1, canonical=canonical, members=group, score=best))

    # Rank entities by best member similarity (stable), then assign ids by rank.
    entities.sort(key=lambda e: e.score, reverse=True)
    for rank, e in enumerate(entities):
        e.entity_id = rank

    return EntityRetrievalResult(
        entities=entities,
        retrieved=len(hits),
        n_entities=len(entities),
        collapsed=len(hits) - len(entities),
        method="llm" if any_llm else "deterministic",
    )
