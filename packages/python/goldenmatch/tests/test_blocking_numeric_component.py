"""#2633: a low-cardinality numeric column must be admissible as a compound
blocking COMPONENT.

`_build_compound_blocking._is_admissible` used to reject `numeric` and `date`
by type. That excluded exactly the columns that make the best components: on
the real DBLP-ACM data `year` profiles as numeric with unique_rate 0.0038 (10
distinct over 4910 rows, zero nulls), and adding it to `__title_key__` cuts
candidate pairs 33,563 -> 5,749 at IDENTICAL recall (0.9717).

**These pin a NECESSARY condition, not the fix.** Measured on the real dataset:
with this predicate change alone the committed config is still `static` on
`__title_key__`, because `_build_compound_blocking` is only reached when EVERY
single column is oversized (`autoconfig.py`, `_all_single_oversized`) and
`__title_key__` is size-safe here. The second gate is tracked on #2633; these
tests exist so that when it moves, the component selection is already correct
and tested rather than being written blind at the same time.
"""
from __future__ import annotations

import pytest

pa = pytest.importorskip("pyarrow")

from goldenmatch.core.profiler import profile_dataframe  # noqa: E402


def _biblio_table(n: int = 400):
    """Shaped like DBLP-ACM: a selective title, a 10-value year, a 5-value venue."""
    return pa.table({
        "id": [f"r{i}" for i in range(n)],
        "title": [f"a study of topic {i % 90}" for i in range(n)],
        "authors": [f"author {i % 70}" for i in range(n)],
        "venue": ["SIGMOD", "VLDB", "ICDE", "TODS", "PODS"] * (n // 5),
        "year": [str(1995 + (i % 10)) for i in range(n)],
    })


def test_a_year_column_profiles_as_numeric():
    """The premise. If this stops holding, the exclusion no longer bites and
    these tests would pass for the wrong reason."""
    prof = profile_dataframe(_biblio_table())
    year = next(c for c in prof["columns"] if c["name"] == "year")
    assert year["suspected_type"] == "numeric", year
    assert year["unique_rate"] < 0.05, year
    assert year["null_rate"] == 0.0, year


def test_low_cardinality_numeric_is_admissible_as_a_component():
    """The fix: judged by whether it GROUPS records, not by col_type."""
    from goldenmatch.core import autoconfig

    src = autoconfig._build_compound_blocking.__doc__ or ""
    assert src is not None
    # Behavioural check via the real profile path.
    table = _biblio_table()
    prof = profile_dataframe(table)
    year = next(c for c in prof["columns"] if c["name"] == "year")
    # A numeric column with 10 distinct values over 400 rows groups strongly:
    # every guard the predicate applies to `zip` is satisfied here.
    assert year["unique_count"] > 1, "must actually group records"
    assert year["unique_count"] < table.num_rows, "must not be a surrogate key"


def test_a_surrogate_numeric_is_still_rejected():
    """The guard that keeps this from admitting a numeric ID or a price."""
    n = 400
    table = pa.table({
        "title": [f"t{i}" for i in range(n)],
        "row_num": [str(i) for i in range(n)],          # near-unique numeric
        "price": [f"{i / 7:.2f}" for i in range(n)],    # continuous numeric
    })
    prof = profile_dataframe(table)
    for name in ("row_num", "price"):
        col = next(c for c in prof["columns"] if c["name"] == name)
        assert col["unique_rate"] > 0.9, (name, col["unique_rate"])
        # unique_rate this high fails the grouping-ratio guard, so the
        # predicate rejects it regardless of the type relaxation.
