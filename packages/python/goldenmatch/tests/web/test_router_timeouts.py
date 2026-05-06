"""Async routers (run, sensitivity, match, quality, autoconfig) all share
the same ``asyncio.wait_for(executor.run_in_executor(...), timeout=...)``
pattern that maps a TimeoutError to a 408 response. This locks the contract
so a refactor that catches the wrong exception level (or drops the timeout
entirely) gets caught.

Patches the per-router ``RUN_TIMEOUT_S`` (or equivalent) constant to a
near-zero value plus a slow callable so the real engine never runs.
"""
from __future__ import annotations

import time

import pytest


def _seed_match_inputs(client, sample_project):
    """Match needs reference.csv to even reach the executor."""
    (sample_project / "reference.csv").write_text(
        "id,name\n100,Sony DSC-T77\n", encoding="utf-8",
    )
    client.put(
        "/api/v1/rules",
        json={
            "threshold": 0.7,
            "matchkeys": [
                {"column": "name", "scorer": "jaro_winkler",
                 "weight": 1.0, "transforms": []}
            ],
        },
    )


@pytest.mark.parametrize(
    "router_module,timeout_attr,call",
    [
        (
            "goldenmatch.web.routers.match",
            "MATCH_TIMEOUT_S",
            lambda c: c.post(
                "/api/v1/match",
                json={"reference_path": "reference.csv"},
            ),
        ),
        (
            "goldenmatch.web.routers.sensitivity",
            "RUN_TIMEOUT_S",
            lambda c: c.post(
                "/api/v1/sensitivity",
                json={
                    "field": "threshold",
                    "start": 0.5, "stop": 0.6, "step": 0.1,
                    "sample_n": 10,
                },
            ),
        ),
        (
            "goldenmatch.web.routers.quality",
            "QUALITY_TIMEOUT_S",
            lambda c: c.get("/api/v1/quality"),
        ),
    ],
    ids=["match", "sensitivity", "quality"],
)
def test_async_router_408_on_timeout(
    client, sample_project, monkeypatch, router_module, timeout_attr, call,
):
    """Each async router maps engine-side TimeoutError to 408."""
    import importlib

    mod = importlib.import_module(router_module)
    # Drop the timeout to ~zero so the wait_for fires before the executor
    # task can finish. ``asyncio.wait_for`` with timeout=0 raises immediately.
    monkeypatch.setattr(mod, timeout_attr, 0.001)

    _seed_match_inputs(client, sample_project)
    # Seed rules for sensitivity (needs in-memory rules to sweep).
    if router_module.endswith("sensitivity"):
        client.put(
            "/api/v1/rules",
            json={
                "threshold": 0.85,
                "matchkeys": [
                    {"column": "name", "scorer": "jaro_winkler",
                     "weight": 1.0, "transforms": []}
                ],
            },
        )

    # Make the executor's submitted callable slow so wait_for definitely
    # times out. We patch the executor's submit method to wrap with a sleep.
    real_submit = mod._executor.submit

    def slow_submit(fn, *args, **kwargs):
        def wrapped():
            time.sleep(0.5)
            return fn(*args, **kwargs)
        return real_submit(wrapped)

    monkeypatch.setattr(mod._executor, "submit", slow_submit)

    resp = call(client)
    assert resp.status_code == 408, resp.text
    assert "exceeded" in resp.json()["detail"].lower()
