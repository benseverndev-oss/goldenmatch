"""Tests for ExecutionPlan throughput telemetry fields (#1083)."""
import dataclasses
from goldenmatch.core.execution_plan import ExecutionPlan


def test_verify_mode_defaults_to_full():
    assert ExecutionPlan().verify_mode == "full"
    assert ExecutionPlan().sketch_bands is None


def test_replace_overlays_verify_fields_preserving_backend():
    base = ExecutionPlan(backend="bucket", max_workers=8)
    p = dataclasses.replace(base, verify_mode="sketch_distance",
                            sketch_bands=16, sketch_rows=8, sketch_similarity=0.8)
    assert p.backend == "bucket" and p.max_workers == 8
    assert p.verify_mode == "sketch_distance" and p.sketch_bands == 16
