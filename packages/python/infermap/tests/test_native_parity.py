"""Parity: the native ``detect_domain`` kernel must produce byte-identical output to
the pure-Python reference (``infermap.detect._detect_core_pure``). This is the gate
that lets it sit in ``_native_loader._GATED_ON`` (run under ``INFERMAP_NATIVE=auto``).

Skips cleanly when the native extension isn't built (pure-Python-only env). The CI
``infermap_native`` lane builds the wheel and runs this un-skipped under
``INFERMAP_NATIVE=1``.

Coverage: confident, tie, 3-way score tie (stable-sort host order), below-min-score,
empty columns -> no_data, all-hint-less -> no_data, multi-token hint, hint longer than
column. ASCII-only fixtures -- the ``str.lower()`` / ``\\s`` Unicode divergence is the
documented parity edge (design spec §6), out of scope here.
"""
from __future__ import annotations

import pytest
from infermap._native_loader import native_available, native_module
from infermap.detect import _detect_core_pure

native_only = pytest.mark.skipif(
    not native_available(), reason="infermap native extension not built"
)

# (columns, domains, min_score)
_CASES = [
    (["provider_npi", "first_name"], [("health", ["provider npi"]), ("fin", ["iban"])], 0.3),
    (["a", "b"], [("x", ["a"]), ("y", ["b"])], 0.3),  # tie
    (["a", "b"], [("x", ["a"]), ("y", ["b"]), ("z", ["a"])], 0.3),  # 3-way tie, host order
    (["a", "b", "c", "d"], [("h", ["a"])], 0.3),  # below_min_score (0.25)
    ([], [("h", ["x"])], 0.3),  # no_data (empty columns)
    (["a"], [("h", [])], 0.3),  # no_data (all hint-less)
    (["patient_id", "provider_npi", "dob"], [("health", ["patient id", "npi"]), ("fin", ["iban"])], 0.3),
    (["a"], [("h", ["a b c"])], 0.3),  # hint longer than column
    (["ORDER_ID", "Sku"], [("ecom", ["order id", "sku"])], 0.3),  # ASCII case-insensitivity
]


@native_only
@pytest.mark.parametrize("columns,domains,min_score", _CASES)
def test_detect_parity(columns, domains, min_score):
    native = tuple(native_module().detect_domain(columns, domains, min_score))
    assert native == _detect_core_pure(columns, domains, min_score)


def test_pure_stands_alone_without_wheel():
    """Box-runnable: the pure reference works regardless of the native ext."""
    assert _detect_core_pure(["a", "b"], [("x", ["a"])], 0.3) == ("x", 0.5, None, 0.0, "confident")
