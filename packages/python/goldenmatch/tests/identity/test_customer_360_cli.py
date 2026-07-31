"""CLI: `goldenmatch identity 360 <entity_id>` (Customer 360 serving view)."""
from __future__ import annotations

import json

from goldenmatch.cli.identity import identity_app
from goldenmatch.identity import IdentityNode, IdentityStore, SourceRecord, new_entity_id
from typer.testing import CliRunner

runner = CliRunner()


def _seed(db) -> str:
    eid = new_entity_id()
    with IdentityStore(path=str(db)) as s:
        s.upsert_identity(IdentityNode(entity_id=eid, dataset="crm", confidence=0.9))
        s.upsert_record(SourceRecord("crm:1", "crm", "1", "h1", entity_id=eid, dataset="crm",
                                     payload={"name": "Robert Smith"}))
    return eid


def test_identity_360_json(tmp_path):
    db = tmp_path / "id.db"
    eid = _seed(db)
    res = runner.invoke(identity_app, ["360", eid, "--path", str(db), "--json"])
    assert res.exit_code == 0
    page = json.loads(res.stdout)
    assert page["entity_id"] == eid
    assert page["record_count"] == 1


def test_identity_360_table(tmp_path):
    db = tmp_path / "id.db"
    eid = _seed(db)
    res = runner.invoke(identity_app, ["360", eid, "--path", str(db)])
    assert res.exit_code == 0
    assert eid in res.stdout
    assert "records: 1" in res.stdout
    assert "sources: crm" in res.stdout


def test_identity_360_not_found(tmp_path):
    db = tmp_path / "id.db"
    _seed(db)
    res = runner.invoke(identity_app, ["360", "nonexistent", "--path", str(db)])
    assert res.exit_code == 1
