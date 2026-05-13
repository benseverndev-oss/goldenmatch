"""Verify v1.9-vintage memory cache entries load cleanly into v1.10."""
import json
from pathlib import Path

import pytest


def test_v1_9_memory_snapshot_loads_cleanly():
    """A v1.9-vintage memory entry deserializes into a valid GoldenMatchConfig."""
    from goldenmatch.config.schemas import GoldenMatchConfig
    fixture = (
        Path(__file__).parent / "fixtures" / "autoconfig"
        / "v1_9_memory_snapshot.json"
    )
    if not fixture.exists():
        pytest.skip(f"missing fixture: {fixture}")
    entry = json.loads(fixture.read_text())
    cfg = GoldenMatchConfig.model_validate(entry["config_json"])
    assert cfg.get_matchkeys()[0].name == "primary"
    assert cfg.get_matchkeys()[0].threshold == 0.85


def test_v1_10_data_profile_column_priors_default_none_for_legacy_data():
    """A DataProfile constructed without column_priors (as v1.9 did) has
    column_priors == None — backward compat preserved."""
    from goldenmatch.core.complexity_profile import DataProfile
    dp = DataProfile(
        n_rows=100, n_cols=4,
        column_types={"a": "text", "b": "id-like", "c": "text", "d": "date"},
    )
    assert dp.column_priors is None


def test_v1_10_complexity_profile_indicators_default_none():
    from goldenmatch.core.complexity_profile import ComplexityProfile, DataProfile
    cp = ComplexityProfile(data=DataProfile(n_rows=100))
    assert cp.indicators is None
