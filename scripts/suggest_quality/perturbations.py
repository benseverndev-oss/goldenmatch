"""Perturbation catalog for the suggester gym (Task 3).

Each ``Perturbation`` is a pure config -> config mutation:
  - deep-copies the input before mutating (caller's original is NEVER touched)
  - tagged with the kernel suggestion-kind that should reverse it
  - has an ``applies_to`` guard that returns False (never raises) for configs
    that don't have the relevant structure

Built-rule perturbations (``builds_on_existing_rule=True``) use one of the
four real ``SuggestionKind`` snake_case strings emitted by the native kernel:

    raise_threshold | lower_threshold | swap_scorer | add_negative_evidence

Unbuilt-rule perturbations (``builds_on_existing_rule=False``) use placeholder
``expected_rule`` strings (``add_blocking_pass``, ``adjust_field_weight``, ``""``)
that intentionally will NOT match any current ``SuggestionKind`` -- they document
the intended future rule that a later kernel version would supply.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Callable

from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)

# ── Free-text field heuristics ────────────────────────────────────────────────

_FREETEXT_FIELD_NAMES: frozenset[str] = frozenset({
    "address", "street", "name", "first_name", "last_name",
    "full_name", "description", "notes", "title", "city",
})

# Address-like fields are the canonical target for token_sort (word-order-robust,
# used for multi-token strings like "123 Main St" where corruption scrambles tokens).
_ADDRESS_FIELD_NAMES: frozenset[str] = frozenset({"address", "street", "city"})

# Only `ensemble` signals free text purely from the scorer. `jaro_winkler` is
# routinely auto-configured on email/phone/id fields, so it is NOT a free-text
# signal -- relying on it would let bad_freetext_scorer swap the scorer on an
# email field, producing a meaningless swap_scorer recovery signal in the gym.
# `token_sort` is excluded anyway by the `scorer != "token_sort"` guard. The
# field-NAME set above is the primary free-text signal.
_FREETEXT_SCORERS: frozenset[str] = frozenset({"ensemble"})


def _is_freetext_field(mf: MatchkeyField) -> bool:
    """Return True if the field looks like a free-text field."""
    if mf.field in _FREETEXT_FIELD_NAMES:
        return True
    if mf.scorer in _FREETEXT_SCORERS:
        return True
    return False


# ── Primary matchkey helpers ──────────────────────────────────────────────────

def _primary_weighted_mk(config: GoldenMatchConfig) -> MatchkeyConfig | None:
    """Return the first weighted or fuzzy matchkey with a non-None threshold."""
    for mk in config.get_matchkeys():
        if mk.type in ("weighted", "fuzzy") and mk.threshold is not None:
            return mk
    return None


def _first_freetext_field(mk: MatchkeyConfig) -> MatchkeyField | None:
    """Return a free-text field that is not already token_sort.

    Prefers address-like fields (address, street, city) over name fields,
    since token_sort is the canonical fix for multi-token address strings
    where corruption scrambles token order.  Falls back to any other
    freetext field if no address-like field is found.
    """
    # First pass: prefer address-like field names.
    for f in mk.fields:
        if f.field in _ADDRESS_FIELD_NAMES and f.scorer != "token_sort":
            return f
    # Second pass: any other freetext field.
    for f in mk.fields:
        if _is_freetext_field(f) and f.scorer != "token_sort":
            return f
    return None


# ── Perturbation dataclass ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Perturbation:
    name: str
    expected_rule: str            # the suggestion kind that should reverse it
    builds_on_existing_rule: bool # True = fixing rule exists today (counts in headline)
    description: str
    applies_to: Callable          # (config) -> bool
    apply: Callable               # (config) -> new config (deep-copied, immutable)


# ── Mutators ──────────────────────────────────────────────────────────────────

def _apply_threshold_too_low(config: GoldenMatchConfig) -> GoldenMatchConfig:
    """Lower primary weighted matchkey threshold by 0.15, floor 0.50."""
    new = copy.deepcopy(config)
    mk = _primary_weighted_mk(new)
    if mk is None:
        return new
    mk.threshold = max(0.50, mk.threshold - 0.15)
    return new


def _applies_threshold_too_low(config: GoldenMatchConfig) -> bool:
    try:
        return _primary_weighted_mk(config) is not None
    except AttributeError:
        return False


def _apply_threshold_too_high(config: GoldenMatchConfig) -> GoldenMatchConfig:
    """Raise primary weighted matchkey threshold by 0.10, ceiling 0.99."""
    new = copy.deepcopy(config)
    mk = _primary_weighted_mk(new)
    if mk is None:
        return new
    mk.threshold = min(0.99, mk.threshold + 0.10)
    return new


def _applies_threshold_too_high(config: GoldenMatchConfig) -> bool:
    try:
        return _primary_weighted_mk(config) is not None
    except AttributeError:
        return False


def _apply_bad_freetext_scorer(config: GoldenMatchConfig) -> GoldenMatchConfig:
    """Set the first free-text field's scorer to token_sort.

    token_sort is the *bad* choice here: it is word-order-robust (good for
    reordered tokens) but character-noise-fragile -- under typo/OCR corruption
    it collapses, so swapping a free-text field onto it degrades recall. The
    swap_scorer rule should detect this and swap back to a noise-tolerant
    scorer.
    """
    new = copy.deepcopy(config)
    mk = _primary_weighted_mk(new)
    if mk is None:
        return new
    f = _first_freetext_field(mk)
    if f is None:
        return new
    f.scorer = "token_sort"
    return new


def _applies_bad_freetext_scorer(config: GoldenMatchConfig) -> bool:
    try:
        mk = _primary_weighted_mk(config)
        if mk is None:
            return False
        return _first_freetext_field(mk) is not None
    except AttributeError:
        return False


def _apply_missing_negative_evidence(config: GoldenMatchConfig) -> GoldenMatchConfig:
    """Drop one entry from the primary weighted matchkey's negative_evidence."""
    new = copy.deepcopy(config)
    mk = _primary_weighted_mk(new)
    if mk is None or not mk.negative_evidence:
        return new
    # Drop the last entry (deterministic, reversible).
    mk.negative_evidence = mk.negative_evidence[:-1]
    return new


