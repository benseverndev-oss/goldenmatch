"""End-to-end test for the review_config adapter (Task 11).

Guards:
- Skips when the native kernel is absent or doesn't expose ``suggest_config``.
- Never loads torch / HuggingFace models (rerank disabled explicitly in adapter).
- Uses a tiny synthetic person-like DataFrame with known duplicates so the
  kernel has real signal to work with.
"""
from __future__ import annotations

import polars as pl
import pytest

# ── Native guard ──────────────────────────────────────────────────────────

def _native_suggest_available() -> bool:
    try:
        from goldenmatch.core._native_loader import native_module
        nm = native_module()
        return nm is not None and hasattr(nm, "suggest_config")
    except Exception:
        return False


# ── Pure-Python helper tests (no native needed) ───────────────────────────
# These run unconditionally -- they exercise _collision_rates and
# _config_summary, which never touch the kernel.

def _helper_config():
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
        threshold=0.3,
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", weight=0.5),
            MatchkeyField(field="last_name", scorer="jaro_winkler", weight=0.5),
        ],
    )
    blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["last_name"])],
        auto_suggest=False,
    )
    return GoldenMatchConfig(matchkeys=[mk], blocking=blocking)


def test_collision_rates_all_identical_is_zero():
    """A multi-member cluster where the column is all-identical -> 0.0."""
    from goldenmatch.core.suggest.adapter import _collision_rates

    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "name": ["Alice", "Alice"],  # identical
    })
    clusters = {0: {"size": 2, "oversized": False, "members": [0, 1]}}
    rates = _collision_rates(clusters, df)
    assert rates.get("name") == 0.0


def test_collision_rates_two_distinct_is_one():
    """A multi-member cluster with two distinct non-null values -> 1.0."""
    from goldenmatch.core.suggest.adapter import _collision_rates

    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "name": ["Alice", "Bob"],  # distinct
    })
    clusters = {0: {"size": 2, "oversized": False, "members": [0, 1]}}
    rates = _collision_rates(clusters, df)
    assert rates.get("name") == 1.0


def test_collision_rates_single_member_ignored():
    """Single-member clusters are not counted (no multi-member -> empty)."""
    from goldenmatch.core.suggest.adapter import _collision_rates

    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "name": ["Alice", "Bob"],
    })
    clusters = {
        0: {"size": 1, "oversized": False, "members": [0]},
        1: {"size": 1, "oversized": False, "members": [1]},
    }
    rates = _collision_rates(clusters, df)
    assert rates == {}  # no multi-member clusters


def test_collision_rates_mixed_clusters():
    """Two multi-member clusters: one collides, one doesn't -> 0.5."""
    from goldenmatch.core.suggest.adapter import _collision_rates

    df = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "name": ["Alice", "Alice", "Bob", "Carol"],  # cluster1 same, cluster2 differs
    })
    clusters = {
        0: {"size": 2, "oversized": False, "members": [0, 1]},  # identical -> no collision
        1: {"size": 2, "oversized": False, "members": [2, 3]},  # distinct -> collision
    }
    rates = _collision_rates(clusters, df)
    assert rates.get("name") == 0.5


def test_config_summary_shape():
    """_config_summary returns the kernel ConfigSummary shape with 'kind'."""
    from goldenmatch.core.suggest.adapter import _config_summary

    summary = _config_summary(_helper_config())
    assert set(summary.keys()) == {"matchkeys", "negative_evidence"}
    assert isinstance(summary["matchkeys"], list)
    assert len(summary["matchkeys"]) == 1
    mk = summary["matchkeys"][0]
    assert mk["name"] == "fuzzy_match"
    assert mk["kind"] == "weighted"  # the required field discovered at runtime
    assert mk["threshold"] == 0.3
    assert len(mk["fields"]) == 2
    assert mk["fields"][0]["field"] == "first_name"
    assert mk["fields"][0]["scorer"] == "jaro_winkler"
    assert summary["negative_evidence"] == []


# ── Native-gated tests below ──────────────────────────────────────────────
# The helper tests above run unconditionally; everything below needs the
# kernel, so they carry a per-test skipif marker (NOT a module-level skip,
# which would also skip the pure-Python helper tests above).

requires_native = pytest.mark.skipif(
    not _native_suggest_available(),
    reason="native suggest_config not built",
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

@requires_native
def test_review_config_returns_suggestions():
    """review_config returns a non-empty list of Suggestion objects."""
    from goldenmatch.core.suggest import Suggestion, review_config

    df = _make_df()
    config = _make_config()
    suggestions = review_config(df, config)

    assert isinstance(suggestions, list), "review_config must return a list"
    assert len(suggestions) > 0, (
        "Expected at least one suggestion for a loose-threshold config "
        "(got zero; kernel output may be empty or adapter failed silently)"
    )
    for s in suggestions:
        assert isinstance(s, Suggestion), f"Expected Suggestion, got {type(s)}"


@requires_native
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


@requires_native
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


@requires_native
def test_review_config_adds_row_id_if_absent():
    """review_config works even when __row_id__ is not pre-set on the df."""
    from goldenmatch.core.suggest import review_config

    # _make_df() returns a plain df without __row_id__
    df = _make_df()
    assert "__row_id__" not in df.columns

    # Should not raise; adapter adds __row_id__ internally
    suggestions = review_config(df, _make_config())
    assert isinstance(suggestions, list)


@requires_native
def test_review_config_does_not_mutate_caller_config():
    """review_config must deep-copy the config; the caller's rerank stays set."""
    from goldenmatch.core.suggest import review_config

    config = _make_config()
    # Force rerank ON on the caller's config; the adapter disables it on its
    # OWN copy, so the caller's object must come back unchanged.
    for mk in config.get_matchkeys():
        mk.rerank = True

    review_config(_make_df(), config)

    for mk in config.get_matchkeys():
        assert mk.rerank is True, (
            "review_config mutated the caller's config (rerank was flipped off)"
        )


def test_arrow_batch_helpers():
    """Unit-test the three Arrow batch builders directly for schema correctness."""
    from goldenmatch.core.suggest.adapter import (
        _CLUSTERS_SCHEMA,
        _COLUMN_SIGNALS_SCHEMA,
        _SCORED_PAIRS_SCHEMA,
        _build_clusters_batch,
        _build_column_signals_batch,
        _build_scored_pairs_batch,
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
