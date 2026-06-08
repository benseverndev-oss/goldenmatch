import os

import polars as pl
import pytest
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.autoconfig import (
    _blocking_candidate_budget_k,
    _candidate_blocking_passes,
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
