"""Identity Graph REST endpoints."""
from __future__ import annotations

from pathlib import Path

from goldenmatch.identity import (
    IdentityNode,
    IdentityStore,
    SourceRecord,
    new_entity_id,
)
from goldenmatch.identity.model import EvidenceEdge


def _seed_identity_db(project_root: Path) -> dict[str, str]:
    """Pre-populate an identity db inside the project root."""
    db_dir = project_root / ".goldenmatch"
    db_dir.mkdir(parents=True, exist_ok=True)
    eid1 = new_entity_id()
    eid2 = new_entity_id()
    with IdentityStore(path=str(db_dir / "identity.db")) as s:
        s.upsert_identity(IdentityNode(entity_id=eid1, dataset="d", confidence=0.95))
        s.upsert_identity(IdentityNode(entity_id=eid2, dataset="d", confidence=0.80))
        s.upsert_record(SourceRecord("src:1", "src", "1", "h1", entity_id=eid1, dataset="d"))
        s.upsert_record(SourceRecord("src:2", "src", "2", "h2", entity_id=eid1, dataset="d"))
        s.upsert_record(SourceRecord("src:3", "src", "3", "h3", entity_id=eid2, dataset="d"))
        s.add_edge(EvidenceEdge(
            entity_id=eid1, record_a_id="src:1", record_b_id="src:2",
            score=0.95, matchkey_name="m", run_name="r1", dataset="d",
        ))
    return {"eid1": eid1, "eid2": eid2}


def test_identity_404_when_no_db(client):
    r = client.get("/api/v1/identities")
    assert r.status_code == 404
    assert "Identity graph not initialized" in r.json()["detail"]


def test_identity_list(client, sample_project: Path):
    seeded = _seed_identity_db(sample_project)
    r = client.get("/api/v1/identities")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    assert {i["entity_id"] for i in body["items"]} == {seeded["eid1"], seeded["eid2"]}


def test_identity_get_one(client, sample_project: Path):
    seeded = _seed_identity_db(sample_project)
    r = client.get(f"/api/v1/identities/{seeded['eid1']}")
    assert r.status_code == 200
    body = r.json()
    assert body["entity_id"] == seeded["eid1"]
    assert len(body["records"]) == 2
    assert len(body["edges"]) == 1


def test_identity_get_unknown(client, sample_project: Path):
    _seed_identity_db(sample_project)
    r = client.get("/api/v1/identities/does-not-exist")
    assert r.status_code == 404


def test_identity_by_record(client, sample_project: Path):
    seeded = _seed_identity_db(sample_project)
    r = client.get("/api/v1/identities/by-record/src:1")
    assert r.status_code == 200
    assert r.json()["entity_id"] == seeded["eid1"]


def test_identity_history_endpoint(client, sample_project: Path):
    seeded = _seed_identity_db(sample_project)
    # No events seeded -> empty list, 200
    r = client.get(f"/api/v1/identities/{seeded['eid1']}/history")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_identity_evidence_endpoint(client, sample_project: Path):
    seeded = _seed_identity_db(sample_project)
    r = client.get(f"/api/v1/identities/{seeded['eid1']}/evidence")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1


def test_identity_merge_endpoint(client, sample_project: Path):
    seeded = _seed_identity_db(sample_project)
    r = client.post(
        f"/api/v1/identities/{seeded['eid1']}/merge",
        json={"absorb_entity_id": seeded["eid2"], "reason": "dup"},
    )
    assert r.status_code == 200
    # Eid2 records now point at eid1
    after = client.get(f"/api/v1/identities/{seeded['eid1']}").json()
    assert len(after["records"]) == 3


def test_identity_split_endpoint(client, sample_project: Path):
    seeded = _seed_identity_db(sample_project)
    r = client.post(
        f"/api/v1/identities/{seeded['eid1']}/split",
        json={"record_ids": ["src:2"], "reason": "wrong"},
    )
    assert r.status_code == 200
    out = r.json()
    after = client.get(f"/api/v1/identities/{seeded['eid1']}").json()
    assert len(after["records"]) == 1
    new_view = client.get(f"/api/v1/identities/{out['new_entity_id']}").json()
    assert len(new_view["records"]) == 1


def test_identity_conflicts_endpoint(client, sample_project: Path):
    _seed_identity_db(sample_project)
    r = client.get("/api/v1/identities/conflicts")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_identity_stats(client, sample_project: Path):
    _seed_identity_db(sample_project)
    r = client.get("/api/v1/identities/stats")
    assert r.status_code == 200
    assert r.json()["total"] == 2
