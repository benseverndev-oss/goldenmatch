"""The public `certify_structural_json` JSON boundary — the Python analogue of
the `key-integrity-core` kernel, reused by the DuckDB SQL surface and byte-parity
with the Postgres native surface.

Locks it against the committed `key-integrity-core` golden (the same oracle the
Rust core, the wheel, and the TS/WASM surface are locked to), so every surface's
structural certificate agrees by construction.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from goldenmatch.semantic import certify_structural_json

GOLDEN = (
    Path(__file__).parent.parent
    / ".."
    / ".."
    / "rust"
    / "extensions"
    / "key-integrity-core"
    / "golden"
    / "key_integrity_golden.json"
)


def _cases() -> list[dict]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


def test_matches_golden() -> None:
    for case in _cases():
        out = json.loads(certify_structural_json(json.dumps(case["input"])))
        exp = case["expected"]
        assert out["n_key_groups"] == exp["n_key_groups"], case["name"]
        assert out["duplicate_key_groups"] == exp["duplicate_key_groups"], case["name"]
        assert out["max_fan_out"] == exp["max_fan_out"], case["name"]
        assert out["is_unique_at_grain"] == exp["is_unique_at_grain"], case["name"]
        assert out["measure_fan_out"] == exp["measure_fan_out"], case["name"]
        assert out["n_rows"] == case["input"]["n_rows"], case["name"]


def test_zero_group_columns() -> None:
    # No group columns → an n_rows-length frame with a single (empty-tuple) group.
    out = json.loads(
        certify_structural_json('{"n_rows": 4, "group_columns": [], "measures": []}')
    )
    assert out["n_key_groups"] == 1
    assert out["duplicate_key_groups"] == 1
    assert out["max_fan_out"] == 4.0
    assert out["is_unique_at_grain"] is False


def test_invalid_json_raises() -> None:
    with pytest.raises(ValueError):
        certify_structural_json("not json")


def test_wrong_shape_raises() -> None:
    with pytest.raises(ValueError):
        certify_structural_json('{"n_rows": 3}')  # missing group_columns
    with pytest.raises(ValueError):
        certify_structural_json('{"n_rows": -1, "group_columns": []}')
