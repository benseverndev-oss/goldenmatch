def test_project_endpoint(client) -> None:
    resp = client.get("/api/v1/project")
    assert resp.status_code == 200
    body = resp.json()
    assert body["config_path"].endswith("goldenmatch.yml")
    assert len(body["runs"]) == 1
    assert body["runs"][0]["run_name"] == "20260101_000000"
    assert body["rules"]["threshold"] == 0.85
    assert body["rules"]["matchkeys"][0]["scorer"] == "jaro_winkler"
