"""Tests for the hand-rolled dbt -> GoldenMatch converter (``from_dbt``).

Fixture manifests (no live warehouse) exercise each recognized idiom per
dialect, the ER-model identification heuristics, the coverage scorecard, and
the honest ``couldn't extract`` path -- the analogue of the Splink converter's
captured-SQL fixtures.
"""
from __future__ import annotations

import json

import pytest
from goldenmatch.config.from_dbt import (
    DbtConversionError,
    RecognizedSignal,
    _unwrap_column,
    extract_signals,
    from_dbt,
    identify_er_models,
)


def _model(name, compiled="", raw="", uid=None, resource_type="model"):
    return {
        "resource_type": resource_type,
        "name": name,
        "unique_id": uid or f"model.p.{name}",
        "compiled_code": compiled,
        "raw_code": raw,
    }


def _manifest(nodes, adapter="duckdb"):
    return {"metadata": {"adapter_type": adapter}, "nodes": nodes}


# ── _unwrap_column ───────────────────────────────────────────────────────────


def test_unwrap_plain_column():
    base, transforms, unrec = _unwrap_column("a.email")
    assert base == "email"
    assert transforms == []
    assert unrec == []


def test_unwrap_nested_normalizers_apply_order():
    # lower(trim(email)) -> strip then lowercase (inner SQL func applied first)
    base, transforms, unrec = _unwrap_column("lower(trim(a.email))")
    assert base == "email"
    assert transforms == ["strip", "lowercase"]


def test_unwrap_unrecognized_wrap_keeps_base_flags_func():
    base, transforms, unrec = _unwrap_column("initcap(name)")
    assert base == "name"
    assert transforms == []
    assert "initcap" in unrec


def test_unwrap_non_column_expression_returns_none():
    base, _t, _u = _unwrap_column("case when x then y else z end")
    assert base is None


# ── ER-model identification ──────────────────────────────────────────────────


def test_identify_by_name_substring():
    m = _manifest({"model.p.customer_master": _model("customer_master")})
    models = identify_er_models(m)
    assert [x.name for x in models] == ["customer_master"]
    assert any("name contains" in r for r in models[0].reasons)


def test_identify_by_shape_signal_beats_name_only_confidence():
    m = _manifest({
        "model.p.thing": _model(
            "thing",
            compiled="select * from s qualify row_number() over "
            "(partition by email order by x desc) = 1",
        ),
    })
    models = identify_er_models(m)
    assert models[0].confidence >= 0.8


def test_identify_unique_test_hint():
    m = _manifest({
        "model.p.people": _model("people", compiled="select 1"),
        "test.p.u": _model("u", resource_type="test"),
    })
    m["nodes"]["test.p.u"].update(
        {"test_metadata": {"name": "unique"}, "attached_node": "model.p.people",
         "column_name": "ssn"}
    )
    models = identify_er_models(m)
    assert models and models[0].name == "people"
    assert any("unique test" in r for r in models[0].reasons)


def test_non_er_models_not_identified():
    m = _manifest({
        "model.p.orders": _model("orders", compiled="select order_id from raw"),
        "model.p.line_items": _model("line_items", compiled="select 1"),
    })
    assert identify_er_models(m) == []


# ── Idiom extraction ─────────────────────────────────────────────────────────


def test_qualify_rownum_blocking_exact_transform_survivorship():
    node = _model(
        "dim_customers",
        compiled="select * from s qualify row_number() over "
        "(partition by lower(trim(email)) order by updated_at desc) = 1",
    )
    signals = extract_signals(node, "snowflake", is_er=True)
    kinds = {s.kind for s in signals}
    assert {"blocking", "exact_matchkey", "transform", "survivorship"} <= kinds
    block = next(s for s in signals if s.kind == "blocking")
    assert block.columns == ["email"]
    assert block.params["field_transforms"] == {"email": ["strip", "lowercase"]}
    surv = next(s for s in signals if s.kind == "survivorship")
    assert surv.params == {"strategy": "most_recent", "date_column": "updated_at"}


def test_qualify_rownum_asc_order_is_couldnt_extract():
    node = _model(
        "dedup_x",
        compiled="select * from s qualify row_number() over "
        "(partition by k order by seq) = 1",
    )
    signals = extract_signals(node, "duckdb", is_er=True)
    assert not any(s.kind == "survivorship" for s in signals)
    assert any(s.kind == "couldnt_extract" for s in signals)


