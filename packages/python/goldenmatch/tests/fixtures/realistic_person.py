"""Realistic-person fixture for Arrow-native roadmap bench harnesses.

Replaces ``test_autoconfig_regressions._person_df`` for benches that
need a non-degenerate workload at scale. The old fixture used a
30-surname pool with `i`-suffixed unique first names — combined with
soundex(last_name) blocking, this produced 25-30 dense blocks of N/30
records each AND no real duplicates, so the auto-split budget
exhausted regardless of N and every backend's measured workload was
the same small constant.

This fixture fixes both:

1. **Wide surname pool.** Draws from
   ``goldenmatch.refdata.surnames`` (~10k rank-ordered census names).
   Combined with soundex blocking, this produces hundreds of soundex
   codes, each with a small number of records — no oversized
   pathology.

2. **Real duplicates.** Three records per identity, with typo
   variants applied to ``first_name`` (NOT to ``last_name``, which
   is the blocking column — keeping it canonical means all three
   variants land in the same soundex block, so fuzzy scoring within
   the block finds them as duplicates).

3. **Realistic null injection.** 3% nulls in ``first_name``,
   ``address``, ``city``; never in ``last_name`` (blocking field)
   or ``email`` (identity grouping field).

The fixture is designed for Arrow-native roadmap phase benches
(Phases 0-6, see ``docs/superpowers/specs/2026-05-31-arrow-native-roadmap.md``
— gitignored). Each phase's bench can call ``realistic_person_df(n)``
to get the same input shape; comparing wall + RSS across phases is
then directly meaningful.

Compatible with the bench harness's matchkey shape (single-field
weighted ``jaro_winkler`` on ``last_name``, soundex blocking on
``last_name``) AND with the autoconfig-driven path (full column set
matches what auto_configure_df expects from a person dataset).
"""
from __future__ import annotations

import random
from typing import Final

import polars as pl


# Three typo variants per identity. Applied to first_name only so
# last_name stays canonical for stable soundex blocking. Soundex on
# the canonical last_name is the same across all three variants of an
# identity → all three records land in the same block → fuzzy scoring
# inside the block finds the pair_a/pair_b/pair_c pairs.
def _first_variant(s: str, kind: int) -> str:
    """Three typo variants:

    - kind=0: canonical (no change).
    - kind=1: lowercase + drop last character ("Alice" → "alic"). Low-edit
      variant; jaro_winkler should still score very high.
    - kind=2: drop second character ("Alice" → "Alce"). Bigger edit but
      still recognizable.

    Strings shorter than 3 chars fall back to canonical (no good variant).
    """
    if kind == 0 or len(s) < 3:
        return s
    if kind == 1:
        return s[:-1].lower()
    return s[0] + s[2:]


# Streets / cities / states are realistic but small pools — they're
# secondary signal in a typical person-dedupe matchkey config. The
# bench config uses single-field-on-last_name so these columns are
# just structural; auto-config can use them for richer matchkeys if
# a bench wants to test that.
_STREETS: Final[tuple[str, ...]] = (
    "Main St", "Oak Ave", "Maple Dr", "Cedar Ln", "Elm St",
    "Pine Rd", "Birch Ave", "Spruce Ct", "Willow Way", "Aspen Pl",
    "Sunset Blvd", "Park Ave", "River Rd", "Lake Dr", "Hill St",
    "Valley Way", "Forest Ln", "Meadow Ct", "Spring Pl", "Summer Dr",
)
_CITIES: Final[tuple[str, ...]] = (
    "Raleigh", "Durham", "Cary", "Charlotte", "Greensboro",
    "Asheville", "Winston-Salem", "Fayetteville", "Wilmington", "Greenville",
)
_STATES: Final[tuple[str, ...]] = ("NC", "SC", "VA", "TN", "GA")


