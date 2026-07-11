import pytest
from goldencheck.core import _native_loader as L


@pytest.mark.skipif(not L.native_available(), reason="native ext not built")
def test_auto_uses_native_wherever_symbol_exists(monkeypatch):
    """Under the reference-mode flip, `auto` no longer consults an allow-list.

    ``_GATED_ON`` is retained on main as byte-exact sign-off documentation (see
    ``_native_loader.py``), but ``native_enabled`` must return True for a
    component whose native symbol(s) exist even when it is absent from
    ``_GATED_ON`` -- proving `auto` no longer gates on that allow-list."""
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "auto")
    assert "regex" not in L._GATED_ON
    assert L.native_enabled("regex") is True


@pytest.mark.skipif(not L.native_available(), reason="native ext not built")
def test_approximate_fd_requires_both_symbols(monkeypatch):
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "auto")
    # approximate_fd needs BOTH discover_approximate_fds AND fd_violation_rows.
    assert L.native_enabled("approximate_fd") is True


def test_native_disabled_env_forces_python(monkeypatch):
    """mode==0 still forces the Python path (unchanged)."""
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "0")
    assert L.native_enabled("benford") is False
