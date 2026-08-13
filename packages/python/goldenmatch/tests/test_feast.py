"""Feast (feature-store) dialect: parse + certify + emit, and the front-door wiring.

The feature-store wedge: a `FeatureView` is keyed on an `Entity`'s `join_keys`, and
a duplicated join key fans out every aggregated feature. These lock the parse of the
declarative repo, the bridge to wedge A (`certify_feast_feature_views`), the
crosswalk emit (wedge B), and that `certify_semantic_model` auto-detects "feast".
"""
from __future__ import annotations

from types import SimpleNamespace

from goldenmatch.semantic.certify import certify_semantic_model, detect_dialect
from goldenmatch.semantic.feast import (
    FeastEntity,
    FeastFeatureView,
    FeastRepo,
    certify_feast_feature_views,
    emit_feast_from_crosswalk,
    emit_feast_yaml,
    feast_join_keys,
    parse_feast_models,
    parse_feast_objects,
)

# A small declarative Feast repo: a customer entity keyed on customer_id, and a
# feature view serving two features against it.
_REPO_DOC = {
    "entities": [
        {"name": "customer", "join_keys": ["customer_id"], "value_type": "STRING"},
    ],
    "feature_views": [
        {
            "name": "customer_stats",
            "entities": ["customer"],
            "features": ["lifetime_value", "n_orders"],
            "source": "customer_stats_source",
        },
    ],
}


def test_parse_reads_entities_and_feature_views():
    repo = parse_feast_models(_REPO_DOC)
    assert [e.name for e in repo.entities] == ["customer"]
    assert repo.entity_by_name("customer").join_keys == ["customer_id"]
    fv = repo.feature_views[0]
    assert fv.name == "customer_stats"
    assert fv.entities == ["customer"]
    assert fv.features == ["lifetime_value", "n_orders"]
    assert fv.source == "customer_stats_source"


def test_entity_without_join_keys_defaults_to_its_name():
    repo = parse_feast_models({"entities": [{"name": "driver"}], "feature_views": []})
    assert repo.entity_by_name("driver").join_keys == ["driver"]


def test_legacy_singular_join_key_is_normalized():
    repo = parse_feast_models(
        {"entities": [{"name": "customer", "join_key": "customer_id"}], "feature_views": []}
    )
    assert repo.entity_by_name("customer").join_keys == ["customer_id"]


def test_features_read_from_schema_kwarg():
    # Feast renamed `features=` -> `schema=[Field(...)]`; accept either.
    repo = parse_feast_models({
        "entities": [{"name": "c", "join_keys": ["id"]}],
        "feature_views": [{"name": "v", "entities": ["c"], "schema": [{"name": "amt"}, {"name": "qty"}]}],
    })
    assert repo.feature_views[0].features == ["amt", "qty"]


def test_feast_join_keys_resolves_entity_to_join_keys():
    jk = feast_join_keys(parse_feast_models(_REPO_DOC))
    assert jk == [{
        "feature_view": "customer_stats",
        "entity": "customer",
        "key": ["customer_id"],
        "features": ["lifetime_value", "n_orders"],
        "source": "customer_stats_source",
    }]


def test_parse_feast_objects_duck_types_sdk_objects():
    # Emulate feast SDK objects (store.list_entities() / list_feature_views()).
    ent = SimpleNamespace(name="customer", join_keys=["customer_id"], value_type="STRING", description="")
    src = SimpleNamespace(name="customer_stats_source")
    fv = SimpleNamespace(
        name="customer_stats", entities=["customer"],
        features=[SimpleNamespace(name="lifetime_value"), SimpleNamespace(name="n_orders")],
        batch_source=src, tags=None,
    )
    repo = parse_feast_objects([ent], [fv])
    assert repo.entity_by_name("customer").join_keys == ["customer_id"]
    assert repo.feature_views[0].features == ["lifetime_value", "n_orders"]
    assert repo.feature_views[0].source == "customer_stats_source"


