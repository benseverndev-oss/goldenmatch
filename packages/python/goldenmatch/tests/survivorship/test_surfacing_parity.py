"""Phase A parity gate: no-levers configs stay byte-identical.

When no survivorship lever (field_groups / conditional / validated rules) is
active, the batch builder must NOT carry ``__survivorship_prov__`` and the
adapter must produce provenance with no groups -- the carry-through path is
strictly additive over the plain config.

Phase F2: fail-open + parity tests for NL-render errors in save_lineage and
explain_cluster_nl.
"""
import json

import polars as pl
from goldenmatch.config.schemas import GoldenRulesConfig
from goldenmatch.core.golden import (
    ClusterProvenance,
    GroupProvenance,
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


# ── F2: fail-open tests ───────────────────────────────────────────────────


def _group_prov():
    g = GroupProvenance(
        name="addr",
        columns=["street", "city"],
        strategy="most_complete",
        winner_row_id=7,
        winner_source=None,
        values={"street": "1 Main", "city": "LA"},
        tie=False,
        confidence=1.0,
    )
    return [ClusterProvenance(
        cluster_id=5,
        cluster_quality="strong",
        cluster_confidence=0.9,
        fields={},
        groups=[g],
    )]


def test_save_lineage_failopen_on_render_error(tmp_path, monkeypatch):
    import goldenmatch.core.lineage as lin

    def boom(cp):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(lin, "render_cluster_provenance_nl", boom)
    # save_lineage must still write the golden_records section (structured groups
    # intact), with audit == "" for the cluster -- no crash.
    path = lin.save_lineage([], tmp_path, "run", golden_provenance=_group_prov())
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["golden_records"][0]["groups"][0]["name"] == "addr"   # structured survives
    assert data["golden_records"][0]["audit"] == ""                    # NL degraded to empty


def test_explain_cluster_failopen_on_render_error(monkeypatch):
    import goldenmatch.core.lineage as lin
    from goldenmatch.core.explain import explain_cluster_nl

    def boom(cp):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(lin, "render_cluster_provenance_nl", boom)
    cinfo = {"id": 5, "members": [10, 11], "size": 2}
    df = pl.DataFrame({
        "__row_id__": [10, 11],
        "street": ["a", "b"],
        "city": ["LA", "LA"],
    })
    # Must return the base summary without the Survivorship block -- no crash.
    out = explain_cluster_nl(cinfo, df, [], cluster_provenance=_group_prov()[0])
    assert "Survivorship:" not in out
    assert isinstance(out, str) and len(out) > 0


def test_plain_provenance_lineage_has_no_audit(tmp_path):
    from goldenmatch.core.lineage import save_lineage

    plain = [ClusterProvenance(
        cluster_id=1,
        cluster_quality="strong",
        cluster_confidence=0.0,
        fields={},
        groups=[],
    )]
    path = save_lineage([], tmp_path, "run", golden_provenance=plain)
    rec = json.loads(path.read_text(encoding="utf-8"))["golden_records"][0]
    assert rec.get("audit", "") == ""           # plain cluster -> empty audit (parity)
    assert rec["groups"] == []
