"""POST /api/v1/match — target × reference one-to-many workflow."""
from __future__ import annotations

from pathlib import Path


def _add_reference(project: Path) -> None:
    """Drop a reference.csv next to data.csv. Reference set has one record
    that is a near-match to data.csv row 0 ("Sony DSC-T77 Silver")."""
    (project / "reference.csv").write_text(
        "id,name\n100,Sony DSC-T77\n200,Nikon D90\n",
        encoding="utf-8",
    )


def _set_rules(client) -> None:
    client.put("/api/v1/rules", json={
        "threshold": 0.7,
        "matchkeys": [
            {
                "column": "name",
                "scorer": "jaro_winkler",
                "weight": 1.0,
                "transforms": ["lowercase", "strip"],
            }
        ],
    })


def test_match_returns_matched_and_unmatched(client, sample_project: Path):
    _add_reference(sample_project)
    _set_rules(client)
    body = client.post("/api/v1/match", json={
        "reference_path": "reference.csv",
        "target_path": "data.csv",
    }).json()
    assert "stats" in body
    assert body["stats"]["target_total"] == 3
    assert body["stats"]["reference_total"] == 2
    # At least one Sony target should match the Sony reference.
    assert body["stats"]["matched_pairs"] >= 1
    # Matched rows carry the target_/ref_ projection plus engine metadata.
    if body["matched"]:
        row = body["matched"][0]
        assert "__target_row_id__" in row
        assert "__ref_row_id__" in row
        assert "__match_score__" in row


def test_match_400_on_missing_reference(client):
    _set_rules(client)
    resp = client.post("/api/v1/match", json={
        "reference_path": "does_not_exist.csv",
    })
    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"].lower()


def test_match_400_on_path_traversal(client, sample_project: Path):
    _set_rules(client)
    resp = client.post("/api/v1/match", json={
        "reference_path": "../../../etc/passwd",
    })
    assert resp.status_code == 400
    assert "escapes" in resp.json()["detail"].lower()


def test_match_caps_returned_rows(client, sample_project: Path):
    """Result rows are capped at ROW_CAP and the response flags truncation.

    With a 3-row dataset we can't actually exercise the cap, but we can
    verify the truncation flags are wired and default false on small data.
    """
    _add_reference(sample_project)
    _set_rules(client)
    body = client.post("/api/v1/match", json={"reference_path": "reference.csv"}).json()
    assert body["row_cap"] == 500
    assert body["matched_truncated"] is False
    assert body["unmatched_truncated"] is False
