"""At-scale A/B prove bench for the scorer columnar pipeline.

Legacy list[tuple] scorer (GOLDENMATCH_COLUMNAR_PIPELINE=0) vs columnar DataFrame
scorer (=1), on an EXPLICIT single-weighted-fuzzy-matchkey config (auto-config is
ineligible). Each variant runs in its own subprocess for a clean peak RSS; the
legacy path is expected to OOM at 5M+ (recorded as a result). Parity (pair-set
identity) is checked in-process at a capped small scale.

Local smoke: python scripts/bench_scorer_columnar.py --rows 2000 --runs 1
Workflow: bench-scorer-columnar.yml (large-new-64GB) passes --rows 1000000,5000000 / 25000000.
"""
from __future__ import annotations

# Curated name pools -- surnames spread across soundex codes (blocking-hang guard).
_SURNAMES = [
    "Anderson", "Brown", "Clark", "Davis", "Evans", "Foster", "Garcia", "Harris",
    "Iverson", "Johnson", "King", "Lopez", "Martin", "Nguyen", "Oconnor", "Parker",
    "Quinn", "Roberts", "Smith", "Turner", "Underwood", "Vasquez", "Walker", "Young",
    "Zimmerman", "Bailey", "Coleman", "Dixon", "Edwards", "Fisher",
]
_FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael",
          "Linda", "David", "Elizabeth", "William", "Susan", "Richard", "Karen"]


def make_workload(rows: int, dupe_rate: float = 0.2, seed: int = 7):
    """Return a polars DataFrame of `rows` person records, ~dupe_rate of which are
    lightly-corrupted near-duplicates of an earlier record."""
    import random

    import polars as pl

    rng = random.Random(seed)
    given: list[str] = []
    surname: list[str] = []
    n_base = max(1, int(rows * (1 - dupe_rate)))
    for _ in range(n_base):
        given.append(rng.choice(_FIRST))
        surname.append(rng.choice(_SURNAMES))
    while len(given) < rows:
        src = rng.randrange(n_base)
        g = given[src]
        if len(g) > 3 and rng.random() < 0.5:
            i = rng.randrange(1, len(g) - 1)
            g = g[:i] + rng.choice("aeiou") + g[i + 1:]
        given.append(g)
        surname.append(surname[src])
    return pl.DataFrame({"given_name": given[:rows], "surname": surname[:rows]})


def make_config():
    """An EXPLICIT GoldenMatchConfig satisfying _is_columnar_eligible."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    mk = MatchkeyConfig(
        name="fuzzy_name",
        type="weighted",
        threshold=0.85,
        fields=[
            MatchkeyField(field="given_name", scorer="jaro_winkler", weight=0.4),
            MatchkeyField(field="surname", scorer="jaro_winkler", weight=0.6),
        ],
    )
    blocking = BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["surname"])])
    return GoldenMatchConfig(matchkeys=[mk], blocking=blocking)
