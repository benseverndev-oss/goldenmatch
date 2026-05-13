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
        MatchkeyConfig,
        MatchkeyField,
        NegativeEvidenceField,
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
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    from goldenmatch.core.scorer import _apply_negative_evidence
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
    from goldenmatch.config.schemas import (
        MatchkeyConfig,
        MatchkeyField,
        NegativeEvidenceField,
    )
    from goldenmatch.core.scorer import _apply_negative_evidence
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
    from goldenmatch.config.schemas import (
        MatchkeyConfig,
        MatchkeyField,
        NegativeEvidenceField,
    )
    from goldenmatch.core.scorer import _apply_negative_evidence
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
        MatchkeyConfig,
        MatchkeyField,
        NegativeEvidenceField,
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
    from goldenmatch.config.schemas import (
        MatchkeyConfig,
        MatchkeyField,
        NegativeEvidenceField,
    )
    from goldenmatch.core.scorer import _apply_negative_evidence
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
    from goldenmatch._api import dedupe_df
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
        NegativeEvidenceField,
    )

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


# ============================================================
# v1.12 Path Y: NE on exact matchkeys
# ============================================================

def _build_exact_matchkey_with_ne(threshold=None, ne_fields=None):
    """Helper: build an exact matchkey with optional NE + threshold."""
    from goldenmatch.config.schemas import (
        MatchkeyConfig,
        MatchkeyField,
    )
    return MatchkeyConfig(
        name="exact_email", type="exact", threshold=threshold,
        fields=[MatchkeyField(field="email", transforms=["lowercase"],
                              scorer="exact", weight=1.0)],
        negative_evidence=ne_fields,
    )


def test_exact_matchkey_with_ne_filters_disagreeing_pair():
    """T3 case: same email, divergent phone+address → final 0.3 < 0.5 → no match."""
    from goldenmatch.config.schemas import NegativeEvidenceField
    from goldenmatch.core.scorer import _apply_negative_evidence
    mk = _build_exact_matchkey_with_ne(
        threshold=0.5,
        ne_fields=[
            NegativeEvidenceField(field="phone", transforms=["digits_only"],
                                  scorer="exact", threshold=0.4, penalty=0.3),
            NegativeEvidenceField(field="address", transforms=[],
                                  scorer="token_sort", threshold=0.4, penalty=0.4),
        ],
    )
    pair = {
        "email": ("a@x.com", "a@x.com"),    # match
        "phone": ("555-1234", "555-9999"),    # disagree
        "address": ("123 Main", "456 Oak"),    # disagree
    }
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.7    # 0.3 + 0.4
    final = max(0.0, 1.0 - penalty)
    assert final < 0.5    # below threshold; would not emit


def test_exact_matchkey_with_ne_keeps_agreeing_pair():
    """True duplicate: NE fields agree → no penalty → final 1.0 → match."""
    from goldenmatch.config.schemas import NegativeEvidenceField
    from goldenmatch.core.scorer import _apply_negative_evidence
    mk = _build_exact_matchkey_with_ne(
        threshold=0.5,
        ne_fields=[
            NegativeEvidenceField(field="phone", transforms=["digits_only"],
                                  scorer="exact", threshold=0.4, penalty=0.3),
        ],
    )
    pair = {"email": ("a@x.com", "a@x.com"), "phone": ("5551234", "555-1234")}
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.0    # phones agree after digits_only transform
    assert max(0.0, 1.0 - penalty) == 1.0


def test_exact_matchkey_without_ne_preserves_binary_behavior():
    """Backward compat: exact matchkey with NE=None → today's binary 1.0 emit."""
    from goldenmatch.core.scorer import _apply_negative_evidence
    mk = _build_exact_matchkey_with_ne()    # threshold=None, ne_fields=None
    penalty = _apply_negative_evidence(mk, {"email": ("a@x.com", "a@x.com")})
    assert penalty == 0.0    # NE=None → returns 0


def test_exact_matchkey_minor_address_noise_preserves_match():
    """Single-field disagreement at penalty 0.4 → final 0.6 ≥ 0.5 → match."""
    from goldenmatch.config.schemas import NegativeEvidenceField
    from goldenmatch.core.scorer import _apply_negative_evidence
    mk = _build_exact_matchkey_with_ne(
        threshold=0.5,
        ne_fields=[
            NegativeEvidenceField(field="address", transforms=[],
                                  scorer="token_sort", threshold=0.4, penalty=0.4),
        ],
    )
    # address sim ~0.30 (severely different), penalty 0.4 fires
    pair = {"email": ("a@x.com", "a@x.com"),
            "address": ("123 Main St", "456 Oak Avenue")}
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.4
    final = max(0.0, 1.0 - penalty)
    assert final == 0.6    # at threshold 0.5 → still emitted


