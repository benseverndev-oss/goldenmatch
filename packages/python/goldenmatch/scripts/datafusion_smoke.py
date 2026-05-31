"""Day-1 prove-out for the DataFusion backend spike.

Goal: get ONE block scored end-to-end via DataFusion using a
vectorized Arrow-batch Python UDF that delegates to the existing
``goldenmatch._native.jaro_winkler_similarity`` Rust kernel.

This is NOT a benchmark. It exercises plumbing:
- can we import datafusion alongside the existing stack
- can we register a Python UDF that processes Arrow batches
- can we call into the native pyo3 scorer from the UDF without
  pickling, threading, or marshalling cost beyond per-batch overhead
- does the result set look right on a hand-crafted fixture

Run::

    pip install -e packages/python/goldenmatch[datafusion]
    python packages/python/goldenmatch/scripts/datafusion_smoke.py

Expected output: ~3-5 pair rows, scores in [0.85, 1.0], no errors.
If native isn't built, falls back to per-row jellyfish (slower; the
prove-out still works so the FFI shape is what we're validating).

Spec:
    docs/superpowers/specs/2026-05-30-datafusion-backend-spike-design.md
"""
from __future__ import annotations

import sys

import pyarrow as pa

try:
    import datafusion
except ImportError:
    print(
        "ERROR: datafusion not installed. Install with:\n"
        "    pip install -e packages/python/goldenmatch[datafusion]",
        file=sys.stderr,
    )
    sys.exit(2)


# Try the native kernel first; fall back to jellyfish so the plumbing
# prove-out works even without a built native module. The benchmark
# itself (Day 3) will assert native is loaded.
try:
    from goldenmatch._native import (
        jaro_winkler_similarity as _native_jw,  # type: ignore[import-not-found]
    )
    _IMPL = "native"
except ImportError:
    import jellyfish
    _native_jw = jellyfish.jaro_winkler_similarity
    _IMPL = "jellyfish-fallback"


def _jw_batch(left: pa.Array, right: pa.Array) -> pa.Array:
    """Vectorized scoring UDF. Called once per record batch.

    DataFusion passes two Arrow string arrays; we iterate the
    decoded values, call the per-pair scorer, and return a Float64
    array of the same length. The for-loop is in Python but each
    iteration's WORK is in Rust (native path) -- Python overhead is
    constant per batch, not per pair.

    A future iteration replaces this with a true Rust UDF registered
    via datafusion-python's Rust FFI (spec tier B2).
    """
    lefts = left.to_pylist()
    rights = right.to_pylist()
    scores = [_native_jw(a or "", b or "") for a, b in zip(lefts, rights, strict=True)]
    return pa.array(scores, type=pa.float64())


def main() -> int:
    print(f"datafusion {datafusion.__version__} -- scorer impl: {_IMPL}")

    # Tiny hand-crafted "block": 5 records, all in one block. We'll
    # join the table to itself, filter to (id_a < id_b) to canonicalize
    # pairs, score, and threshold.
    records = pa.table({
        "id": pa.array([1, 2, 3, 4, 5], type=pa.int64()),
        "name": pa.array(
            ["John Smith", "Jon Smith", "Jane Smith", "John Smyth", "Bob Jones"],
            type=pa.string(),
        ),
    })

    ctx = datafusion.SessionContext()
    ctx.register_record_batches("records", [records.to_batches()])

    # Register the vectorized scorer. datafusion-python's UDF wrapper
    # treats Python callables as batch-in/batch-out by default when the
    # input/output types are arrays -- that's the path we want. (If we
    # used .udf(volatility="immutable") with scalar types, it'd be
    # per-row and the FFI cost would dominate.)
    # datafusion 53+ API: input_fields / return_field (DataType is
    # accepted as shorthand for "nullable field, no metadata"). Earlier
    # versions used input_types / return_type.
    score_udf = datafusion.udf(
        _jw_batch,
        input_fields=[pa.string(), pa.string()],
        return_field=pa.float64(),
        volatility="immutable",
    )
    ctx.register_udf(score_udf)

    # SQL prove-out: self-join with id_a < id_b canonicalization,
    # score, threshold at 0.85. Matches the contract of the bucket
    # backend's per-block scoring.
    result = ctx.sql(
        """
        SELECT
            a.id AS id_a,
            b.id AS id_b,
            _jw_batch(a.name, b.name) AS score
        FROM records a
        JOIN records b ON a.id < b.id
        WHERE _jw_batch(a.name, b.name) >= 0.85
        ORDER BY score DESC
        """
    ).to_pandas()

    print(f"\n{len(result)} pair(s) above threshold:")
    print(result.to_string(index=False))

    # Sanity checks: the high-similarity pairs must be present.
    pairs_set = {(int(r.id_a), int(r.id_b)) for r in result.itertuples()}
    expected_present = [
        (1, 2),  # John Smith / Jon Smith     ~0.97
        (1, 4),  # John Smith / John Smyth    ~0.94
        (2, 4),  # Jon Smith  / John Smyth    ~0.93
    ]
    missing = [p for p in expected_present if p not in pairs_set]
    if missing:
        print(f"\nFAIL: expected pairs missing: {missing}", file=sys.stderr)
        return 1

    print(f"\nOK: {len(expected_present)} high-similarity pairs present, "
          f"end-to-end DataFusion->native FFI works.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
