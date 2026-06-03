"""Tests for the scale-mode DataFusion cluster edge view
(``goldenmatch.core.cluster_edges_df``).

Layout:
  - A LOUD guard (``test_datafusion_is_importable``) with NO importorskip: it
    FAILS if datafusion isn't installed, so a missing CI install can't hide
    behind the importorskip'd tests below (which would SILENTLY skip = false
    green). CI installs `datafusion>=53,<54` for the goldenmatch lane.
  - A from_arrow ingest smoke test.
  - Parity tests against the hand-built fixture in
    ``tests.fixtures.cluster_edges_shapes``.
  - A determinism test across target_partitions {1, 4, 17}.
"""
from __future__ import annotations

import importlib.util

import pytest

pa = pytest.importorskip("pyarrow")


# --------------------------------------------------------------------------- #
# LOUD guard: no importorskip -- fails if datafusion isn't installed in CI.
# --------------------------------------------------------------------------- #
def test_datafusion_is_importable():
    """datafusion MUST be importable in the goldenmatch CI lane. If this fails,
    the `uv pip install 'datafusion>=53,<54'` step in .github/workflows/ci.yml
    (python job, goldenmatch only) is missing or broke -- the importorskip'd
    parity tests below would otherwise silently skip (false green)."""
    assert importlib.util.find_spec("datafusion") is not None, (
        "datafusion not importable -- the CI install step for the goldenmatch "
        "lane is missing. The scale-mode edge parity tests would silently skip."
    )


# --------------------------------------------------------------------------- #
# from_arrow ingest smoke
# --------------------------------------------------------------------------- #
def test_datafusion_from_arrow_ingest():
    pytest.importorskip("datafusion")
    from datafusion import SessionContext

    ctx = SessionContext()
    tbl = pa.table({"x": pa.array([1, 2, 3], pa.int64())})
    ctx.from_arrow(tbl, name="t")  # verified signature for datafusion>=53
    out = ctx.sql("SELECT sum(x) AS s FROM t").to_arrow_table()
    assert out.column("s")[0].as_py() == 6


# --------------------------------------------------------------------------- #
# Shared helpers for the parity tests
# --------------------------------------------------------------------------- #
def _run(target_partitions=None):
    """Run cluster_edges_datafusion against the fixture, returning
    (collected_runs, rollup_by_cid)."""
    pytest.importorskip("datafusion")
    from goldenmatch.core.cluster_edges_df import (
        _collect_runs,
        cluster_edges_datafusion,
    )

    from tests.fixtures.cluster_edges_shapes import build_cluster_edges_fixture

    pairs, assignments, expected = build_cluster_edges_fixture()
    stream, rollup = cluster_edges_datafusion(
        pairs, assignments, target_partitions=target_partitions
    )
    runs = _collect_runs(stream)
    rollup_by_cid = _rollup_to_dict(rollup)
    return runs, rollup_by_cid, expected


def _rollup_to_dict(rollup_table):
    """Index the rollup Arrow table by cid -> dict(column -> value)."""
    rows = rollup_table.to_pylist()
    out = {}
    for r in rows:
        out[int(r["cid"])] = r
    return out


# --------------------------------------------------------------------------- #
# Task 3 parity tests
# --------------------------------------------------------------------------- #
def test_edge_sets_match_legacy():
    """Per-cid edge SETS (AS-GIVEN keys, MAX-deduped, membership-filtered) match
    the hand-built expectation, including the dropped cross-cut edge and the
    edgeless singleton."""
    runs, _rollup, expected = _run()
    for cid, exp in expected.items():
        got = runs.get(cid, {})
        assert got == exp["edges"], f"cid {cid}: edges {got} != {exp['edges']}"
    # No stray cids beyond the expected ones (cross-cut produced nothing).
    assert set(runs) <= set(expected)


def test_rollup_matches_legacy_incl_singleton_and_sparse():
    """size, edge_count, min, avg, derived confidence, and bottleneck match for
    EVERY cid -- including the singleton (cid 40) and the sparse cluster (cid 20)."""
    from goldenmatch.core.cluster_edges_df import _confidence

    _runs, rollup, expected = _run()
    # Every cluster, including the edgeless singleton, must appear (LEFT join).
    assert set(rollup) == set(expected)

    for cid, exp in expected.items():
        row = rollup[cid]
        assert int(row["size"]) == exp["size"], f"cid {cid} size"
        assert int(row["edge_count"]) == len(exp["edges"]), f"cid {cid} edge_count"
        assert row["min_edge"] == pytest.approx(exp["min_edge"], abs=1e-12), (
            f"cid {cid} min_edge"
        )
        assert row["avg_edge"] == pytest.approx(exp["avg_edge"], abs=1e-12), (
            f"cid {cid} avg_edge"
        )
        conf = _confidence(row)
        assert conf == pytest.approx(exp["confidence"], abs=1e-9), (
            f"cid {cid} confidence {conf} != {exp['confidence']}"
        )
        # bottleneck: (a, b) tuple or None for the singleton.
        if exp["bottleneck"] is None:
            assert row["bottleneck_a"] is None and row["bottleneck_b"] is None, (
                f"cid {cid} expected no bottleneck"
            )
        else:
            got_bn = (int(row["bottleneck_a"]), int(row["bottleneck_b"]))
            assert got_bn == exp["bottleneck"], f"cid {cid} bottleneck"


def test_max_dedup_not_last_wins():
    """The (1,2,0.9) then (1,2,0.4) duplicate resolves to MAX (0.9), NOT the
    later 0.4 (last-wins)."""
    runs, _rollup, _expected = _run()
    assert runs[10][(1, 2)] == pytest.approx(0.9, abs=1e-12)


# --------------------------------------------------------------------------- #
# Task 4 determinism
# --------------------------------------------------------------------------- #
def test_determinism_across_target_partitions():
    """Identical cluster_id/size/edge_count/bottleneck/edge-sets and avg_edge
    (abs=1e-12) across target_partitions in {1, 4, 17}. A drift here means the
    impl must pin the reduction order."""
    results = {}
    for tp in (1, 4, 17):
        runs, rollup, _expected = _run(target_partitions=tp)
        results[tp] = (runs, rollup)

    base_runs, base_rollup = results[1]
    for tp in (4, 17):
        runs, rollup = results[tp]
        assert set(runs) == set(base_runs), f"tp={tp}: cid set drift"
        for cid in base_runs:
            assert runs[cid] == base_runs[cid], (
                f"tp={tp}: cid {cid} edge-set drift (reduction not pinned)"
            )
        assert set(rollup) == set(base_rollup), f"tp={tp}: rollup cid set drift"
        for cid, base_row in base_rollup.items():
            row = rollup[cid]
            assert int(row["size"]) == int(base_row["size"]), f"tp={tp} cid {cid} size"
            assert int(row["edge_count"]) == int(base_row["edge_count"]), (
                f"tp={tp} cid {cid} edge_count"
            )
            assert (row["bottleneck_a"], row["bottleneck_b"]) == (
                base_row["bottleneck_a"],
                base_row["bottleneck_b"],
            ), f"tp={tp} cid {cid} bottleneck drift"
            assert row["avg_edge"] == pytest.approx(base_row["avg_edge"], abs=1e-12), (
                f"tp={tp} cid {cid} avg_edge drift -- pin the reduction"
            )
