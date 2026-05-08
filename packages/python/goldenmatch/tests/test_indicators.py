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


# ---------------------------------------------------------------------------
# Task 2.2: estimate_sparse_match_signal
# ---------------------------------------------------------------------------

def test_estimate_sparse_match_signal_marks_sparse_when_few_hits():
    """When sample's exact-matchkey hits < 50, mark sparse."""
    from goldenmatch.core.indicators import estimate_sparse_match_signal
    df = pl.DataFrame({
        "id": [f"id_{i}" for i in range(1000)],   # all unique = no exact hits
        "email": [f"u{i}@x.com" for i in range(1000)],
    })
    sv = estimate_sparse_match_signal(df, exact_columns=["email"])
    assert sv.is_sparse is True
    assert sv.estimated_n_true_pairs < 50


def test_estimate_sparse_match_signal_not_sparse_when_many_hits():
    """When sample has plenty of exact-matchkey collisions, not sparse."""
    from goldenmatch.core.indicators import estimate_sparse_match_signal
    df = pl.DataFrame({
        # 200 records, 100 emails each appearing twice -> 100 exact-match pairs
        "email": [f"u{i % 100}@x.com" for i in range(200)],
    })
    sv = estimate_sparse_match_signal(df, exact_columns=["email"])
    assert sv.is_sparse is False
    assert sv.estimated_n_true_pairs >= 50


def test_estimate_sparse_match_signal_no_columns_marks_sparse():
    """No exact columns provided -> can't estimate; treat as sparse."""
    from goldenmatch.core.indicators import estimate_sparse_match_signal
    df = pl.DataFrame({"x": [1, 2, 3]})
    sv = estimate_sparse_match_signal(df, exact_columns=[])
    assert sv.is_sparse is True


# ---------------------------------------------------------------------------
# Task 2.3: compute_corruption_score (public alias)
# ---------------------------------------------------------------------------

def test_compute_corruption_score_brian_variants():
    """Brian/BRIAN/brian collapse to 1 normalized form -> corruption_score > 0.5."""
    from goldenmatch.core.indicators import compute_corruption_score
    df = pl.DataFrame({
        "name": ["Brian", "BRIAN", "brian", "Brian "] * 25,
    })
    score = compute_corruption_score(df, "name")
    assert score > 0.5


def test_compute_corruption_score_clean_email():
    """Distinct emails with no case noise -> corruption_score < 0.1."""
    from goldenmatch.core.indicators import compute_corruption_score
    df = pl.DataFrame({
        "email": [f"u{i}@x.com" for i in range(100)],
    })
    score = compute_corruption_score(df, "email")
    assert score < 0.1


def test_compute_corruption_score_missing_column_returns_zero():
    from goldenmatch.core.indicators import compute_corruption_score
    df = pl.DataFrame({"x": [1, 2, 3]})
    score = compute_corruption_score(df, "nonexistent")
    assert score == 0.0


# ---------------------------------------------------------------------------
# Task 2.4: estimate_full_pop_hits
# ---------------------------------------------------------------------------

def test_estimate_full_pop_hits_disjoint_zero():
    from goldenmatch.core.indicators import estimate_full_pop_hits
    df = pl.DataFrame({
        "email": [f"unique_{i}@x.com" for i in range(1000)],
    })
    hits = estimate_full_pop_hits(df, "email")
    assert hits == 0


def test_estimate_full_pop_hits_with_duplicates():
    from goldenmatch.core.indicators import estimate_full_pop_hits
    df = pl.DataFrame({
        "email": [f"u{i % 50}@x.com" for i in range(200)],   # 4x collision per email
    })
    hits = estimate_full_pop_hits(df, "email")
    # 50 emails x C(4,2) = 50 x 6 = 300 pairs
    assert hits >= 100


def test_estimate_full_pop_hits_budget_returns_none(monkeypatch):
    """Synthetic slow path -> returns None."""
    from goldenmatch.core import indicators
    monkeypatch.setattr(indicators, "BUDGET_FULL_POP_HITS", 0.0)
    df = pl.DataFrame({"email": ["a@x.com"] * 1000})
    hits = indicators.estimate_full_pop_hits(df, "email")
    assert hits is None


# ---------------------------------------------------------------------------
# Task 2.5: compute_cross_blocking_overlap
# ---------------------------------------------------------------------------

def test_cross_blocking_overlap_identical_keys_returns_one():
    """Same key on both sides -> overlap = 1.0 (degenerate guard)."""
    from goldenmatch.core.indicators import compute_cross_blocking_overlap
    df = pl.DataFrame({"city": ["nyc", "nyc", "la"] * 10})
    overlap = compute_cross_blocking_overlap(df, "city", "city")
    assert overlap == 1.0


def test_cross_blocking_overlap_orthogonal_keys_low():
    """Two unrelated keys with no record-level correlation -> low overlap."""
    from goldenmatch.core.indicators import compute_cross_blocking_overlap
    import random
    random.seed(42)
    n = 100
    df = pl.DataFrame({
        "city": [random.choice(["nyc", "la", "sf"]) for _ in range(n)],
        "category": [random.choice(["a", "b", "c", "d", "e"]) for _ in range(n)],
    })
    overlap = compute_cross_blocking_overlap(df, "city", "category")
    # Random correlation should be small but not zero
    assert overlap < 0.5


def test_cross_blocking_overlap_budget_returns_none(monkeypatch):
    from goldenmatch.core import indicators
    monkeypatch.setattr(indicators, "BUDGET_CROSS_BLOCKING", 0.0)
    df = pl.DataFrame({"city": ["nyc"] * 100, "state": ["NY"] * 100})
    result = indicators.compute_cross_blocking_overlap(df, "city", "state")
    assert result is None
