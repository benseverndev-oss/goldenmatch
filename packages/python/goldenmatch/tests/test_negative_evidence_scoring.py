"""v1.11: NegativeEvidenceField schema + scoring integration tests."""
import pytest


def test_negative_evidence_field_construct():
    from goldenmatch.config.schemas import NegativeEvidenceField
    ne = NegativeEvidenceField(
        field="phone", transforms=["digits_only"], scorer="exact",
        threshold=0.5, penalty=0.3,
    )
    assert ne.field == "phone"
    assert ne.transforms == ["digits_only"]
    assert ne.scorer == "exact"
    assert ne.threshold == 0.5
    assert ne.penalty == 0.3


def test_negative_evidence_field_default_transforms_empty():
    from goldenmatch.config.schemas import NegativeEvidenceField
    ne = NegativeEvidenceField(
        field="address", scorer="token_sort", threshold=0.4, penalty=0.4,
    )
    assert ne.transforms == []


def test_negative_evidence_field_validates_scorer():
    """scorer must be in VALID_SCORERS; 'digits_only_exact' is not registered."""
    import pydantic
    from goldenmatch.config.schemas import NegativeEvidenceField
    with pytest.raises(pydantic.ValidationError, match=r"scorer"):
        NegativeEvidenceField(
            field="phone", scorer="digits_only_exact",
            threshold=0.5, penalty=0.3,
        )


def test_negative_evidence_field_rejects_threshold_out_of_range():
    import pydantic
    from goldenmatch.config.schemas import NegativeEvidenceField
    with pytest.raises(pydantic.ValidationError, match=r"threshold"):
        NegativeEvidenceField(
            field="x", scorer="exact", threshold=1.5, penalty=0.3,
        )


def test_negative_evidence_field_rejects_penalty_out_of_range():
    import pydantic
    from goldenmatch.config.schemas import NegativeEvidenceField
    with pytest.raises(pydantic.ValidationError, match=r"penalty"):
        NegativeEvidenceField(
            field="x", scorer="exact", threshold=0.4, penalty=-0.1,
        )


def test_negative_evidence_field_validates_transforms():
    """transforms must be in VALID_SIMPLE_TRANSFORMS."""
    import pydantic
    from goldenmatch.config.schemas import NegativeEvidenceField
    with pytest.raises(pydantic.ValidationError):
        NegativeEvidenceField(
            field="x", transforms=["nonexistent_transform"],
            scorer="exact", threshold=0.4, penalty=0.3,
        )


def test_matchkey_config_negative_evidence_default_none():
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    mk = MatchkeyConfig(
        name="test",
        type="weighted",
        threshold=0.85,
        fields=[MatchkeyField(field="email", transforms=["lowercase"],
                              scorer="ensemble", weight=1.0)],
    )
    assert mk.negative_evidence is None


def test_matchkey_config_accepts_negative_evidence():
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    mk = MatchkeyConfig(
        name="test", type="weighted", threshold=0.85,
        fields=[MatchkeyField(field="email", transforms=["lowercase"],
                              scorer="ensemble", weight=1.0)],
        negative_evidence=[
            NegativeEvidenceField(field="phone", transforms=["digits_only"],
                                  scorer="exact", threshold=0.5, penalty=0.3),
        ],
    )
    assert mk.negative_evidence is not None
    assert len(mk.negative_evidence) == 1
    assert mk.negative_evidence[0].field == "phone"


def test_matchkey_config_v110_cache_compat_no_field():
    """v1.10-saved JSON without negative_evidence key deserializes cleanly."""
    from goldenmatch.config.schemas import MatchkeyConfig
    legacy_json = {
        "name": "primary", "type": "weighted", "threshold": 0.85,
        "fields": [{"field": "email", "transforms": ["lowercase"],
                    "scorer": "ensemble", "weight": 1.0}],
    }
    mk = MatchkeyConfig.model_validate(legacy_json)
    assert mk.negative_evidence is None


# ---------------------------------------------------------------------------
# Task 2.1: _apply_negative_evidence helper tests
# ---------------------------------------------------------------------------

def test_apply_negative_evidence_returns_zero_when_no_ne():
    """Empty/None negative_evidence → zero penalty."""
    from goldenmatch.core.scorer import _apply_negative_evidence
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    mk = MatchkeyConfig(
        name="t", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="email", transforms=[],
                              scorer="ensemble", weight=1.0)],
    )
    pair = {"email": ("a@x.com", "a@x.com"), "phone": ("555-1234", "555-9999")}
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.0


