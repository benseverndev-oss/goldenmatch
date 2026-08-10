"""The surrogate-key guard must not change its mind with SCALE.

`ColumnProfile.cardinality_ratio` is a distinct-FRACTION over the ~20K profiling
sample. For any column whose distinct count grows with the row count -- which is
every real identifier -- that fraction climbs toward 1.0 as the frame grows, so
comparing it to an absolute 1.0 makes the "is this a per-record surrogate key?"
verdict a function of n rather than of the data.

Measured on the QIS realistic shape, an `email` column whose true full-frame
ratio is a flat 0.28 profiles at 0.72 / 0.94 / 0.97 / 0.98 / 1.00 for
100K / 500K / 1M / 2M / 5M rows. At 5M zero-config therefore discarded its only
exact identity column as a "perfectly-unique surrogate key", fell back to
fuzzy-only matchkeys plus name-based blocking, and produced a 185M-candidate-pair
run that reached 66 GB RSS and killed the CI runner (bench-quality-scale's heavy
tier, which had never once passed).

`_is_perfect_surrogate` is the fix: one authority for the question, preferring an
EXACT full-frame count and falling back to the sampled ratio only when the exact
value was never computed.

NOTE ON COVERAGE. These tests pin the semantics, the fallback and the exactness
deterministically. They do NOT reproduce the 5M crossing itself -- that is
inherently a scale phenomenon, and forcing it in a unit test would mean betting
on a sample of a large frame happening to miss a rare duplicate, i.e. a flaky
test. The crossing is the QIS heavy tier's job; that gate is exactly what this
class of bug needs, which is why it must stay alive.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.core.autoconfig import (
    ColumnProfile,
    _is_perfect_surrogate,
    profile_columns,
)


def _profile(**kw) -> ColumnProfile:
    base = {"name": "email", "dtype": "String", "col_type": "email", "confidence": 0.9}
    return ColumnProfile(**{**base, "cardinality_ratio": 1.0, **kw})


class TestIsPerfectSurrogate:
    def test_exact_full_frame_ratio_overrules_a_saturated_sample(self):
        """THE regression. The sample says "every value distinct"; the full frame
        says 3.5 records per address. It is not a surrogate key."""
        p = _profile(cardinality_ratio=1.0, full_cardinality_ratio=0.2818)
        assert _is_perfect_surrogate(p) is False

    def test_a_genuine_row_pk_is_still_a_surrogate(self):
        """The guard must keep doing its job -- this is what it was built for."""
        p = _profile(name="id", col_type="identifier",
                     cardinality_ratio=1.0, full_cardinality_ratio=1.0)
        assert _is_perfect_surrogate(p) is True

    def test_falls_back_to_the_sampled_ratio_when_the_exact_value_is_absent(self):
        """Hand-built profiles and callers with no full frame keep their previous
        verdict rather than silently flipping."""
        assert _is_perfect_surrogate(_profile(cardinality_ratio=1.0)) is True
        assert _is_perfect_surrogate(_profile(cardinality_ratio=0.4)) is False

    def test_exact_zero_is_not_read_as_missing(self):
        """0.0 is falsy; the None check must be an identity test, not truthiness."""
        p = _profile(cardinality_ratio=1.0, full_cardinality_ratio=0.0)
        assert _is_perfect_surrogate(p) is False


class TestProfileColumnsStampsTheExactRatio:
    """The post-pass runs after BOTH the Python and native classify paths."""

    def test_unique_column_gets_an_exact_1_0_and_stays_a_surrogate(self):
        """A perfectly unique column necessarily profiles at sample==1.0, so the
        trigger fires deterministically and the exact count confirms it."""
        n = 3000
        df = pl.DataFrame({
            "row_id": [f"pk-{i}" for i in range(n)],
            "city": ["Springfield"] * n,
        })
        by_name = {p.name: p for p in profile_columns(df)}
        assert by_name["row_id"].full_cardinality_ratio == 1.0
        assert _is_perfect_surrogate(by_name["row_id"]) is True

    def test_duplicated_column_is_not_charged_for_the_exact_count(self):
        """Below sample==1.0 the column cannot be a perfect surrogate, so the
        exact count is skipped -- the cost guard is a sound implication, not a
        heuristic: full==1.0 implies sample==1.0 for every subset."""
        n = 3000
        df = pl.DataFrame({
            "row_id": [f"pk-{i}" for i in range(n)],
            "email": [f"user{i % 100}@example.com" for i in range(n)],
        })
        by_name = {p.name: p for p in profile_columns(df)}
        assert by_name["email"].full_cardinality_ratio is None
        assert _is_perfect_surrogate(by_name["email"]) is False

    def test_saturated_sample_over_a_duplicated_column_is_not_a_surrogate(self):
        """THE crossing, reproduced deterministically.

        50,000 rows where `email` carries 50 duplicate pairs, profiled through the
        default 1,000-row sample. The sample is drawn with a FIXED seed and the
        margin is enormous -- the expected number of duplicate pairs landing whole
        in a 1,000-of-50,000 draw is 50 * (1/50)^2 = 0.02 -- so the sample
        saturates at 1.0 while the full frame sits at 0.999. That is exactly the
        divergence that appears naturally at 5M, and the assertions below fail on
        the pre-fix code, which read only the sampled number.
        """
        n, pairs = 50_000, 50
        emails = [f"user{i}@example.com" for i in range(n - pairs)]
        emails += emails[:pairs]  # 50 values now appear twice
        df = pl.DataFrame({"row_id": [f"pk-{i}" for i in range(n)], "email": emails})

        p = {q.name: q for q in profile_columns(df)}["email"]

        # The sample cannot tell this column from a row PK...
        assert p.cardinality_ratio == 1.0
        # ...but the full frame can, and that is what the guard now reads.
        assert p.full_cardinality_ratio == (n - pairs) / n
        assert _is_perfect_surrogate(p) is False
