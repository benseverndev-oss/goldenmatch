"""Privacy + safety contract for the opt-in product-analytics client.

These tests ARE the privacy guarantee -- if one fails, anonymous/PII-free/opt-in
has regressed. Every property key that can leave the process is asserted against
the allow-list, and the default-off + fail-open behavior is locked.
"""
from __future__ import annotations

import uuid

import pytest
from goldenmatch.core import analytics


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    # Anonymous id lands in a temp HOME, never the real one.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("GOLDENMATCH_ANALYTICS", raising=False)
    monkeypatch.delenv("POSTHOG_API_KEY", raising=False)


def test_off_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr(analytics, "_emit", lambda p: calls.append(p))
    assert analytics.analytics_enabled() is False
    analytics.capture("dedupe_run", {"backend": "bucket"})
    assert calls == []  # nothing emitted without explicit opt-in


def test_enabled_requires_both_flag_and_key(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ANALYTICS", "1")
    assert analytics.analytics_enabled() is False  # flag alone is not enough
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
    assert analytics.analytics_enabled() is True


def test_payload_drops_disallowed_keys():
    # filename / row_count / df / path are NOT on the allow-list.
    p = analytics._build_payload("e", {
        "backend": "bucket", "filename": "customers.csv",
        "row_count": 12345, "first_name": "Smith",
    })["properties"]
    assert p["backend"] == "bucket"
    for leaked in ("filename", "row_count", "first_name"):
        assert leaked not in p


def test_payload_drops_pii_like_values():
    p = analytics._build_payload("e", {
        "backend": "/secret/path/data",   # path-like -> dropped
        "command": "x" * 200,             # over-long -> dropped
        "mode": "win\\path",              # backslash -> dropped
    })["properties"]
    for k in ("backend", "command", "mode"):
        assert k not in p


def test_payload_auto_props_are_present_and_safe():
    payload = analytics._build_payload("dedupe_run", {"backend": "bucket"})
    props = payload["properties"]
    assert props["gm_version"] and props["python_version"] and props["os"]
    assert payload["event"] == "dedupe_run"
    assert payload["distinct_id"]
    # The only string value in user props is the whitelisted backend.
    assert props["backend"] == "bucket"


def test_capture_is_fail_open(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ANALYTICS", "1")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")

    def boom(_payload):
        raise RuntimeError("network down")

    monkeypatch.setattr(analytics, "_emit", boom)
    # Must NOT raise even though the emitter blows up.
    assert analytics.capture("dedupe_run", {"backend": "bucket"}) is None


def test_distinct_id_anonymous_and_stable():
    a = analytics._distinct_id()
    b = analytics._distinct_id()
    assert a == b
    uuid.UUID(a)  # well-formed UUID, not a hostname / username / mac


@pytest.mark.parametrize("n,expected", [
    (50, "<100"), (500, "100-1K"), (5_000, "1K-10K"),
    (50_000, "10K-100K"), (500_000, "100K-1M"), (5_000_000, "1M-10M"),
    (50_000_000, "10M+"),
])
def test_scale_bucket_never_exact(n, expected):
    assert analytics.scale_bucket(n) == expected
