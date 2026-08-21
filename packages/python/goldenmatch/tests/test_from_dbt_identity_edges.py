"""from_dbt: pure shared-key edge / crosswalk models -> identity idioms.

The MVP recognizers target IN-dbt fuzzy matching (window-dedup, GROUP BY,
levenshtein/soundex). But when the probabilistic matcher lives OUTSIDE dbt (e.g.
an external Splink step), dbt's remaining ER models are deterministic edge /
crosswalk self-joins on a shared value -- which the fuzzy-only self-join signal
deliberately skips. These map onto GoldenMatch's identity idioms: an
authoritative id -> deterministic_merge_keys; a soft attribute -> a
RelationshipRule.
"""
from __future__ import annotations

from goldenmatch.config.from_dbt import from_dbt


def _model(name: str, sql: str) -> dict:
    return {
        "resource_type": "model",
        "name": name,
        "unique_id": f"model.proj.{name}",
        "compiled_code": sql,
    }


def _manifest(*models: dict) -> dict:
    return {
        "metadata": {"adapter_type": "snowflake"},
        "nodes": {m["unique_id"]: m for m in models},
    }


def test_guarded_selfjoin_becomes_composite_merge_key():
    # Crosswalk idiom: link records sharing an NPI + a same-name GUARD, no fuzzy
    # predicate. NPI is authoritative -> a GUARDED (composite) deterministic merge
    # key: the guard column is captured, not dropped.
    sql = (
        "select a.id as left_id, b.id as right_id "
        "from persons a join persons b "
        "on a.npi = b.npi and a.last_name = b.last_name "
        "where a.id < b.id"
    )
    conv = from_dbt(_manifest(_model("dedup_npi_link", sql)))
    assert conv.config is not None
    assert conv.config.identity is not None
    assert conv.config.identity.enabled is True
    assert conv.config.identity.deterministic_merge_keys == [["npi", "last_name"]]
    assert conv.coverage.deterministic_merge_keys == 1


def test_phone_edge_selfjoin_becomes_relationship_rule():
    # EMAIL/PHONE edge idiom: link records sharing a soft attribute -> a
    # relationship edge, not a hard merge.
    sql = (
        "select a.id, b.id from contacts a join contacts b "
        "on a.phone_number = b.phone_number where a.id <> b.id"
    )
    conv = from_dbt(_manifest(_model("contact_phone_xref", sql)))
    assert conv.config is not None
    assert conv.config.identity is not None
    rels = conv.config.identity.relationships
    assert rels is not None and len(rels) == 1
    assert rels[0].field == "phone_number"
    assert rels[0].kind == "shares_phone"
    assert conv.coverage.relationship_edges == 1


def test_org_edge_carries_normalize_company_transform():
    sql = (
        "select a.id, b.id from accounts a join accounts b "
        "on a.org_name = b.org_name where a.id < b.id"
    )
    conv = from_dbt(_manifest(_model("org_dedup_xref", sql)))
    assert conv.config is not None and conv.config.identity is not None
    rel = conv.config.identity.relationships[0]
    assert rel.field == "org_name"
    assert rel.kind == "same_org"
    assert rel.transform == "normalize_company"


def test_fuzzy_selfjoin_is_not_treated_as_identity_edge():
    # A self-join PAIRED with a fuzzy predicate stays the existing blocking-key
    # path -- signal 7 must not fire (no identity edges emitted for it).
    sql = (
        "select a.id, b.id from persons a join persons b "
        "on a.npi = b.npi where jaro_winkler_similarity(a.last_name, b.last_name) > 0.9"
    )
    conv = from_dbt(_manifest(_model("fuzzy_dedupe", sql)))
    # config exists (fuzzy field extracted), but no deterministic-merge idiom.
    ident = conv.config.identity if conv.config else None
    assert ident is None or ident.deterministic_merge_keys == []
    assert conv.coverage.deterministic_merge_keys == 0


def test_surrogate_key_selfjoin_is_ignored():
    # A self-join on a non-authoritative surrogate key is not necessarily ER;
    # signal 7 stays silent rather than emit a false identity edge.
    sql = (
        "select a.*, b.val from t a join t b "
        "on a.customer_id = b.customer_id where a.id < b.id"
    )
    conv = from_dbt(_manifest(_model("cust_selfjoin_dedup", sql)))
    ident = conv.config.identity if conv.config else None
    assert ident is None or (
        ident.deterministic_merge_keys == [] and not ident.relationships
    )
