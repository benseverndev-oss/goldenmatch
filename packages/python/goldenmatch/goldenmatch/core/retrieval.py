"""Semantic retrieval -- find records similar to a query string (#1089).

The public retrieval surface over the vector machinery: embed a query, embed a
column of a frame, and return the top-K most similar records with cosine scores.
This is the read side of the RAG entity-canonicalization epic (#1087) -- an
agent or app can semantically fetch candidate records by free-text query without
running a full dedupe.

Built entirely on existing primitives -- ``get_embedder`` (any provider; the
zero-config ``"inhouse"`` model needs no cloud/torch) and ``ANNBlocker`` (FAISS
with a byte-identical numpy fallback) -- so it adds no new dependency and has
zero impact on the dedupe/blocking pipeline.

Scope: this is the in-memory retrieval API (embed-then-ANN over the supplied
frame). It is exposed over the wire as the MCP ``retrieve_similar`` tool, the
A2A ``retrieve_similar`` skill, and the REST ``POST /retrieve`` endpoint -- all
of which call this same function and return its ``RetrievedRecord`` shape. A
PERSISTENT vector index that survives across runs (#1088) shares the result type
so a persistent backend is a drop-in for the in-memory path here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from goldenmatch._polars_lazy import pl

from goldenmatch.core.ann_blocker import ANNBlocker
from goldenmatch.core.embedder import get_embedder

logger = logging.getLogger(__name__)


@dataclass
class RetrievedRecord:
    """One record returned by ``retrieve_similar_records``."""

    row_id: int
    score: float
    record: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "score": round(self.score, 4),
            "record": self.record,
        }


def retrieve_similar_records(
    df: pl.DataFrame,
    query: str,
    column: str,
    *,
    k: int = 20,
    model: str = "inhouse",
    threshold: float = 0.0,
    filters: dict[str, Any] | None = None,
    embedder: Any = None,
) -> list[RetrievedRecord]:
    """Retrieve the top-``k`` records in ``df`` most similar to ``query``.

    Args:
        df: the corpus frame. ``__row_id__`` is used for the returned id when
            present; otherwise the row's position is used.
        query: the free-text query to embed and search for.
        column: the column of ``df`` to embed as the corpus.
        k: maximum number of records to return (ranked by similarity desc).
        model: embedder id passed to ``get_embedder`` -- ``"inhouse"`` (default,
            local + deterministic, no cloud/torch), ``"all-MiniLM-L6-v2"``,
            ``"inhouse:<path>"``, a Vertex/OpenAI model, etc.
        threshold: minimum cosine similarity in ``[-1, 1]`` a record must reach.
        filters: optional ``{column: value}`` equality predicates applied to
            ``df`` BEFORE embedding (metadata pre-filter). A filter on a column
            not in ``df`` yields no results.
        embedder: an explicit embedder object (must expose
            ``embed_column(values, cache_key) -> np.ndarray``); overrides
            ``model``. Handy for tests and custom providers.

    Returns:
        ``list[RetrievedRecord]`` ranked highest-similarity first. Empty when the
        query is blank, the frame (or filtered frame) is empty, or nothing clears
        ``threshold``.

    Raises:
        ValueError: if ``column`` is not in ``df``.
    """
    if column not in df.columns:
        raise ValueError(
            f"retrieve_similar_records: column {column!r} not in dataframe "
            f"(have {df.columns})"
        )
    if not query or df.is_empty():
        return []

    work = df
    if filters:
        cond: pl.Expr | None = None
        for col, val in filters.items():
            if col not in work.columns:
                return []
            pred = pl.col(col) == val
            cond = pred if cond is None else (cond & pred)
        if cond is not None:
            work = work.filter(cond)
    if work.is_empty():
        return []

    emb = embedder if embedder is not None else get_embedder(model)
    values = ["" if v is None else str(v) for v in work[column].to_list()]
    try:
        corpus = emb.embed_column(values, cache_key=f"retrieve:{column}:{hash(tuple(values))}")
        q_vec = emb.embed_column([str(query)], cache_key=f"retrieve_q:{hash(str(query))}")
    except Exception:
        logger.warning("retrieve_similar_records: embedding failed", exc_info=True)
        return []

    blocker = ANNBlocker(top_k=k)
    blocker.build_index(corpus)
    neighbors = blocker.query_one(q_vec[0])  # [(position, cosine_score), ...] desc

    if "__row_id__" in work.columns:
        row_ids = [int(r) for r in work["__row_id__"].to_list()]
    else:
        row_ids = list(range(work.height))
    rows = work.to_dicts()

    out: list[RetrievedRecord] = []
    for pos, score in neighbors:
        if score < threshold:
            continue
        if pos < 0 or pos >= work.height:
            continue
        record = {k2: v2 for k2, v2 in rows[pos].items() if not k2.startswith("__")}
        out.append(RetrievedRecord(row_id=row_ids[pos], score=float(score), record=record))
    return out
