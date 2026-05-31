"""Canonical record fingerprint (cross-surface stable hash).

Pure-Python reference for the canonicalization spec; the native kernel
(``goldenmatch._native.record_fingerprint``) must match this byte-for-byte.
Spec + rationale: ``docs/design/2026-05-26-stable-record-hash-cabi-plan.md``.

Why this exists: a record's durable identity id is derived from a hash of its
content (``identity/resolve.py::_hash_payload``). The SHA-256 is portable, but
the current ``json.dumps(..., sort_keys=True, default=str)`` *byte formatting*
is not reproducible in Rust / DuckDB / Node, so any non-Python surface deriving
the same record's id would mint a different one and split the identity graph.
One canonical implementation (this spec, called by every surface) fixes that.

Phase 1 (this module): ship the spec + native kernel, default-OFF. Nothing in
the package calls ``record_fingerprint`` yet -- ``native_enabled("hashing")`` is
False under ``GOLDENMATCH_NATIVE=auto`` because "hashing" is not in
``_GATED_ON``. Wiring ``_hash_payload`` to it (and the ``:h1:`` id-scheme
migration) is Phase 2.
"""
from __future__ import annotations

import hashlib
import math
import struct
from typing import Any

from goldenmatch.core._native_loader import native_enabled, native_module

_US = b"\x1f"  # unit separator: between a field name and its value
_RS = b"\x1e"  # record separator: end of one field


def _value_bytes(name: str, value: Any) -> bytes:
    """TAG + value bytes for one value. Type-tagged so int ``1`` != str ``"1"``
    != ``True``. Must match ``hash.rs::append_value`` exactly."""
    if value is None:
        return b"n"
    # bool BEFORE int: in Python `bool` is a subclass of `int`.
    if isinstance(value, bool):
        return b"b" + (b"1" if value else b"0")
    if isinstance(value, int):
        return b"i" + str(value).encode("utf-8")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(
                f"field {name!r}: non-finite float {value} is not canonicalizable"
            )
        norm = 0.0 if value == 0.0 else value  # collapse -0.0 -> 0.0
        return b"f" + struct.pack(">d", norm).hex().encode("ascii")
    if isinstance(value, str):
        return b"s" + value.encode("utf-8")
    if isinstance(value, bytes):
        return b"y" + value
    raise TypeError(
        f"field {name!r}: unsupported value type {type(value).__name__} "
        "(v1 record fingerprint is primitive-only: None/bool/int/float/str/bytes)"
    )


def _fingerprint_py(record: dict[str, Any]) -> str:
    """Pure-Python reference implementation of canonicalization v1."""
    buf = bytearray()
    for name in sorted(k for k in record if not k.startswith("__")):
        buf += name.encode("utf-8")
        buf += _US
        buf += _value_bytes(name, record[name])
        buf += _RS
    return hashlib.sha256(bytes(buf)).hexdigest()


def record_fingerprint(record: dict[str, Any]) -> str:
    """Deterministic, cross-surface-stable SHA-256 fingerprint of a record's
    content fields (``__``-prefixed keys dropped). Uses the native kernel when
    ``native_enabled("hashing")``, else the pure-Python reference. The two
    produce identical output (asserted in tests/test_record_fingerprint.py)."""
    if native_enabled("hashing"):
        return native_module().record_fingerprint(record)
    return _fingerprint_py(record)


def record_fingerprints_batch(records: list[dict[str, Any]]) -> list[str]:
    """Bulk variant of ``record_fingerprint`` -- one hex string per input
    record, in order. Identical per-record semantics; the kernel amortizes
    Python-interpreter overhead across the batch and parallelizes SHA-256 via
    rayon under ``py.allow_threads``. Falls back to a per-record loop when
    the native module isn't loaded or the bulk kernel isn't exposed.

    Realistic benefit only on the identity-resolve hot path
    (``identity/resolve.py:_record_id_candidates``) when ``config.identity.
    enabled`` is True. Decision-gate spec:
    docs/superpowers/specs/2026-05-30-bulk-record-fingerprint-kernel-spec.md
    (local, gitignored)."""
    if native_enabled("hashing"):
        native = native_module()
        bulk = getattr(native, "record_fingerprints_batch", None)
        if bulk is not None:
            return bulk(records)
    # Fallback: per-record loop. Same shape as the existing single-record
    # path, just iterated.
    return [record_fingerprint(r) for r in records]


def record_fingerprints_batch_arrow(records_df):  # pl.DataFrame -> list[str]
    """Arrow-native bulk fingerprints. Reads the DataFrame's columns
    directly as Arrow arrays (zero-copy) and calls the Rust kernel,
    which iterates the buffers in place -- no per-record Python dict
    construction, no per-cell pyo3 marshalling.

    Phase 3 deliverable per the Arrow-native roadmap (#625). Strategic
    load-bearing for DataFusion B2: the kernel signature accepts
    column-name + Arrow array list, the exact shape a PyCapsule
    ScalarUDF would expose.

    Falls back to ``record_fingerprints_batch`` (dict-shaped) when the
    Arrow kernel isn't exposed -- gives older native builds a graceful
    degrade path with identical output values.

    Args:
        records_df: Polars DataFrame whose columns ARE the record
            fields. ``__``-prefixed columns are dropped (same contract
            as the dict kernel). Supported column dtypes: Utf8,
            LargeUtf8, Int64, Float64, Boolean. Null cells map to
            ``FpValue::Null``.

    Returns:
        List of 64-char lowercase hex fingerprint strings, one per
        row in input order.
    """
    if not native_enabled("hashing"):
        # Convert to dicts and use the per-record loop -- same shape
        # the existing record_fingerprints_batch fallback gives.
        return record_fingerprints_batch(records_df.to_dicts())
    native = native_module()
    arrow_fn = getattr(native, "record_fingerprints_batch_arrow", None)
    if arrow_fn is None:
        # Older native build -- delegate to the dict kernel.
        return record_fingerprints_batch(records_df.to_dicts())

    field_names: list[str] = []
    field_arrays: list = []
    for col in records_df.columns:
        if col.startswith("__"):
            continue
        field_names.append(col)
        field_arrays.append(records_df[col].to_arrow())

    arrow_out = arrow_fn(field_names, field_arrays)
    # arrow_out is a PyArrowType<ArrayData> -> LargeStringArray. Convert
    # to a Python list[str] via Polars (handles the Arrow -> Python
    # decoding without per-row overhead beyond the final list build).
    import polars as _pl
    return _pl.from_arrow(arrow_out).to_list()
