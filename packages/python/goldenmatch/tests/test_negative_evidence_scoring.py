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
