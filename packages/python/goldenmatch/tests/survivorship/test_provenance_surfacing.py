"""Phase A: GroupProvenance surfacing through the survivorship paths.

resolve_cluster computes a rich ClusterProvenance (groups + per-field
condition/validator info). These tests pin that the rich prov flows through
the batch survivorship branch, the golden_records_to_provenance adapter, and
build_golden_record_with_provenance instead of being discarded or lossily
reconstructed.
"""
import polars as pl
from goldenmatch.config.schemas import GoldenGroupRule, GoldenRulesConfig
from goldenmatch.core.survivorship.conditions import build_resolution_order
from goldenmatch.core.survivorship.resolve import resolve_cluster


def _addr_rules():
    return GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", columns=["street", "city", "zip"])],
    )


def _cluster_df():
    return pl.DataFrame({
        "__cluster_id__": [5, 5],
        "__row_id__": [10, 11],
        "street": ["1 Main St", "1 Main"],
        "city": ["LA", "LA"],
        "zip": [None, "90001"],
    })


def test_resolve_cluster_stamps_cluster_id():
    rules = _addr_rules()
    order = build_resolution_order(rules.field_rules, rules.field_groups, ["street", "city", "zip"])
    _, prov = resolve_cluster(_cluster_df(), rules, order, provenance=True, cluster_id=5)
    assert prov is not None
    assert prov.cluster_id == 5
    assert len(prov.groups) == 1
    assert prov.groups[0].name == "addr"
