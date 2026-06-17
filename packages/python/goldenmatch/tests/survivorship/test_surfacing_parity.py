"""Phase A parity gate: no-levers configs stay byte-identical.

When no survivorship lever (field_groups / conditional / validated rules) is
active, the batch builder must NOT carry ``__survivorship_prov__`` and the
adapter must produce provenance with no groups -- the carry-through path is
strictly additive over the plain config.
"""
import polars as pl
from goldenmatch.config.schemas import GoldenRulesConfig
from goldenmatch.core.golden import (
    build_golden_records_batch,
    golden_records_to_provenance,
)


def test_no_levers_byte_identical_provenance():
    df = pl.DataFrame({
        "__cluster_id__": [1, 1], "__row_id__": [1, 2],
        "name": ["Acme", "Acme Inc"], "city": ["LA", "LA"],
    })
    rules = GoldenRulesConfig(default_strategy="most_complete")
    rows = build_golden_records_batch(df, rules, provenance=True)   # NO user_cols arg
    assert all("__survivorship_prov__" not in r for r in rows)
    provs = golden_records_to_provenance(rows, {1: {}}, rules)
    assert provs[0].groups == []
