"""Tests for the GoldenCheck deep-profiling UDFs (``goldencheck_*``).

Aggregate a column (or a list of columns) into a DuckDB ``LIST`` and get an
index / count structure back. Each UDF reuses the native-gated
``goldencheck.core.kernels`` entry point, so the SQL output must match that
kernel exactly (the surface is not a reimplementation) and must be identical
whether the native ``goldencheck-core`` kernel or the pure-Python fallback runs.
"""
from __future__ import annotations

import importlib

import duckdb
import pytest


@pytest.fixture()
def con():
    c = duckdb.connect()
    # Register only the goldencheck UDFs so the test does not require the whole
    # goldenmatch package (the full ``register`` pulls goldenmatch in).
    from goldenmatch_duckdb.goldencheck_kernels import register_goldencheck_functions

    register_goldencheck_functions(c)
    return c


# ── pytest guard: goldencheck must be importable for these UDFs to register ──

goldencheck = pytest.importorskip("goldencheck", reason="goldencheck not installed")


def _fallback_env(monkeypatch):
    """Force the pure-Python fallback path and reload the gate + kernels."""
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "0")
    import goldencheck.core._native_loader as nl
    import goldencheck.core.kernels as K

    importlib.reload(nl)
    importlib.reload(K)


class TestBenford:
    def test_pinned_histogram(self, con):
        res = con.execute(
            "SELECT goldencheck_benford([1,1,2,11,19,3,100,7,9,9]::DOUBLE[])"
        ).fetchone()[0]
        # 1->{1,1,11,19,100}=5, 2->{2}=1, 3->{3}=1, 7->{7}=1, 9->{9,9}=2
        assert res == [5, 1, 1, 0, 0, 0, 1, 0, 2]

    def test_skips_nonpositive_and_nonfinite(self, con):
        res = con.execute(
            "SELECT goldencheck_benford([0, -5, 4, 4, 4]::DOUBLE[])"
        ).fetchone()[0]
        # 0 and -5 skipped; three 4s -> leading digit 4 (index 3).
        assert res == [0, 0, 0, 3, 0, 0, 0, 0, 0]

    def test_matches_reference(self, con):
        from goldencheck.core.kernels import benford_histogram

        vals = [12.0, 3.3, 1.0, 250.0, 2000.0, 8.0, 8.0, 90.0, 100.0]
        got = con.execute(
            "SELECT goldencheck_benford(?::DOUBLE[])", [vals]
        ).fetchone()[0]
        assert got == benford_histogram(vals)


class TestNearDuplicates:
    def test_pinned_clusters(self, con):
        res = con.execute(
            "SELECT goldencheck_near_duplicates("
            "['California','Californa','CALIFORNIA','Texas','texas'], 0.7)"
        ).fetchone()[0]
        assert res == [[0, 1, 2], [3, 4]]

    def test_matches_reference(self, con):
        from goldencheck.core.kernels import near_duplicate_clusters

        vals = ["Jon", "John", "Jonh", "Mary", "Marie", "Zed"]
        got = con.execute(
            "SELECT goldencheck_near_duplicates(?, 0.75)", [vals]
        ).fetchone()[0]
        assert got == near_duplicate_clusters(vals, 0.75)


class TestFunctionalDependencies:
    def test_strict_fd(self, con):
        # zip -> city holds strictly; the unique id column determines nothing.
        res = con.execute(
            "SELECT goldencheck_discover_fds("
            "[['1','1','2','2'],['A','A','B','B'],['r0','r1','r2','r3']])"
        ).fetchone()[0]
        assert {(d["det"], d["dep"]) for d in res} == {(0, 1), (1, 0)}

    def test_matches_reference(self, con):
        from goldencheck.core.kernels import discover_functional_dependencies

        cols = [
            ["1", "1", "2", "2", "3", "3"],
            ["A", "A", "B", "B", "C", "C"],
            ["x", "y", "z", "w", "u", "v"],
        ]
        got = con.execute("SELECT goldencheck_discover_fds(?)", [cols]).fetchone()[0]
        assert [(d["det"], d["dep"]) for d in got] == list(
            discover_functional_dependencies(cols)
        )


class TestApproximateFDs:
    def test_matches_reference(self, con):
        from goldencheck.core.kernels import discover_approximate_fds

        # 9 rows: det has 3 groups of 3 (avg group size 3 >= guard); dep breaks
        # the det->dep pattern on exactly one row.
        det = ["a", "a", "a", "b", "b", "b", "c", "c", "c"]
        dep = ["x", "x", "x", "y", "y", "z", "w", "w", "w"]
        cols = [det, dep]
        got = con.execute(
            "SELECT goldencheck_discover_approx_fds(?, 0.5)", [cols]
        ).fetchone()[0]
        ref = discover_approximate_fds(cols, 0.5)
        assert [(d["det"], d["dep"], d["violations"]) for d in got] == list(ref)


class TestCompositeKeys:
    def test_finds_composite_key(self, con):
        # (order, line) is a composite key; neither column is unique alone.
        res = con.execute(
            "SELECT goldencheck_composite_keys("
            "[['o1','o1','o2','o2','o3'],['1','2','1','2','1'],"
            "['mon','mon','tue','tue','wed']], 3)"
        ).fetchone()[0]
        assert [0, 1] in res

    def test_matches_reference(self, con):
        from goldencheck.core.kernels import composite_key_search

        cols = [
            ["o1", "o1", "o2", "o2", "o3"],
            ["1", "2", "1", "2", "1"],
            ["mon", "mon", "tue", "tue", "wed"],
        ]
        got = con.execute(
            "SELECT goldencheck_composite_keys(?, 3)", [cols]
        ).fetchone()[0]
        assert got == composite_key_search(cols, 3)


class TestFallbackParity:
    """The pure-Python fallback (GOLDENCHECK_NATIVE=0) must reproduce the pinned
    values -- this is the path the ``duckdb_extensions`` CI lane exercises (no
    native wheel built there)."""

    def test_benford_fallback_pinned(self, con, monkeypatch):
        _fallback_env(monkeypatch)
        res = con.execute(
            "SELECT goldencheck_benford([1,1,2,11,19,3,100,7,9,9]::DOUBLE[])"
        ).fetchone()[0]
        assert res == [5, 1, 1, 0, 0, 0, 1, 0, 2]

    def test_composite_fallback_pinned(self, con, monkeypatch):
        _fallback_env(monkeypatch)
        res = con.execute(
            "SELECT goldencheck_composite_keys("
            "[['o1','o1','o2','o2','o3'],['1','2','1','2','1'],"
            "['mon','mon','tue','tue','wed']], 3)"
        ).fetchone()[0]
        assert [0, 1] in res
