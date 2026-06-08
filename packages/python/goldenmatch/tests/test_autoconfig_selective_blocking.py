import polars as pl
import pytest
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
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
