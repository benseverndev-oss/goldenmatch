"""CLI: `goldenmatch identity certify-serving-joins` + `identity emit-catalog`
(the semantic-layer <-> Customer 360 serving surfaces, PR-D)."""
from __future__ import annotations

import json

from goldenmatch.cli.identity import identity_app
from goldenmatch.identity import IdentityNode, IdentityStore, SourceRecord, new_entity_id
from typer.testing import CliRunner

runner = CliRunner()


def _seed(db) -> tuple[str, str]:
    e1, e2 = new_entity_id(), new_entity_id()
    with IdentityStore(path=str(db)) as s:
        s.upsert_identity(IdentityNode(entity_id=e1, dataset="crm", confidence=0.9))
        s.upsert_identity(IdentityNode(entity_id=e2, dataset="crm", confidence=0.9))
        s.upsert_record(SourceRecord("crm:1", "crm", "1", "h1", entity_id=e1, dataset="crm"))
        s.upsert_record(SourceRecord("crm:2", "crm", "2", "h2", entity_id=e1, dataset="crm"))
        s.upsert_record(SourceRecord("crm:3", "crm", "3", "h3", entity_id=e2, dataset="crm"))
    return e1, e2


def test_certify_serving_joins_json(tmp_path):
    db = tmp_path / "id.db"
    _seed(db)
    res = runner.invoke(
        identity_app, ["certify-serving-joins", "--path", str(db), "--dataset", "crm", "--json"]
    )
    assert res.exit_code == 0
    cert = json.loads(res.stdout)
    assert cert["trustworthy"] is True
    assert cert["n_entities"] == 2
    assert cert["n_records"] == 3
    assert cert["record_id"]["duplicate_key_groups"] == 0


def test_certify_serving_joins_table(tmp_path):
    db = tmp_path / "id.db"
    _seed(db)
    res = runner.invoke(identity_app, ["certify-serving-joins", "--path", str(db), "--dataset", "crm"])
    assert res.exit_code == 0
    assert "TRUSTWORTHY" in res.stdout


def test_emit_catalog_stdout(tmp_path):
    db = tmp_path / "id.db"
    _seed(db)
    res = runner.invoke(
        identity_app,
        ["emit-catalog", "crm", "customer_id", "--path", str(db), "--dataset", "crm"],
    )
    assert res.exit_code == 0
    assert "resolved_entity_id" in res.stdout


def test_emit_catalog_writes_file(tmp_path):
    db = tmp_path / "id.db"
    _seed(db)
    out_file = tmp_path / "catalog.yml"
    res = runner.invoke(
        identity_app,
        ["emit-catalog", "crm", "customer_id", "--path", str(db), "--dataset", "crm",
         "--out", str(out_file)],
    )
    assert res.exit_code == 0
    assert out_file.exists()
    assert "resolved_entity_id" in out_file.read_text()
