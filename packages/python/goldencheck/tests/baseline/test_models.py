"""Tests for goldencheck.baseline.models — TDD baseline."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import pytest
import yaml

from goldencheck.baseline.models import (
    BaselineProfile,
    ConfidencePrior,
    CorrelationEntry,
    FunctionalDependency,
    PatternGrammar,
    StatProfile,
    TemporalOrder,
)


# ---------------------------------------------------------------------------
# StatProfile
# ---------------------------------------------------------------------------


def test_stat_profile_all_fields():
    sp = StatProfile(
        distribution="normal",
        params={"mean": 0.0, "std": 1.0},
        benford={"1": 0.301, "2": 0.176},
        entropy=2.3,
        bounds={"min": -3.0, "max": 3.0},
    )
    assert sp.distribution == "normal"
    assert sp.params == {"mean": 0.0, "std": 1.0}
    assert sp.benford == {"1": 0.301, "2": 0.176}
    assert sp.entropy == pytest.approx(2.3)
    assert sp.bounds == {"min": -3.0, "max": 3.0}


def test_stat_profile_minimal():
    sp = StatProfile(entropy=1.0, bounds={"min": 0, "max": 10})
    assert sp.distribution is None
    assert sp.params is None
    assert sp.benford is None
    assert sp.entropy == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# FunctionalDependency
# ---------------------------------------------------------------------------


def test_functional_dependency():
    fd = FunctionalDependency(
        determinant=["zip_code"],
        dependent=["city", "state"],
        confidence=0.95,
    )
    assert fd.determinant == ["zip_code"]
    assert fd.dependent == ["city", "state"]
    assert fd.confidence == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# BaselineProfile — construction helpers
# ---------------------------------------------------------------------------


def _make_minimal_profile(source: str = "tests/data.csv") -> BaselineProfile:
    return BaselineProfile(
        version="1.0",
        created=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source=source,
        rows=100,
        columns=["id", "name", "age"],
    )


def _make_full_profile() -> BaselineProfile:
    bp = _make_minimal_profile()
    bp.stat_profiles = {
        "age": StatProfile(
            distribution="normal",
            params={"mean": 30.0, "std": 5.0},
            entropy=3.2,
            bounds={"min": 18, "max": 65},
        )
    }
    bp.constraints_fd = [
        FunctionalDependency(determinant=["id"], dependent=["name"], confidence=0.99)
    ]
    bp.constraints_keys = [["id"]]
    bp.constraints_temporal = [
        TemporalOrder(before="created_at", after="updated_at", violation_rate=0.01)
    ]
    bp.correlations = [
        CorrelationEntry(
            columns=["age", "salary"],
            measure="pearson",
            value=0.72,
            strength="strong",
            note=None,
        )
    ]
    bp.patterns = {
        "email": PatternGrammar(pattern=r"[^@]+@[^@]+\.[^@]+", coverage=0.98)
    }
    bp.semantic_types = {"email": "email_address"}
    bp.priors = {
        "age_missing": ConfidencePrior(confidence=0.05, evidence_count=100)
    }
    return bp


# ---------------------------------------------------------------------------
# BaselineProfile — YAML round-trip
# ---------------------------------------------------------------------------


def test_baseline_yaml_roundtrip():
    bp = _make_full_profile()

    with tempfile.NamedTemporaryFile(suffix=".yml", delete=False) as f:
        path = f.name
    try:
        bp.save(path)
        loaded = BaselineProfile.load(path)

        assert loaded.version == bp.version
        assert loaded.source == bp.source
        assert loaded.rows == bp.rows
        assert loaded.columns == bp.columns

        # stat_profiles
        assert "age" in loaded.stat_profiles
        assert loaded.stat_profiles["age"].distribution == "normal"
        assert loaded.stat_profiles["age"].entropy == pytest.approx(3.2)

        # constraints FD (nested -> flat translation)
        assert len(loaded.constraints_fd) == 1
        assert loaded.constraints_fd[0].determinant == ["id"]
        assert loaded.constraints_fd[0].confidence == pytest.approx(0.99)

        # candidate keys
        assert loaded.constraints_keys == [["id"]]

        # temporal
        assert len(loaded.constraints_temporal) == 1
        assert loaded.constraints_temporal[0].before == "created_at"
        assert loaded.constraints_temporal[0].violation_rate == pytest.approx(0.01)

        # correlations
        assert len(loaded.correlations) == 1
        assert loaded.correlations[0].measure == "pearson"
        assert loaded.correlations[0].value == pytest.approx(0.72)

        # patterns (nested with grammars key)
        assert "email" in loaded.patterns
        assert loaded.patterns["email"].coverage == pytest.approx(0.98)

        # semantic types
        assert loaded.semantic_types == {"email": "email_address"}

        # priors
        assert "age_missing" in loaded.priors
        assert loaded.priors["age_missing"].confidence == pytest.approx(0.05)
        assert loaded.priors["age_missing"].evidence_count == 100

    finally:
        os.unlink(path)


def test_yaml_nested_constraints_structure():
    """Saved YAML must use nested constraints dict, not flat field names."""
    bp = _make_full_profile()

    with tempfile.NamedTemporaryFile(suffix=".yml", delete=False, mode="w") as f:
        path = f.name
    try:
        bp.save(path)
        with open(path) as fh:
            raw = yaml.safe_load(fh)

        # Must have nested structure
        assert "constraints" in raw
        assert "functional_dependencies" in raw["constraints"]
        assert "candidate_keys" in raw["constraints"]
        assert "temporal_orders" in raw["constraints"]

        # Must NOT have flat field names at top level
        assert "constraints_fd" not in raw
        assert "constraints_keys" not in raw
        assert "constraints_temporal" not in raw

    finally:
        os.unlink(path)


def test_yaml_patterns_structure():
    """Saved YAML patterns must have grammars list and total_coverage."""
    bp = _make_full_profile()

    with tempfile.NamedTemporaryFile(suffix=".yml", delete=False, mode="w") as f:
        path = f.name
    try:
        bp.save(path)
        with open(path) as fh:
            raw = yaml.safe_load(fh)

        assert "patterns" in raw
        assert "email" in raw["patterns"]
        col_patterns = raw["patterns"]["email"]
        assert "grammars" in col_patterns
        assert "total_coverage" in col_patterns

    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# load() ignores unknown keys
# ---------------------------------------------------------------------------


def test_load_ignores_unknown_keys(caplog):
    bp = _make_minimal_profile()

    with tempfile.NamedTemporaryFile(suffix=".yml", delete=False, mode="w") as f:
        path = f.name
    try:
        bp.save(path)
        # Inject unknown key
        with open(path) as fh:
            data = yaml.safe_load(fh)
        data["totally_unknown_field"] = "surprise"
        data["another_unknown"] = 42
        with open(path, "w") as fh:
            yaml.safe_dump(data, fh)

        import logging
        with caplog.at_level(logging.WARNING):
            loaded = BaselineProfile.load(path)

        assert loaded.source == bp.source
        assert "totally_unknown_field" in caplog.text or "another_unknown" in caplog.text

    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# source_filename property
# ---------------------------------------------------------------------------


def test_source_filename_simple():
    bp = _make_minimal_profile(source="data/exports/customers.csv")
    assert bp.source_filename == "customers.csv"


def test_source_filename_bare():
    bp = _make_minimal_profile(source="myfile.parquet")
    assert bp.source_filename == "myfile.parquet"


def test_source_filename_windows_path():
    bp = _make_minimal_profile(source=r"C:\data\sales\orders.csv")
    assert bp.source_filename == "orders.csv"


# ---------------------------------------------------------------------------
# update_from() merge semantics
# ---------------------------------------------------------------------------


def _make_update_target() -> BaselineProfile:
    bp = _make_minimal_profile(source="old_data.csv")
    bp.created = datetime(2025, 6, 1, tzinfo=timezone.utc)
    bp.priors = {
        "col_missing": ConfidencePrior(confidence=0.10, evidence_count=100),
        "col_outlier": ConfidencePrior(confidence=0.20, evidence_count=50),
    }
    bp.constraints_fd = [
        # High confidence: should be kept if still >= 0.8 in new
        FunctionalDependency(determinant=["zip"], dependent=["city"], confidence=0.95),
        # Low confidence in new: should be dropped
        FunctionalDependency(determinant=["x"], dependent=["y"], confidence=0.85),
    ]
    bp.semantic_types = {"email": "email_address", "name": "full_name"}
    return bp


def _make_update_source() -> BaselineProfile:
    bp = BaselineProfile(
        version="1.0",
        created=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source="new_data.csv",
        rows=200,
        columns=["id", "name", "age", "zip"],
    )
    bp.priors = {
        "col_missing": ConfidencePrior(confidence=0.30, evidence_count=200),
        "col_outlier": ConfidencePrior(confidence=0.40, evidence_count=150),
    }
    bp.constraints_fd = [
        # zip->city: keep (>= 0.8 in new)
        FunctionalDependency(determinant=["zip"], dependent=["city"], confidence=0.90),
        # x->y: drop from old (< 0.8 in new)
        FunctionalDependency(determinant=["x"], dependent=["y"], confidence=0.70),
        # Brand new FD with high confidence >= 0.9: should be added
        FunctionalDependency(determinant=["id"], dependent=["name"], confidence=0.92),
        # Brand new FD with confidence < 0.9: should NOT be added
        FunctionalDependency(determinant=["a"], dependent=["b"], confidence=0.85),
    ]
    bp.semantic_types = {"email": "pii_email", "phone": "phone_number"}
    return bp


def test_update_from_priors_weighted_average():
    target = _make_update_target()
    source = _make_update_source()
    target.update_from(source)

    # col_missing: (0.10*100 + 0.30*200) / (100+200) = (10+60)/300 = 70/300 ≈ 0.2333
    prior = target.priors["col_missing"]
    assert prior.confidence == pytest.approx(70 / 300, abs=1e-6)
    assert prior.evidence_count == 300

    # col_outlier: (0.20*50 + 0.40*150) / (50+150) = (10+60)/200 = 70/200 = 0.35
    prior2 = target.priors["col_outlier"]
    assert prior2.confidence == pytest.approx(0.35, abs=1e-6)
    assert prior2.evidence_count == 200


def test_update_from_fd_merge():
    target = _make_update_target()
    source = _make_update_source()
    target.update_from(source)

    determinants = [tuple(fd.determinant) for fd in target.constraints_fd]

    # zip->city kept (new confidence 0.90 >= 0.8)
    assert ("zip",) in determinants

    # x->y dropped (new confidence 0.70 < 0.8)
    assert ("x",) not in determinants

    # id->name added (new, confidence 0.92 >= 0.9)
    assert ("id",) in determinants

    # a->b NOT added (new, confidence 0.85 < 0.9)
    assert ("a",) not in determinants


def test_update_from_semantic_types_replaced():
    target = _make_update_target()
    source = _make_update_source()
    target.update_from(source)

    # Replaced with new data
    assert target.semantic_types == source.semantic_types


def test_update_from_source_and_created_updated():
    target = _make_update_target()
    source = _make_update_source()
    target.update_from(source)

    assert target.source == "new_data.csv"
    assert target.created == source.created
    assert target.rows == 200
    assert target.columns == source.columns


def test_update_from_history_recorded():
    target = _make_update_target()
    old_source = target.source
    old_created = target.created
    source = _make_update_source()
    target.update_from(source)

    assert len(target.history) >= 1
    entry = target.history[-1]
    assert entry["source"] == old_source
    assert entry["created"] == old_created.isoformat()
