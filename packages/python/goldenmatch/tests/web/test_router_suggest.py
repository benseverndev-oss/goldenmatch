"""GET /api/v1/suggest — config-healer suggestions for the workbench dataset."""
from __future__ import annotations

from pathlib import Path

import goldenmatch.core.suggest as suggest_mod
from goldenmatch.core.suggest import Suggestion, SuggestionsNativeRequired


def _stub_suggestion() -> Suggestion:
    return Suggestion(
        id="lower_threshold",
        kind="threshold",
        target="matchkeys[0].threshold",
        current_value=0.9,
        proposed_value=0.8,
        rationale="a score-histogram dip suggests a lower threshold",
        predicted_effect="recovers borderline matches",
        confidence=0.7,
        patch={"op": "replace", "path": "matchkeys[0].threshold", "value": 0.8},
    )


def test_suggest_returns_serialized_wire_shape(client, monkeypatch):
    monkeypatch.setattr(
        suggest_mod, "review_config", lambda df, cfg: [_stub_suggestion()],
    )
    body = client.get("/api/v1/suggest").json()
    assert body["suggestions"] == [
        {
            "id": "lower_threshold",
            "kind": "threshold",
            "target": "matchkeys[0].threshold",
            "rationale": "a score-histogram dip suggests a lower threshold",
            "verified": True,
            "patch": {"op": "replace", "path": "matchkeys[0].threshold", "value": 0.8},
        }
    ]


def test_suggest_native_required_is_graceful(client, monkeypatch):
    def _raise(df, cfg):
        raise SuggestionsNativeRequired("install goldenmatch[native]")

    monkeypatch.setattr(suggest_mod, "review_config", _raise)
    body = client.get("/api/v1/suggest").json()
    assert body["suggestions"] == []
    assert body["native_required"] is True


def test_suggest_400_when_data_csv_missing(client, sample_project: Path):
    (sample_project / "data.csv").unlink()
    resp = client.get("/api/v1/suggest")
    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"].lower()
