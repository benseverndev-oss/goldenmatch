from __future__ import annotations


def test_get_rules_seeds_from_yaml(client):
    body = client.get("/api/v1/rules").json()
    assert body["threshold"] == 0.85
    assert body["matchkeys"][0]["scorer"] == "jaro_winkler"


def test_put_rules_validates(client):
    bad = {"threshold": 0.9, "matchkeys": [
        {"column": "name", "scorer": "not_a_scorer", "weight": 1.0, "transforms": []}
    ]}
    resp = client.put("/api/v1/rules", json=bad)
    assert resp.status_code == 422
    # pydantic surfaces the failing field; model_validator errors land at the
    # matchkey loc with the offending field named in the message body.
    detail = resp.json()["detail"]
    assert any("matchkeys" in str(e["loc"]) for e in detail)
    assert any("scorer" in e.get("msg", "") for e in detail)


def test_put_rules_then_get_returns_edits(client):
    new = {"threshold": 0.7, "matchkeys": [
        {"column": "name", "scorer": "exact", "weight": 1.0, "transforms": ["lowercase"]}
    ]}
    assert client.put("/api/v1/rules", json=new).status_code == 200
    body = client.get("/api/v1/rules").json()
    assert body["threshold"] == 0.7
    assert body["matchkeys"][0]["scorer"] == "exact"


def test_save_rules_writes_yaml_and_backup(client, sample_project):
    new = {"threshold": 0.5, "matchkeys": [
        {"column": "name", "scorer": "exact", "weight": 1.0, "transforms": []}
    ]}
    client.put("/api/v1/rules", json=new)
    resp = client.post("/api/v1/rules/save")
    assert resp.status_code == 200
    yml = (sample_project / "goldenmatch.yml").read_text(encoding="utf-8")
    bak = (sample_project / "goldenmatch.yml.bak").read_text(encoding="utf-8")
    assert "0.5" in yml
    assert "0.85" in bak  # backup keeps old threshold


def test_save_rules_drops_stale_plural_matchkeys_key(sample_project, client):
    """If the on-disk YAML used the plural `matchkeys:` spelling, the save
    path must not leave both keys side by side after rewriting the canonical
    singular `matchkey:`.
    """
    import yaml

    cfg = sample_project / "goldenmatch.yml"
    cfg.write_text(yaml.safe_dump({
        "threshold": 0.6,
        "matchkeys": [{"column": "name", "scorer": "jaro_winkler",
                       "weight": 1.0, "transforms": []}],
        "extra_top_level": "preserve_me",
    }), encoding="utf-8")

    # Re-build the app so it picks up the rewritten config (lazy seed reads it).
    from fastapi.testclient import TestClient
    from goldenmatch.web.app import create_app
    from goldenmatch.web.state import AppState
    fresh = TestClient(create_app(AppState.from_project_dir(sample_project)))

    fresh.put("/api/v1/rules", json={
        "threshold": 0.7,
        "matchkeys": [{"column": "name", "scorer": "exact",
                       "weight": 1.0, "transforms": []}],
    })
    assert fresh.post("/api/v1/rules/save").status_code == 200

    written = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert "matchkeys" not in written  # stale plural key dropped
    assert written["matchkey"][0]["scorer"] == "exact"
    assert written["extra_top_level"] == "preserve_me"
