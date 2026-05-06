"""Workbench probabilistic matchkey rows translate into Fellegi-Sunter MatchkeyConfigs."""
from __future__ import annotations

from goldenmatch.config.schemas import RulesPayload
from goldenmatch.web.preview import _build_config


def test_build_config_probabilistic_type_emits_probabilistic_matchkey():
    rules = RulesPayload(
        threshold=0.85,
        matchkeys=[
            {
                "column": "name",
                "scorer": "jaro_winkler",
                "weight": 1.0,
                "transforms": ["lowercase"],
                "type": "probabilistic",
                "levels": 3,
                "partial_threshold": 0.75,
                "em_iterations": 30,
            },
        ],
    )
    cfg = _build_config(rules)
    mks = cfg.get_matchkeys()
    assert len(mks) == 1
    mk = mks[0]
    assert mk.type == "probabilistic"
    assert mk.em_iterations == 30
    assert mk.fields[0].levels == 3
    assert mk.fields[0].partial_threshold == 0.75


def test_build_config_explicit_exact_type_overrides_scorer_heuristic():
    """Even with a fuzzy scorer, type='exact' produces an exact MatchkeyConfig."""
    rules = RulesPayload(
        threshold=0.85,
        matchkeys=[
            {
                "column": "email",
                "scorer": "jaro_winkler",
                "weight": 1.0,
                "transforms": [],
                "type": "exact",
            },
        ],
    )
    cfg = _build_config(rules)
    mk = cfg.get_matchkeys()[0]
    assert mk.type == "exact"


def test_put_rules_round_trips_probabilistic_fields(client):
    """The workbench-only fields (type, levels, partial_threshold, em_iterations)
    survive PUT → GET so the editor can reload its state without losing tuning."""
    body = client.put("/api/v1/rules", json={
        "threshold": 0.85,
        "matchkeys": [
            {
                "column": "name",
                "scorer": "jaro_winkler",
                "weight": 1.0,
                "transforms": [],
                "type": "probabilistic",
                "levels": 3,
                "partial_threshold": 0.7,
                "em_iterations": 25,
            }
        ],
    }).json()
    mk = body["matchkeys"][0]
    assert mk["type"] == "probabilistic"
    assert mk["levels"] == 3
    assert mk["partial_threshold"] == 0.7
    assert mk["em_iterations"] == 25
