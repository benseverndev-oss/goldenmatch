"""Columnar (Polars-free) transform execution — Phase 1 of the Polars eviction
(docs/design/2026-07-07-polars-eviction-plan.md).

Applies a config's OWNED string transforms to plain Python-list columns via the
native arrow-free fused chain (``apply_chain_str_list``) with **zero Polars, zero
pyarrow, zero Arrow** — the proof that the owned transform path runs on the
native/Arrow substrate alone. Non-owned / non-string / multi-column transforms are
not handled here yet (Phase 3); a config that needs them declines to the Polars
engine, so behavior is never wrong — only the fully-owned-string path is
Polars-free today.

Enabled with ``GOLDENFLOW_ENGINE=columnar``. Byte-identical to the Polars engine
for the configs it accepts (gated by ``tests/engine/test_columnar_engine.py``).

PERFORMANCE NOTE (measured, honest): this LIST substrate is the correctness
foundation + a zero-dependency fallback, but it is currently ~3.3× SLOWER than the
Polars engine at scale — the cost is the Python-list marshaling
(``Polars→list→Rust→list→Polars``), not the kernels. "Lighter AND faster" (the
project's bar) needs the columns held as **Arrow buffers threaded zero-copy through
the native kernels** (the chosen native/Arrow substrate), which avoids the per-
element Python round-trip entirely — that is Phase 1b, layered on this correctness
proof. The list path stays as the pure-Python fallback for platforms without the
native wheel.
"""
from __future__ import annotations

import os

from goldenflow.core._native_loader import native_module
from goldenflow.engine.manifest import Manifest, TransformRecord
from goldenflow.transforms import get_transform, parse_transform_name
from goldenflow.transforms._chain import FUSABLE_KERNELS, FUSABLE_PARAM_KERNELS

Column = list  # a column is a plain Python list (str | None)


def columnar_engine_selected() -> bool:
    """True when ``GOLDENFLOW_ENGINE=columnar`` opts into the Polars-free path."""
    return os.environ.get("GOLDENFLOW_ENGINE", "").lower() == "columnar"


# The owned string kernels the columnar path can run natively (no-arg + param).
_OWNED_STRING = FUSABLE_KERNELS | FUSABLE_PARAM_KERNELS


def config_is_columnar_ready(config) -> bool:
    """A config runs on the columnar engine iff EVERY op is an owned string kernel
    (fusable) and there are no frame-level ops (splits/renames/drops/filters/dedup)
    that Phase 1 doesn't handle yet. Otherwise the caller uses the Polars engine —
    correctness first; coverage grows over the later phases."""
    if config.splits or config.renames or config.drop or config.filters or config.dedup:
        return False
    if not config.transforms:
        return False
    nm = native_module()
    if nm is None or not hasattr(nm, "apply_chain_str_list"):
        return False
    for spec in config.transforms:
        for op_raw in spec.ops:
            name, _params = parse_transform_name(op_raw)
            if name not in _OWNED_STRING:
                return False
            info = get_transform(name)
            if info is None:
                return False
    return True


def _sample3(col: Column) -> list:
    """First 3 values, null-preserving — mirrors the Polars engine's
    ``series.head(3).cast(Utf8).to_list()`` (a string column casts to itself, nulls
    stay ``None``). The columnar path only runs on string columns, so no coercion."""
    return list(col[:3])


def transform_columns(
    columns: dict[str, Column],
    config,
    source: str = "<dataframe>",
) -> tuple[dict[str, Column], Manifest]:
    """Apply ``config``'s owned string transforms to ``columns`` (dict of Python
    lists) entirely via the native arrow-free chain — no Polars. Returns the new
    columns + the audit manifest, byte-identical to the Polars engine."""
    nm = native_module()
    manifest = Manifest(source=source)
    columns = dict(columns)  # shallow copy; we replace whole columns

    for spec in config.transforms:
        if spec.column not in columns:
            continue
        ops = [parse_transform_name(op) for op in spec.ops]  # [(name, params)]
        col = columns[spec.column]
        total_rows = len(col)
        # One native pass over the whole run (owned string kernels only here).
        new_col, changed = nm.apply_chain_str_list(col, [(n, list(p)) for n, p in ops])
        new_col = list(new_col)
        # Per-op audit: exact affected counts from the kernel + a head(3) replay
        # through the same native chain for before/after samples.
        sample = col[:3]
        for (name, params), n_changed in zip(ops, changed):
            before = _sample3(sample)
            sample = list(nm.apply_chain_str_list(sample, [(name, list(params))])[0])
            after = _sample3(sample)
            manifest.add_record(TransformRecord(
                column=spec.column,
                transform=name,
                affected_rows=int(n_changed),
                total_rows=total_rows,
                sample_before=before,
                sample_after=after,
            ))
        columns[spec.column] = new_col

    return columns, manifest
