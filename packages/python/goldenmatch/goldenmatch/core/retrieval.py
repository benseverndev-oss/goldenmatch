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

from goldenmatch.core.ann_blocker import ANNBlocker
from goldenmatch.core.embedder import get_embedder
from goldenmatch.core.frame import to_frame

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


def _validated_frame(df, column: str, what: str):
    """Seam-normalise ``df`` and check ``column`` exists.

    Both retrievers took a ``pl.DataFrame`` and asked ``column not in df.columns``.
    That is a seam trap rather than a typo: polars' ``.columns`` is a list of
    NAMES, pyarrow's is a list of ChunkedArrayS. Once MatchEngine started handing
    these functions an arrow table the membership test compared a name against
    arrays, so every lookup "failed" -- and the error message then printed the
    array CONTENTS, which is what the MCP and a2a retrieval tools reported.
    """
    frame = to_frame(df)
    if column not in frame.columns:
        raise ValueError(
            f"{what}: column {column!r} not in dataframe (have {frame.columns})"
        )
    return frame


def _filtered_rows(frame, column: str, filters: dict | None):
    """``(values, row_ids, rows, height)``, or None when nothing is left to rank.

    Equality filters go through ``filter_eq`` instead of a composed ``pl.Expr``,
    so this runs on either backend.
    """
    if frame.height == 0:
        return None
    for col, val in (filters or {}).items():
        if col not in frame.columns:
            return None
        frame = frame.filter_eq(col, val)
    if frame.height == 0:
        return None
    values = ["" if v is None else str(v) for v in frame.utf8_values(column)]
    rows = frame.to_dicts()
    if "__row_id__" in frame.columns:
        row_ids = [int(r["__row_id__"]) for r in rows]
    else:
        row_ids = list(range(frame.height))
    return values, row_ids, rows, frame.height


def retrieve_similar_records(
    df: Any,  # any Frame-able: pl.DataFrame or pa.Table (routed via to_frame)
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
    frame = _validated_frame(df, column, "retrieve_similar_records")
    if not query:
        return []
    prepared = _filtered_rows(frame, column, filters)
    if prepared is None:
        return []
    values, row_ids, rows, height = prepared

    emb = embedder if embedder is not None else get_embedder(model)
    try:
        corpus = emb.embed_column(values, cache_key=f"retrieve:{column}:{hash(tuple(values))}")
        q_vec = emb.embed_column([str(query)], cache_key=f"retrieve_q:{hash(str(query))}")
    except Exception:
        logger.warning("retrieve_similar_records: embedding failed", exc_info=True)
        return []

    blocker = ANNBlocker(top_k=k)
    blocker.build_index(corpus)
    neighbors = blocker.query_one(q_vec[0])  # [(position, cosine_score), ...] desc

    out: list[RetrievedRecord] = []
    for pos, score in neighbors:
        if score < threshold:
            continue
        if pos < 0 or pos >= height:
            continue
        record = {k2: v2 for k2, v2 in rows[pos].items() if not k2.startswith("__")}
        out.append(RetrievedRecord(row_id=row_ids[pos], score=float(score), record=record))
    return out


_FUZZY_SCORERS = ("jaro_winkler", "levenshtein", "indel")


def _fuzzy_extract(
    query: str, values: list[str], scorer: str, cutoff: float, limit: int
) -> list[tuple[int, float]]:
    """One-vs-many top-k lexical ranking of ``values`` against ``query``.

    Prefers the ``goldenfuzz`` wheel (native, builds the query bitmap once and
    reuses it across every value -- see ``goldenfuzz.extract``). Falls back to the
    vendored pure-Python ``core.strsim`` when the wheel is absent; the two are
    byte-identical (both are the ``goldenfuzz-core`` math), so the ranking is the
    same either way -- the wheel is just faster.
    """
    try:
        import goldenfuzz  # optional: pip install goldenfuzz

        return goldenfuzz.extract(query, values, scorer=scorer, score_cutoff=cutoff, limit=limit)
    except ImportError:
        from goldenmatch.core import strsim

        fn = {
            "jaro_winkler": strsim.jaro_winkler_normalized_similarity,
            "levenshtein": strsim.levenshtein_normalized_similarity,
            "indel": strsim.indel_normalized_similarity,
        }[scorer]
        scored = [(i, fn(query, v)) for i, v in enumerate(values)]
        scored = [(i, s) for (i, s) in scored if s >= cutoff]
        scored.sort(key=lambda t: (-t[1], t[0]))
        return scored[:limit] if limit else scored


def retrieve_similar_fuzzy(
    df: Any,  # any Frame-able: pl.DataFrame or pa.Table (routed via to_frame)
    query: str,
    column: str,
    *,
    k: int = 20,
    scorer: str = "jaro_winkler",
    threshold: float = 0.0,
    filters: dict[str, Any] | None = None,
) -> list[RetrievedRecord]:
    """Lexical (fuzzy-string) sibling of :func:`retrieve_similar_records`.

    Ranks the records in ``df`` by fuzzy similarity of ``column`` to ``query`` --
    typos, abbreviations, near-duplicates -- with NO embeddings and NO
    torch/cloud dependency. Complements the semantic path: use this when the
    signal is lexical (names, addresses, SKUs) rather than meaning.

    Powered by ``goldenfuzz`` (byte-identical to rapidfuzz; the query bitmap is
    built once and reused across the corpus), with a pure-Python fallback.

    Args:
        df/query/column/k/threshold/filters: as in
            :func:`retrieve_similar_records`, but ``threshold`` is a
            normalized-similarity cutoff in ``[0, 1]``.
        scorer: one of ``jaro_winkler`` | ``levenshtein`` | ``indel``.

    Returns:
        ``list[RetrievedRecord]`` ranked highest-similarity first.

    Raises:
        ValueError: if ``column`` is not in ``df`` or ``scorer`` is unknown.
    """
    frame = _validated_frame(df, column, "retrieve_similar_fuzzy")
    if scorer not in _FUZZY_SCORERS:
        raise ValueError(f"retrieve_similar_fuzzy: scorer must be one of {_FUZZY_SCORERS}, got {scorer!r}")
    if not query:
        return []
    prepared = _filtered_rows(frame, column, filters)
    if prepared is None:
        return []
    values, row_ids, rows, height = prepared

    ranked = _fuzzy_extract(str(query), values, scorer, threshold, k)

    out: list[RetrievedRecord] = []
    for pos, score in ranked:
        if pos < 0 or pos >= height:
            continue
        record = {k2: v2 for k2, v2 in rows[pos].items() if not k2.startswith("__")}
        out.append(RetrievedRecord(row_id=row_ids[pos], score=float(score), record=record))
    return out