def _applies_missing_negative_evidence(config: GoldenMatchConfig) -> bool:
    try:
        mk = _primary_weighted_mk(config)
        if mk is None:
            return False
        return bool(mk.negative_evidence)
    except AttributeError:
        return False


def _apply_dropped_blocking_pass(config: GoldenMatchConfig) -> GoldenMatchConfig:
    """Drop one blocking pass, keeping at least one."""
    new = copy.deepcopy(config)
    if new.blocking is None or not new.blocking.passes or len(new.blocking.passes) < 2:
        return new
    # Drop the last pass; keep at least one.
    new.blocking.passes = new.blocking.passes[:-1]
    return new


def _applies_dropped_blocking_pass(config: GoldenMatchConfig) -> bool:
    # Pure None-checks / len comparisons on the typed `BlockingConfig | None`
    # field -- nothing to catch, and a bare except would hide a real
    # malformed-blocking AttributeError.
    return (
        config.blocking is not None
        and config.blocking.passes is not None
        and len(config.blocking.passes) > 1
    )


def _apply_flattened_weights(config: GoldenMatchConfig) -> GoldenMatchConfig:
    """Set every field weight in the primary weighted matchkey to 1.0."""
    new = copy.deepcopy(config)
    mk = _primary_weighted_mk(new)
    if mk is None:
        return new
    for f in mk.fields:
        f.weight = 1.0
    return new


def _applies_flattened_weights(config: GoldenMatchConfig) -> bool:
    try:
        mk = _primary_weighted_mk(config)
        if mk is None or len(mk.fields) < 2:
            return False
        weights = [f.weight for f in mk.fields if f.weight is not None]
        if not weights:
            return False
        # Only applies if weights are NOT already all equal.
        return len(set(weights)) > 1
    except AttributeError:
        return False


def _apply_skewed_weight(config: GoldenMatchConfig) -> GoldenMatchConfig:
    """Multiply the first field's weight by 5."""
    new = copy.deepcopy(config)
    mk = _primary_weighted_mk(new)
    if mk is None or len(mk.fields) < 2:
        return new
    w = mk.fields[0].weight
    mk.fields[0].weight = (w if w is not None else 1.0) * 5
    return new


def _applies_skewed_weight(config: GoldenMatchConfig) -> bool:
    try:
        mk = _primary_weighted_mk(config)
        return mk is not None and len(mk.fields) >= 2
    except AttributeError:
        return False


def _apply_naive_single_fuzzy(config: GoldenMatchConfig) -> GoldenMatchConfig:
    """Replace config with a minimal single weighted matchkey on the first field."""
    new = copy.deepcopy(config)
    mks = new.get_matchkeys()
    if not mks:
        return new

    # Pick the most-identifying string field: the first field of the first matchkey.
    source_mk = mks[0]
    if not source_mk.fields:
        return new
    first_field = source_mk.fields[0]
    field_name = first_field.field or "name"

    single_mk = MatchkeyConfig(
        name="naive_fuzzy",
        type="weighted",
        threshold=0.85,
        fields=[
            MatchkeyField(
                field=field_name,
                scorer="jaro_winkler",
                weight=1.0,
            )
        ],
    )

    # ALWAYS build a fresh single-field static blocking config. Copying the
    # original (possibly multi-key / multi-pass) blocking would reference
    # fields the naive single-field matchkey doesn't cover -> empty blocks ->
    # a spurious F1=0 in the gym rather than the honest naive-baseline signal.
    blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=[field_name])],
    )

    # Build a brand-new config with just the one matchkey.
    naive = GoldenMatchConfig(
        matchkeys=[single_mk],
        blocking=blocking,
    )
    return naive