def test_apply_negative_evidence_subtracts_when_field_disagrees():
    """NE field disagrees (sim < threshold) → penalty applied."""
    from goldenmatch.core.scorer import _apply_negative_evidence
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    mk = MatchkeyConfig(
        name="t", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="email", transforms=[],
                              scorer="ensemble", weight=1.0)],
        negative_evidence=[
            NegativeEvidenceField(
                field="phone", transforms=["digits_only"],
                scorer="exact", threshold=0.5, penalty=0.3,
            ),
        ],
    )
    pair = {"email": ("a@x.com", "a@x.com"), "phone": ("555-1234", "555-9999")}
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.3   # phones disagree → full penalty


def test_apply_negative_evidence_no_subtract_when_field_agrees():
    from goldenmatch.core.scorer import _apply_negative_evidence
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    mk = MatchkeyConfig(
        name="t", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="email", transforms=[],
                              scorer="ensemble", weight=1.0)],
        negative_evidence=[
            NegativeEvidenceField(
                field="phone", transforms=["digits_only"],
                scorer="exact", threshold=0.5, penalty=0.3,
            ),
        ],
    )
    pair = {"email": ("a@x.com", "a@x.com"), "phone": ("555-1234", "5551234")}
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.0   # phones agree (after digits_only transform)


def test_apply_negative_evidence_skips_unregistered_scorer(caplog):
    """Defensive: unknown scorer → skip + WARNING."""
    import logging
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    from goldenmatch.core.scorer import _apply_negative_evidence
    mk = MatchkeyConfig(
        name="t", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="email", transforms=[],
                              scorer="ensemble", weight=1.0)],
    )
    bogus = NegativeEvidenceField.model_construct(
        field="phone", transforms=[],
        scorer="nonexistent_scorer", threshold=0.5, penalty=0.3,
    )
    mk.negative_evidence = [bogus]
    pair = {"email": ("a@x.com", "a@x.com"), "phone": ("555", "999")}
    with caplog.at_level(logging.WARNING):
        penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.0    # skipped, not crashed
    assert any("nonexistent_scorer" in r.message for r in caplog.records)


def test_apply_negative_evidence_skips_missing_field():
    """Field not in pair dict → skip (defensive)."""
    from goldenmatch.core.scorer import _apply_negative_evidence
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    mk = MatchkeyConfig(
        name="t", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="email", transforms=[],
                              scorer="ensemble", weight=1.0)],
        negative_evidence=[
            NegativeEvidenceField(
                field="missing_field", transforms=[],
                scorer="exact", threshold=0.5, penalty=0.3,
            ),
        ],
    )
    pair = {"email": ("a@x.com", "a@x.com")}    # no missing_field
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.0


# ---------------------------------------------------------------------------
# Task 2.2: E2E scoring loop integration test
# ---------------------------------------------------------------------------

def test_score_pair_with_negative_evidence_drops_below_threshold():
    """E2E: positive=1.0, NE penalty=0.4, threshold=0.8 → final=0.6 → no match."""
    import polars as pl
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        NegativeEvidenceField, BlockingConfig, BlockingKeyConfig,
    )
    from goldenmatch._api import dedupe_df

    df = pl.DataFrame({
        # 2 records: same name+email, different phones (collision pair)
        "first_name": ["Brian", "Brian"],
        "email": ["b@x.com", "b@x.com"],
        "phone": ["5551234", "5559999"],
    })
    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="primary", type="weighted", threshold=0.8,
            fields=[
                MatchkeyField(field="first_name", transforms=["lowercase"],
                              scorer="ensemble", weight=0.5),
                MatchkeyField(field="email", transforms=["lowercase"],
                              scorer="exact", weight=0.5),
            ],
            negative_evidence=[
                NegativeEvidenceField(
                    field="phone", transforms=["digits_only"],
                    scorer="exact", threshold=0.5, penalty=0.4,
                ),
            ],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase"])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    result = dedupe_df(df, config=config)
    # NE on phone disagrees → penalty 0.4 → final ≤ 1.0 - 0.4 = 0.6 < 0.8
    # → 0 matches, 2 distinct clusters
    assert len(result.clusters) == 2 or result.clusters == {}
