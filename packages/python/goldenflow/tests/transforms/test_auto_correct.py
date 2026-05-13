import polars as pl
from goldenflow.transforms.auto_correct import _build_canonical_map, category_auto_correct


def test_case_variant_correction():
    """ACTIVE, Active -> active (most frequent casing)."""
    values = ["active"] * 100 + ["ACTIVE"] * 3 + ["Active"] * 2
    s = pl.Series("status", values)
    result = category_auto_correct(s)
    # All should be "active" (most frequent)
    assert result[-1] == "active"
    assert result[-3] == "active"


def test_misspelling_correction():
    """actve -> active via fuzzy match."""
    values = ["active"] * 100 + ["inactive"] * 80 + ["pending"] * 50 + ["actve"] * 3 + ["pendng"] * 2
    s = pl.Series("status", values)
    result = category_auto_correct(s)
    corrected = result.to_list()
    # "actve" should be corrected to "active"
    assert corrected[230] == "active"  # first "actve"
    # "pendng" should be corrected to "pending"
    assert corrected[233] == "pending"  # first "pendng"


def test_no_correction_for_distinct_values():
    """Values that don't fuzzy-match should stay unchanged."""
    values = ["active"] * 100 + ["inactive"] * 80 + ["cancelled"] * 2
    s = pl.Series("status", values)
    result = category_auto_correct(s)
    corrected = result.to_list()
    # "cancelled" doesn't fuzzy-match "active" or "inactive" well enough
    assert corrected[-1] == "cancelled"


def test_build_canonical_map_basic():
    # value_counts-style input: (distinct_value, count) pairs.
    pairs = [("active", 50), ("Active", 3), ("actve", 2), ("inactive", 40)]
    corrections = _build_canonical_map(pairs)
    assert corrections.get("Active") == "active"
    assert corrections.get("actve") == "active"
    assert "inactive" not in corrections  # high-freq, no correction needed


def test_large_series_does_not_materialize_to_list():
    """Regression for goldenflow #174.

    Earlier versions called ``series.to_list()`` which, at 1M+ rows under
    memory pressure, could trip a pyo3 PanicException
    ("PyObject pointer is null"). The rewritten path goes through
    ``Series.value_counts()`` so work is O(n_unique). This test exercises
    a 200K-row mixed-casing column shape similar to the synthetic person
    fixture that surfaced the panic and asserts the transform completes
    without raising.
    """
    import random
    random.seed(0)
    base = ["Active", "INACTIVE", "Pending", "Cancelled", "ACTIVE", "active", "inactive"]
    values = [random.choice(base) for _ in range(200_000)]
    s = pl.Series("status", values)
    result = category_auto_correct(s)
    assert len(result) == 200_000
    # The transform should converge "ACTIVE" / "active" to one canonical casing.
    distinct_lower = {v.lower() for v in result.to_list() if v}
    assert distinct_lower.issubset({"active", "inactive", "pending", "cancelled"})


def test_preserves_nulls():
    values = ["active"] * 10 + [None] * 5
    s = pl.Series("status", values)
    result = category_auto_correct(s)
    assert result[10] is None


def test_empty_series():
    s = pl.Series("status", [], dtype=pl.Utf8)
    result = category_auto_correct(s)
    assert len(result) == 0
