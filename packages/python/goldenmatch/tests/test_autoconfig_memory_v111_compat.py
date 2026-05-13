"""v1.12: verify v1.11-vintage memory cache entries load cleanly into v1.12.

v1.11 stored: GoldenMatchConfig with NE optional on weighted matchkeys only.
v1.12 adds: NE on exact matchkeys + threshold default 0.5.

A v1.11 cache entry has no NE on exact matchkeys (NE was never promoted on
exact in v1.11). v1.12's deserializer must handle this cleanly.
"""
import json
from pathlib import Path

import pytest


def test_v1_11_cache_entry_loads_cleanly():
    """v1.11 cache entry (no NE on exact) -> v1.12 deserialization OK."""
    from goldenmatch.config.schemas import GoldenMatchConfig
    fixture = (
        Path(__file__).parent / "fixtures" / "autoconfig"
        / "v1_11_memory_snapshot.json"
    )
    if not fixture.exists():
        pytest.skip(f"missing fixture: {fixture}")
    entry = json.loads(fixture.read_text())
    cfg = GoldenMatchConfig.model_validate(entry["config_json"])
    # Verify exact matchkey deserializes with NE=None preserved
    exact_mks = [mk for mk in cfg.matchkeys if mk.type == "exact"]
    if exact_mks:
        for mk in exact_mks:
            assert mk.negative_evidence is None, (
                f"v1.11 entry should have NE=None on exact matchkey '{mk.name}'"
            )


def test_v1_10_chain_compat_through_v112():
    """v1.10 -> v1.11 -> v1.12 chain: oldest fixture still loads."""
    from goldenmatch.config.schemas import GoldenMatchConfig
    fixture = (
        Path(__file__).parent / "fixtures" / "autoconfig"
        / "v1_10_memory_snapshot.json"
    )
    if not fixture.exists():
        pytest.skip(f"missing fixture: {fixture}")
    entry = json.loads(fixture.read_text())
    cfg = GoldenMatchConfig.model_validate(entry["config_json"])
    assert cfg.matchkeys[0].negative_evidence is None


def test_v1_12_cache_entry_with_ne_on_exact_round_trips():
    """v1.12 cache entry with NE on exact serializes + deserializes losslessly."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
        NegativeEvidenceField,
    )
    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="exact_email",
                type="exact",
                threshold=0.5,
                fields=[
                    MatchkeyField(
                        field="email",
                        transforms=["lowercase"],
                        scorer="exact",
                        weight=1.0,
                    )
                ],
                negative_evidence=[
                    NegativeEvidenceField(
                        field="phone",
                        transforms=["digits_only"],
                        scorer="exact",
                        threshold=0.4,
                        penalty=0.3,
                    ),
                ],
            )
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase"])],
            max_block_size=1000,
            skip_oversized=False,
        ),
    )
    serialized = cfg.model_dump_json()
    reloaded = GoldenMatchConfig.model_validate_json(serialized)
    assert reloaded.matchkeys[0].threshold == 0.5
    assert reloaded.matchkeys[0].negative_evidence is not None
    assert reloaded.matchkeys[0].negative_evidence[0].field == "phone"
