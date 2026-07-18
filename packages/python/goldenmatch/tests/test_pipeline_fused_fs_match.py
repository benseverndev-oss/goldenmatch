"""Parity: the pipeline short-circuits a covered Fellegi-Sunter dedupe to the
fused ``match_fused_fs`` kernel when the controller flagged the run
(``config._use_fused_match``) AND the config-driven divergence gate is clear
(#1804 item 2, the FS twin of ``test_pipeline_fused_match.py``).

Capacity-survival mode: the fused-routed FS run sheds ``scored_pairs`` /
``review_pairs`` + per-cluster confidence, but CLUSTER MEMBERSHIP + GOLDEN are
byte-identical to the classic block->score->cluster FS path -- both train the
SAME (seeded) EM and run the SAME kernel FS math, so the link-threshold pairs
and their connected components match. ``match_fused_capacity_mode=True`` marks
the shed so it is never silent.
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    GoldenRulesConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.fused_match import (
    match_fused_fs_multipass_ready,
    match_fused_fs_ready,
)
from goldenmatch.core.fused_routing import config_needs_artifacts
from goldenmatch.core.pipeline import run_dedupe_df
from polars.testing import assert_frame_equal


def _kernel_present() -> bool:
    try:
        from goldenmatch.core._native_loader import native_module

        return hasattr(native_module(), "match_fused_fs")
    except Exception:
        return False


requires_kernel = pytest.mark.skipif(
    not _kernel_present(),
    reason="match_fused_fs native kernel not built (build_native.py); CI builds it",
)


def _people_df(n_clusters: int = 8, members: int = 3, n_singletons: int = 5) -> pl.DataFrame:
    """Personlike frame: ``n_clusters`` groups sharing a zip block + an identical
    name (FS exact-agreement -> high weight -> link), plus ``n_singletons`` rows
    on their own unique zip block. A second orthogonal ``city`` block key gives
    the multi-pass config something real to union on."""
    rows: list[dict] = []
    for c in range(n_clusters):
        for _m in range(members):
            rows.append(
                {"name": f"Cluster Person {c}", "zip": f"200{c:02d}", "city": f"town{c % 4}"}
            )
    for s in range(n_singletons):
        rows.append(
            {"name": f"Solo Human {s}", "zip": f"900{s:02d}", "city": f"solo{s}"}
        )
    return pl.DataFrame(rows)


def _fs_config(scorer: str = "jaro_winkler") -> GoldenMatchConfig:
    """Static single-key blocking (zip) + one probabilistic matchkey (name) --
    the match_fused_fs-covered shape. auto_split off + quality_weighting off + no
    identity/lineage -> config_needs_artifacts False, so the short-circuit is
    allowed when the flag is set."""
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="name_fs",
                type="probabilistic",
                link_threshold=0.5,
                fields=[MatchkeyField(
                    field="name", scorer=scorer, levels=3, partial_threshold=0.8,
                )],
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["zip"])],
            max_block_size=1000,
            skip_oversized=False,
        ),
        golden_rules=GoldenRulesConfig(
            default_strategy="most_complete",
            auto_split=False,
            quality_weighting=False,
        ),
    )


def _fs_multipass_config(scorer: str = "jaro_winkler") -> GoldenMatchConfig:
    """multi_pass blocking (zip + city, orthogonal) + one probabilistic matchkey
    -- the compound-union shape the single-key gate declines (#1798)."""
    cfg = _fs_config(scorer)
    cfg.blocking = BlockingConfig(
        strategy="multi_pass",
        keys=[BlockingKeyConfig(fields=["zip"])],
        passes=[BlockingKeyConfig(fields=["zip"]), BlockingKeyConfig(fields=["city"])],
        max_block_size=1000,
        skip_oversized=False,
    )
    return cfg


def _flag(cfg: GoldenMatchConfig) -> GoldenMatchConfig:
    """Simulate the controller post-step setting ExecutionPlan.use_fused_match."""
    cfg._use_fused_match = True
    return cfg


def _multi_partition(clusters: dict) -> set[frozenset[int]]:
    return {frozenset(c["members"]) for c in clusters.values() if c["size"] > 1}


def _golden_content(g) -> pl.DataFrame:
    if not isinstance(g, pl.DataFrame):
        g = pl.from_arrow(g)
    cols = [c for c in g.columns if c not in ("__cluster_id__", "__golden_confidence__")]
    return g.select(sorted(cols)).sort(sorted(cols))


def test_fs_config_covered_and_artifact_free():
    assert match_fused_fs_ready(_fs_config()) is True
    assert config_needs_artifacts(_fs_config()) is False
    assert match_fused_fs_multipass_ready(_fs_multipass_config()) is True
    assert config_needs_artifacts(_fs_multipass_config()) is False


@requires_kernel
def test_fs_fused_parity_single_key(monkeypatch):
    """Flag set + FS-covered + artifact-free -> short-circuit to match_fused_fs;
    membership + golden byte-identical to classic FS, empty scored_pairs +
    capacity marker."""
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    df = _people_df()

    classic = run_dedupe_df(df, _fs_config())
    assert classic.get("match_fused_capacity_mode") is not True

    fused = run_dedupe_df(df, _flag(_fs_config()))
    assert fused["match_fused_capacity_mode"] is True
    assert fused["scored_pairs"] == []
    assert fused["review_pairs"] == []

    assert _multi_partition(fused["clusters"]) == _multi_partition(classic["clusters"])
    assert set(fused["dupes"]["__row_id__"].to_pylist()) == set(
        classic["dupes"]["__row_id__"].to_pylist()
    )
    assert set(fused["unique"]["__row_id__"].to_pylist()) == set(
        classic["unique"]["__row_id__"].to_pylist()
    )
    assert fused["golden"] is not None and classic["golden"] is not None
    assert_frame_equal(
        _golden_content(fused["golden"]), _golden_content(classic["golden"])
    )


@requires_kernel
def test_fs_fused_parity_multipass(monkeypatch):
    """The multi-pass FS fused path (compound-union blocking, the #1798 shape) is
    byte-identical to the classic multi-pass FS dedupe."""
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    df = _people_df()

    classic = run_dedupe_df(df, _fs_multipass_config())
    fused = run_dedupe_df(df, _flag(_fs_multipass_config()))
    assert fused["match_fused_capacity_mode"] is True

    assert _multi_partition(fused["clusters"]) == _multi_partition(classic["clusters"])
    assert set(fused["dupes"]["__row_id__"].to_pylist()) == set(
        classic["dupes"]["__row_id__"].to_pylist()
    )
    assert fused["golden"] is not None and classic["golden"] is not None
    assert_frame_equal(
        _golden_content(fused["golden"]), _golden_content(classic["golden"])
    )


@requires_kernel
def test_fs_kill_switch_uses_classic(monkeypatch):
    """GOLDENMATCH_MATCH_FUSED=0 -> the FS short-circuit declines even with the
    flag set; classic FS runs byte-identical."""
    df = _people_df()
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    classic = run_dedupe_df(df, _fs_config())

    monkeypatch.setenv("GOLDENMATCH_MATCH_FUSED", "0")
    killed = run_dedupe_df(df, _flag(_fs_config()))
    assert killed.get("match_fused_capacity_mode") is not True
    assert _multi_partition(killed["clusters"]) == _multi_partition(classic["clusters"])
    assert_frame_equal(
        _golden_content(killed["golden"]), _golden_content(classic["golden"])
    )


@requires_kernel
def test_fs_fused_declines_uncovered_falls_through(monkeypatch):
    """Flag set but the FS config is NOT covered (a valid classic FS scorer that
    is outside the fused-FS scorer set) -> both the weighted and FS
    short-circuits decline and classic FS runs unchanged."""
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    df = _people_df()
    # qgram is a valid FS block scorer but NOT in _FUSED_FS_SCORER_IDS.
    assert match_fused_fs_ready(_fs_config(scorer="qgram")) is False

    plain = run_dedupe_df(df, _fs_config(scorer="qgram"))
    flagged = run_dedupe_df(df, _flag(_fs_config(scorer="qgram")))
    assert flagged.get("match_fused_capacity_mode") is not True
    assert _multi_partition(flagged["clusters"]) == _multi_partition(plain["clusters"])
    assert_frame_equal(
        _golden_content(flagged["golden"]), _golden_content(plain["golden"])
    )


def test_fs_no_flag_uses_classic(monkeypatch):
    """No _use_fused_match flag (no est-RSS pressure) -> classic FS, no capacity
    marker. Runs without the kernel (classic path only)."""
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    df = _people_df()
    result = run_dedupe_df(df, _fs_config())
    assert result.get("match_fused_capacity_mode") is not True
    assert result["scored_pairs"] is not None
