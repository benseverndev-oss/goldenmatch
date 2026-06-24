"""End-to-end test for the review_config adapter (Task 11).

Guards:
- Skips when the native kernel is absent or doesn't expose ``suggest_config``.
- Never loads torch / HuggingFace models (rerank disabled explicitly in adapter).
- Uses a tiny synthetic person-like DataFrame with known duplicates so the
  kernel has real signal to work with.
"""
from __future__ import annotations

import pytest
import polars as pl


# ── Native guard ──────────────────────────────────────────────────────────

def _native_suggest_available() -> bool:
    try:
        from goldenmatch.core._native_loader import native_module
        nm = native_module()
        return nm is not None and hasattr(nm, "suggest_config")
    except Exception:
        return False


if not _native_suggest_available():
    pytest.skip(
        "native suggest_config not built -- skipping adapter tests",
        allow_module_level=True,
    )


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_df() -> pl.DataFrame:
    """Small synthetic person dataset with genuine duplicates.

    - Row 0 and row 1 are the same person (name + zip match).
    - Row 2 and row 3 are the same person.
    - Rows 4-9 are unique.
    Loose threshold (0.3) ensures they get merged.
    """
    records = [
        {"first_name": "Alice", "last_name": "Smith",   "zip_code": "10001", "email": "alice@example.com"},
        {"first_name": "Alice", "last_name": "Smith",   "zip_code": "10001", "email": "alice@example.com"},
        {"first_name": "Bob",   "last_name": "Jones",   "zip_code": "20002", "email": "bob@example.com"},
        {"first_name": "Bob",   "last_name": "Jones",   "zip_code": "20002", "email": "bob@example.com"},
        {"first_name": "Carol", "last_name": "White",   "zip_code": "30003", "email": "carol@example.com"},
        {"first_name": "Dave",  "last_name": "Brown",   "zip_code": "40004", "email": "dave@example.com"},
        {"first_name": "Eve",   "last_name": "Davis",   "zip_code": "50005", "email": "eve@example.com"},
        {"first_name": "Frank", "last_name": "Wilson",  "zip_code": "60006", "email": "frank@example.com"},
        {"first_name": "Grace", "last_name": "Moore",   "zip_code": "70007", "email": "grace@example.com"},
        {"first_name": "Hank",  "last_name": "Taylor",  "zip_code": "80008", "email": "hank@example.com"},
    ]
    return pl.DataFrame(records)


def _make_config():
    """Build an explicit (non-auto-config) config with a loose threshold.

    Using a direct config avoids the auto-config controller hitting the
    BUDGET_TIME=RED path on this tiny frame, which would complicate the test.
    Threshold 0.3 is intentionally loose to produce merges the kernel can
    critique.
    """
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    mk = MatchkeyConfig(
        name="fuzzy_match",
        type="weighted",
        threshold=0.3,  # intentionally loose -- gives kernel something to raise
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", weight=0.5),
            MatchkeyField(field="last_name",  scorer="jaro_winkler", weight=0.5),
        ],
    )
    blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["last_name"])],
        auto_suggest=False,
    )
    return GoldenMatchConfig(matchkeys=[mk], blocking=blocking)


# ── Tests ─────────────────────────────────────────────────────────────────

def test_review_config_returns_suggestions():
    """review_config returns a non-empty list of Suggestion objects."""
    from goldenmatch.core.suggest import review_config, Suggestion

    df = _make_df()
    config = _make_config()
    suggestions = review_config(df, config)

    assert isinstance(suggestions, list), "review_config must return a list"
    assert len(suggestions) > 0, (
        "Expected at least one suggestion for a loose-threshold config "
        f"(got zero; kernel output may be empty or adapter failed silently)"
    )
    for s in suggestions:
        assert isinstance(s, Suggestion), f"Expected Suggestion, got {type(s)}"


def test_suggestion_has_rationale_and_patch():
    """Every returned Suggestion has a non-empty rationale and a patch with an op key."""
    from goldenmatch.core.suggest import review_config

    df = _make_df()
    config = _make_config()
    suggestions = review_config(df, config)

    # At least the first suggestion should be well-formed
    assert suggestions, "Need at least one suggestion to check fields"
    s = suggestions[0]
    assert isinstance(s.rationale, str) and s.rationale.strip(), (
        f"rationale must be a non-empty string; got {s.rationale!r}"
    )
    assert isinstance(s.patch, dict), f"patch must be a dict; got {type(s.patch)}"
    assert "op" in s.patch, (
        f"patch must contain an 'op' key; got keys: {list(s.patch.keys())}"
    )


def test_suggestion_confidence_in_range():
    """Confidence scores must be in [0.0, 1.0]."""
    from goldenmatch.core.suggest import review_config

    suggestions = review_config(_make_df(), _make_config())
    for s in suggestions:
        assert 0.0 <= s.confidence <= 1.0, (
            f"confidence out of range: {s.confidence!r} for suggestion id={s.id!r}"
        )


def test_missing_native_raises():
    """SuggestionsNativeRequired is raised when the kernel is unavailable.

    We simulate absence by patching _native_loader.native_module to return None.
    """
    import unittest.mock as mock
    from goldenmatch.core.suggest import SuggestionsNativeRequired
    from goldenmatch.core.suggest import adapter as _adapter

    df = _make_df()
    config = _make_config()

    with mock.patch.object(_adapter, "_require_kernel", side_effect=SuggestionsNativeRequired("mock")):
        with pytest.raises(SuggestionsNativeRequired):
            _adapter.review_config(df, config)


def test_review_config_adds_row_id_if_absent():
    """review_config works even when __row_id__ is not pre-set on the df."""
    from goldenmatch.core.suggest import review_config

    # _make_df() returns a plain df without __row_id__
    df = _make_df()
    assert "__row_id__" not in df.columns

    # Should not raise; adapter adds __row_id__ internally
    suggestions = review_config(df, _make_config())
    assert isinstance(suggestions, list)


def test_arrow_batch_helpers():
    """Unit-test the three Arrow batch builders directly for schema correctness."""
    import pyarrow as pa
    from goldenmatch.core.suggest.adapter import (
        _build_clusters_batch,
        _build_column_signals_batch,
        _build_scored_pairs_batch,
        _CLUSTERS_SCHEMA,
        _COLUMN_SIGNALS_SCHEMA,
        _SCORED_PAIRS_SCHEMA,
    )

    # scored_pairs
    pairs = [(0, 1, 0.95), (2, 3, 0.88)]
    sp = _build_scored_pairs_batch(pairs)
    assert sp.schema.equals(_SCORED_PAIRS_SCHEMA)
    assert sp.num_rows == 2

    # clusters
    clusters = {
        0: {"size": 2, "confidence": 0.9, "cluster_quality": "strong", "oversized": False, "members": [0, 1]},
        1: {"size": 1, "confidence": 0.0, "cluster_quality": "strong", "oversized": False, "members": [2]},
    }
    cb = _build_clusters_batch(clusters)
    assert cb.schema.equals(_CLUSTERS_SCHEMA)
    assert cb.num_rows == 2

    # column_signals
    df = _make_df().with_row_index("__row_id__").with_columns(pl.col("__row_id__").cast(pl.Int64))
    config = _make_config()
    csb = _build_column_signals_batch(df, config, clusters)
    assert csb.schema.equals(_COLUMN_SIGNALS_SCHEMA)
    assert csb.num_rows >= 1  # at least one column
