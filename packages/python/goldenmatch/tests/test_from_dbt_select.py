"""from_dbt --select: scope analysis to the ER models on a real warehouse.

Model identification is fuzzy at warehouse scale (every dim_/xref/unique-tested
staging model looks a little like ER), so from_dbt over-extracts without a
scope. A dbt-style selector (name glob / path: / tag:) restricts analysis to the
models the user asserts are ER, and those bypass the confidence gate.
"""
from __future__ import annotations

from goldenmatch.config.from_dbt import from_dbt


def _model(name: str, sql: str, *, path: str = "", tags: list[str] | None = None) -> dict:
    return {
        "resource_type": "model",
        "name": name,
        "unique_id": f"model.proj.{name}",
        "compiled_code": sql,
        "original_file_path": path or f"models/{name}.sql",
        "tags": tags or [],
    }


def _manifest(*models: dict) -> dict:
    return {"metadata": {"adapter_type": "snowflake"}, "nodes": {m["unique_id"]: m for m in models}}


# A real ER model (npi crosswalk self-join) + noise that the loose heuristics
# would sweep in (a dim_, a unique-tested staging model).
ER_SQL = (
    "select a.id, b.id from persons a join persons b "
    "on a.npi = b.npi and a.last_name = b.last_name where a.id < b.id"
)
NOISE_SQL = "select brand_id, brand_name from raw.brands"


def _manifest_mixed() -> dict:
    return _manifest(
        _model("dedup_shared_npi", ER_SQL, path="models/core/entity_resolution/dedup_shared_npi.sql", tags=["entity_resolution"]),
        _model("dim_brand", NOISE_SQL, path="models/marts/dim_brand.sql"),
        _model("stg_workday__suppliers", "select supplier_id from raw.suppliers", path="models/staging/stg_workday__suppliers.sql"),
    )


def test_without_select_over_extracts():
    conv = from_dbt(_manifest_mixed())
    # dim_brand + stg_* get swept in alongside the real ER model.
    assert conv.coverage.er_models_analyzed >= 2


def test_select_name_glob_scopes_to_er_models():
    conv = from_dbt(_manifest_mixed(), select=["dedup_*"])
    assert conv.coverage.er_models_analyzed == 1
    assert conv.config is not None and conv.config.identity is not None
    assert conv.config.identity.deterministic_merge_keys == [["npi", "last_name"]]


def test_select_by_path():
    conv = from_dbt(_manifest_mixed(), select=["path:*entity_resolution*"])
    assert conv.coverage.er_models_analyzed == 1
    assert conv.config.identity.deterministic_merge_keys == [["npi", "last_name"]]


def test_select_by_tag():
    conv = from_dbt(_manifest_mixed(), select=["tag:entity_resolution"])
    assert conv.coverage.er_models_analyzed == 1


def test_select_is_case_insensitive():
    conv = from_dbt(_manifest_mixed(), select=["DEDUP_*"])
    assert conv.coverage.er_models_analyzed == 1


def test_select_overrides_confidence_gate():
    # A model whose only ER signal is a name hint (confidence 0.5) is still
    # analyzed when explicitly selected, even with a high min_confidence.
    conv = from_dbt(_manifest_mixed(), select=["dedup_*"], min_confidence=0.99)
    assert conv.coverage.er_models_analyzed == 1
