"""v1.11: eager promotion of identity-prior columns to negative evidence.

Spec: docs/superpowers/specs/2026-05-08-autoconfig-negative-evidence-and-clustered-identity-design.md §Architecture #2.

Pure function — no controller state, no I/O. Called from auto_configure_df
between config-v0 build and the iteration loop.
"""
from __future__ import annotations

import logging

import polars as pl

from goldenmatch.config.schemas import (
    GoldenMatchConfig,
    MatchkeyConfig,
    NegativeEvidenceField,
    VALID_SCORERS,
)
from goldenmatch.core.complexity_profile import ColumnPrior

logger = logging.getLogger(__name__)

_IDENTITY_SCORE_THRESHOLD = 0.7
_CARDINALITY_THRESHOLD = 0.5
_DEFAULT_NE_THRESHOLD = 0.4
_DEFAULT_NE_PENALTY = 0.3


def _pick_scorer_for_column(col_name: str, col_type: str) -> tuple[list[str], str]:
    """Pick (transforms, scorer) tuple for negative-evidence on a column.

    Scoring is name-keyed (col_name substring match) because the Polars
    dtype class name passed as col_type (e.g. "utf8", "int64") does NOT
    match the ColumnType vocabulary this function expects ("email", "phone",
    etc.) — see Phase 1 review fix S3-4.  The col_type branches are kept
    for future callers that pass a ColumnType-vocabulary string.

    Always returns a scorer from VALID_SCORERS.  Defaults to ([], 'ensemble')
    for unknown column types.
    """
    name_lower = col_name.lower()
    type_lower = (col_type or "").lower()

    if "phone" in name_lower or type_lower == "phone":
        return (["digits_only"], "exact")
    if "email" in name_lower or type_lower == "email":
        return ([], "token_sort")
    if "address" in name_lower or "addr" in name_lower or type_lower == "address":
        return ([], "token_sort")
    if type_lower in {"date", "datetime"}:
        return ([], "exact")
    return ([], "ensemble")


def _is_in_matchkey_fields(col: str, mk: MatchkeyConfig) -> bool:
    """Return True if col appears in the positive fields of this matchkey."""
    return any(f.field == col for f in mk.fields)


def _is_in_blocking(col: str, blocking) -> bool:
    """Return True if col appears in any blocking key's fields list."""
    if blocking is None:
        return False
    for key in blocking.keys or []:
        if col in (key.fields or []):
            return True
    return False


def promote_negative_evidence(
    config: GoldenMatchConfig,
    df: pl.DataFrame,
    column_priors: dict[str, ColumnPrior],
) -> GoldenMatchConfig:
    """Add NE fields to all weighted matchkeys based on column priors.

    Eligibility per column:
        column_priors[col].identity_score >= _IDENTITY_SCORE_THRESHOLD (0.7)
        AND cardinality_ratio (n_unique / n_rows) >= _CARDINALITY_THRESHOLD (0.5)
        AND col NOT in matchkey.fields
        AND col NOT in blocking.keys

    Idempotent: skips columns already in the NE list for each matchkey.

    Returns a new GoldenMatchConfig (via model_copy); does not mutate input.
    Empty df or empty column_priors → returns config unchanged.
    """
    if df.is_empty() or not column_priors:
        return config

    new_matchkeys: list[MatchkeyConfig] = []
    for mk in config.matchkeys:
        if mk.type != "weighted":
            new_matchkeys.append(mk)
            continue

        existing_ne_fields: set[str] = {n.field for n in (mk.negative_evidence or [])}
        new_ne: list[NegativeEvidenceField] = list(mk.negative_evidence) if mk.negative_evidence else []

        for col, prior in column_priors.items():
            # Already in NE list (idempotency guard)
            if col in existing_ne_fields:
                continue
            # Identity score gate
            if prior.identity_score < _IDENTITY_SCORE_THRESHOLD:
                continue
            # Positive matchkey fields gate
            if _is_in_matchkey_fields(col, mk):
                continue
            # Blocking gate
            if _is_in_blocking(col, config.blocking):
                continue
            # Column must exist in df for cardinality check
            if col not in df.columns:
                continue
            # Cardinality gate
            try:
                cardinality_ratio = df[col].n_unique() / max(1, df.height)
            except Exception:
                continue
            if cardinality_ratio < _CARDINALITY_THRESHOLD:
                continue

            # NOTE: col_type_hint is intentionally empty ("") here.
            # df.schema.get(col).__class__.__name__.lower() returns Polars dtype
            # names like "utf8" or "int64", NOT the ColumnType vocabulary
            # ("email", "phone", "address") that _pick_scorer_for_column's
            # type-keyed branches expect.  Passing "" ensures the name-keyed
            # branches in _pick_scorer_for_column handle the dispatch.
            col_type_hint = ""
            transforms, scorer = _pick_scorer_for_column(col, col_type_hint)

            new_ne.append(NegativeEvidenceField(
                field=col,
                transforms=transforms,
                scorer=scorer,
                threshold=_DEFAULT_NE_THRESHOLD,
                penalty=_DEFAULT_NE_PENALTY,
            ))
            logger.info(
                "auto-config: promoted negative_evidence field=%s "
                "(identity_score=%.2f, cardinality_ratio=%.2f, "
                "transforms=%s, scorer=%s)",
                col,
                prior.identity_score,
                cardinality_ratio,
                transforms,
                scorer,
            )

        new_matchkeys.append(
            mk.model_copy(update={"negative_evidence": new_ne if new_ne else None})
        )

    return config.model_copy(update={"matchkeys": new_matchkeys})