def test_deduplicate_macro_partition_and_order():
    node = _model(
        "master_products",
        raw='{{ dbt_utils.deduplicate(relation=ref("stg"), '
            'partition_by="sku, upc", order_by="loaded_at desc") }}',
    )
    signals = extract_signals(node, "duckdb", is_er=True)
    block = next(s for s in signals if s.kind == "blocking")
    assert block.columns == ["sku", "upc"]
    surv = next(s for s in signals if s.kind == "survivorship")
    assert surv.params["date_column"] == "loaded_at"


def test_generate_surrogate_key_args():
    node = _model(
        "identity_key",
        raw="select {{ dbt_utils.generate_surrogate_key(['first_name', 'last_name']) }} as id",
    )
    signals = extract_signals(node, "bigquery", is_er=True)
    block = next(s for s in signals if s.kind == "blocking")
    assert block.columns == ["first_name", "last_name"]


def test_fuzzy_jaro_winkler_threshold_direct():
    node = _model(
        "matches",
        compiled="select a.id from t a join t b on a.zip=b.zip where "
        "jaro_winkler_similarity(a.name, b.name) >= 0.9",
    )
    signals = extract_signals(node, "duckdb", is_er=True)
    fuzzy = next(s for s in signals if s.kind == "fuzzy_field")
    assert fuzzy.columns == ["name"]
    assert fuzzy.params["scorer"] == "jaro_winkler"
    assert fuzzy.params["partial_threshold"] == 0.9
    # self-join ON equality becomes a blocking key
    assert any(s.kind == "blocking" and s.columns == ["zip"] for s in signals)


def test_fuzzy_snowflake_jarowinkler_0_100_scaled():
    node = _model(
        "matches",
        compiled="select 1 from t a join t b on a.z=b.z where "
        "jarowinkler_similarity(a.name, b.name) >= 90",
    )
    signals = extract_signals(node, "snowflake", is_er=True)
    fuzzy = next(s for s in signals if s.kind == "fuzzy_field")
    assert fuzzy.params["scorer"] == "jaro_winkler"
    assert fuzzy.params["partial_threshold"] == pytest.approx(0.9)


def test_fuzzy_edit_distance_to_similarity():
    node = _model(
        "matches",
        compiled="select 1 from t a join t b on a.z=b.z where "
        "levenshtein(a.city, b.city) <= 2",
    )
    signals = extract_signals(node, "duckdb", is_er=True)
    fuzzy = next(s for s in signals if s.kind == "fuzzy_field" and s.columns == ["city"])
    assert fuzzy.params["scorer"] == "levenshtein"
    # 1 - 2/10 = 0.8 (assumed-length approximation, from_splink's constant)
    assert fuzzy.params["partial_threshold"] == pytest.approx(0.8)


def test_soundex_equality_phonetic():
    node = _model(
        "matches",
        compiled="select 1 from t a join t b on a.z=b.z where "
        "soundex(a.surname) = soundex(b.surname)",
    )
    signals = extract_signals(node, "duckdb", is_er=True)
    fuzzy = next(s for s in signals if s.kind == "fuzzy_field")
    assert fuzzy.columns == ["surname"]
    assert fuzzy.params["scorer"] == "soundex_match"


def test_cross_column_fuzzy_not_extracted():
    node = _model(
        "matches",
        compiled="select 1 from t a join t b on a.z=b.z where "
        "jaro_winkler_similarity(a.first, b.last) >= 0.9",
    )
    signals = extract_signals(node, "duckdb", is_er=True)
    assert not any(s.kind == "fuzzy_field" for s in signals)


def test_group_by_only_fires_for_er_model():
    node = _model("customer_dim", compiled="select customer_id, max(name) from s group by customer_id")
    # is_er False -> GROUP BY not treated as ER
    assert not any(s.kind == "blocking" for s in extract_signals(node, "duckdb", is_er=False))
    # is_er True -> GROUP BY natural key becomes blocking + exact
    er_signals = extract_signals(node, "duckdb", is_er=True)
    assert any(s.kind == "blocking" and s.columns == ["customer_id"] for s in er_signals)


def test_positional_group_by_is_couldnt_extract():
    node = _model("dim_x", compiled="select 1 from s group by 1, 2")
    signals = extract_signals(node, "duckdb", is_er=True)
    assert any(s.kind == "couldnt_extract" for s in signals)
    assert not any(s.kind == "blocking" for s in signals)


# ── Full conversion ──────────────────────────────────────────────────────────


def test_from_dbt_exact_dedup_story():
    m = _manifest({
        "model.s.dim_customers": _model(
            "dim_customers",
            compiled="select * from s qualify row_number() over "
            "(partition by email order by updated_at desc) = 1",
        ),
    })
    conv = from_dbt(m)
    assert conv.config is not None
    assert conv.coverage.story == "exact-dedup"
    mk = conv.config.matchkeys[0]
    assert mk.type == "exact"
    assert [f.field for f in mk.fields] == ["email"]
    assert conv.config.blocking.strategy == "static"


