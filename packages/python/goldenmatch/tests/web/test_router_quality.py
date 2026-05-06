"""GET /api/v1/quality — GoldenCheck scan-only output."""
from __future__ import annotations

from pathlib import Path


def test_quality_returns_available_flag_or_findings(client):
    """GoldenCheck is an optional dep. The route must return a coherent
    shape regardless of whether it's installed: `available` flag plus
    summary counts that match the issue list length.
    """
    body = client.get("/api/v1/quality").json()
    assert "available" in body
    assert "issues" in body
    assert "summary" in body
    s = body["summary"]
    assert s["total"] == len(body["issues"])
    assert s["errors"] + s["warnings"] <= s["total"]
    if not body["available"]:
        assert body["issues"] == []
        assert s["total"] == 0


def test_quality_400_when_data_csv_missing(client, sample_project: Path):
    """Removing data.csv → router surfaces the FileNotFoundError as 400."""
    (sample_project / "data.csv").unlink()
    resp = client.get("/api/v1/quality")
    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"].lower()


def test_quality_soft_fails_with_error_field_on_scan_exception(
    client, monkeypatch,
):
    """When _scan_only raises (upstream goldencheck version drift), the
    router catches and returns available=true, issues=[], plus a structured
    `error` field carrying the exception type + message."""
    import goldenmatch.web.routers.quality as quality_router

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated finding-attribute drift")

    monkeypatch.setattr(quality_router, "_scan_only", _boom, raising=False)
    # Force the available branch so we hit the try/except, regardless of
    # whether goldencheck is installed in the test env.
    monkeypatch.setattr(
        quality_router, "_goldencheck_available", lambda: True, raising=False,
    )
    # The names are imported inside _execute_scan via `from ... import`, so
    # we patch the source module too.
    import goldenmatch.core.quality as core_quality
    monkeypatch.setattr(core_quality, "_scan_only", _boom)
    monkeypatch.setattr(core_quality, "_goldencheck_available", lambda: True)

    body = client.get("/api/v1/quality").json()
    assert body["available"] is True
    assert body["issues"] == []
    assert body["summary"]["total"] == 0
    assert "error" in body
    assert "RuntimeError" in body["error"]
    assert "simulated finding-attribute drift" in body["error"]
