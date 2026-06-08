import polars as pl
import pytest
from goldenmatch.config.schemas import BlockingKeyConfig
from goldenmatch.core.blocker import _build_block_key_expr


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
