"""GET /api/v1/quality — GoldenCheck scan-only output."""
from __future__ import annotations


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
