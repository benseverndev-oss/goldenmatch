"""Structural sanity for ``realistic_person_df``.

Tests are STRUCTURAL only — they verify fixture properties without
running ``dedupe_df``, so they're cheap and runnable in any CI lane.
The dedupe-output assertions (e.g. ``dupes > 50K at 100K``) live in
the bench harness (`scripts/bench_datafusion_vs_bucket.py`) and run on
``large-new-64GB`` per the Arrow-native roadmap Phase 0 kill criterion
(GH issue #622).

Scope: 10K-row fixture only. 100K and 1M structural checks would
add minutes to CI for no additional signal — the same Polars
aggregations run at scale via the bench harness when needed.
"""
from __future__ import annotations

import polars as pl
import pytest


@pytest.fixture(scope="module")
def fx_10k() -> pl.DataFrame:
    from tests.fixtures.realistic_person import realistic_person_df
    return realistic_person_df(10_000)


# ── Shape ────────────────────────────────────────────────────────────


class TestShape:
    def test_height_matches_request(self, fx_10k):
        assert fx_10k.height == 10_000

    def test_expected_columns(self, fx_10k):
        assert set(fx_10k.columns) == {
            "first_name", "last_name", "email", "zip",
            "address", "city", "state",
        }


# ── Surname distribution (the Phase 0 raison d'etre) ────────────────


class TestSurnameDistribution:
    def test_distinct_surnames_above_floor(self, fx_10k):
        """At 10K rows = 3333 identities, drawing from a 10k+
        surname pool, distinct surnames should be ≥ 1000 (Phase 0
        kill criterion). The old fixture had 30."""
        n_distinct = fx_10k["last_name"].n_unique()
        assert n_distinct >= 1000, (
            f"only {n_distinct} distinct surnames; degenerate-fixture "
            f"regression. Phase 0 floor is 1000 at 10K rows."
        )

    def test_soundex_blocks_not_oversized(self, fx_10k):
        """Soundex on last_name must produce many small blocks, not
        a handful of dense ones (the old fixture's pathology). The
        single largest soundex block must be ≤ 5000 records (the
        auto-split oversized threshold)."""
        soundex_sizes = (
            fx_10k
            .with_columns(pl.col("last_name").map_elements(
                _soundex, return_dtype=pl.Utf8,
            ).alias("__sx__"))
            .group_by("__sx__")
            .agg(pl.len().alias("size"))
        )
        max_block = soundex_sizes["size"].max()
        assert max_block <= 5000, (
            f"max soundex block size = {max_block}; degenerate-fixture "
            f"regression. Phase 0 floor is ≤ 5000 (the oversized "
            f"auto-split threshold)."
        )


# ── Duplicate skeleton ───────────────────────────────────────────────


class TestDuplicateSkeleton:
    def test_email_groups_size_three(self, fx_10k):
        """Each email represents one identity; the design says 3
        records per identity (modulo the last group if n % 3 != 0)."""
        sizes = (
            fx_10k.group_by("email").agg(pl.len().alias("size"))
            ["size"].to_list()
        )
        # 10000 / 3 = 3333 groups of 3 + 1 stray. Allow up to 1 group
        # of != 3 for the n % 3 remainder.
        n_size_3 = sum(1 for s in sizes if s == 3)
        n_other = sum(1 for s in sizes if s != 3)
        assert n_size_3 >= 3000, f"expected ~3333 size-3 groups, got {n_size_3}"
        assert n_other <= 1, (
            f"expected at most 1 non-size-3 group (the n%3 remainder); "
            f"got {n_other}"
        )

    def test_last_name_stable_within_identity(self, fx_10k):
        """All 3 records of an identity share the SAME canonical
        last_name (Phase 0 design — keeps soundex stable so the 3
        records land in the same fuzzy-scoring block)."""
        per_email = (
            fx_10k
            .group_by("email")
            .agg(pl.col("last_name").n_unique().alias("n_distinct"))
        )
        max_distinct = per_email["n_distinct"].max()
        assert max_distinct == 1, (
            f"some identity has multiple last_names ({max_distinct}); "
            f"breaks soundex-block stability. Phase 0 design says "
            f"last_name is canonical per identity, only first_name varies."
        )

    def test_first_name_varies_within_identity(self, fx_10k):
        """The 3 records of an identity should have ≥ 2 distinct
        first_name values (kind=0 canonical, kind=1 lowercase-drop-last,
        kind=2 drop-second-char). With null injection at 3% it's
        possible for an identity to lose 1-2 variants to nulls, so
        the floor is "at least one identity has all 3 distinct"."""
        per_email = (
            fx_10k
            .filter(pl.col("first_name").is_not_null())
            .group_by("email")
            .agg(pl.col("first_name").n_unique().alias("n_distinct"))
        )
        max_distinct = per_email["n_distinct"].max()
        assert max_distinct >= 2, (
            "no identity has multiple distinct first_name variants; "
            "_first_variant may be broken."
        )


# ── Null injection ───────────────────────────────────────────────────


class TestNullInjection:
    def test_first_name_null_rate_in_range(self, fx_10k):
        """3% null rate target, 2-5% accepted band (RNG variance at
        10K is roughly ± 0.5pp)."""
        n_null = fx_10k["first_name"].null_count()
        rate = n_null / fx_10k.height
        assert 0.02 <= rate <= 0.05, f"first_name null rate {rate:.3f} outside [0.02, 0.05]"

    def test_last_name_never_null(self, fx_10k):
        """last_name is the blocking column; nulls there break
        blocking. Phase 0 design says no nulls in identity/blocking
        columns."""
        assert fx_10k["last_name"].null_count() == 0

    def test_email_never_null(self, fx_10k):
        assert fx_10k["email"].null_count() == 0

    def test_address_null_rate_in_range(self, fx_10k):
        rate = fx_10k["address"].null_count() / fx_10k.height
        assert 0.02 <= rate <= 0.05


# ── Helpers ──────────────────────────────────────────────────────────


def _soundex(name: str) -> str:
    """Minimal soundex matching jellyfish's algorithm for the test.
    Inlined to avoid a jellyfish dep just for this test module.

    Standard 4-char American Soundex: keep first letter, encode
    remaining consonants to digits, drop vowels/h/w, collapse
    adjacent duplicates, pad with zeros to length 4.
    """
    if not name:
        return "0000"
    name = name.upper()
    mapping = {
        "B": "1", "F": "1", "P": "1", "V": "1",
        "C": "2", "G": "2", "J": "2", "K": "2", "Q": "2", "S": "2", "X": "2", "Z": "2",
        "D": "3", "T": "3",
        "L": "4",
        "M": "5", "N": "5",
        "R": "6",
    }
    first = name[0]
    encoded = [mapping.get(c, "") for c in name[1:]]
    # Collapse adjacent duplicates.
    collapsed = []
    last = ""
    for d in encoded:
        if d and d != last:
            collapsed.append(d)
        last = d
    return (first + "".join(collapsed) + "000")[:4]
