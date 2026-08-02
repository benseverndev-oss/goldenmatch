"""Parity: the `key-integrity-core` kernel (via `certify_structural_json`) vs
the pyarrow group_by reference in `certify_key_integrity`.

The structural key-integrity reduction (group-by uniqueness + fan-out) has two
Python code paths — the pyarrow group_by (`_structural_pyarrow`, the default +
reference the cross-surface golden is generated from) and the shared Rust kernel
(`_structural_native`, opt-in via `GOLDENMATCH_KEY_INTEGRITY_NATIVE`). This locks
native == pyarrow == the committed golden, so the opt-in single-source path can
never drift from the reference.

Skipped when the native module isn't built / doesn't expose the kernel.
"""
from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pytest

native = pytest.importorskip("goldenmatch._native")
if not hasattr(native, "certify_structural_json"):
    pytest.skip(
        "native module loaded but certify_structural_json not exposed -- rebuild",
        allow_module_level=True,
    )

from goldenmatch.semantic.key_integrity import _structural_native, _structural_pyarrow

# The Rust kernel's golden — the same oracle key-integrity-core/tests/golden.rs
# and the TS/WASM surface read — generated from the pyarrow reference. Read it
# DIRECTLY (no copy) so there's no second drift surface.
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


def _table_from_case(inp: dict) -> tuple[pa.Table, list[str], list[str]]:
    """Reconstruct the pyarrow table + group/measure column names for a golden case."""
    group_columns = [f"g{i}" for i in range(len(inp["group_columns"]))]
    measure_names = [m["name"] for m in inp["measures"]]
    columns: dict[str, list] = {}
    for name, values in zip(group_columns, inp["group_columns"]):
        columns[name] = values
    for m in inp["measures"]:
        columns[m["name"]] = m["values"]
    if not columns:  # zero group columns is not a real certify shape; skip guard upstream
        table = pa.table({"_": [None] * inp["n_rows"]})
    else:
        table = pa.table(columns)
    return table, group_columns, measure_names


@pytest.fixture(scope="module")
def golden_cases() -> list[dict]:
    with open(GOLDEN, encoding="utf-8") as fh:
        return json.load(fh)["cases"]


def test_native_matches_pyarrow_and_golden(golden_cases: list[dict]) -> None:
    for case in golden_cases:
        inp = case["input"]
        if not inp["group_columns"]:
            continue  # certify_key_integrity always has >=1 key column
        table, group_columns, measure_names = _table_from_case(inp)

        pyarrow_out = _structural_pyarrow(
            table, group_columns, measure_names, inp["n_rows"]
        )
        native_out = _structural_native(table, group_columns, measure_names)
        assert native_out is not None, f"native returned None for case {case['name']!r}"

        # native == pyarrow (the two Python code paths agree)
        assert native_out == pyarrow_out, f"native != pyarrow for {case['name']!r}"

        # ...and both == the committed golden (the cross-surface oracle)
        exp = case["expected"]
        assert native_out["n_key_groups"] == exp["n_key_groups"], case["name"]
        assert native_out["duplicate_key_groups"] == exp["duplicate_key_groups"], case["name"]
        assert native_out["max_fan_out"] == exp["max_fan_out"], case["name"]
        assert native_out["is_unique_at_grain"] == exp["is_unique_at_grain"], case["name"]
        assert native_out["measure_fan_out"] == exp["measure_fan_out"], case["name"]
