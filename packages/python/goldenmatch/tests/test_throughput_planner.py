"""Tests for ExecutionPlan throughput telemetry fields (#1083)."""
import dataclasses
from goldenmatch.core.execution_plan import ExecutionPlan
from goldenmatch.core.autoconfig_planner import apply_throughput_overlay
from goldenmatch.config.schemas import ThroughputConfig, GoldenMatchConfig


def test_verify_mode_defaults_to_full():
    assert ExecutionPlan().verify_mode == "full"
    assert ExecutionPlan().sketch_bands is None


def test_replace_overlays_verify_fields_preserving_backend():
    base = ExecutionPlan(backend="bucket", max_workers=8)
    p = dataclasses.replace(base, verify_mode="sketch_distance",
                            sketch_bands=16, sketch_rows=8, sketch_similarity=0.8)
    assert p.backend == "bucket" and p.max_workers == 8
    assert p.verify_mode == "sketch_distance" and p.sketch_bands == 16


def test_overlay_sets_sketch_distance_preserving_backend():
    base = ExecutionPlan(backend="bucket", max_workers=8)
    cfg = ThroughputConfig(enabled=True, recall_target=0.95)
    plan = apply_throughput_overlay(base, cfg, metric="jaccard", signature_len=128)
    assert plan.verify_mode == "sketch_distance"
    assert plan.backend == "bucket" and plan.max_workers == 8
    assert plan.sketch_bands * plan.sketch_rows == 128
    assert plan.sketch_similarity == 0.8


def test_overlay_honors_similarity_override():
    cfg = ThroughputConfig(enabled=True, similarity_threshold=0.9)
    plan = apply_throughput_overlay(ExecutionPlan(), cfg, metric="jaccard", signature_len=128)
    assert plan.sketch_similarity == 0.9


def test_apply_to_writes_throughput_plan_onto_config():
    plan = ExecutionPlan(verify_mode="sketch_distance", sketch_bands=16, sketch_rows=8, sketch_similarity=0.8)
    cfg = GoldenMatchConfig(throughput=ThroughputConfig(enabled=True))
    plan.apply_to(cfg)
    assert cfg._throughput_plan is plan and cfg._throughput_plan.verify_mode == "sketch_distance"

def test_controller_emits_sketch_distance_plan_for_throughput(monkeypatch):
    import polars as pl
    from goldenmatch.core import autoconfig
    monkeypatch.setattr(autoconfig, "_embedder_available", lambda config=None: False)
    df = pl.DataFrame({"body": ["the cat sat", "the cat sat on the mat", "a different sentence"] * 20})
    cfg = autoconfig.auto_configure_df(df, throughput=0.95)
    plan = getattr(cfg, "_throughput_plan", None)
    assert plan is not None and plan.verify_mode == "sketch_distance"
    assert plan.sketch_similarity == 0.8
    assert plan.sketch_bands * plan.sketch_rows == cfg.blocking.lsh.num_perms