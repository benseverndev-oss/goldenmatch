"""Smoke test: the package imports and exports its version."""
from __future__ import annotations


def test_imports_and_version():
    import infermap_bench
    assert infermap_bench.__version__ == "0.1.0"
    assert infermap_bench.MANIFEST_VERSION == 1
    assert infermap_bench.REPORT_VERSION == 1
