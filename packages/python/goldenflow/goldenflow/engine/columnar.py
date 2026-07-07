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
from goldenflow.transforms._chain import (
    FUSABLE_KERNELS,
    FUSABLE_NULLABLE_KERNELS,
    FUSABLE_PARAM_KERNELS,
)

Column = list  # a column is a plain Python list (str | None)


def columnar_engine_selected() -> bool:
    """True when ``GOLDENFLOW_ENGINE=columnar`` opts into the Polars-free path."""
    return os.environ.get("GOLDENFLOW_ENGINE", "").lower() == "columnar"


# The owned string kernels the columnar path can run natively (no-arg + param).
_OWNED_STRING = FUSABLE_KERNELS | FUSABLE_PARAM_KERNELS
# Owned Option-returning kernels (URL/company/email) — the nullable chain (Phase 3
# wave 2). Accepted only when the native build auto-routes them (``chain_supports_
# nullable``, native-flow 0.20+); a run may mix these with the total kernels.
_OWNED_NULLABLE = FUSABLE_NULLABLE_KERNELS


def _frame_level_blocked(config) -> bool:
    """Frame-level ops (splits/renames/drops/filters/dedup) the columnar path
    doesn't handle yet — any of them forces the Polars engine."""
    return bool(
        config.splits or config.renames or config.drop or config.filters or config.dedup
    )


def _accepted_string(nm) -> frozenset[str]:
    """The owned string kernels this native build accepts: total + parameterized,
    plus the nullable URL/company/email family when it auto-routes them (0.20+)."""
    if hasattr(nm, "chain_supports_nullable"):
        return _OWNED_STRING | _OWNED_NULLABLE
    return _OWNED_STRING


def _spec_string_ready(spec, accepted: frozenset[str]) -> bool:
    for op_raw in spec.ops:
        name, _params = parse_transform_name(op_raw)
        if name not in accepted or get_transform(name) is None:
            return False
    return True


def _numeric_inmem_ok(nm) -> bool:
    """The in-memory Column path can run a numeric spec when the kernel exposes the
    shape probe AND the Column egresses a raw numeric array (``apply_numeric``,
    native-flow 0.23+). Skew-safe: an older wheel lacks it -> numeric declines to
    Polars in-memory, no hard error."""
    col_cls = getattr(nm, "Column", None)
    return hasattr(nm, "columnar_numeric_ready") and col_cls is not None and hasattr(
        col_cls, "apply_numeric"
    )


def config_is_columnar_ready(config) -> bool:
    """A config runs on the IN-MEMORY columnar engine iff EVERY spec is an owned
    string run (total fusable, or — when supported — a nullable URL/company/email
    kernel) OR — Phase 3 wave 3d — a valid NUMERIC shape (``string* parser f64*``,
    via the native ``columnar_numeric_ready`` probe + ``Column.apply_numeric``), and
    there are no frame-level ops. Otherwise the caller uses the Polars engine —
    correctness first; coverage grows over the phases."""
    if _frame_level_blocked(config) or not config.transforms:
        return False
    nm = native_module()
    if nm is None or not hasattr(nm, "apply_chain_str_list"):
        return False
    accepted = _accepted_string(nm)
    numeric_ok = _numeric_inmem_ok(nm)
    for spec in config.transforms:
        if _spec_string_ready(spec, accepted):
            continue
        ops_spec = [(n, list(p)) for n, p in (parse_transform_name(o) for o in spec.ops)]
        if numeric_ok and nm.columnar_numeric_ready(ops_spec):
            continue
        return False
    return True


def columnar_file_ready(config) -> bool:
    """True when the native whole-file CSV path can run this config: no frame-level
    ops, and every spec is either an owned-string run OR — Phase 3 wave 3b — a valid
    NUMERIC shape (``string* parser f64*``, validated by the native
    ``columnar_numeric_ready`` probe, the single source of truth so host and kernel
    never disagree). When true, a CSV runs read->transform->write entirely in Rust —
    no ``pl.DataFrame``, no Polars, no pyarrow."""
    if _frame_level_blocked(config) or not config.transforms:
        return False
    nm = native_module()
    if nm is None or not hasattr(nm, "transform_csv") or not hasattr(nm, "apply_chain_str_list"):
        return False
    accepted = _accepted_string(nm)
    numeric_ok = hasattr(nm, "columnar_numeric_ready")
    for spec in config.transforms:
        if _spec_string_ready(spec, accepted):
            continue
        ops_spec = [(n, list(p)) for n, p in (parse_transform_name(o) for o in spec.ops)]
        if numeric_ok and nm.columnar_numeric_ready(ops_spec):
            continue
        return False
    return True


def transform_file(in_path, out_path, config, source: str | None = None) -> Manifest:
    """Transform a CSV ``in_path`` to ``out_path`` entirely on the native substrate
    (Phase 2): one Rust call reads the CSV into owned Arrow string columns, applies
    the owned chain to the configured columns, and writes the CSV back — **no
    ``pl.DataFrame``, no Polars, no pyarrow**. Returns the audit :class:`Manifest`,
    byte-identical to the Polars engine (data + manifest; see the parity contract in
    the eviction design doc). Callers must gate on :func:`columnar_file_ready`."""
    nm = native_module()
    specs = []
    for spec in config.transforms:
        ops = [parse_transform_name(op) for op in spec.ops]  # [(name, params)]
        specs.append((spec.column, [(name, list(params)) for name, params in ops]))
    records = nm.transform_csv(str(in_path), str(out_path), specs)
    manifest = Manifest(source=source or str(in_path))
    for col_name, op_records in records:
        for name, affected, total, before, after in op_records:
            manifest.add_record(TransformRecord(
                column=col_name,
                transform=name,
                affected_rows=int(affected),
                total_rows=int(total),
                sample_before=list(before),
                sample_after=list(after),
            ))
    return manifest


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
    numeric_ok = _numeric_inmem_ok(nm)
    out_series = []
    for spec in config.transforms:
        if spec.column not in df.columns:
            continue
        ops = [parse_transform_name(op) for op in spec.ops]
        ops_spec = [(n, list(p)) for n, p in ops]
        # Numeric spec (string* parser f64*): cast the column to Utf8 (Polars' numeric
        # transforms cast to Utf8 internally, so this matches even a non-string input)
        # and run the numeric plan, egressing the RAW numeric array (Int64/Float64) as
        # a real numeric column. The manifest records come straight from the kernel.
        if numeric_ok and nm.columnar_numeric_ready(ops_spec):
            col = nm.Column.from_arrow(df.select([pl.col(spec.column).cast(pl.Utf8)]))
            num_col, records = col.apply_numeric(ops_spec)
            for name, affected, total, before, after in records:
                manifest.add_record(TransformRecord(
                    column=spec.column,
                    transform=name,
                    affected_rows=int(affected),
                    total_rows=int(total),
                    sample_before=list(before),
                    sample_after=list(after),
                ))
            imported = pl.from_arrow(num_col)
            series = imported.to_series(0) if isinstance(imported, pl.DataFrame) else imported
            out_series.append(series.alias(spec.column))
            continue
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
