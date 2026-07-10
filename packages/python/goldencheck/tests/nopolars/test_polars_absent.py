"""GoldenCheck Stage-2 S2.0: goldencheck works with **polars genuinely uninstalled**.

This module imports polars NOWHERE. It is the living proof for the Polars-eviction end
state (P4, where `polars` moves to the `[polars]` extra). Every other polars-free test in
the suite still touches polars somewhere, so none of them can run in a polars-absent
interpreter; this one can.

It is `skipif`'d OUT of the normal suite (where polars IS present), so it is inert there
and only executes in the dedicated `goldencheck_nopolars` CI lane (and any local run where
polars is absent).

NOTE (S2.0): goldencheck has no non-Polars `Column`/`Frame` backend yet (that arrives with
S2.1), so this lane asserts import-survival + a clean decline on the uncovered tail ONLY --
NOT a covered scan. The covered-scan assertions land when S2.1 ships the backend.
"""
from __future__ import annotations

import importlib.util
import sys

import pytest

_HAS_POLARS = importlib.util.find_spec("polars") is not None

pytestmark = pytest.mark.skipif(
    _HAS_POLARS,
    reason="polars-absent proof -- only runs where polars is NOT installed (the S2.0 lane)",
)


def test_import_goldencheck_without_polars() -> None:
    import goldencheck  # must not raise, must not import polars

    assert "polars" not in sys.modules
    # the public entry points survive a polars-absent import
    for name in ("scan_dataframe", "scan_file", "read_file",
                 "functional_dependencies", "Finding", "Severity"):
        assert hasattr(goldencheck, name), name


def test_uncovered_path_raises_clean_error_without_polars() -> None:
    # Touching the lazy proxy fires the deferred `import polars`, which is absent here.
    from goldencheck._polars_lazy import pl

    with pytest.raises(ModuleNotFoundError):
        _ = pl.DataFrame


def test_covered_scan_columns_without_polars() -> None:
    from goldencheck import scan_columns

    findings = scan_columns({
        "pk": list(range(120)),
        "grade": ["A", "B", "C"] * 40,
        "note": [None] * 120,
    })
    checks = sorted({f.check for f in findings})
    # covered structural checks fire; nothing polars-only ran
    assert "uniqueness" in checks      # pk is 100% unique
    assert "cardinality" in checks     # grade is low-cardinality
    assert "nullability" in checks     # note is entirely null
    assert "polars" not in sys.modules


def test_hard_checks_run_without_polars() -> None:
    import pytest
    from goldencheck.core._native_loader import native_enabled
    if not native_enabled("regex"):
        pytest.skip("nopolars lane without native regex kernel; hard checks skip by design")
    from goldencheck import scan_columns

    findings = scan_columns({"email": [f"u{i}@x.com" for i in range(18)] + ["bad", "worse"]})
    checks = {f.check for f in findings}
    assert "format_detection" in checks       # regex ran polars-free
    assert "polars" not in sys.modules
