"""v1.11: tests for promote_negative_evidence + _pick_scorer_for_column."""
import polars as pl
import pytest


def test_pick_scorer_for_column_email():
    from goldenmatch.core.autoconfig_negative_evidence import _pick_scorer_for_column
    transforms, scorer = _pick_scorer_for_column("email", "email")
    assert transforms == []
    assert scorer == "token_sort"


def test_pick_scorer_for_column_phone():
    from goldenmatch.core.autoconfig_negative_evidence import _pick_scorer_for_column
    transforms, scorer = _pick_scorer_for_column("phone", "phone")
    assert transforms == ["digits_only"]
    assert scorer == "exact"


def test_pick_scorer_for_column_address():
    from goldenmatch.core.autoconfig_negative_evidence import _pick_scorer_for_column
    transforms, scorer = _pick_scorer_for_column("home_address", "address")
    assert transforms == []
    assert scorer == "token_sort"


def test_pick_scorer_for_column_unknown_falls_back_to_ensemble():
    from goldenmatch.core.autoconfig_negative_evidence import _pick_scorer_for_column
    transforms, scorer = _pick_scorer_for_column("custom_field", "text")
    assert transforms == []
    assert scorer == "ensemble"


def test_pick_scorer_validates_against_VALID_SCORERS():
    """Returned scorer is always in VALID_SCORERS."""
    from goldenmatch.config.schemas import VALID_SCORERS
    from goldenmatch.core.autoconfig_negative_evidence import _pick_scorer_for_column
    for col_type in ["email", "phone", "address", "text", "id-like", "date"]:
        _, scorer = _pick_scorer_for_column("any", col_type)
        assert scorer in VALID_SCORERS


def test_promote_negative_evidence_t3_pattern():
    """T3-shaped df: phone+address get promoted to NE on the weighted matchkey."""
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    from goldenmatch.core.complexity_profile import ColumnPrior
    from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence

    df = pl.DataFrame({
        "first_name": ["Brian"] * 10,
        "email": [f"u{i}@x.com" for i in range(10)],
        "phone": [f"555-{1000+i}" for i in range(10)],
        "address": [f"{i} Main St" for i in range(10)],
    })
    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="primary", type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="first_name", transforms=["lowercase"],
                                  scorer="ensemble", weight=1.0)],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase"])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    column_priors = {
        "first_name": ColumnPrior(identity_score=0.3, corruption_score=0.0),
        "email": ColumnPrior(identity_score=0.95, corruption_score=0.0),
        "phone": ColumnPrior(identity_score=0.85, corruption_score=0.0),
        "address": ColumnPrior(identity_score=0.7, corruption_score=0.0),
    }
    new_config = promote_negative_evidence(config, df, column_priors)
    ne = new_config.matchkeys[0].negative_evidence
    assert ne is not None
    ne_fields = {n.field for n in ne}
    # phone, address get promoted (identity_score >= 0.7 + cardinality_ratio >= 0.5)
    assert "phone" in ne_fields
    assert "address" in ne_fields
    # email is in blocking → skipped
    # first_name is in matchkey.fields → skipped
    # first_name has identity_score 0.3 → wouldn't qualify anyway
    assert "first_name" not in ne_fields
    assert "email" not in ne_fields


def test_promote_negative_evidence_idempotent():
    """Calling twice doesn't double-add."""
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    from goldenmatch.core.complexity_profile import ColumnPrior
    from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence

    df = pl.DataFrame({
        "first_name": ["x"] * 10, "phone": [f"5551{i:03d}" for i in range(10)],
    })
    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="primary", type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="first_name", transforms=[],
                                  scorer="ensemble", weight=1.0)],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["first_name"], transforms=[])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    priors = {
        "first_name": ColumnPrior(0.3, 0.0),
        "phone": ColumnPrior(0.85, 0.0),
    }
    once = promote_negative_evidence(config, df, priors)
    twice = promote_negative_evidence(once, df, priors)
    # NE list should be identical length on both
    assert len(once.matchkeys[0].negative_evidence) == len(twice.matchkeys[0].negative_evidence)


def test_promote_negative_evidence_skips_blocking_columns():
    """A column used in blocking should not be promoted as NE."""
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    from goldenmatch.core.complexity_profile import ColumnPrior
    from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence

    df = pl.DataFrame({
        "name": ["x"] * 10, "phone": [f"5551{i:03d}" for i in range(10)],
    })
    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="primary", type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="name", transforms=[],
                                  scorer="ensemble", weight=1.0)],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["phone"], transforms=[])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    priors = {
        "name": ColumnPrior(0.3, 0.0),
        "phone": ColumnPrior(0.85, 0.0),
    }
    new_config = promote_negative_evidence(config, df, priors)
    ne = new_config.matchkeys[0].negative_evidence
    # phone is in blocking → not promoted
    assert ne is None or all(n.field != "phone" for n in ne)


def test_promote_negative_evidence_empty_df_returns_unchanged():
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence
    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="t", type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="x", transforms=[],
                                  scorer="ensemble", weight=1.0)],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["x"], transforms=[])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    new_config = promote_negative_evidence(config, pl.DataFrame(), {})
    assert new_config == config


def test_auto_configure_df_calls_promote_negative_evidence():
    """auto_configure_df produces a config with NE populated on T3-pattern data."""
    import os
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
    import polars as pl
    from goldenmatch.core.autoconfig import auto_configure_df

    df = pl.DataFrame({
        "first_name": ["Brian"] * 50,
        "email": [f"u{i}@x.com" for i in range(50)],
        "phone": [f"555-{1000+i}" for i in range(50)],
        "address": [f"{i} Main St" for i in range(50)],
    })
    config = auto_configure_df(df)
    weighted_mks = [m for m in config.matchkeys if m.type == "weighted"]
    if weighted_mks:
        # At least one weighted matchkey should have NE on identity columns
        any_with_ne = any(mk.negative_evidence for mk in weighted_mks)
        assert any_with_ne, "expected promote_negative_evidence to add NE fields"