def test_certify_clean_join_key_is_trustworthy():
    frames = {"customer_stats": {
        "customer_id": ["a", "b", "c"],
        "lifetime_value": [10, 20, 30],
        "n_orders": [1, 2, 3],
    }}
    reps = certify_feast_feature_views(parse_feast_models(_REPO_DOC), frames)
    assert len(reps) == 1
    cert = reps[0]["certificate"]
    assert reps[0]["key"] == ["customer_id"]
    assert cert.is_unique_at_grain
    assert cert.is_trustworthy()
    assert cert.max_fan_out == 1.0


def test_certify_duplicated_join_key_fans_out_features():
    # customer 'a' appears twice -> a sum(lifetime_value) double-counts it.
    frames = {"customer_stats": {
        "customer_id": ["a", "a", "b"],
        "lifetime_value": [10, 10, 5],
        "n_orders": [1, 1, 2],
    }}
    reps = certify_feast_feature_views(parse_feast_models(_REPO_DOC), frames)
    cert = reps[0]["certificate"]
    assert not cert.is_unique_at_grain
    assert cert.duplicate_key_groups == 1
    assert cert.max_fan_out == 2.0
    # sum over raw rows (10+10+5=25) vs one-per-entity (10+5=15) -> 25/15.
    assert cert.measure_fan_out["lifetime_value"] == 25 / 15


def test_certify_semantic_model_auto_detects_feast():
    assert detect_dialect(_REPO_DOC) == "feast"
    frames = {"customer_stats": {
        "customer_id": ["a", "a", "b"],
        "lifetime_value": [10, 10, 5],
        "n_orders": [1, 1, 2],
    }}
    report = certify_semantic_model(_REPO_DOC, frames)
    assert report.dialect == "feast"
    assert report.n_certified == 1
    e = report.entries[0]
    assert e.target == "customer_stats"
    assert e.context == "entity customer"
    assert not report.all_trustworthy  # the duplicated key is caught


def test_feature_view_without_a_frame_is_skipped():
    reps = certify_feast_feature_views(parse_feast_models(_REPO_DOC), {})
    assert reps == []


def test_emit_from_crosswalk_round_trips():
    crosswalk = SimpleNamespace(
        resolved_key="resolved_entity_id", n_records=100, n_entities=60,
        reduction_ratio=0.4, source="customer_stats",
    )
    yaml_text = emit_feast_from_crosswalk(
        crosswalk, feature_view="customer_stats_resolved",
        features=["lifetime_value"], certificate=None,
    )
    repo = parse_feast_models(yaml_text)
    # the emitted entity declares the resolved key as its join key
    ent = repo.entities[0]
    assert ent.join_keys == ["resolved_entity_id"]
    fv = repo.feature_views[0]
    assert fv.name == "customer_stats_resolved"
    assert fv.entities == [ent.name]
    assert fv.features == ["lifetime_value"]
    assert fv.tags["goldenmatch"]["resolved_key"] == "resolved_entity_id"


def test_emit_feast_yaml_round_trips():
    repo = FeastRepo(
        entities=[FeastEntity(name="customer", join_keys=["customer_id"])],
        feature_views=[FeastFeatureView(
            name="v", entities=["customer"], features=["amt"], source="v_src"
        )],
    )
    back = parse_feast_models(emit_feast_yaml(repo))
    assert back.entities[0].join_keys == ["customer_id"]
    assert back.feature_views[0].features == ["amt"]
    assert back.feature_views[0].source == "v_src"


def test_semantic_field_roles_treats_features_as_measures():
    from goldenmatch.semantic.blocking import semantic_field_roles

    roles = semantic_field_roles(_REPO_DOC)
    assert "customer_id" in roles.keys
    # a feature value is never identity evidence
    assert "lifetime_value" in roles.measures
    assert "n_orders" in roles.measures
