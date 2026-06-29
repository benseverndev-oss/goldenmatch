"""apply_suggestion: apply a ConfigPatch to a GoldenMatchConfig.

The function is a pure, side-effect-free transformation: it deep-copies the
config first so the caller's original is never mutated.

Supported patch ops (matching the Rust kernel's serde snake_case tag):

    {"op": "set_threshold", "matchkey": "<name>", "value": <float>}
        Set the threshold of the named matchkey.

    {"op": "set_scorer", "matchkey": "<name>", "field": "<field>", "scorer": "<scorer>"}
        Set the scorer of a named field within a named matchkey.

    {"op": "drop_matchkey", "matchkey": "<name>"}
        Remove the named matchkey (an over-broad exact matchkey on a derived
        key that is collapsing precision). Refuses to drop the last matchkey.

    {"op": "add_negative_evidence", "field": "<field>"}
        Add a NegativeEvidenceField to the first weighted matchkey.
        If the field is already present, the list is left unchanged (idempotent).
        The patch carries no matchkey name; this op targets the first weighted
        matchkey because NE is meaningful only on weighted/exact matchkeys and
        the kernel emits this op only when it has already identified the relevant
        matchkey type.  If no weighted matchkey exists, the first matchkey of any
        type is used as a fallback.

NegativeEvidenceField defaults when constructed from an add_negative_evidence
patch (the patch carries only the field name; defaults mirror the autoconfig
promote_negative_evidence constants):
    scorer:    "ensemble"
    threshold: 0.4
    penalty:   0.3
    transforms: []
"""
from __future__ import annotations

import copy
import logging

from goldenmatch.config.schemas import (
    GoldenMatchConfig,
    MatchkeyConfig,
    NegativeEvidenceField,
)
from goldenmatch.core.suggest.types import Suggestion

logger = logging.getLogger(__name__)

# Defaults for a kernel-suggested NE field.  Mirror the constants in
# autoconfig_negative_evidence.py (_DEFAULT_NE_THRESHOLD / _DEFAULT_NE_PENALTY).
_DEFAULT_NE_SCORER = "ensemble"
_DEFAULT_NE_THRESHOLD = 0.4
_DEFAULT_NE_PENALTY = 0.3


def apply_suggestion(
    config: GoldenMatchConfig,
    suggestion: Suggestion,
) -> GoldenMatchConfig:
    """Return a NEW GoldenMatchConfig with *suggestion*'s patch applied.

    The original *config* is never modified.

    Raises
    ------
    ValueError
        If the patch op is unknown, or if the patch references a matchkey or
        field that does not exist in the config.
    """
    # Deep-copy first — never mutate the caller's config.
    new_config: GoldenMatchConfig = copy.deepcopy(config)

    patch = suggestion.patch
    op = patch.get("op", "")

    if op == "set_threshold":
        _apply_set_threshold(new_config, patch)
    elif op == "set_scorer":
        _apply_set_scorer(new_config, patch)
    elif op == "add_negative_evidence":
        _apply_add_negative_evidence(new_config, patch)
    elif op == "drop_matchkey":
        _apply_drop_matchkey(new_config, patch)
    else:
        raise ValueError(f"unknown patch op: {op!r}")

    return new_config


# ── private dispatch helpers ───────────────────────────────────────────────────

def _find_matchkey(config: GoldenMatchConfig, name: str) -> MatchkeyConfig:
    """Return the matchkey with the given name, or raise ValueError."""
    for mk in config.get_matchkeys():
        if mk.name == name:
            return mk
    raise ValueError(
        f"patch references matchkey {name!r} which does not exist in the config "
        f"(available: {[m.name for m in config.get_matchkeys()]})"
    )


def _apply_set_threshold(config: GoldenMatchConfig, patch: dict) -> None:
    mk = _find_matchkey(config, patch["matchkey"])
    mk.threshold = float(patch["value"])


def _apply_set_scorer(config: GoldenMatchConfig, patch: dict) -> None:
    mk = _find_matchkey(config, patch["matchkey"])
    field_name = patch["field"]
    for f in mk.fields:
        if f.field == field_name:
            f.scorer = patch["scorer"]
            return
    raise ValueError(
        f"patch references field {field_name!r} in matchkey {mk.name!r} "
        f"which does not exist (available: {[f.field for f in mk.fields]})"
    )


def _apply_drop_matchkey(config: GoldenMatchConfig, patch: dict) -> None:
    """Remove the named matchkey from the config.

    Matchkeys live either at ``config.matchkeys`` (top-level) or at
    ``config.match_settings.matchkeys``; remove from whichever list holds the
    name. Refuses to drop the last remaining matchkey (a config with no
    matchkeys produces no pairs) -- the kernel only emits this op when >= 2
    matchkeys exist, so this guard is defensive.
    """
    name = patch["matchkey"]
    # _find_matchkey raises ValueError if the name is absent.
    _find_matchkey(config, name)

    if len(config.get_matchkeys()) <= 1:
        raise ValueError(
            f"refusing to drop matchkey {name!r}: it is the only matchkey "
            "(dropping it would leave the config with no matchkeys)"
        )

    for holder in (config, getattr(config, "match_settings", None)):
        mks = getattr(holder, "matchkeys", None)
        if mks:
            kept = [mk for mk in mks if mk.name != name]
            if len(kept) != len(mks):
                holder.matchkeys = kept
                return


def _apply_add_negative_evidence(config: GoldenMatchConfig, patch: dict) -> None:
    """Add a NegativeEvidenceField to the first weighted matchkey.

    Selection order:
    1. First matchkey with type == "weighted".
    2. First matchkey with type == "exact" (also supports NE).
    3. First matchkey of any type (fallback).

    Idempotent: if the field is already in the NE list, nothing changes.
    """
    field_name: str = patch["field"]
    matchkeys = config.get_matchkeys()
    if not matchkeys:
        return

    # Pick target matchkey.
    weighted = next((m for m in matchkeys if m.type == "weighted"), None)
    exact = next((m for m in matchkeys if m.type == "exact"), None)
    target = weighted or exact
    if target is None:
        # The kernel should only emit AddNegativeEvidence when a weighted or
        # exact matchkey exists; reaching this branch indicates an unexpected
        # kernel output.
        logger.warning(
            "_apply_add_negative_evidence: no weighted or exact matchkey found; "
            "falling back to first matchkey %r (unexpected kernel output for field %r)",
            matchkeys[0].name, field_name,
        )
        target = matchkeys[0]

    # Initialise NE list if absent.
    if target.negative_evidence is None:
        target.negative_evidence = []

    # Idempotency guard.
    if any(ne.field == field_name for ne in target.negative_evidence):
        return

    target.negative_evidence.append(
        NegativeEvidenceField(
            field=field_name,
            transforms=[],
            scorer=_DEFAULT_NE_SCORER,
            threshold=_DEFAULT_NE_THRESHOLD,
            penalty=_DEFAULT_NE_PENALTY,
        )
    )
