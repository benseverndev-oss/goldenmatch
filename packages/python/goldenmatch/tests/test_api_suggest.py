"""Task 9 (REST): the matching server exposes config-healer suggestions.

`GET /suggest` -> MatchServer.suggest_config() delegates to
goldenmatch.core.suggest.review_config and serializes via the shared
serialize_suggestions, mirroring the MCP review_config tool / A2A skill.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.api.server import MatchServer
from goldenmatch.config.schemas import (
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.suggest import Suggestion, SuggestionsNativeRequired


def _cfg() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="email_exact",
                type="exact",
                fields=[MatchkeyField(field="email")],
            )
        ]
    )


class _StubEngine:
    def __init__(self, data):
        self.data = data


def _df() -> pl.DataFrame:
    return pl.DataFrame({"email": ["a@x.com", "a@x.com", "b@x.com"]})


def test_suggest_serializes_wire_shape(monkeypatch):
    sugs = [
        Suggestion(
            id="lower_threshold",
            kind="threshold",
            target="email_exact",
            current_value=0.9,
            proposed_value=0.8,
            rationale="a dip suggests a lower threshold",
            predicted_effect="recovers borderline matches",
            confidence=0.7,
            patch={"threshold": 0.8},
        )
    ]
    monkeypatch.setattr(
        "goldenmatch.core.suggest.review_config", lambda df, cfg: sugs
    )
    srv = MatchServer(_StubEngine(_df()), _cfg())
    out = srv.suggest_config()
    assert "error" not in out
    assert out["suggestions"] == [
        {
            "id": "lower_threshold",
            "kind": "threshold",
            "target": "email_exact",
            "rationale": "a dip suggests a lower threshold",
            "verified": True,
            "patch": {"threshold": 0.8},
        }
    ]


def test_suggest_native_required_is_graceful(monkeypatch):
    def _raise(df, cfg):
        raise SuggestionsNativeRequired("native wheel absent")

    monkeypatch.setattr("goldenmatch.core.suggest.review_config", _raise)
    srv = MatchServer(_StubEngine(_df()), _cfg())
    out = srv.suggest_config()
    assert out["suggestions"] == []
    assert out["native_required"] is True


def test_suggest_no_data_errors():
    srv = MatchServer(_StubEngine(None), _cfg())
    out = srv.suggest_config()
    assert "error" in out


def test_suggest_other_failure_is_caught(monkeypatch):
    def _boom(df, cfg):
        raise ValueError("kaboom")

    monkeypatch.setattr("goldenmatch.core.suggest.review_config", _boom)
    srv = MatchServer(_StubEngine(_df()), _cfg())
    out = srv.suggest_config()
    assert "error" in out
    assert "kaboom" in out["error"]
