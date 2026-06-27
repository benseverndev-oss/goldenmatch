"""Python cross-surface parity for the shared healer kernel.

Backs the "Python == Rust == TS" claim: loads the SAME golden fixtures the TS
wasm parity test asserts (``packages/typescript/goldenmatch/tests/parity/
fixtures/suggest/*.json``), feeds each fixture ``input`` through the Python
native Arrow ``suggest_config`` kernel, and asserts the result equals the
fixture ``expected``.

The fixtures' ``input`` is the packed five-JSON-string form (the wasm
``suggest_from_json`` shape). The native ``suggest_config`` kernel takes Arrow
RecordBatches, so we rebuild the three batches from the JSON inputs using the
schemas frozen in ``goldenmatch/core/suggest/adapter.py``. The cluster batch's
size/confidence and the scored-pairs id_a/id_b columns are kernel-irrelevant
(the kernel reads quality/oversized + the score column + row count), so dummy
values are safe.

Native-gated: skips when the native ``suggest_config`` symbol is absent (mirror
of ``test_suggest_oracle_smoke.py``'s gate). Runs in the native CI lane.
"""
from __future__ import annotations

# Set BEFORE any goldenmatch import so the native-loader probe can't hit the
# Polars CPU-check WMI hang on Windows.
import os

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import json
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")


def _suggest_config_available() -> bool:
    """True when the native ``suggest_config`` kernel symbol is present."""
    try:
        from goldenmatch.core._native_loader import native_module  # noqa: PLC0415

        nm = native_module()
        return nm is not None and hasattr(nm, "suggest_config")
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _suggest_config_available(),
    reason="native suggest_config kernel unavailable (install goldenmatch[native])",
)

# Anchored at the `packages/` dir (parents[3]) -> the SHARED TS fixtures (the
# cross-surface contract).
_FIXTURE_DIR = (
    Path(__file__).resolve().parents[3]
    / "typescript"
    / "goldenmatch"
    / "tests"
    / "parity"
    / "fixtures"
    / "suggest"
)

_CASES = [
    "empty",
    "lower_threshold",
    "raise_threshold",
    "swap_scorer",
    "add_negative_evidence",
]

# Frozen schemas (mirror goldenmatch/core/suggest/adapter.py).
_SCORED_PAIRS_SCHEMA = pa.schema(
    [
        pa.field("id_a", pa.int64()),
        pa.field("id_b", pa.int64()),
        pa.field("score", pa.float64()),
    ]
)
_CLUSTERS_SCHEMA = pa.schema(
    [
        pa.field("cluster_id", pa.int64()),
        pa.field("size", pa.int64()),
        pa.field("confidence", pa.float64()),
        pa.field("quality", pa.utf8()),
        pa.field("oversized", pa.bool_()),
    ]
)
_COLUMN_SIGNALS_SCHEMA = pa.schema(
    [
        pa.field("field", pa.utf8()),
        pa.field("col_type", pa.utf8()),
        pa.field("scorer", pa.utf8()),
        pa.field("in_blocking", pa.bool_()),
        pa.field("in_negative_evidence", pa.bool_()),
        pa.field("identity_score", pa.float64()),
        pa.field("corruption_score", pa.float64()),
        pa.field("collision_rate", pa.float64()),
        pa.field("cardinality_ratio", pa.float64()),
        pa.field("null_rate", pa.float64()),
        pa.field("variant_rate", pa.float64()),
    ]
)


def _scored_pairs_batch(scored_pairs_json: str) -> "pa.RecordBatch":
    d = json.loads(scored_pairs_json)
    scores = list(d.get("score", []))
    n_pairs = int(d.get("n_pairs", len(scores)))
    # Pad to n_pairs total rows with null scores (the kernel reads non-null
    # scores + total row count; dummy ids are irrelevant).
    padded = scores + [None] * (n_pairs - len(scores))
    return pa.record_batch(
        {
            "id_a": pa.array(list(range(n_pairs)), type=pa.int64()),
            "id_b": pa.array(list(range(n_pairs)), type=pa.int64()),
            "score": pa.array(padded, type=pa.float64()),
        },
        schema=_SCORED_PAIRS_SCHEMA,
    )


def _clusters_batch(clusters_json: str) -> "pa.RecordBatch":
    rows = json.loads(clusters_json)
    return pa.record_batch(
        {
            "cluster_id": pa.array(list(range(len(rows))), type=pa.int64()),
            "size": pa.array([2] * len(rows), type=pa.int64()),
            "confidence": pa.array([0.9] * len(rows), type=pa.float64()),
            "quality": pa.array(
                [str(r.get("quality", "strong")) for r in rows], type=pa.utf8()
            ),
            "oversized": pa.array(
                [bool(r.get("oversized", False)) for r in rows], type=pa.bool_()
            ),
        },
        schema=_CLUSTERS_SCHEMA,
    )


def _column_signals_batch(column_signals_json: str) -> "pa.RecordBatch":
    rows = json.loads(column_signals_json)
    return pa.record_batch(
        {
            "field": pa.array([str(r["field"]) for r in rows], type=pa.utf8()),
            "col_type": pa.array(
                [str(r["col_type"]) for r in rows], type=pa.utf8()
            ),
            "scorer": pa.array([str(r["scorer"]) for r in rows], type=pa.utf8()),
            "in_blocking": pa.array(
                [bool(r["in_blocking"]) for r in rows], type=pa.bool_()
            ),
            "in_negative_evidence": pa.array(
                [bool(r["in_negative_evidence"]) for r in rows], type=pa.bool_()
            ),
            "identity_score": pa.array(
                [float(r["identity_score"]) for r in rows], type=pa.float64()
            ),
            "corruption_score": pa.array(
                [float(r["corruption_score"]) for r in rows], type=pa.float64()
            ),
            "collision_rate": pa.array(
                [float(r["collision_rate"]) for r in rows], type=pa.float64()
            ),
            "cardinality_ratio": pa.array(
                [float(r["cardinality_ratio"]) for r in rows], type=pa.float64()
            ),
            "null_rate": pa.array(
                [float(r["null_rate"]) for r in rows], type=pa.float64()
            ),
            "variant_rate": pa.array(
                [float(r["variant_rate"]) for r in rows], type=pa.float64()
            ),
        },
        schema=_COLUMN_SIGNALS_SCHEMA,
    )


@pytest.mark.parametrize("case", _CASES)
def test_native_suggest_config_matches_golden(case: str) -> None:
    from goldenmatch.core._native_loader import native_module

    nm = native_module()
    assert nm is not None and hasattr(nm, "suggest_config")

    fixture_path = _FIXTURE_DIR / f"{case}.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    inp = fixture["input"]
    expected = fixture["expected"]

    raw = nm.suggest_config(
        _scored_pairs_batch(inp["scored_pairs"]),
        _clusters_batch(inp["clusters"]),
        _column_signals_batch(inp["column_signals"]),
        inp["config"],
        inp["priors"],
    )
    actual = json.loads(raw)
    assert actual == expected
