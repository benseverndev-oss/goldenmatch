"""Stage A feasibility gate: a Rust-crate ScalarUDF registers into the Python
``datafusion`` SessionContext via datafusion-ffi (PyCapsule).

``pyarrow`` and ``datafusion`` are soft deps (skip if absent), but
``goldenmatch_datafusion_udf`` is a HARD import on purpose: the CI lane builds
that crate before pytest, so an import failure here means the build broke and
MUST surface as a test FAILURE, not a silent skip. This is the loud guard for
the whole DataFusion spine.
"""

import pytest

pa = pytest.importorskip("pyarrow")
datafusion = pytest.importorskip("datafusion")
import goldenmatch_datafusion_udf  # noqa: E402,F401  HARD import (loud guard, no importorskip)


def test_ffi_scalar_udf_registers_and_evaluates():
    from datafusion import SessionContext, udf
    from goldenmatch_datafusion_udf import AddOneUDF

    ctx = SessionContext()
    ctx.register_udf(udf(AddOneUDF()))
    ctx.from_arrow(pa.table({"x": pa.array([1, 2, 3], pa.int64())}), name="t")
    batches = ctx.sql("SELECT add_one(x) AS y FROM t ORDER BY x").collect()
    got = [v for b in batches for v in b.column(0).to_pylist()]
    assert got == [2, 3, 4]
