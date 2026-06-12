"""Smoke test: the package imports and reports its version."""

from __future__ import annotations


def test_import_and_version() -> None:
    import goldenanalysis

    assert goldenanalysis.__version__ == "0.1.0"