def realistic_person_df(n: int, seed: int = 42) -> pl.DataFrame:
    """Build an n-row realistic person dataset with real fuzzy duplicates.

    Args:
        n: Total row count. Identities = n // 3 (some remainder rows
           drop to fewer-than-3 if n % 3 != 0).
        seed: Random seed for null injection. Surname / given-name
           cycling is deterministic (not seeded) so the same n produces
           the same identity skeleton regardless of seed.

    Returns:
        Polars DataFrame with columns:
        ``first_name`` (3% nulls), ``last_name`` (no nulls,
        canonical across variants), ``email`` (no nulls, identity
        grouping key), ``zip``, ``address`` (3% nulls),
        ``city`` (3% nulls), ``state``.

    Shape guarantees:
        - ≥ 5,000 distinct last_names at n >= 15,000 (3 records per
          identity, drawing from a ~10k surname pool).
        - ~ n // 3 multi-member clusters at the dedupe stage, each
          size 3 (modulo fuzzy-scoring recall on the typo variants —
          empirically ~90% of designed clusters get caught).
        - Soundex(last_name) produces hundreds of distinct codes →
          no single block exceeds the 5,000-record oversized
          threshold even at 1M rows.
    """
    from goldenmatch.refdata import given_names, surnames

    # Load the census pools. _load() is idempotent + cheap.
    surnames._load()
    given_names._load()
    first_pool = [g.title() for g in sorted(given_names._state.canonicals)]
    last_pool = [s.title() for s in surnames._state.ranks.keys()]
    n_first = len(first_pool)
    n_last = len(last_pool)

    if n_last < 5000:
        raise RuntimeError(
            f"surname pool has only {n_last} entries; need >= 5000 for "
            f"non-degenerate blocking. refdata may have changed."
        )

    rng = random.Random(seed)

    # Pre-roll the null mask once (faster than per-row rng.random()
    # calls for n > ~10K). NULL_RATE applies per-column independently.
    NULL_RATE = 0.03
    null_mask_first = [rng.random() < NULL_RATE for _ in range(n)]
    null_mask_addr = [rng.random() < NULL_RATE for _ in range(n)]
    null_mask_city = [rng.random() < NULL_RATE for _ in range(n)]

    first_names: list[str | None] = []
    last_names: list[str] = []
    emails: list[str] = []
    zips: list[str] = []
    addresses: list[str | None] = []
    cities: list[str | None] = []
    states: list[str] = []

    for i in range(n):
        group_id = i // 3
        within = i % 3

        # last_name: SAME canonical for all 3 variants in an identity.
        # This is the key change vs the old generator that varied
        # last_name across variants — keeping last_name canonical
        # means soundex(last_name) is stable per identity, so all 3
        # variants land in the same fuzzy-scoring block.
        last_base = last_pool[group_id % n_last]
        last_names.append(last_base)

        # first_name: typo variants per within=0,1,2.
        first_base = first_pool[group_id % n_first]
        if null_mask_first[i]:
            first_names.append(None)
        else:
            first_names.append(_first_variant(first_base, within))

        # email: SAME per identity (the design says 3 records share
        # an email). This is also a duplicate signal a real config
        # would pick up; the bench's single-matchkey config doesn't
        # use it but auto-config does.
        emails.append(f"u{group_id}@example.com")

        # Geo: cycled deterministically so it doesn't dominate any
        # blocking decision. zip cycles 0-99 over 100 codes; city/
        # state cycle their own pools.
        zips.append(f"{10000 + (group_id % 100):05d}")
        cities.append(None if null_mask_city[i] else _CITIES[group_id % len(_CITIES)])
        states.append(_STATES[group_id % len(_STATES)])
        if null_mask_addr[i]:
            addresses.append(None)
        else:
            addresses.append(f"{group_id + 1} {_STREETS[group_id % len(_STREETS)]}")

    return pl.DataFrame({
        "first_name": first_names,
        "last_name": last_names,
        "email": emails,
        "zip": zips,
        "address": addresses,
        "city": cities,
        "state": states,
    })
