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
