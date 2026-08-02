"""Tests for the structural key-integrity certifier UDF
(``goldenmatch_certify_structural``).

JSON-in/JSON-out over ``goldenmatch.semantic.certify_structural_json`` -- the
SAME reference the Postgres native ``key-integrity-core`` surface + the wheel
run, so the SQL output must match that reference exactly (the surface is not a
reimplementation) and must match the committed ``key-integrity-core`` golden.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest


@pytest.fixture()
def con():
    c = duckdb.connect()
    # Register only the key-integrity UDF (the full ``register`` pulls in the
    # rest of the goldenmatch DuckDB surface).
    from goldenmatch_duckdb.key_integrity_kernels import register_key_integrity_functions

    register_key_integrity_functions(c)
    return c


# ── pytest guard: goldenmatch.semantic must import for the UDF to register ──

pytest.importorskip("goldenmatch.semantic", reason="goldenmatch not installed")


def _certify(con, payload: dict) -> dict:
    row = con.execute(
        "SELECT goldenmatch_certify_structural(?)", [json.dumps(payload)]
    ).fetchone()
    assert row is not None
    return json.loads(row[0])


class TestPinned:
    def test_unique_key_no_fanout(self, con):
        out = _certify(
            con, {"n_rows": 3, "group_columns": [["e1", "e2", "e3"]], "measures": []}
        )
        assert out["n_key_groups"] == 3
        assert out["duplicate_key_groups"] == 0
        assert out["max_fan_out"] == 1.0
        assert out["is_unique_at_grain"] is True

    def test_duplicated_key_with_measure(self, con):
        out = _certify(
            con,
            {
                "n_rows": 3,
                "group_columns": [["e1", "e1", "e2"]],
                "measures": [{"name": "amt", "values": [10.0, 30.0, 5.0]}],
            },
        )
        assert out["n_key_groups"] == 2
        assert out["duplicate_key_groups"] == 1
        assert out["max_fan_out"] == 2.0
        assert out["is_unique_at_grain"] is False
        # amt fan-out = (10+30+5) / (30 + 5) = 45/35
        assert abs(out["measure_fan_out"]["amt"] - 45.0 / 35.0) < 1e-12

    def test_invalid_json_fails_soft(self, con):
        row = con.execute(
            "SELECT goldenmatch_certify_structural('not json')"
        ).fetchone()
        assert row is not None
        assert "error" in json.loads(row[0])


class TestGoldenParity:
    """The UDF must reproduce the committed key-integrity-core golden — the same
    oracle the Rust core, the wheel, and the TS/WASM surface are locked to."""

    def _golden_cases(self):
        golden = (
            Path(__file__).resolve().parent.parent.parent
            / "key-integrity-core"
            / "golden"
            / "key_integrity_golden.json"
        )
        return json.loads(golden.read_text(encoding="utf-8"))["cases"]

    def test_matches_golden(self, con):
        for case in self._golden_cases():
            inp = case["input"]
            out = _certify(con, inp)
            exp = case["expected"]
            assert out["n_key_groups"] == exp["n_key_groups"], case["name"]
            assert out["duplicate_key_groups"] == exp["duplicate_key_groups"], case["name"]
            assert out["max_fan_out"] == exp["max_fan_out"], case["name"]
            assert out["is_unique_at_grain"] == exp["is_unique_at_grain"], case["name"]
            assert out["measure_fan_out"] == exp["measure_fan_out"], case["name"]


class TestMatchesReference:
    """The UDF must equal calling the Python reference directly (no divergence)."""

    def test_udf_equals_reference(self, con):
        from goldenmatch.semantic import certify_structural_json

        payload = {
            "n_rows": 4,
            "group_columns": [["a", "a", "b", "c"], ["x", "x", "y", "z"]],
            "measures": [{"name": "amt", "values": [1.0, 2.0, 3.0, 4.0]}],
        }
        via_udf = _certify(con, payload)
        via_ref = json.loads(certify_structural_json(json.dumps(payload)))
        assert via_udf == via_ref
