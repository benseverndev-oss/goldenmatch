"""Tests for apply_suggestion — the config patcher.

TDD: tests written first; run against the apply.py implementation.

Coverage:
- set_threshold: updates the named matchkey's threshold; original is unchanged
- set_scorer: updates the named field's scorer within a matchkey; original unchanged
- add_negative_evidence: appends a NegativeEvidenceField; original unchanged
- unknown op: raises ValueError
"""
from __future__ import annotations

import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    NegativeEvidenceField,
)
from goldenmatch.core.suggest.apply import apply_suggestion
from goldenmatch.core.suggest.types import Suggestion

# ── helpers ────────────────────────────────────────────────────────────────────

def _mk_suggestion(patch: dict) -> Suggestion:
    """Build a minimal Suggestion with placeholder values for all non-patch fields."""
    return Suggestion(
        id="test-suggestion",
        kind="test",
        target="matchkeys[0]",
        current_value=None,
        proposed_value=None,
        rationale="test",
        predicted_effect="test",
        confidence=0.9,
        patch=patch,
    )


def _build_weighted_config(
    mk_name: str = "person",
    threshold: float = 0.80,
    field_name: str = "name",
    scorer: str = "jaro_winkler",
    addr_scorer: str = "token_sort",
) -> GoldenMatchConfig:
    """Minimal weighted GoldenMatchConfig for patching tests."""
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name=mk_name,
                type="weighted",
                threshold=threshold,
                fields=[
                    MatchkeyField(field=field_name, scorer=scorer, weight=0.7),
                    MatchkeyField(field="addr", scorer=addr_scorer, weight=0.3),
                ],
            )
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=[field_name])],
        ),
    )


# ── set_threshold ──────────────────────────────────────────────────────────────

def test_apply_set_threshold_updates_named_matchkey():
    config = _build_weighted_config(threshold=0.80)
    suggestion = _mk_suggestion(
        {"op": "set_threshold", "matchkey": "person", "value": 0.88}
    )

    result = apply_suggestion(config, suggestion)

    mk = next(m for m in result.get_matchkeys() if m.name == "person")
    assert mk.threshold == pytest.approx(0.88)


def test_apply_set_threshold_leaves_original_unchanged():
    config = _build_weighted_config(threshold=0.80)
    suggestion = _mk_suggestion(
        {"op": "set_threshold", "matchkey": "person", "value": 0.88}
    )

    apply_suggestion(config, suggestion)

    # Original must be untouched.
    orig_mk = next(m for m in config.get_matchkeys() if m.name == "person")
    assert orig_mk.threshold == pytest.approx(0.80)


def test_apply_set_threshold_unknown_matchkey_raises():
    config = _build_weighted_config()
    suggestion = _mk_suggestion(
        {"op": "set_threshold", "matchkey": "nonexistent", "value": 0.88}
    )

    with pytest.raises(ValueError, match="matchkey"):
        apply_suggestion(config, suggestion)


# ── set_scorer ─────────────────────────────────────────────────────────────────

def test_apply_set_scorer_updates_named_field():
    config = _build_weighted_config(addr_scorer="token_sort")
    suggestion = _mk_suggestion(
        {"op": "set_scorer", "matchkey": "person", "field": "addr", "scorer": "jaro_winkler"}
    )

    result = apply_suggestion(config, suggestion)

    mk = next(m for m in result.get_matchkeys() if m.name == "person")
    addr_field = next(f for f in mk.fields if f.field == "addr")
    assert addr_field.scorer == "jaro_winkler"


def test_apply_set_scorer_leaves_original_unchanged():
    config = _build_weighted_config(addr_scorer="token_sort")
    suggestion = _mk_suggestion(
        {"op": "set_scorer", "matchkey": "person", "field": "addr", "scorer": "jaro_winkler"}
    )

    apply_suggestion(config, suggestion)

    orig_mk = next(m for m in config.get_matchkeys() if m.name == "person")
    orig_addr = next(f for f in orig_mk.fields if f.field == "addr")
    assert orig_addr.scorer == "token_sort"


def test_apply_set_scorer_unknown_matchkey_raises():
    config = _build_weighted_config()
    suggestion = _mk_suggestion(
        {"op": "set_scorer", "matchkey": "ghost", "field": "addr", "scorer": "jaro_winkler"}
    )

    with pytest.raises(ValueError, match="matchkey"):
        apply_suggestion(config, suggestion)


def test_apply_set_scorer_unknown_field_raises():
    config = _build_weighted_config()
    suggestion = _mk_suggestion(
        {"op": "set_scorer", "matchkey": "person", "field": "ghost_field", "scorer": "jaro_winkler"}
    )

    with pytest.raises(ValueError, match="field"):
        apply_suggestion(config, suggestion)


# ── add_negative_evidence ──────────────────────────────────────────────────────

def test_apply_add_negative_evidence_adds_field():
    config = _build_weighted_config()
    suggestion = _mk_suggestion(
        {"op": "add_negative_evidence", "field": "email"}
    )

    result = apply_suggestion(config, suggestion)

    mk = result.get_matchkeys()[0]
    assert mk.negative_evidence is not None
    ne_fields = [ne.field for ne in mk.negative_evidence]
    assert "email" in ne_fields


def test_apply_add_negative_evidence_leaves_original_unchanged():
    config = _build_weighted_config()
    # Original has no NE.
    assert config.get_matchkeys()[0].negative_evidence is None

    suggestion = _mk_suggestion(
        {"op": "add_negative_evidence", "field": "email"}
    )

    apply_suggestion(config, suggestion)

    # Original must still have no NE.
    assert config.get_matchkeys()[0].negative_evidence is None


def test_apply_add_negative_evidence_idempotent():
    """Applying NE for a field already in the list doesn't duplicate it."""
    existing_ne = NegativeEvidenceField(
        field="email", transforms=[], scorer="token_sort", threshold=0.4, penalty=0.3
    )
    config = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="person",
                type="weighted",
                threshold=0.80,
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
                negative_evidence=[existing_ne],
            )
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["name"])],
        ),
    )
    suggestion = _mk_suggestion(
        {"op": "add_negative_evidence", "field": "email"}
    )

    result = apply_suggestion(config, suggestion)

    mk = result.get_matchkeys()[0]
    assert mk.negative_evidence is not None
    count = sum(1 for ne in mk.negative_evidence if ne.field == "email")
    assert count == 1


def test_apply_add_negative_evidence_ne_fields_are_ne_objects():
    """The NE list must contain NegativeEvidenceField objects, not plain strings."""
    config = _build_weighted_config()
    suggestion = _mk_suggestion(
        {"op": "add_negative_evidence", "field": "phone"}
    )

    result = apply_suggestion(config, suggestion)

    mk = result.get_matchkeys()[0]
    assert mk.negative_evidence is not None
    for ne in mk.negative_evidence:
        assert isinstance(ne, NegativeEvidenceField)


# ── unknown op ────────────────────────────────────────────────────────────────

def test_apply_unknown_op_raises():
    config = _build_weighted_config()
    suggestion = _mk_suggestion({"op": "bogus"})

    with pytest.raises(ValueError, match="bogus"):
        apply_suggestion(config, suggestion)