def _applies_naive_single_fuzzy(config: GoldenMatchConfig) -> bool:
    try:
        mks = config.get_matchkeys()
        return bool(mks) and bool(mks[0].fields)
    except AttributeError:
        return False


# ── Catalog ───────────────────────────────────────────────────────────────────

CATALOG: list[Perturbation] = [
    Perturbation(
        name="threshold_too_low",
        expected_rule="raise_threshold",
        builds_on_existing_rule=True,
        description=(
            "Lower the primary weighted matchkey threshold by 0.15 "
            "(floor 0.50) -- simulates an over-permissive threshold that "
            "the raise_threshold rule should detect and correct."
        ),
        applies_to=_applies_threshold_too_low,
        apply=_apply_threshold_too_low,
    ),
    Perturbation(
        name="threshold_too_high",
        expected_rule="lower_threshold",
        builds_on_existing_rule=True,
        description=(
            "Raise the primary weighted matchkey threshold by 0.10 "
            "(ceiling 0.99) -- simulates an over-strict threshold that "
            "the lower_threshold rule should detect and correct."
        ),
        applies_to=_applies_threshold_too_high,
        apply=_apply_threshold_too_high,
    ),
    Perturbation(
        name="bad_freetext_scorer",
        expected_rule="swap_scorer",
        builds_on_existing_rule=True,
        description=(
            "Set the first free-text field's scorer to token_sort "
            "(a word-order-robust but character-noise-fragile scorer) -- "
            "simulates a sub-optimal scorer choice that swap_scorer should fix."
        ),
        applies_to=_applies_bad_freetext_scorer,
        apply=_apply_bad_freetext_scorer,
    ),
    Perturbation(
        name="missing_negative_evidence",
        expected_rule="add_negative_evidence",
        builds_on_existing_rule=True,
        description=(
            "Drop one NegativeEvidenceField from the primary weighted "
            "matchkey -- simulates a missing NE entry that add_negative_evidence "
            "should reinstate."
        ),
        applies_to=_applies_missing_negative_evidence,
        apply=_apply_missing_negative_evidence,
    ),
    Perturbation(
        name="dropped_blocking_pass",
        # Future rule placeholder -- intentionally NOT a current SuggestionKind.
        expected_rule="add_blocking_pass",
        builds_on_existing_rule=False,
        description=(
            "Drop one blocking pass from a multi-pass config, keeping at "
            "least one pass -- simulates a recall-reducing blocking change "
            "that a future add_blocking_pass rule would detect."
        ),
        applies_to=_applies_dropped_blocking_pass,
        apply=_apply_dropped_blocking_pass,
    ),
    Perturbation(
        name="flattened_weights",
        # Future rule placeholder -- intentionally NOT a current SuggestionKind.
        expected_rule="adjust_field_weight",
        builds_on_existing_rule=False,
        description=(
            "Set every field weight to 1.0 in the primary weighted matchkey "
            "-- simulates loss of learned weight diversity that a future "
            "adjust_field_weight rule would restore."
        ),
        applies_to=_applies_flattened_weights,
        apply=_apply_flattened_weights,
    ),
    Perturbation(
        name="skewed_weight",
        # Future rule placeholder -- intentionally NOT a current SuggestionKind.
        expected_rule="adjust_field_weight",
        builds_on_existing_rule=False,
        description=(
            "Multiply the first field's weight by 5 -- simulates an "
            "over-dominant field that a future adjust_field_weight rule would "
            "rebalance."
        ),
        applies_to=_applies_skewed_weight,
        apply=_apply_skewed_weight,
    ),
    Perturbation(
        name="naive_single_fuzzy",
        # No corresponding current rule (intentionally empty).
        expected_rule="",
        builds_on_existing_rule=False,
        description=(
            "Replace the entire config with a minimal single weighted "
            "matchkey on the most-identifying string field at the default "
            "0.85 threshold -- simulates a naive starting point with no "
            "multi-field scoring."
        ),
        applies_to=_applies_naive_single_fuzzy,
        apply=_apply_naive_single_fuzzy,
    ),
]

# ── Lookup ────────────────────────────────────────────────────────────────────

_BY_NAME: dict[str, Perturbation] = {p.name: p for p in CATALOG}


def get(name: str) -> Perturbation:
    """Return the Perturbation with the given name.

    Raises
    ------
    KeyError
        If no perturbation with that name exists in the catalog.
    """
    if name not in _BY_NAME:
        raise KeyError(f"Unknown perturbation {name!r}. Available: {sorted(_BY_NAME)}")
    return _BY_NAME[name]
