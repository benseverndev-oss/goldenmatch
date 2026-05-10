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
    """T3-shaped df: phone gets promoted to NE on the weighted matchkey.

    Phase 7 fix: NE is only promoted for columns that have an exact matchkey
    counterpart. Phone has identity_score=0.85 + an exact_phone matchkey →
    qualifies. Address has identity_score=0.7 (below 0.75 threshold) + no
    exact matchkey → does NOT qualify. Email is in blocking → skipped.
    """
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
        matchkeys=[
            # exact_phone matchkey: phone qualifies for NE on weighted matchkey
            MatchkeyConfig(
                name="exact_phone", type="exact", threshold=None,
                fields=[MatchkeyField(field="phone", transforms=["strip"],
                                      scorer=None, weight=None)],
            ),
            MatchkeyConfig(
                name="primary", type="weighted", threshold=0.8,
                fields=[MatchkeyField(field="first_name", transforms=["lowercase"],
                                      scorer="ensemble", weight=1.0)],
            ),
        ],
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
    # weighted matchkey is index 1 after the exact_phone matchkey
    weighted_mk = next(mk for mk in new_config.matchkeys if mk.type == "weighted")
    ne = weighted_mk.negative_evidence
    assert ne is not None
    ne_fields = {n.field for n in ne}
    # phone: identity_score=0.85 >= 0.75 + has exact_phone matchkey → promoted
    assert "phone" in ne_fields
    # address: identity_score=0.7 < 0.75 threshold → NOT promoted
    assert "address" not in ne_fields
    # email is in blocking → skipped
    assert "email" not in ne_fields
    # first_name is in weighted matchkey.fields → skipped
    assert "first_name" not in ne_fields


def test_promote_negative_evidence_idempotent():
    """Calling twice doesn't double-add.

    Config includes exact_phone matchkey so phone qualifies for NE promotion.
    """
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
        matchkeys=[
            # exact_phone: phone qualifies for NE on the weighted matchkey
            MatchkeyConfig(
                name="exact_phone", type="exact", threshold=None,
                fields=[MatchkeyField(field="phone", transforms=[], scorer=None, weight=None)],
            ),
            MatchkeyConfig(
                name="primary", type="weighted", threshold=0.8,
                fields=[MatchkeyField(field="first_name", transforms=[],
                                      scorer="ensemble", weight=1.0)],
            ),
        ],
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
    weighted_once = next(mk for mk in once.matchkeys if mk.type == "weighted")
    weighted_twice = next(mk for mk in twice.matchkeys if mk.type == "weighted")
    # NE list should be identical length on both (idempotency)
    assert len(weighted_once.negative_evidence) == len(weighted_twice.negative_evidence)


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
    """auto_configure_df produces a config with NE on weighted matchkeys when
    an exact matchkey exists for a qualifying identity column.

    Phase 7 fix: NE is only promoted for columns that have an exact matchkey
    counterpart. This test uses a realistic person shape where email cardinality
    is 0.5-0.95 so it stays classified as 'email' (not 'identifier') and an
    exact_email matchkey is created. Phone is then eligible for NE promotion
    on the weighted matchkey (phone cardinality < 0.95 → type=phone → exact_phone).
    """
    import os
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
    from goldenmatch.core.autoconfig import auto_configure_df

    # Use a realistic person dataset where email/phone cardinality < 0.95
    # (some repeats so the profiler keeps col_type=email/phone, not identifier)
    import random
    rng = random.Random(42)
    n = 100
    emails = [f"user{i % 70}@example.com" for i in range(n)]  # cardinality ~0.70
    phones = [f"555-{1000 + (i % 80):04d}" for i in range(n)]  # cardinality ~0.80
    df = pl.DataFrame({
        "first_name": [rng.choice(["Alice", "Bob", "Carol", "Dave"]) for _ in range(n)],
        "last_name": [rng.choice(["Smith", "Jones", "Brown"]) for _ in range(n)],
        "email": emails,
        "phone": phones,
        "address": [f"{i} Oak St" for i in range(n)],
    })
    config = auto_configure_df(df)
    # Check if any exact matchkeys were created (needed for NE gate)
    exact_mks = [mk for mk in config.matchkeys if mk.type == "exact"]
    weighted_mks = [m for m in config.matchkeys if m.type == "weighted"]
    if exact_mks and weighted_mks:
        # With exact matchkeys present, at least one weighted matchkey should have NE
        any_with_ne = any(mk.negative_evidence for mk in weighted_mks)
        assert any_with_ne, (
            f"expected promote_negative_evidence to add NE fields when exact matchkeys "
            f"exist; exact_mks={[mk.name for mk in exact_mks]}, "
            f"weighted_mks={[mk.name for mk in weighted_mks]}"
        )
    else:
        # If auto-config didn't create exact matchkeys (e.g. all-weighted config),
        # NE should be absent — the gate correctly prevents recall regression.
        for mk in weighted_mks:
            assert not mk.negative_evidence, (
                f"NE should only be promoted when an exact matchkey counterpart exists; "
                f"got NE on {mk.name} with no exact matchkeys"
            )
