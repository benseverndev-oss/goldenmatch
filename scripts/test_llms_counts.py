"""Gate for the capability-count checker (scripts/check_llms_counts.py).

Two behaviours here are load-bearing and were both, at different times, wrong in a
way that produced a CONFIDENTLY WRONG remediation instruction rather than a
visible failure. That is the class worth pinning:

  * `mcp_tools()` swallowing an ImportError returned None, which skipped the
    package silently AND summed its tools as zero -- so the only symptom was the
    suite total failing short, telling you to edit llms.txt DOWN to the broken
    number. Unintrospectable packages now REFUSE the total.
  * That refusal then had to distinguish "no MCP server" from "MCP server will not
    import", or onboarding a types-only package would break the gate.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_llms_counts as clc

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# mcp_tools(): three-way, not two-way
# --------------------------------------------------------------------------- #
def test_real_package_returns_a_count():
    n = clc.mcp_tools("goldenmatch")
    assert isinstance(n, int) and n > 0


def test_package_without_an_mcp_server_is_absent_not_broken():
    """goldencheck-types ships no MCP server. That is not a failure to introspect.

    Conflating the two is what would break the gate the moment a types-only or
    meta-package joins the roster -- the suite total would be refused forever.
    """
    assert clc.mcp_tools("goldencheck_types") is clc.NO_MCP_SERVER


def test_broken_mcp_import_is_none_not_absent(monkeypatch):
    """A module that EXISTS but whose own imports fail must stay loud."""
    real = clc.importlib.import_module

    def broken(name, *a, **k):
        if name == "goldenanalysis.mcp.server":
            raise ModuleNotFoundError("No module named 'mcp'", name="mcp")
        return real(name, *a, **k)

    monkeypatch.setattr(clc.importlib, "import_module", broken)
    assert clc.mcp_tools("goldenanalysis") is None


def test_unintrospectable_package_refuses_the_suite_total(monkeypatch, capsys):
    """The whole point of the refusal: never emit a total built on partial data."""
    real = clc.importlib.import_module

    def broken(name, *a, **k):
        if name == "goldenanalysis.mcp.server":
            raise ModuleNotFoundError("No module named 'mcp'", name="mcp")
        return real(name, *a, **k)

    monkeypatch.setattr(clc.importlib, "import_module", broken)
    rc = clc.run(write=False)
    out = capsys.readouterr().out
    assert rc == 1
    assert "UNVERIFIED" in out
    assert "suite total REFUSED" in out
    # And it must NOT tell you to edit llms.txt down to the short number.
    assert "do NOT edit llms.txt" in out


# --------------------------------------------------------------------------- #
# --write
# --------------------------------------------------------------------------- #
def test_write_round_trips_injected_drift():
    """--write must repair a stale count to exactly what the code reports."""
    rel = "packages/python/goldenmatch/goldenmatch/llms.txt"
    path = ROOT / rel
    original = path.read_bytes()
    real = clc.mcp_tools("goldenmatch")
    try:
        path.write_bytes(original.replace(f"{real} tools".encode(), b"3 tools"))
        assert clc.run(write=False) == 1, "injected drift was not detected"
        assert clc.run(write=True) == 0, "--write did not repair the drift"
        assert clc.run(write=False) == 0, "tree still stale after --write"
        assert path.read_bytes() == original, "--write did not restore byte-identically"
    finally:
        path.write_bytes(original)


def test_declared_subcount_is_left_alone():
    """The one real per-category sub-count must not be rewritten to the total.

    goldencheck's README annotates its mcp/ source directory with "(7 tools)" inside
    a tree diagram. Before _SUBCOUNT_ALLOW the rule allowed ANY value below the
    total, which is what let a stale SECOND statement of the total hide.
    """
    rel = "packages/python/goldencheck/README.md"
    assert rel in clc._SUBCOUNT_ALLOW
    before = (ROOT / rel).read_bytes()
    assert clc.run(write=True) == 0
    assert (ROOT / rel).read_bytes() == before, "a declared sub-count was rewritten"


def test_subcount_allowlist_markers_still_match():
    """A declared exception whose marker no longer matches silently re-gates a line."""
    for rel, markers in clc._SUBCOUNT_ALLOW.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for marker, reason in markers:
            assert marker in text, f"{rel}: sub-count marker {marker!r} no longer matches"
            assert len(reason) > 20, f"{rel}: sub-count reason is too thin to review"
