import os

import polars as pl
import pytest
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.autoconfig import (
    _blocking_candidate_budget_k,
    _candidate_blocking_passes,
    _estimate_pass_stats,
    _select_passes_within_budget,
    profile_columns,
)
from goldenmatch.core.blocker import _build_block_key_expr
from goldenmatch.core.chunked import ChunkedMatcher
from goldenmatch.db.blocking import build_blocking_query


def _key(df, cfg):
    return df.lazy().with_columns(_build_block_key_expr(cfg)).collect()["__block_key__"].to_list()

def test_field_transforms_per_field():
    df = pl.DataFrame({"surname": ["SMITH", "Jones"], "dob": ["1990-05-01", "1985-12-30"]})
    cfg = BlockingKeyConfig(
        fields=["surname", "dob"],
        field_transforms=[["lowercase"], ["substring:0:4"]],
    )
    # surname lowercased, dob year-truncated, concatenated with ||
    assert _key(df, cfg) == ["smith||1990", "jones||1985"]

def test_field_transforms_none_matches_shared_transforms():
    df = pl.DataFrame({"surname": ["SMITH", "Jones"], "dob": ["1990-05-01", "1985-12-30"]})
    shared = BlockingKeyConfig(fields=["surname", "dob"], transforms=["lowercase"])
    # field_transforms is None -> shared transforms apply to every field (today's behavior)
    assert _key(df, shared) == ["smith||1990-05-01", "jones||1985-12-30"]

def test_field_transforms_length_must_match_fields():
    with pytest.raises(ValueError):
        BlockingKeyConfig(fields=["surname", "dob"], field_transforms=[["lowercase"]])


def test_db_blocking_query_honors_field_transforms():
    cfg = BlockingConfig(keys=[BlockingKeyConfig(
        fields=["surname", "dob"],
        field_transforms=[["soundex"], ["substring:0:4"]],
    )])
    sql = build_blocking_query("people", {"surname": "Smith", "dob": "1990-05-01"}, cfg).lower()
    assert 'soundex("surname")' in sql      # soundex wraps surname, NOT dob
    assert 'substring("dob", 1, 4)' in sql  # substring wraps dob, NOT surname


def test_chunked_block_key_honors_field_transforms():
    df = pl.DataFrame({"surname": ["SMITH", "Jones"], "dob": ["1990-05-01", "1985-12-30"]})
    cfg = BlockingKeyConfig(fields=["surname", "dob"], field_transforms=[["lowercase"], ["substring:0:4"]])
    expected = df.lazy().with_columns(_build_block_key_expr(cfg)).collect()["__block_key__"].to_list()
    # _block_key_column uses self only to be a method (reads no instance state),
    # so an uninitialized instance is sufficient and avoids constructing a full
    # GoldenMatchConfig.
    inst = object.__new__(ChunkedMatcher)
    got = inst._block_key_column(df, cfg)["__block_key__"].to_list()
    assert got == expected == ["smith||1990", "jones||1985"]


def test_blocking_candidate_budget_k_default_and_override():
    assert _blocking_candidate_budget_k() == 25
    for raw, expected in [("10", 10), ("100", 100), ("0", 25), ("-5", 25), ("junk", 25)]:
        os.environ["GOLDENMATCH_BLOCKING_CANDIDATE_BUDGET_K"] = raw
        try:
            assert _blocking_candidate_budget_k() == expected
        finally:
            os.environ.pop("GOLDENMATCH_BLOCKING_CANDIDATE_BUDGET_K", None)


def _person_df():
    return pl.DataFrame({
        "first_name": ["ann", "ann", "bob", "bob", "cara", "dan", "eve", "fay"],
        "surname":    ["lee", "lee", "kim", "kim", "ng", "ono", "poe", "qua"],
        "dob":        ["1990-01-02", "1990-01-02", "1985-03-04", "1985-03-04",
                       "1972-07-08", "1965-09-10", "1959-11-12", "1944-02-14"],
        "postcode":   ["AA1", "AA1", "BB2", "BB2", "CC3", "DD4", "EE5", "FF6"],
    })

def test_candidate_pool_has_name_passes_and_compounds():
    df = _person_df()
    profiles = profile_columns(df)
    pool = _candidate_blocking_passes(profiles, df)
    field_sets = [tuple(p.fields) for p in pool]
    assert any(len(fs) == 1 for fs in field_sets)        # >= one single-field pass
    compounds = [p for p in pool if len(p.fields) == 2]
    assert compounds, "expected compound (name x orthogonal) passes in the pool"
    date_compounds = [p for p in compounds if "dob" in p.fields]
    assert date_compounds
    dc = date_compounds[0]
    assert dc.field_transforms is not None and len(dc.field_transforms) == 2
    dob_i = dc.fields.index("dob")
    assert any("substring:0:4" in t for t in dc.field_transforms[dob_i])      # date coarsened
    other_i = 1 - dob_i
    assert all("substring:0:4" not in t for t in dc.field_transforms[other_i])  # name NOT coarsened


def test_estimate_pass_stats_exact_count_and_coverage():
    # surname blocks: {smith: rows 0,1,2} (3 -> C(3,2)=3 pairs), {jones: rows 3,4} (1 pair), lee singleton (0)
    df = pl.DataFrame({
        "surname": ["smith", "smith", "smith", "jones", "jones", "lee"],
        "dob":     ["x", "x", "x", "y", "y", "z"],
    })
    cfg = BlockingKeyConfig(fields=["surname"])
    count, coverage = _estimate_pass_stats(cfg, df)
    assert count == 3 + 1  # 4 candidate pairs
    N = 6
    expected = {0*N+1, 0*N+2, 1*N+2, 3*N+4}  # smith pairs + jones pair, canonical min*N+max
    assert coverage == expected


def _mkpass(fields):
    return BlockingKeyConfig(fields=list(fields))

def test_select_respects_budget_and_is_coverage_greedy():
    # pool entries: (pass, candidate_count, coverage_set)
    name = _mkpass(["surname"])                 # broad, expensive, covers {1,2,3,4}
    cA   = _mkpass(["surname", "dob"])           # tight, covers {1,2}
    cB   = _mkpass(["surname", "postcode"])      # tight, covers {3,4}
    cDup = _mkpass(["surname", "city"])          # tight but REDUNDANT, covers {1,2}
    pool = [
        (name, 100, {1, 2, 3, 4}),
        (cA, 10, {1, 2}),
        (cB, 10, {3, 4}),
        (cDup, 10, {1, 2}),
    ]
    selected = _select_passes_within_budget(pool, budget=25)
    fsets = {tuple(p.fields) for p in selected}
    # within budget 25: the broad name (100) does NOT fit; cA + cB (20) cover everything,
    # cDup adds zero NEW coverage so it is NOT chosen.
    assert ("surname", "dob") in fsets and ("surname", "postcode") in fsets
    assert ("surname",) not in fsets          # too expensive for the budget
    assert ("surname", "city") not in fsets   # redundant, no marginal coverage
    # total candidate_count of the selection is within budget
    cost = {("surname",): 100, ("surname","dob"): 10, ("surname","postcode"): 10, ("surname","city"): 10}
    assert sum(cost[tuple(p.fields)] for p in selected) <= 25

def test_select_always_emits_a_name_bearing_pass():
    # budget too tight for even the cheapest name-bearing pass -> anchor override
    name = _mkpass(["surname"])
    pool = [(name, 1000, {1, 2, 3})]
    selected = _select_passes_within_budget(pool, budget=10, name_fields={"surname"})
    assert selected, "must never return an empty config"
    assert any("surname" in p.fields for p in selected)
