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
