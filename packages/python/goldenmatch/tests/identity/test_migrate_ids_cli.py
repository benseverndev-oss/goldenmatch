from goldenmatch.cli.identity import identity_app
from goldenmatch.identity import IdentityStore
from typer.testing import CliRunner

from tests.identity.test_migrate_ids import _seed_legacy_record  # reuse helper

runner = CliRunner()


def test_migrate_ids_dry_run(tmp_path):
    db = tmp_path / "id.db"
    store = IdentityStore(backend="sqlite", path=str(db))
    _seed_legacy_record(store, "acme", {"name": "Ann"}, "ent-1")
    store.close()
    res = runner.invoke(identity_app, ["migrate-ids", "--path", str(db), "--dry-run"])
    assert res.exit_code == 0
    assert "rewritten" in res.stdout.lower()


def test_migrate_ids_runs(tmp_path):
    db = tmp_path / "id.db"
    store = IdentityStore(backend="sqlite", path=str(db))
    rid = _seed_legacy_record(store, "acme", {"name": "Ann"}, "ent-1")
    store.close()
    res = runner.invoke(identity_app, ["migrate-ids", "--path", str(db)])
    assert res.exit_code == 0
    store2 = IdentityStore(backend="sqlite", path=str(db))
    assert store2.get_record(rid) is None
