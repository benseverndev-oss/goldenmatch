"""Tests for ThroughputConfig schema + resolve_throughput_config (#1083)."""
import pytest
from goldenmatch.config.schemas import GoldenMatchConfig, ThroughputConfig
from pydantic import ValidationError


def test_defaults():
    c = ThroughputConfig()
    assert c.enabled is False
    assert c.recall_target == 0.95
    assert c.similarity_threshold is None


def test_recall_target_must_be_in_open_unit_interval():
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValidationError):
            ThroughputConfig(recall_target=bad)


def test_similarity_threshold_bounds():
    ThroughputConfig(similarity_threshold=0.8)  # ok
    for bad in (0.0, 1.0, 1.2):
        with pytest.raises(ValidationError):
            ThroughputConfig(similarity_threshold=bad)


def test_goldenmatch_config_has_throughput_field_defaulting_none():
    assert GoldenMatchConfig().throughput is None


def test_config_accepts_runtime_throughput_plan_private_attr():
    # Pydantic v2 rejects undeclared private attrs; it MUST be a declared PrivateAttr.
    c = GoldenMatchConfig()
    c._throughput_plan = object()          # must not raise
    assert c._throughput_plan is not None


# ── Task 2: resolve_throughput_config + error type ──────────────────────────

from goldenmatch.core.throughput_verify import (
    ThroughputNotApplicableError,
    resolve_throughput_config,
)


def test_resolve_none_returns_none(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_THROUGHPUT", raising=False)
    assert resolve_throughput_config(None) is None


def test_resolve_true_enables_defaults(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_THROUGHPUT", raising=False)
    c = resolve_throughput_config(True)
    assert c.enabled and c.recall_target == 0.95


def test_resolve_float_is_recall_target(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_THROUGHPUT", raising=False)
    c = resolve_throughput_config(0.9)
    assert c.enabled and c.recall_target == 0.9


def test_env_enables_when_kwarg_absent(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_THROUGHPUT", "1")
    monkeypatch.setenv("GOLDENMATCH_THROUGHPUT_RECALL", "0.8")
    c = resolve_throughput_config(None)
    assert c.enabled and c.recall_target == 0.8


def test_kwarg_beats_env(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_THROUGHPUT", "1")
    c = resolve_throughput_config(0.7)
    assert c.recall_target == 0.7


def test_error_type_exists():
    assert issubclass(ThroughputNotApplicableError, Exception)
