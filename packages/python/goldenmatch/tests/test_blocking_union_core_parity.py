"""Cross-surface parity for the #1207 strong-identifier blocking union core
(increment 3 — the Python reroute through the shared ``autoconfig-core`` kernel).

Two guarantees:

1. **Golden parity** — the pure-Python mirror (`assemble_strong_id_union_pure` /
   `finalize_strong_id_union_pure`) reproduces the SAME
   ``select_blocking_vectors.json`` the Rust ``tests/golden.rs`` and the TS parity
   test read. So Python == Rust == TS by construction; the native pyo3 shim IS
   the Rust core, so native == Rust too (its own path is exercised by the
   ``native`` CI lane).

2. **Core-path == legacy-path equivalence** — with the core path forced on (via
   the pure mirror; no wheel needed), ``build_blocking`` emits the SAME
   ``BlockingConfig`` as the legacy ``_build_strong_identifier_union`` +
   call-site survivor filter, both on a union-firing dataset and on a dataset
   where the union declines. This proves the reroute is output-identical to the
   path it replaces when the wheel is present.
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest
from goldenmatch.core import autoconfig
from goldenmatch.core.autoconfig import build_blocking, profile_columns
from goldenmatch.core.blocking_union_core import (
    assemble_strong_id_union_pure,
    finalize_strong_id_union_pure,
)

_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "rust"
    / "extensions"
    / "autoconfig-core"
    / "golden"
    / "select_blocking_vectors.json"
)


def _load_fixture() -> dict:
    return json.loads(_FIXTURE.read_text())


@pytest.mark.skipif(not _FIXTURE.exists(), reason="autoconfig-core golden fixture not present")
def test_pure_mirror_reproduces_golden_assemble():
    fx = _load_fixture()
    assert len(fx["assemble"]) >= 6
    for case in fx["assemble"]:
        got = assemble_strong_id_union_pure(case["input"])
        assert got == case["expected"], f"assemble[{case['name']}]"


@pytest.mark.skipif(not _FIXTURE.exists(), reason="autoconfig-core golden fixture not present")
def test_pure_mirror_reproduces_golden_finalize():
    fx = _load_fixture()
    assert len(fx["finalize"]) >= 5
    for case in fx["finalize"]:
        i = case["input"]
        got = finalize_strong_id_union_pure(
            i["passes"], i["coverage"], i["pass_survives"], i["max_safe_block"]
        )
        assert got == case["expected"], f"finalize[{case['name']}]"


def _union_dataset() -> pl.DataFrame:
    """Null-sparse multi-source strong-id shape — no single exact key clears the
    0.20 null ceiling, so ``build_blocking`` reaches the union path."""
    rows = []
    for i in range(12):
        rows.append({
            "first_name": f"First{i}", "last_name": f"Last{i}",
            "member_id": f"MID{i:04d}", "email": None, "city": f"City{i % 5}",
        })
        rows.append({
            "first_name": f"First{i}", "last_name": f"Last{i}",
            "member_id": None, "email": f"user{i}@ex.com", "city": f"City{i % 5}",
        })
    return pl.DataFrame(rows)


def _decline_dataset() -> pl.DataFrame:
    """Bare first/last (classify_by_name -> None) -> the union declines and the
    name fallback is emitted."""
    return pl.DataFrame([
        {"first": "Alice", "last": "Smith", "phone": "5551234", "zip": "10001"},
        {"first": "Alice", "last": "Smyth", "phone": "5551234", "zip": "10001"},
        {"first": "Bob", "last": "Jones", "phone": "5552222", "zip": "10002"},
        {"first": "Bob", "last": "Jones", "phone": "5552222", "zip": "10002"},
        {"first": "Carol", "last": "White", "phone": "5553333", "zip": "10003"},
        {"first": "Dan", "last": "Brown", "phone": "5554444", "zip": "10004"},
        {"first": "Eve", "last": "Green", "phone": "5555555", "zip": "10005"},
    ])


def _blocking_shape(cfg) -> dict:
    """Normalize a BlockingConfig to its union-determining structure."""
    return {
        "strategy": cfg.strategy,
        "keys": [(list(k.fields), list(k.transforms)) for k in (cfg.keys or [])],
        "passes": [(list(p.fields), list(p.transforms)) for p in (cfg.passes or [])],
    }


@pytest.mark.parametrize("make_df", [_union_dataset, _decline_dataset])
def test_core_path_equals_legacy_path(monkeypatch, make_df):
    df = make_df()
    profiles = profile_columns(df)

    # Legacy path (default: no wheel -> union_via_core_enabled() is False).
    legacy = build_blocking(profiles, df, n_rows_full=df.height)

    # Core path forced on. No wheel is present, so assemble_union/finalize_union
    # run the pure-Python mirror — the exact fallback a symbol-less wheel uses.
    monkeypatch.setattr(autoconfig, "union_via_core_enabled", lambda: True)
    core = build_blocking(profiles, df, n_rows_full=df.height)

    assert _blocking_shape(core) == _blocking_shape(legacy)


def test_union_dataset_actually_emits_union():
    """Guard: the union dataset really exercises the union (not some other path),
    so the equivalence test above is meaningful."""
    df = _union_dataset()
    cfg = build_blocking(profile_columns(df), df, n_rows_full=df.height)
    assert cfg.strategy == "multi_pass"
    pass_fields = [list(p.fields) for p in (cfg.passes or [])]
    assert ["member_id"] in pass_fields
    assert ["email"] in pass_fields