def test_from_dbt_fuzzy_story_multi_pass_blocking():
    m = _manifest({
        "model.p.matches": _model(
            "customer_matches",
            compiled="select 1 from c a join c b on a.zip=b.zip where "
            "jaro_winkler_similarity(a.name, b.name) >= 0.9",
        ),
        "model.p.master": _model(
            "master_products",
            raw='{{ dbt_utils.deduplicate(relation=ref("s"), partition_by="sku", order_by="t desc") }}',
        ),
    })
    conv = from_dbt(m)
    assert conv.coverage.story == "fuzzy-er"
    types = {mk.type for mk in conv.config.matchkeys}
    assert types == {"exact", "weighted"}
    assert conv.config.blocking.strategy == "multi_pass"


def test_from_dbt_threshold_conflict_keeps_looser():
    m = _manifest({
        "model.p.m1": _model(
            "resolve_a",
            compiled="select 1 from t a join t b on a.z=b.z where "
            "jaro_winkler_similarity(a.name, b.name) >= 0.95",
        ),
        "model.p.m2": _model(
            "resolve_b",
            compiled="select 1 from t a join t b on a.z=b.z where "
            "jaro_winkler_similarity(a.name, b.name) >= 0.85",
        ),
    })
    conv = from_dbt(m)
    fuzzy_mk = next(mk for mk in conv.config.matchkeys if mk.type == "weighted")
    assert fuzzy_mk.threshold == pytest.approx(0.85)
    assert conv.report.has_warnings


def test_from_dbt_non_er_project_returns_none_config():
    m = _manifest({
        "model.j.orders": _model("orders", compiled="select order_id from raw"),
        "model.j.customers": _model("customers", compiled="select * from raw"),
    })
    conv = from_dbt(m)
    assert conv.config is None
    assert conv.coverage.story == "none"
    assert not conv.coverage.has_config
    assert conv.report.has_warnings  # the "no ER logic" warning


def test_from_dbt_unknown_dialect_warns():
    m = _manifest({
        "model.p.dim_x": _model(
            "dim_x",
            compiled="select * from s qualify row_number() over (partition by k) = 1",
        ),
    }, adapter="oracle")
    conv = from_dbt(m)
    assert any(
        f.severity == "warning" and "oracle" in f.message for f in conv.report.findings
    )


def test_from_dbt_phase2_dialects_not_warned():
    # Phase 2 broadened the supported set beyond the DuckDB/Snowflake/BigQuery
    # trio to the ANSI window-dedup + levenshtein/soundex warehouses.
    for adapter in ("postgres", "redshift", "spark", "databricks", "trino", "athena"):
        m = _manifest({
            "model.p.dim_x": _model(
                "dim_x",
                compiled="select * from s qualify row_number() over (partition by k) = 1",
            ),
        }, adapter=adapter)
        conv = from_dbt(m)
        assert not any(
            f.severity == "warning" and "outside the supported" in f.message
            for f in conv.report.findings
        ), adapter


def test_from_dbt_min_confidence_gates_analysis():
    # A name-only match (confidence 0.5) is skipped when the floor is raised.
    m = _manifest({"model.p.xref": _model("xref", compiled="select * from a join b on a.k=b.k")})
    conv_low = from_dbt(m, min_confidence=0.5)
    conv_high = from_dbt(m, min_confidence=0.7)
    assert conv_low.coverage.er_models_analyzed == 1
    assert conv_high.coverage.er_models_analyzed == 0


def test_from_dbt_strict_raises_on_warning():
    m = _manifest({
        "model.p.dim_x": _model("dim_x", compiled="select 1 from s group by 1"),
    }, adapter="redshift")
    with pytest.raises(DbtConversionError):
        from_dbt(m, strict=True)


def test_from_dbt_malformed_manifest_raises():
    with pytest.raises(DbtConversionError):
        from_dbt("this is not a path and not a dict")  # type: ignore[arg-type]


def test_from_dbt_reads_manifest_file(tmp_path):
    m = _manifest({
        "model.s.dim_customers": _model(
            "dim_customers",
            compiled="select * from s qualify row_number() over (partition by email) = 1",
        ),
    })
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(m))
    conv = from_dbt(str(path))
    assert conv.config is not None
    assert conv.coverage.story == "exact-dedup"


