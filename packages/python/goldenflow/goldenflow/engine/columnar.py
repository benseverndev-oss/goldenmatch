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

TWO substrates, byte-identical, best-available:
- **Phase 1c — native Arrow `Column` (preferred).** Columns are held as Rust-owned
  Arrow buffers ingested from Polars over the C-Data / PyCapsule interface
  (``__arrow_c_stream__``) — **pyarrow-free, zero-copy** — and the owned fused chain
  runs on them directly (no per-element Python round-trip). This is the "lighter AND
  faster" substrate: the ~35 MB polars + ~40 MB pyarrow drop off the execution path.
- **Phase 1 — Python list (fallback).** Where the native `Column` isn't available,
  the list path (``apply_chain_str_list``) still runs Polars-free but marshals
  ``Polars→list→Rust→list→Polars`` (~3.3× slower — the correctness floor).

Enabled with ``GOLDENFLOW_ENGINE=columnar``. Byte-identical to the Polars engine for
the configs it accepts (gated by ``tests/engine/test_columnar_engine.py``).
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


def transform(df, config, source: str = "<dataframe>"):
    """Apply an owned-string ``config`` to ``df`` (a ``pl.DataFrame``), preferring
    the native Arrow ``Column`` path (zero-copy, pyarrow-free) and falling back to
    the Python-list path. Returns ``(out_df, manifest)`` — byte-identical to the
    Polars engine. The transform EXECUTION is Polars-free; only the pl.DataFrame
    in/out boundary (`select`/`from_arrow`/`with_columns`) still uses Polars
    (Phase 2 replaces it with native I/O)."""
    import polars as pl

    nm = native_module()
    if nm is not None and hasattr(nm, "Column"):
        return _transform_via_columns(df, config, source, nm, pl)
    # Fallback: the Phase 1 list path (marshals, but Polars-free execution).
    names = [s.column for s in config.transforms if s.column in df.columns]
    cols = {c: df[c].to_list() for c in names}
    new_cols, manifest = transform_columns(cols, config, source=source)
    out = df.with_columns([pl.Series(n, v) for n, v in new_cols.items()])
    return out, manifest


def _transform_via_columns(df, config, source, nm, pl):
    """Native Arrow Column path: ingest each transformed column zero-copy over the
    C-Data interface, run the owned chain on the Rust-held Arrow buffer, egress back.
    No list marshaling, no pyarrow."""
    manifest = Manifest(source=source)
    out_series = []
    for spec in config.transforms:
        if spec.column not in df.columns:
            continue
        ops = [parse_transform_name(op) for op in spec.ops]
        ops_spec = [(n, list(p)) for n, p in ops]
        # Zero-copy, pyarrow-free ingest (a 1-column DataFrame -> a struct stream).
        col = nm.Column.from_arrow(df.select([spec.column]))
        total_rows = len(col)
        new_col, changed = col.apply_chain(ops_spec)
        # Per-op audit: exact kernel counts + a cheap 3-row replay for samples.
        sample = df[spec.column].head(3).to_list()
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
        # Egress the Column -> Polars Series (the Column IS an Arrow producer).
        imported = pl.from_arrow(new_col)
        series = imported.to_series(0) if isinstance(imported, pl.DataFrame) else imported
        out_series.append(series.alias(spec.column))
    out = df.with_columns(out_series) if out_series else df
    return out, manifest


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
