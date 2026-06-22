"""One-to-many screening -- screen a single record against a watchlist (#1095).

The compliance primitive AML/KYC/sanctions needs: submit ONE record, get back
the watchlist entries it resembles, each with a score AND a per-field reason for
*why* it hit. This is the inverse shape of dedupe (many-to-many): here one query
record is scored against a reference list (OFAC / PEP / internal blocklist).

Built on the existing ``match_one`` scoring primitive -- no new matching engine.
``screen_record`` runs each threshold-bearing matchkey against the watchlist,
keeps the best score per watchlist row, attaches a per-field explanation, and
(optionally) collapses alias/AKA rows that share an ``entity_id_column`` to one
hit per sanctioned entity.

Scope: matching uses ``match_one``, which covers threshold-bearing matchkeys
(``weighted`` / ``probabilistic``); exact matchkeys return ``[]`` there, so an
exact-only screening path is a follow-up. Surfacing this on MCP / A2A / REST is
also a follow-up -- this module is the library core those will call.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import polars as pl

from goldenmatch.core.match_one import match_one
from goldenmatch.core.scorer import apply_transforms, score_field

if TYPE_CHECKING:
    from goldenmatch.config.schemas import MatchkeyConfig

logger = logging.getLogger(__name__)

# A per-field similarity at or above this reads as "this field agreed" in the
# reason breakdown. The aggregate match decision is still the matchkey's own
# threshold via ``match_one`` -- this only labels the per-field explanation.
_FIELD_AGREE = 0.85


@dataclass
class FieldReason:
    """Why one field contributed to a hit: the two values and their similarity."""

    field: str
    record_value: Any
    candidate_value: Any
    score: float | None
    agreed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "record_value": self.record_value,
            "candidate_value": self.candidate_value,
            "score": round(self.score, 4) if self.score is not None else None,
            "agreed": self.agreed,
        }


@dataclass
class ScreeningHit:
    """A single watchlist entry the query record resembles."""

    entity_id: Any
    row_id: int
    score: float
    matchkey: str | None
    reasons: list[FieldReason] = field(default_factory=list)
    candidate: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "row_id": self.row_id,
            "score": round(self.score, 4),
            "matchkey": self.matchkey,
            "reasons": [r.as_dict() for r in self.reasons],
            "candidate": self.candidate,
        }


@dataclass
class ScreeningResult:
    """The hits for one screened record, ranked highest-score first."""

    hits: list[ScreeningHit] = field(default_factory=list)
    screened: int = 0

    @property
    def is_hit(self) -> bool:
        return bool(self.hits)

    @property
    def top(self) -> ScreeningHit | None:
        return self.hits[0] if self.hits else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "is_hit": self.is_hit,
            "screened": self.screened,
            "hits": [h.as_dict() for h in self.hits],
        }


def _ensure_row_id(watchlist: pl.DataFrame) -> pl.DataFrame:
    """``match_one`` keys hits by ``__row_id__``; add one if absent."""
    if "__row_id__" in watchlist.columns:
        return watchlist
    return watchlist.with_row_index("__row_id__")


def _field_reasons(
    record: dict[str, Any], candidate: dict[str, Any], mk: MatchkeyConfig
) -> list[FieldReason]:
    """Per-field similarity breakdown for one (record, candidate) pair, using the
    same scorer + transforms the matchkey uses to score."""
    out: list[FieldReason] = []
    for f in mk.fields:
        name = f.field
        if name is None:
            continue
        rv = record.get(name)
        cv = candidate.get(name)
        score: float | None
        if f.scorer:
            try:
                score = score_field(
                    apply_transforms(rv, f.transforms),
                    apply_transforms(cv, f.transforms),
                    f.scorer,
                )
            except Exception:
                score = None
        else:
            score = 1.0 if rv is not None and str(rv) == str(cv) else 0.0
        out.append(
            FieldReason(
                field=name,
                record_value=rv,
                candidate_value=cv,
                score=score,
                agreed=score is not None and score >= _FIELD_AGREE,
            )
        )
    return out


def screen_record(
    record: dict[str, Any],
    watchlist: pl.DataFrame,
    matchkeys: MatchkeyConfig | list[MatchkeyConfig],
    *,
    threshold: float | None = None,
    entity_id_column: str | None = None,
    limit: int | None = None,
    ann_blocker: Any = None,
    embedder: Any = None,
    ann_column: str | None = None,
    ann_top_k: int = 20,
    base_store: Any = None,
) -> ScreeningResult:
    """Screen one record against a watchlist and return scored, explained hits.

    Args:
        record: the query record (field -> value), e.g. an onboarding applicant.
        watchlist: the reference frame (OFAC / PEP / blocklist). A ``__row_id__``
            is added if absent.
        matchkeys: one matchkey or a list. Threshold-bearing types
            (``weighted`` / ``probabilistic``) are used; exact matchkeys are
            skipped (``match_one`` returns ``[]`` for them).
        threshold: optional extra score floor applied ON TOP of each matchkey's
            own threshold (only hits ``>= threshold`` are kept).
        entity_id_column: when set, alias/AKA rows sharing this value collapse to
            ONE hit per sanctioned entity (the best-scoring alias row), so a
            multi-name watchlist entry isn't reported N times. When unset, the
            hit's ``entity_id`` is its ``row_id``.
        limit: cap the number of hits returned (after ranking).
        ann_blocker / embedder / ann_column / ann_top_k / base_store: forwarded
            to ``match_one`` for ANN-accelerated candidate retrieval on large
            watchlists.

    Returns:
        A ``ScreeningResult`` whose ``hits`` are ranked highest-score first, each
        carrying a per-field ``reasons`` breakdown.
    """
    mks = [matchkeys] if not isinstance(matchkeys, list) else matchkeys
    wl = _ensure_row_id(watchlist)

    # Best (score, matchkey) per watchlist row across all matchkeys.
    best: dict[int, tuple[float, MatchkeyConfig]] = {}
    for mk in mks:
        if mk.threshold is None:
            continue  # exact matchkey -> match_one returns []
        try:
            hits = match_one(
                record, wl, mk,
                ann_blocker=ann_blocker, embedder=embedder,
                ann_column=ann_column, top_k=ann_top_k, store=base_store,
            )
        except Exception:
            logger.warning(
                "screen_record: match_one failed for matchkey %r; skipping",
                getattr(mk, "name", "?"),
            )
            continue
        for row_id, score in hits:
            if threshold is not None and score < threshold:
                continue
            irid = int(row_id)
            cur = best.get(irid)
            if cur is None or score > cur[0]:
                best[irid] = (float(score), mk)

    if not best:
        return ScreeningResult(hits=[], screened=wl.height)

    candidate_rows = {
        int(r["__row_id__"]): r
        for r in wl.filter(pl.col("__row_id__").is_in(list(best.keys()))).to_dicts()
    }

    hits: list[ScreeningHit] = []
    for row_id, (score, mk) in best.items():
        cand = candidate_rows.get(row_id, {})
        payload = {k: v for k, v in cand.items() if not k.startswith("__")}
        entity_id = cand.get(entity_id_column) if entity_id_column else row_id
        hits.append(
            ScreeningHit(
                entity_id=entity_id,
                row_id=row_id,
                score=score,
                matchkey=getattr(mk, "name", None),
                reasons=_field_reasons(record, cand, mk),
                candidate=payload,
            )
        )

    # Collapse alias/AKA rows of the same entity to the best-scoring hit.
    if entity_id_column:
        by_entity: dict[Any, ScreeningHit] = {}
        for h in hits:
            cur = by_entity.get(h.entity_id)
            if cur is None or h.score > cur.score:
                by_entity[h.entity_id] = h
        hits = list(by_entity.values())

    hits.sort(key=lambda h: h.score, reverse=True)
    if limit is not None:
        hits = hits[:limit]
    return ScreeningResult(hits=hits, screened=wl.height)


def screen_records(
    records: list[dict[str, Any]],
    watchlist: pl.DataFrame,
    matchkeys: MatchkeyConfig | list[MatchkeyConfig],
    **kwargs: Any,
) -> list[ScreeningResult]:
    """Batch screening: ``screen_record`` for each record (one ``__row_id__``
    pass over the watchlist is shared). Returns one result per input record,
    positionally aligned."""
    wl = _ensure_row_id(watchlist)
    return [screen_record(r, wl, matchkeys, **kwargs) for r in records]
