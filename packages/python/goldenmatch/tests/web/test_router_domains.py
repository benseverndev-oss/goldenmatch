"""GET /api/v1/domains — discover available rulebooks; autoconfig accepts override."""
from __future__ import annotations


def test_list_domains_returns_built_in_packs(client):
    body = client.get("/api/v1/domains").json()
    assert isinstance(body, list)
    assert body, "built-in rulebooks should always be discovered"
    names = {d["name"] for d in body}
    # Built-ins shipped with the package.
    assert {"electronics", "people", "healthcare"} <= names
    sample = body[0]
    assert {"name", "signals", "signal_count", "brand_count", "identifier_count"} <= set(sample)
    assert isinstance(sample["signals"], list)


def test_autoconfig_accepts_domain_override(client):
    """Passing ?domain= shouldn't error and should adopt the resulting rules.

    Concrete extraction depends on data shape, so this is a wiring check —
    the route accepts the param, autoconfig runs, and rules update on
    AppState.
    """
    body = client.post("/api/v1/autoconfig?domain=people").json()
    assert "threshold" in body
    assert "matchkeys" in body
    follow = client.get("/api/v1/rules").json()
    assert follow["matchkeys"] == body["matchkeys"]
