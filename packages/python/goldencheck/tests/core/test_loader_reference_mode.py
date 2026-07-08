import pytest
from goldencheck.core import _native_loader as L


def test_auto_uses_native_wherever_symbol_exists():
    """Under the reference-mode flip, `auto` no longer consults an allow-list."""
    assert not hasattr(L, "_GATED_ON")  # allow-list is gone


@pytest.mark.skipif(not L.native_available(), reason="native ext not built")
def test_approximate_fd_requires_both_symbols(monkeypatch):
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "auto")
    # approximate_fd needs BOTH discover_approximate_fds AND fd_violation_rows.
    assert L.native_enabled("approximate_fd") is True


def test_native_disabled_env_forces_python(monkeypatch):
    """mode==0 still forces the Python path (unchanged)."""
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "0")
    assert L.native_enabled("benford") is False
