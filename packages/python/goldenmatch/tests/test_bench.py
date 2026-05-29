"""Tests for the stage-timing + metrics harness in `core/bench.py`.

The bench module is the load-bearing infrastructure for "benchmark
truth" — every scale-audit and speed-optimization PR depends on the
per-stage breakdown being trustworthy.
"""
from __future__ import annotations

import time

import polars as pl
from goldenmatch.core.bench import (
    BenchmarkRecorder,
    bench_capture,
    current_recorder,
    record_metric,
    record_metrics,
    stage,
)


class TestBenchmarkRecorder:
    def test_add_timing_accumulates(self):
        rec = BenchmarkRecorder()
        rec.add_timing("foo", 1.0)
        rec.add_timing("foo", 2.5)
        rec.add_timing("bar", 0.1)
        assert rec.timings == {"foo": 3.5, "bar": 0.1}

    def test_set_metric_last_writer_wins(self):
        rec = BenchmarkRecorder()
        rec.set_metric("count", 100)
        rec.set_metric("count", 200)
        assert rec.metrics == {"count": 200}

    def test_to_dict_rounds_timings(self):
        rec = BenchmarkRecorder()
        rec.add_timing("foo", 1.2345678)
        rec.set_metric("count", 100)
        out = rec.to_dict()
        assert out["stage_timings_seconds"]["foo"] == 1.2346
        assert out["metrics"]["count"] == 100

    def test_to_dict_exposes_stage_peak_rss(self):
        """The new RSS-instrumentation surface ships an empty dict by default."""
        rec = BenchmarkRecorder()
        assert "stage_peak_rss_kb" in rec.to_dict()
        assert rec.to_dict()["stage_peak_rss_kb"] == {}

    def test_set_stage_peak_rss_last_writer_wins(self):
        """ru_maxrss is monotonic; reusing a stage name takes the later peak."""
        rec = BenchmarkRecorder()
        rec.set_stage_peak_rss("scoring", 12_000)
        rec.set_stage_peak_rss("scoring", 18_000)
        assert rec.to_dict()["stage_peak_rss_kb"] == {"scoring": 18_000}


class TestStageHelper:
    def test_stage_with_no_recorder_is_noop(self):
        """No recorder pushed → no error, no global mutation."""
        with stage("foo"):
            pass  # nothing should happen

    def test_stage_records_elapsed_time(self):
        with bench_capture() as rec:
            with stage("phase_a"):
                time.sleep(0.01)
        assert "phase_a" in rec.timings
        assert rec.timings["phase_a"] >= 0.01

    def test_stage_accumulates_across_calls(self):
        with bench_capture() as rec:
            with stage("phase"):
                time.sleep(0.005)
            with stage("phase"):
                time.sleep(0.005)
        assert rec.timings["phase"] >= 0.01

    def test_stage_records_on_exception(self):
        """Exceptions inside the stage must still register the elapsed time."""
        with bench_capture() as rec:
            try:
                with stage("error_phase"):
                    time.sleep(0.005)
                    raise RuntimeError("boom")
            except RuntimeError:
                pass
        assert "error_phase" in rec.timings
        assert rec.timings["error_phase"] >= 0.005

    def test_stage_records_peak_rss_on_linux(self):
        """stage(...) populates stage_peak_rss_kb on platforms with the resource module."""
        import sys
        if sys.platform == "win32":
            import pytest
            pytest.skip("resource module unavailable on Windows; ru_maxrss has no equivalent")
        with bench_capture() as rec:
            with stage("rss_phase"):
                # Allocate ~50 MB so ru_maxrss likely advances measurably.
                blob = bytearray(50 * 1024 * 1024)  # noqa: F841
                time.sleep(0.001)
        assert "rss_phase" in rec.stage_peak_rss_kb
        # ru_maxrss is in KB on Linux; any sane process running this test is
        # already >5 MB. Just assert the field is populated with a real value.
        assert rec.stage_peak_rss_kb["rss_phase"] > 5_000

    def test_nested_stages_record_independently(self):
        with bench_capture() as rec:
            with stage("outer"):
                time.sleep(0.005)
                with stage("inner"):
                    time.sleep(0.005)
        assert rec.timings["outer"] >= 0.01
        assert rec.timings["inner"] >= 0.005
        # Outer includes inner (this is intentional — outer is wall-clock).
        assert rec.timings["outer"] >= rec.timings["inner"]


class TestMetricHelpers:
    def test_record_metric_with_no_recorder_is_noop(self):
        record_metric("foo", 42)  # must not raise

    def test_record_metric_sets_on_active_recorder(self):
        with bench_capture() as rec:
            record_metric("block_count", 1234)
        assert rec.metrics == {"block_count": 1234}

    def test_record_metrics_bulk_set(self):
        with bench_capture() as rec:
            record_metrics({"a": 1, "b": "two", "c": [3, 4]})
        assert rec.metrics == {"a": 1, "b": "two", "c": [3, 4]}


class TestRecorderStackIsolation:
    def test_current_recorder_returns_none_by_default(self):
        assert current_recorder() is None

    def test_nested_capture_pushes_inner(self):
        with bench_capture() as outer:
            assert current_recorder() is outer
            with bench_capture() as inner:
                assert current_recorder() is inner
                record_metric("x", 1)
            # After inner exits, outer is current again.
            assert current_recorder() is outer
            record_metric("x", 2)
        assert outer.metrics == {"x": 2}
        assert inner.metrics == {"x": 1}


class TestPipelineIntegration:
    """End-to-end: running dedupe_df under bench_capture must populate metrics."""

    def _personlike_df(self, n: int) -> pl.DataFrame:
        return pl.DataFrame({
            "id": list(range(n)),
            "name": [f"Person {i // 2}" for i in range(n)],  # pairs share name
            "email": [f"u{i // 2}@example.com" for i in range(n)],
            "zip": [f"100{i % 5:02d}" for i in range(n)],
        })

    def test_dedupe_df_populates_recorder(self, monkeypatch):
        """The pipeline should emit stage timings + key metrics."""
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
        monkeypatch.delenv("GOLDENMATCH_AUTOCONFIG_BACKEND", raising=False)
        from goldenmatch import dedupe_df

        df = self._personlike_df(40)
        with bench_capture() as rec:
            dedupe_df(df)

        # Auto-config ran (the helpful but expensive stage).
        assert "auto_configure" in rec.timings
        # Clustering ran.
        assert "cluster" in rec.timings
        # Record count metric was populated.
        assert rec.metrics.get("record_count") == 40
        # Pair counts present (even if zero).
        assert "exact_pair_count" in rec.metrics
        assert "fuzzy_pair_count" in rec.metrics
        assert "scored_pair_count" in rec.metrics
        # Cluster counts present.
        assert "cluster_count" in rec.metrics
        assert "multi_member_cluster_count" in rec.metrics
