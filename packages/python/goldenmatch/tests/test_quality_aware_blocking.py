"""Quality-aware blocking (GoldenCheck -> GoldenMatch door #1).

The mechanism is additive and fail-open: when GoldenCheck flags a blocking
column as edit-distance-fuzzy, a fuzzy-tolerant pass is ADDED so the variants
co-block (recall recovery). The original key is never removed.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.autoconfig import ColumnProfile, apply_quality_aware_blocking
from goldenmatch.core.quality import _goldencheck_available, blocking_risk

pytestmark = pytest.mark.skipif(not _goldencheck_available(), reason="goldencheck not installed")


def _fuzzy_df(n: int = 90) -> pl.DataFrame:
    # 'state' has edit-distance variants of a frequent value; 'clean' does not.
    states = ["California"] * 40 + ["Californa"] * 4 + ["Texas"] * 46
    clean = ["alpha", "beta", "gamma"] * 30
    return pl.DataFrame({"state": states[:n], "clean": clean[:n]})


def _profiles() -> list[ColumnProfile]:
    return [
        ColumnProfile(name="state", dtype="str", col_type="string", confidence=1.0),
        ColumnProfile(name="clean", dtype="str", col_type="string", confidence=1.0),
    ]


# --- blocking_risk bridge ---------------------------------------------------

def test_blocking_risk_detects_fuzzy_column() -> None:
    risk = blocking_risk(_fuzzy_df())
    assert risk is not None
    assert risk.get("state", 0.0) > 0.0   # 'Californa' variants flagged
    assert "clean" not in risk            # clean column carries no risk


def test_blocking_risk_clean_frame_is_none() -> None:
    df = pl.DataFrame({"a": ["alpha", "beta", "gamma"] * 30})
    assert blocking_risk(df) is None


# --- apply_quality_aware_blocking -------------------------------------------

def _static(field: str, transforms: list[str]) -> BlockingConfig:
    return BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=[field], transforms=transforms)])


def test_adds_fuzzy_pass_for_fuzzy_key() -> None:
    cfg = _static("state", ["lowercase", "strip"])
    out = apply_quality_aware_blocking(cfg, _profiles(), _fuzzy_df(), enabled=True)
    assert out.strategy == "multi_pass"
    # original key retained as a pass + a new fuzzy-tolerant pass on 'state'
    state_passes = [p for p in out.passes if p.fields == ["state"]]
    assert len(state_passes) == 2
    assert any("substring:0:6" in p.transforms for p in state_passes)
    # never drops the original
    assert any(p.transforms == ["lowercase", "strip"] for p in state_passes)


def test_name_column_gets_phonetic_pass() -> None:
    # 'Catherina' is a 1-char typo of 'Catherine' (similarity ~0.89, above the
    # fuzzy threshold; a 3-char name like Jon/John is below it by design).
    df = pl.DataFrame({"name": ["Catherine"] * 40 + ["Catherina"] * 4 + ["Michael"] * 46})
    profiles = [ColumnProfile(name="name", dtype="str", col_type="name", confidence=1.0)]
    out = apply_quality_aware_blocking(_static("name", ["lowercase"]), profiles, df, enabled=True)
    assert out.strategy == "multi_pass"
    assert any("soundex" in p.transforms for p in out.passes if p.fields == ["name"])


def test_disabled_is_passthrough() -> None:
    cfg = _static("state", ["lowercase", "strip"])
    out = apply_quality_aware_blocking(cfg, _profiles(), _fuzzy_df(), enabled=False)
    assert out is cfg  # untouched


def test_clean_data_is_passthrough() -> None:
    df = pl.DataFrame({"state": ["alpha", "beta", "gamma"] * 30})
    cfg = _static("state", ["lowercase", "strip"])
    out = apply_quality_aware_blocking(cfg, [_profiles()[0]], df, enabled=True)
    assert out is cfg  # no fuzz -> unchanged


def test_already_tolerant_key_not_doubled() -> None:
    # 'state' key already has soundex -> no extra pass added.
    cfg = _static("state", ["lowercase", "soundex"])
    out = apply_quality_aware_blocking(cfg, _profiles(), _fuzzy_df(), enabled=True)
    assert out is cfg


def test_non_static_strategy_untouched() -> None:
    cfg = BlockingConfig(strategy="ann", ann_column="emb")
    out = apply_quality_aware_blocking(cfg, _profiles(), _fuzzy_df(), enabled=True)
    assert out is cfg


def test_none_config_passthrough() -> None:
    assert apply_quality_aware_blocking(None, _profiles(), _fuzzy_df(), enabled=True) is None
