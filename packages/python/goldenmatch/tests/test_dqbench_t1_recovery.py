"""Synthetic T1-style regression test for v1.10 indicator stack.

Mimics DQbench T1's failure mode: corrupted-email duplicates the v1.9
controller misclassifies. v1.10 indicator stack correctly identifies email
as the high-identity column with corruption noise via column_priors.

This test guards:
1. column_priors are populated on the committed profile (Task 6.1)
2. email's identity_score is high (>= 0.8) and corruption_score > 0 (correct detection)
3. indicators profile is non-None on the committed entry
"""
import os
from pathlib import Path

import pytest


@pytest.fixture
def t1_synthetic_df():
    import polars as pl
    fixture = Path(__file__).parent / "fixtures" / "autoconfig" / "t1_synthetic.csv"
    if not fixture.exists():
        pytest.skip(f"missing fixture: {fixture}")
    return pl.read_csv(fixture)


def test_t1_synthetic_column_priors_detect_email_identity(t1_synthetic_df):
    """v1.10 indicator stack correctly profiles email as high-identity with
    corruption noise (case variants: user000@gmail.com vs USER000@gmail.com)."""
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
    from goldenmatch import dedupe_df
    dedupe_df(t1_synthetic_df)

    from goldenmatch.core.autoconfig_controller import _LAST_CONTROLLER_RUN
    history = _LAST_CONTROLLER_RUN.get()
    assert history is not None
    best = history.pick_committed(precision_collapse_floor=0.9)
    assert best is not None

    # column_priors must be populated (Task 6.1 eager compute)
    column_priors = best.profile.data.column_priors
    assert column_priors is not None, "column_priors not stamped on committed profile"
    assert "email" in column_priors, f"email not in column_priors keys: {list(column_priors.keys())}"

    email_prior = column_priors["email"]
    # Email is a canonical identity column — must have high identity_score
    assert email_prior.identity_score >= 0.8, (
        f"email identity_score={email_prior.identity_score:.2f} < 0.8 — "
        "indicator stack failed to recognize email as identity column"
    )
    # Dataset has corrupted emails (uppercase variants) — corruption_score must be > 0
    assert email_prior.corruption_score > 0.0, (
        f"email corruption_score={email_prior.corruption_score:.2f} — "
        "indicator stack missed case-corruption noise in email column"
    )

    # indicators profile is non-None (Task 6.1 stamping)
    assert best.profile.indicators is not None, (
        "IndicatorsProfile not stamped on committed profile"
    )


def test_t1_synthetic_city_has_low_identity_score(t1_synthetic_df):
    """city is a geo field — identity_score should be low (not suitable for
    entity-resolution blocking)."""
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
    from goldenmatch import dedupe_df
    dedupe_df(t1_synthetic_df)

    from goldenmatch.core.autoconfig_controller import _LAST_CONTROLLER_RUN
    history = _LAST_CONTROLLER_RUN.get()
    assert history is not None
    best = history.pick_committed(precision_collapse_floor=0.9)
    assert best is not None

    column_priors = best.profile.data.column_priors
    assert column_priors is not None
    if "city" in column_priors:
        city_prior = column_priors["city"]
        assert city_prior.identity_score < 0.5, (
            f"city identity_score={city_prior.identity_score:.2f} >= 0.5 — "
            "geo/low-cardinality field incorrectly scored as identity column"
        )