def test_exact_matchkey_two_ne_fields_at_03_each_filters():
    """Cumulative penalty 0.6 > 0.5 → final 0.4 → filtered."""
    from goldenmatch.config.schemas import NegativeEvidenceField
    from goldenmatch.core.scorer import _apply_negative_evidence
    mk = _build_exact_matchkey_with_ne(
        threshold=0.5,
        ne_fields=[
            NegativeEvidenceField(field="phone", transforms=["digits_only"],
                                  scorer="exact", threshold=0.4, penalty=0.3),
            NegativeEvidenceField(field="zip", transforms=[],
                                  scorer="exact", threshold=0.4, penalty=0.3),
        ],
    )
    pair = {"email": ("a@x.com", "a@x.com"),
            "phone": ("555-1234", "555-9999"), "zip": ("90210", "10001")}
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.6
    assert max(0.0, 1.0 - penalty) == 0.4    # below 0.5


def test_exact_matchkey_penalty_exceeds_one_clamps_to_zero():
    """Defensive: penalty sum > 1.0 → final clamped to 0.0."""
    from goldenmatch.config.schemas import NegativeEvidenceField
    from goldenmatch.core.scorer import _apply_negative_evidence
    mk = _build_exact_matchkey_with_ne(
        threshold=0.5,
        ne_fields=[
            NegativeEvidenceField(field="phone", transforms=[],
                                  scorer="exact", threshold=0.4, penalty=0.6),
            NegativeEvidenceField(field="address", transforms=[],
                                  scorer="exact", threshold=0.4, penalty=0.6),
        ],
    )
    pair = {"email": ("a@x.com", "a@x.com"),
            "phone": ("a", "b"), "address": ("c", "d")}
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 1.2
    assert max(0.0, 1.0 - penalty) == 0.0    # clamp to floor


def test_find_exact_matches_emits_when_ne_score_above_threshold():
    """E2E: pair with agreeing NE emits as match."""
    import polars as pl
    from goldenmatch.config.schemas import (
        MatchkeyConfig,
        MatchkeyField,
        NegativeEvidenceField,
    )
    from goldenmatch.core.scorer import (
        _apply_negative_evidence_to_exact_pairs,
        find_exact_matches,
    )
    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "email": ["a@x.com", "a@x.com"],
        "phone": ["5551234", "555-1234"],
    })
    # Pre-compute the matchkey column (find_exact_matches expects __mk_<name>__)
    df = df.with_columns(
        pl.col("email").str.to_lowercase().alias("__mk_exact_email__"),
    )
    mk = MatchkeyConfig(
        name="exact_email", type="exact", threshold=0.5,
        fields=[MatchkeyField(field="email", transforms=["lowercase"],
                              scorer="exact", weight=1.0)],
        negative_evidence=[
            NegativeEvidenceField(field="phone", transforms=["digits_only"],
                                  scorer="exact", threshold=0.4, penalty=0.3),
        ],
    )
    pairs = find_exact_matches(df.lazy(), mk)
    # Apply NE post-filter
    filtered = _apply_negative_evidence_to_exact_pairs(pairs, mk, df)
    assert any(score >= 0.5 for *_, score in filtered)


def test_find_exact_matches_filters_when_ne_score_below_threshold():
    """E2E: pair with disagreeing NE drops below threshold → not emitted."""
    import polars as pl
    from goldenmatch.config.schemas import (
        MatchkeyConfig,
        MatchkeyField,
        NegativeEvidenceField,
    )
    from goldenmatch.core.scorer import (
        _apply_negative_evidence_to_exact_pairs,
        find_exact_matches,
    )
    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "email": ["a@x.com", "a@x.com"],
        "phone": ["5551234", "5559999"],
        "address": ["123 Main", "999 Oak"],
    })
    df = df.with_columns(
        pl.col("email").str.to_lowercase().alias("__mk_exact_email__"),
    )
    mk = MatchkeyConfig(
        name="exact_email", type="exact", threshold=0.5,
        fields=[MatchkeyField(field="email", transforms=["lowercase"],
                              scorer="exact", weight=1.0)],
        negative_evidence=[
            NegativeEvidenceField(field="phone", transforms=["digits_only"],
                                  scorer="exact", threshold=0.4, penalty=0.3),
            NegativeEvidenceField(field="address", transforms=[],
                                  scorer="token_sort", threshold=0.4, penalty=0.4),
        ],
    )
    pairs = find_exact_matches(df.lazy(), mk)
    filtered = _apply_negative_evidence_to_exact_pairs(pairs, mk, df)
    assert len(filtered) == 0    # below threshold
