"""v1.11: verify v1.10-vintage memory cache entries load cleanly."""
import json
from pathlib import Path

import pytest


def test_v1_10_memory_snapshot_loads_cleanly():
    from goldenmatch.config.schemas import GoldenMatchConfig
    fixture = (
        Path(__file__).parent / "fixtures" / "autoconfig"
        / "v1_10_memory_snapshot.json"
    )
    if not fixture.exists():
        pytest.skip(f"missing fixture: {fixture}")
    entry = json.loads(fixture.read_text())
    cfg = GoldenMatchConfig.model_validate(entry["config_json"])
    assert cfg.matchkeys[0].name == "primary"
    assert cfg.matchkeys[0].negative_evidence is None    # v1.10 had no NE


def test_v1_9_memory_snapshot_chain_compat():
    """v1.9 → v1.10 → v1.11 chain compat: v1.9-saved entry still loads."""
    from goldenmatch.config.schemas import GoldenMatchConfig
    fixture = (
        Path(__file__).parent / "fixtures" / "autoconfig"
        / "v1_9_memory_snapshot.json"
    )
    if not fixture.exists():
        pytest.skip(f"missing v1.9 fixture: {fixture}")
    entry = json.loads(fixture.read_text())
    cfg = GoldenMatchConfig.model_validate(entry["config_json"])
    assert cfg.matchkeys[0].negative_evidence is None


def test_matchkey_config_constructed_without_ne():
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    mk = MatchkeyConfig(
        name="t", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="x", transforms=[],
                              scorer="ensemble", weight=1.0)],
    )
    assert mk.negative_evidence is None
