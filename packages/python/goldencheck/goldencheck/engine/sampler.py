"""Smart sampling for large datasets.

Owned deterministic sample (Flip, Stage A3). The default scan path samples an
Arrow table (``ArrowFrame`` / ``pyarrow.Table``) with an evenly-strided,
seed-free index set: ``idx[i] = (i * n) // max_rows`` for ``i in range(max_rows)``.

This replaces the prior Polars PRNG sample (``df.sample(n, seed=42)``). The stride
selection is:

- **deterministic** -- the same rows every run, no RNG state;
- **worker-count independent** -- the index set is a pure function of ``(n,
  max_rows)``, so a distributed / multi-``--workers`` run sees the identical
  sample as a single-box run;
- **order-preserving** -- indices are strictly increasing, so downstream
  order-sensitive checks (sequence/freshness) see rows in original order.

Off-default callers that still hand a ``pl.DataFrame`` (agent preview, baseline
builder, CLI) keep the legacy Polars-backed behaviour -- those surfaces are
gated on the ``[baseline]`` extra in a later Flip stage.
"""
from __future__ import annotations

from typing import Any


def _owned_stride_indices(n: int, max_rows: int) -> list[int]:
    """Evenly-strided, strictly-increasing row indices selecting ``max_rows``
    of ``n`` rows. Pure function of ``(n, max_rows)`` -- no RNG, stable across
    runs and worker counts."""
    return [(i * n) // max_rows for i in range(max_rows)]


def maybe_sample(data: Any, max_rows: int = 100_000) -> Any:
    """Down-sample ``data`` to at most ``max_rows`` rows.

    Accepts an ``ArrowFrame`` / ``pyarrow.Table`` (default scan path -> owned
    deterministic stride sample, returns an ``ArrowFrame``) or a legacy
    ``pl.DataFrame`` (off-default callers -> unchanged Polars-backed behaviour).
    Returns the input unchanged when it already has ``<= max_rows`` rows.
    """
    # Default scan path first: Arrow-native owned deterministic sample. Checked
    # BEFORE any Polars import so the default scan stays polars-free.
    import pyarrow as pa

    from goldencheck.core.frame import ArrowFrame

    if isinstance(data, (ArrowFrame, pa.Table)):
        tbl = data.native if isinstance(data, ArrowFrame) else data
        n = tbl.num_rows
        if n <= max_rows:
            return data if isinstance(data, ArrowFrame) else ArrowFrame(tbl)
        idx = pa.array(_owned_stride_indices(n, max_rows), type=pa.int64())
        return ArrowFrame(tbl.take(idx))

    # Legacy Polars path (off-default callers: agent/baseline/cli). Kept until
    # those surfaces move behind the [baseline] extra.
    try:
        import polars as pl
    except ImportError:
        pl = None
    if pl is not None and isinstance(data, pl.DataFrame):
        if len(data) <= max_rows:
            return data
        return data.sample(n=max_rows, seed=42)

    raise TypeError(f"maybe_sample expects an ArrowFrame, pyarrow.Table, or polars.DataFrame; got {type(data)!r}")
