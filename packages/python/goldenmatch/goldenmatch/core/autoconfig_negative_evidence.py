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
)
from goldenmatch.core.complexity_profile import ColumnPrior

logger = logging.getLogger(__name__)

_IDENTITY_SCORE_THRESHOLD = 0.75
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


def _is_exact_matchkey_field(col: str, all_matchkeys: list[MatchkeyConfig]) -> bool:
    """Return True if col is used as a field in any exact matchkey.

    NE promotion is gated on this check (Phase 7 fix): auto-promoted NE is
    only safe when the column already has an exact-matchkey counterpart.
    Without an exact matchkey, a disagreeing phone/email in a pair captured
    by the weighted matchkey is ambiguous (could be corruption, not an FP),
    and the NE penalty will cause recall regression on noisy ER datasets.
    """
    return any(
        mk.type == "exact" and any(f.field == col for f in mk.fields)
        for mk in all_matchkeys
    )


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
    """Add NE fields to weighted AND exact matchkeys based on column priors.

    v1.12: walks all matchkey types (was weighted-only in v1.11).
    Probabilistic matchkeys are still skipped (see §Non-goals in spec).

    Eligibility per column (weighted matchkey branch):
        column_priors[col].identity_score >= _IDENTITY_SCORE_THRESHOLD (0.75)
        AND col is used as a field in at least one exact matchkey
          (prevents recall regression on noisy ER data — Phase 7 fix)
        AND cardinality_ratio (n_unique / n_rows) >= _CARDINALITY_THRESHOLD (0.5)
        AND col NOT in this weighted matchkey's fields
        AND col NOT in blocking.keys

    Eligibility per column (exact matchkey branch — v1.12):
        Same identity_score + cardinality gates apply.
        The _is_exact_matchkey_field gate is SKIPPED — its rationale
        (anchor safety) doesn't apply when iterating the exact matchkey itself.
        When NE is added to an exact matchkey with threshold=None, the
        threshold is set to 0.5 to activate score-and-threshold filtering.

    Idempotent: skips columns already in the NE list for each matchkey.

    Returns a new GoldenMatchConfig (via model_copy); does not mutate input.
    Empty df or empty column_priors → returns config unchanged.
    """
    if df.is_empty() or not column_priors:
        return config

    all_matchkeys = list(config.matchkeys)

    new_matchkeys: list[MatchkeyConfig] = []
    for mk in config.matchkeys:
        # v1.12: walk weighted AND exact matchkeys; skip probabilistic + others
        if mk.type not in ("weighted", "exact"):
            new_matchkeys.append(mk)
            continue

        existing_ne_fields: set[str] = {n.field for n in (mk.negative_evidence or [])}
        new_ne: list[NegativeEvidenceField] = list(mk.negative_evidence) if mk.negative_evidence else []

        for col, prior in column_priors.items():
            # Already in NE list (idempotency guard)
            if col in existing_ne_fields:
                continue
            # Identity score gate (0.75 excludes cardinality-only fallback of 0.7)
            if prior.identity_score < _IDENTITY_SCORE_THRESHOLD:
                continue
            # v1.12: apply _is_exact_matchkey_field gate ONLY on weighted branch.
            # The gate's rationale (anchor safety for NE-on-weighted) doesn't apply
            # when iterating an exact matchkey for itself — skip gate on exact branch.
            if mk.type == "weighted":
                if not _is_exact_matchkey_field(col, all_matchkeys):
                    continue
            # Positive matchkey fields gate (this matchkey)
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
                "auto-config: promoted negative_evidence field=%s on matchkey=%s "
                "(identity_score=%.2f, cardinality_ratio=%.2f, scorer=%s)",
                col, mk.name, prior.identity_score, cardinality_ratio, scorer,
            )

        # v1.12: when NE was added to an exact matchkey with threshold=None,
        # set threshold=0.5 to activate the score-and-threshold scoring path.
        # Preserve user-set thresholds unchanged.
        new_threshold = mk.threshold
        if mk.type == "exact" and len(new_ne) > len(existing_ne_fields):
            if mk.threshold is None:
                new_threshold = 0.5
                logger.info(
                    "auto-config: set default threshold=0.5 on exact matchkey=%s "
                    "(NE was added; threshold was None)",
                    mk.name,
                )

        new_matchkeys.append(
            mk.model_copy(update={
                "negative_evidence": new_ne if new_ne else None,
                "threshold": new_threshold,
            })
        )

    return config.model_copy(update={"matchkeys": new_matchkeys})