def test_couldnt_extract_surfaces_case_partition():
    m = _manifest({
        "model.p.dedup_x": _model(
            "dedup_x",
            compiled="select * from s qualify row_number() over "
            "(partition by case when a then b else c end order by t desc) = 1",
        ),
    })
    conv = from_dbt(m)
    assert conv.coverage.couldnt_extract >= 1
    assert any(
        "couldn't extract" in f.message for f in conv.report.findings if f.severity == "warning"
    )


def _dim_with_columns(cols, adapter="duckdb"):
    node = _model(
        "dim_customers",
        compiled="select * from s qualify row_number() over "
        "(partition by email order by updated_at desc) = 1",
        uid="model.s.dim_customers",
    )
    node["columns"] = {
        c: {"name": c, "index": i} for i, c in enumerate(cols)
    }
    return _manifest({"model.s.dim_customers": node}, adapter=adapter)


def test_survivorship_emitted_from_manifest_columns():
    m = _dim_with_columns(["email", "full_name", "phone", "updated_at"])
    conv = from_dbt(m)
    gr = conv.config.golden_rules
    assert gr is not None
    assert gr.default_strategy == "most_complete"
    # email (blocking key) and updated_at (date column) excluded; rest get rules
    assert set(gr.field_rules) == {"full_name", "phone"}
    rule = gr.field_rules["full_name"]
    assert rule.strategy == "most_recent"
    assert rule.date_column == "updated_at"


def test_survivorship_emitted_from_catalog_columns():
    # manifest carries no columns; catalog.json supplies them.
    node = _model(
        "dim_customers",
        compiled="select * from s qualify row_number() over "
        "(partition by email order by updated_at desc) = 1",
        uid="model.s.dim_customers",
    )
    m = _manifest({"model.s.dim_customers": node})
    catalog = {"nodes": {"model.s.dim_customers": {"columns": {
        "email": {"name": "email", "index": 0},
        "city": {"name": "city", "index": 1},
        "updated_at": {"name": "updated_at", "index": 2},
    }}}}
    conv = from_dbt(m, catalog_path=catalog)
    gr = conv.config.golden_rules
    assert gr is not None
    assert set(gr.field_rules) == {"city"}


def test_survivorship_report_only_without_columns():
    node = _model(
        "dim_customers",
        compiled="select * from s qualify row_number() over "
        "(partition by email order by updated_at desc) = 1",
    )
    conv = from_dbt(_manifest({"model.s.dim_customers": node}))
    assert conv.config.golden_rules is None
    assert any(
        "not auto-wired" in f.message and "survivorship" in f.message
        for f in conv.report.findings
    )


def test_survivorship_conflicting_recency_columns_report_only():
    m = _manifest({
        "model.s.dim_a": {
            "resource_type": "model", "name": "dim_a", "unique_id": "model.s.dim_a",
            "compiled_code": "select * from s qualify row_number() over "
            "(partition by email order by updated_at desc) = 1",
            "raw_code": "",
            "columns": {"email": {"name": "email", "index": 0},
                        "x": {"name": "x", "index": 1},
                        "updated_at": {"name": "updated_at", "index": 2}},
        },
        "model.s.dim_b": {
            "resource_type": "model", "name": "dim_b", "unique_id": "model.s.dim_b",
            "compiled_code": "select * from s qualify row_number() over "
            "(partition by phone order by loaded_at desc) = 1",
            "raw_code": "",
            "columns": {"phone": {"name": "phone", "index": 0},
                        "y": {"name": "y", "index": 1},
                        "loaded_at": {"name": "loaded_at", "index": 2}},
        },
    })
    conv = from_dbt(m)
    assert conv.config.golden_rules is None
    assert any(
        f.severity == "warning" and "recency columns" in f.message
        for f in conv.report.findings
    )


def test_emitted_survivorship_config_runs():
    # The emitted golden_rules must be runnable end to end (not just valid).
    import goldenmatch as gm
    import polars as pl

    m = _dim_with_columns(["email", "full_name", "updated_at"])
    conv = from_dbt(m)
    df = pl.DataFrame({
        "email": ["a@x.com", "a@x.com"],
        "full_name": ["Al B", "Alan B"],
        "updated_at": ["2020-01-01", "2021-06-01"],
    })
    res = gm.dedupe_df(df, config=conv.config)
    golden = res.golden.to_pylist() if res.golden is not None else []
    assert golden and golden[0]["full_name"] == "Alan B"  # most-recent row wins


def test_signals_are_recognized_signal_instances():
    node = _model("dim_x", compiled="select * from s qualify row_number() over (partition by k) = 1")
    signals = extract_signals(node, "duckdb", is_er=True)
    assert all(isinstance(s, RecognizedSignal) for s in signals)
