"""Smoke + eligibility guards for the survivorship columnar measure-first bench.

Loads the bench script by path (scripts/ is not a package) and runs a suite
of fast micro-checks at 1k rows. These are harness bit-rot guards, NOT perf
assertions -- they confirm the workload synthesizer, config builders,
measurement functions, and verdict logic stay wired to the real production
symbols. The at-scale runs (1M/5M) live in bench-survivorship-columnar.yml.
"""
from pathlib import Path

import pytest

_BENCH = Path(__file__).resolve().parent.parent / "scripts" / "bench_survivorship_columnar.py"
_SCRIPTS = str(_BENCH.parent)

# ---------------------------------------------------------------------------
# Loader helper -- import the bench module from its file path.
# ---------------------------------------------------------------------------

def _load():
    import importlib.util
    spec = importlib.util.spec_from_file_location("bench_surv_col", _BENCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Task 1: workload synthesizer
# ---------------------------------------------------------------------------

def test_make_clustered_workload_shape():
    mod = _load()
    df = mod.make_clustered_workload(rows=1000, avg_cluster_size=3, seed=7)
    # tagged multi-member frame
    assert "__cluster_id__" in df.columns
    assert "__row_id__" in df.columns
    assert "__source__" in df.columns       # source metadata for source_priority
    assert {"first_name", "last_name", "street", "city", "state", "zip",
            "phone", "updated_at"} <= set(df.columns)
    assert df.height == 1000
    # multiple clusters, all multi-member (size >= 2), some nulls to give the resolver work
    n_clusters = df["__cluster_id__"].n_unique()
    assert 100 < n_clusters < 500          # ~333 clusters at avg size 3
    assert df["zip"].null_count() > 0       # nulls -> group-winner / fill has work


def test_make_clustered_workload_deterministic():
    """Same seed must produce identical frames."""
    mod = _load()
    df1 = mod.make_clustered_workload(rows=500, avg_cluster_size=3, seed=42)
    df2 = mod.make_clustered_workload(rows=500, avg_cluster_size=3, seed=42)
    assert df1.equals(df2)


# ---------------------------------------------------------------------------
# Task 2: configs + eligibility guard
# ---------------------------------------------------------------------------

def test_configs_and_floor_eligibility():
    mod = _load()
    surv = mod.make_survivorship_config()
    floor = mod.make_floor_config()
    from goldenmatch.core.golden import _polars_native_eligible, _survivorship_active
    # make_*_config return GoldenRulesConfig directly -- check on the returned object
    assert _survivorship_active(surv) is True
    assert _survivorship_active(floor) is False
    # the floor MUST land on the vectorized native path
    assert _polars_native_eligible(floor, None) is True
    mod.assert_floor_eligible(floor)        # raises if not eligible


def test_assert_floor_eligible_raises_on_survivorship_config():
    """assert_floor_eligible must reject a survivorship config."""
    mod = _load()
    surv = mod.make_survivorship_config()
    with pytest.raises(AssertionError):
        mod.assert_floor_eligible(surv)


# ---------------------------------------------------------------------------
# Task 3: measurement core
# ---------------------------------------------------------------------------

def test_measure_returns_phase_split_and_parity():
    mod = _load()
    df = mod.make_clustered_workload(rows=1000, avg_cluster_size=3, seed=7)
    slow = mod.run_slow(df, mod.make_survivorship_config(), runs=1)
    floor = mod.run_floor(df, mod.make_floor_config(), runs=1)
    # phase split keys present in slow result
    for k in ("total_wall_s", "sort_wall_s", "partition_wall_s", "loop_wall_s",
               "n_clusters", "rows_out"):
        assert k in slow, f"key {k!r} missing from slow result"
    assert "total_wall_s" in floor
    assert "rows_out" in floor
    # one golden record per cluster on BOTH paths -> same row count
    n_clusters = df["__cluster_id__"].n_unique()
    assert slow["rows_out"] == n_clusters, \
        f"slow rows_out {slow['rows_out']} != n_clusters {n_clusters}"
    assert floor["rows_out"] == n_clusters, \
        f"floor rows_out {floor['rows_out']} != n_clusters {n_clusters}"


def test_run_floor_rejects_survivorship_config():
    """run_floor must abort if given a survivorship config (eligibility guard)."""
    mod = _load()
    df = mod.make_clustered_workload(rows=200, avg_cluster_size=3, seed=7)
    with pytest.raises(AssertionError):
        mod.run_floor(df, mod.make_survivorship_config(), runs=1)


# ---------------------------------------------------------------------------
# Task 4: verdict + table + main (subprocess per variant)
# ---------------------------------------------------------------------------

def test_verdict_no_go_when_tax_small():
    mod = _load()
    # slow barely above floor -> tax < 25% bar -> NO-GO
    v = mod.verdict(
        slow={"total_wall_s": 1.05, "sort_wall_s": 0.1, "partition_wall_s": 0.1,
              "loop_wall_s": 0.85, "peak_rss_mb": 100},
        floor={"total_wall_s": 1.0, "peak_rss_mb": 95},
    )
    assert "NO-GO" in v


def test_verdict_go_when_vectorizable_tax_large():
    mod = _load()
    # slow 3x floor, dominated by partition+loop (vectorizable) -> GO
    v = mod.verdict(
        slow={"total_wall_s": 3.0, "sort_wall_s": 0.1, "partition_wall_s": 0.4,
              "loop_wall_s": 2.5, "peak_rss_mb": 100},
        floor={"total_wall_s": 1.0, "peak_rss_mb": 95},
    )
    assert "GO" in v and "NO-GO" not in v


def test_verdict_no_go_when_rss_regresses():
    mod = _load()
    # A REAL rewrite RSS regression = the VECTORIZED direction (floor proxy) uses
    # materially MORE RSS than slow. THAT vetoes a GO (the prior columnar failure
    # mode: a columnar path that blew up RSS).
    v = mod.verdict(
        slow={"total_wall_s": 3.0, "sort_wall_s": 0.1, "partition_wall_s": 0.4,
              "loop_wall_s": 2.5, "peak_rss_mb": 100},
        floor={"total_wall_s": 1.0, "peak_rss_mb": 500},   # vectorized proxy 5x RSS -> regress
    )
    assert "NO-GO" in v


def test_verdict_go_when_slow_rss_heavy():
    mod = _load()
    # The MEASURED 1M case: huge wall tax AND the slow path is RSS-heavy vs the
    # vectorized floor. Slow being RSS-heavy is a reason TO rewrite (the vectorized
    # direction is LIGHTER), so it must be GO -- the RSS gate must NOT veto it.
    v = mod.verdict(
        slow={"total_wall_s": 52.99, "sort_wall_s": 0.02, "partition_wall_s": 1.01,
              "loop_wall_s": 51.98, "peak_rss_mb": 5076},
        floor={"total_wall_s": 2.79, "peak_rss_mb": 3080},
    )
    assert "GO" in v and "NO-GO" not in v


def test_verdict_rss_none_treated_as_pass():
    mod = _load()
    # RSS None on both -> rss_ok=True; if wall criterion also passes -> GO
    v = mod.verdict(
        slow={"total_wall_s": 3.0, "sort_wall_s": 0.1, "partition_wall_s": 0.4,
              "loop_wall_s": 2.5, "peak_rss_mb": None},
        floor={"total_wall_s": 1.0, "peak_rss_mb": None},
    )
    assert "GO" in v and "NO-GO" not in v


def test_main_runs_1k_and_emits_table(capsys):
    mod = _load()
    rc = mod.main(["--rows", "1000", "--runs", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    # Title + key columns present
    assert "survivorship-columnar" in out.lower()
    assert "tax" in out.lower() or "verdict" in out.lower()
