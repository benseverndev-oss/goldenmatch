"""Tests for the GOLDENMATCH_BENCH_DUMP_PAIRS hook in the probabilistic branch.

The hook is opt-in (env-gated). When GOLDENMATCH_BENCH_DUMP_PAIRS points at a
directory, the dedupe pipeline's probabilistic branch dumps two parquet files
(in internal __row_id__ space):

  - candidate_pairs.parquet : all within-block candidate pairs (blocking ceiling)
  - emitted_pairs.parquet   : the pairs the probabilistic scorer emitted

A benchmark harness uses these to attribute recall loss between the blocking
ceiling and the scoring threshold. When the env var is unset the branch must be
a behavior-identical no-op (no I/O, no accumulation).
"""

from __future__ import annotations

import os

import polars as pl
import pytest


def _person_df() -> pl.DataFrame:
    """~40-row synthetic person frame with intentional near-duplicates.

    Surnames are spread across several distinct values (don't collapse to one
    soundex/block, per the repo blocking-blowup gotcha). The duplicate rows
    share first_name + surname + city closely enough that the Fellegi-Sunter
    probabilistic scorer emits at least one above-threshold pair.
    """
    base = [
        ("John", "Smith", "Boston"),
        ("Jon", "Smith", "Boston"),       # near-dup of John Smith Boston
        ("Mary", "Jones", "Denver"),
        ("Marie", "Jones", "Denver"),     # near-dup of Mary Jones Denver
        ("Robert", "Brown", "Austin"),
        ("Bob", "Brown", "Austin"),
        ("Linda", "Davis", "Seattle"),
        ("Lynda", "Davis", "Seattle"),    # near-dup of Linda Davis Seattle
        ("James", "Wilson", "Portland"),
        ("Jim", "Wilson", "Portland"),
        ("Patricia", "Moore", "Chicago"),
        ("Pat", "Moore", "Chicago"),
        ("Michael", "Taylor", "Phoenix"),
        ("Mike", "Taylor", "Phoenix"),
        ("Barbara", "Anderson", "Dallas"),
        ("Barb", "Anderson", "Dallas"),
        ("William", "Thomas", "Houston"),
        ("Will", "Thomas", "Houston"),
        ("Elizabeth", "Jackson", "Miami"),
        ("Liz", "Jackson", "Miami"),
        ("David", "White", "Atlanta"),
        ("Dave", "White", "Atlanta"),
        ("Jennifer", "Harris", "Tampa"),
        ("Jen", "Harris", "Tampa"),
        ("Charles", "Martin", "Reno"),
        ("Chuck", "Martin", "Reno"),
        ("Susan", "Clark", "Boise"),
        ("Sue", "Clark", "Boise"),
        ("Joseph", "Lewis", "Tucson"),
        ("Joe", "Lewis", "Tucson"),
        ("Margaret", "Walker", "Fresno"),
        ("Maggie", "Walker", "Fresno"),
        ("Thomas", "Hall", "Mesa"),
        ("Tom", "Hall", "Mesa"),
        ("Sarah", "Allen", "Omaha"),
        ("Sara", "Allen", "Omaha"),       # near-dup
        ("Daniel", "Young", "Tulsa"),
        ("Dan", "Young", "Tulsa"),
        ("Nancy", "King", "Akron"),
        ("Nan", "King", "Akron"),
    ]
    return pl.DataFrame(
        {
            "first_name": [r[0] for r in base],
            "surname": [r[1] for r in base],
            "city": [r[2] for r in base],
        }
    )


def _probabilistic_config(df: pl.DataFrame):
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

    return auto_configure_probabilistic_df(df)


def test_bench_dump_writes_candidate_and_emitted_pairs(tmp_path):
    """With the env var set, the probabilistic branch dumps both parquets."""
    df = _person_df()
    cfg = _probabilistic_config(df)

    from goldenmatch import dedupe_df

    os.environ["GOLDENMATCH_BENCH_DUMP_PAIRS"] = str(tmp_path)
    try:
        dedupe_df(df, config=cfg)
    finally:
        os.environ.pop("GOLDENMATCH_BENCH_DUMP_PAIRS", None)

    cand_path = tmp_path / "candidate_pairs.parquet"
    emit_path = tmp_path / "emitted_pairs.parquet"
    assert cand_path.exists(), "candidate_pairs.parquet was not written"
    assert emit_path.exists(), "emitted_pairs.parquet was not written"

    cand = pl.read_parquet(cand_path)
    emit = pl.read_parquet(emit_path)

    assert set(cand.columns) == {"a", "b"}
    assert set(emit.columns) == {"a", "b"}

    # Every row canonical (a < b).
    if cand.height:
        assert (cand["a"] < cand["b"]).all()
    if emit.height:
        assert (emit["a"] < emit["b"]).all()

    cand_set = set(zip(cand["a"].to_list(), cand["b"].to_list()))
    emit_set = set(zip(emit["a"].to_list(), emit["b"].to_list()))

    assert cand_set, "candidate set must be non-empty (blocking ceiling)"
    assert emit_set, "emitted set must be non-empty (scorer should emit pairs)"
    # Emitted pairs come from within-block candidates -> subset of candidates.
    assert emit_set.issubset(cand_set), (
        f"emitted pairs not a subset of candidates: "
        f"{emit_set - cand_set}"
    )


def test_bench_dump_noop_when_env_unset(tmp_path):
    """With the env var unset, the branch writes NO parquet files (hook off)."""
    df = _person_df()
    cfg = _probabilistic_config(df)

    # Make sure no stray env leaks in from another test.
    os.environ.pop("GOLDENMATCH_BENCH_DUMP_PAIRS", None)

    from goldenmatch import dedupe_df

    result = dedupe_df(df, config=cfg)

    written = list(tmp_path.glob("*.parquet"))
    assert written == [], f"hook wrote files with env unset: {written}"
    # Scoring still ran: the unset path must not accidentally no-op matching.
    assert result.dupes is not None and result.dupes.height > 0, (
        "dedupe produced no duplicate pairs with env unset -- scoring silently no-oped"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
