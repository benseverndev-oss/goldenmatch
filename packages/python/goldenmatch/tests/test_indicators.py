"""Unit tests for goldenmatch.core.indicators (v1.10)."""
import polars as pl
import pytest


def test_compute_column_priors_email_high_identity():
    """Email columns get identity_score >= 0.9 even on noisy samples."""
    from goldenmatch.core.indicators import compute_column_priors
    df = pl.DataFrame({
        "email": ["a@gmail.com", "b@yahoo.com", "c@hotmail.com"] * 30,
        "name": ["Brian", "Brian", "BRIAN"] * 30,
    })
    priors = compute_column_priors(df)
    assert priors["email"].identity_score >= 0.9


def test_compute_column_priors_categorical_low_identity():
    """Categorical/short columns get identity_score 0.0."""
    from goldenmatch.core.indicators import compute_column_priors
    df = pl.DataFrame({
        "status": ["active", "inactive"] * 50,
        "is_member": [True, False] * 50,
    })
    priors = compute_column_priors(df)
    assert priors["status"].identity_score == 0.0
    assert priors["is_member"].identity_score == 0.0


def test_compute_column_priors_corruption_score_high_on_case_noise():
    """A column with mixed-case variants of identical strings gets
    corruption_score > 0.4."""
    from goldenmatch.core.indicators import compute_column_priors
    df = pl.DataFrame({
        "name": ["Brian", "BRIAN", "brian", "Brian "] * 25,
    })
    priors = compute_column_priors(df)
    assert priors["name"].corruption_score > 0.4


def test_compute_column_priors_corruption_score_low_on_clean():
    """A clean column with no within-row variation has low corruption_score."""
    from goldenmatch.core.indicators import compute_column_priors
    df = pl.DataFrame({
        "email": [f"user{i}@example.com" for i in range(100)],
    })
    priors = compute_column_priors(df)
    assert priors["email"].corruption_score < 0.2


def test_compute_column_priors_missing_column_returns_empty_dict():
    """Empty df -> empty priors dict (no raise)."""
    from goldenmatch.core.indicators import compute_column_priors
    df = pl.DataFrame()
    priors = compute_column_priors(df)
    assert priors == {}
