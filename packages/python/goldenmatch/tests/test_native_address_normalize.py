"""Parity: Polars-native address_normalize chain vs the Python plugin.

The native chain (matchkey._address_normalize_native) must produce the
same canonical output as refdata.addresses.normalize_address. v21 QIS
measurement showed the Python path is 65s of pipeline_prep_transform wall
on a 10M-row address column; this chain is the native replacement.

Lookbehind rewrite: Polars uses Rust's `regex` crate which has no
lookbehind. The original `(?<![A-Za-z0-9])#\\s*(\\d+)` pattern is
rewritten as `(^|[^A-Za-z0-9])#\\s*(\\d+)` with prefix-preserving
replacement. These tests pin the equivalence on representative cases.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core.matchkey import _address_normalize_native, _try_native_chain
from goldenmatch.refdata.addresses import is_available, normalize_address

# Skip the whole module when the address data file isn't loadable -- the
# native chain returns None in that case (caller falls back to Python).
pytestmark = pytest.mark.skipif(
    not is_available(),
    reason="refdata address abbreviations data file not available",
)


REPRESENTATIVE_ADDRESSES = [
    # Canonical USPS variant collapses
    "123 Main Street North",
    "123 Main St N",
    "456 Oak Avenue Southwest",
    "456 Oak Ave SW",
    "789 Pine Boulevard",
    "789 Pine Blvd",
    # Apartment / unit prefix
    "100 Maple Dr #5",
    "100 Maple Drive Apt 5",
    # PO Box variants
    "P.O. Box 1234",
    "P. O. Box 1234",
    "PO Box 1234",
    "POBOX 1234",
    # Comma-separated tokens
    "100 Maple Dr, Apt 5",
    "100, Maple, Dr",
    # Whitespace + punctuation edges
    "  100  Maple   St.  ",
    "100 Maple St.",
    # Mixed case
    "100 MAPLE STREET",
    # Empty + None
    "",
    "    ",
    # No address tokens (passthrough lowercase)
    "just some text",
]


def _via_native(value: str | None) -> str | None:
    """Build and run the native chain on a single value via a 1-row LF."""
    df = pl.DataFrame({"addr": [value]})
    e = _address_normalize_native(pl.col("addr").cast(pl.Utf8))
    if e is None:
        pytest.skip("native chain returned None (refdata not loaded)")
    return df.select(e.alias("out"))["out"][0]


@pytest.mark.parametrize("addr", REPRESENTATIVE_ADDRESSES)
def test_parity_with_python_plugin(addr):
    """Native output must equal `normalize_address` output for each address."""
    expected = normalize_address(addr)
    actual = _via_native(addr)
    assert actual == expected, (
        f"native != python on {addr!r}:\n  python={expected!r}\n  native={actual!r}"
    )


def test_canonical_variant_collapse():
    """Two equivalent address strings must produce the same canonical output
    via both paths."""
    a = "123 Main Street North"
    b = "123 Main St N"
    assert normalize_address(a) == normalize_address(b)
    assert _via_native(a) == _via_native(b)


def test_native_chain_returns_expression():
    """The factory returns a Polars expression (not None) when data is loaded."""
    e = _address_normalize_native(pl.col("addr").cast(pl.Utf8))
    assert e is not None
    assert isinstance(e, pl.Expr)


def test_try_native_chain_recognizes_address_normalize(monkeypatch):
    """_try_native_chain accepts ['address_normalize'] when the env opt-in
    is set. Default is OFF until integration parity is locked down."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE_ADDRESS_NORMALIZE", "1")
    result = _try_native_chain("addr", ["address_normalize"])
    assert result is not None
    assert isinstance(result, pl.Expr)


def test_try_native_chain_handles_address_normalize_combined(monkeypatch):
    """Real-world chain from auto-config: address_normalize + lowercase + strip
    resolves native when opt-in is set."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE_ADDRESS_NORMALIZE", "1")
    result = _try_native_chain("addr", ["address_normalize", "lowercase", "strip"])
    assert result is not None


def test_try_native_chain_address_normalize_default_off():
    """Default behavior (env unset): chain returns None so caller falls back
    to the Python plugin. Preserves pre-this-PR behavior."""
    import os
    # Defensive: ensure env is unset for this test
    saved = os.environ.pop("GOLDENMATCH_NATIVE_ADDRESS_NORMALIZE", None)
    try:
        result = _try_native_chain("addr", ["address_normalize"])
        assert result is None, "default OFF: expected None to fall through to Python"
    finally:
        if saved is not None:
            os.environ["GOLDENMATCH_NATIVE_ADDRESS_NORMALIZE"] = saved


def test_vectorized_batch_matches_per_row():
    """Run the chain on a multi-row batch and verify each row's output equals
    the Python plugin's output."""
    addrs = REPRESENTATIVE_ADDRESSES
    df = pl.DataFrame({"addr": addrs})
    e = _address_normalize_native(pl.col("addr").cast(pl.Utf8))
    if e is None:
        pytest.skip("native chain returned None")
    out_series = df.select(e.alias("out"))["out"]
    for i, addr in enumerate(addrs):
        expected = normalize_address(addr)
        actual = out_series[i]
        assert actual == expected, (
            f"row {i} addr={addr!r}: python={expected!r} native={actual!r}"
        )
