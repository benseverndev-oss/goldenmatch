"""Stage F parity: the pipeline block/score/cluster seam short-circuits to the
fused Arrow-native ``match_fused`` kernel when the controller flagged the run
(``config._use_fused_match``) AND the config-driven divergence gate is clear.

Capacity-survival mode: the fused-routed run sheds ``scored_pairs`` + cluster
confidence/bottleneck + lineage (it fires only under est-RSS pressure, where the
classic path would likely OOM), but the CLUSTER MEMBERSHIP + GOLDEN records are
byte-identical to the classic block->score->cluster path on the same config.
``match_fused_capacity_mode=True`` marks the shed so it is never silent.

Coverage of Stage F:
  * F.1 parity: flag set + covered + artifact-free -> short-circuit; clusters
    (membership partition) + golden content byte-identical to classic; empty
    scored_pairs + capacity-mode marker.
  * F.1 fallback: flag set but the kernel declines (uncovered scorer) -> classic
    runs unchanged (byte-identical), no capacity marker.
  * F.2 kill-switch: GOLDENMATCH_MATCH_FUSED=0 -> classic (byte-identical).
  * F.2 no-pressure: flag NOT set -> classic (the default, no capacity marker).
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
from goldenmatch.core.fused_match import match_fused_ready
from goldenmatch.core.fused_routing import config_needs_artifacts
from goldenmatch.core.pipeline import run_dedupe_df
from polars.testing import assert_frame_equal


def _kernel_present() -> bool:
    try:
        from goldenmatch.core._native_loader import native_module

        return hasattr(native_module(), "match_fused")
    except Exception:
        return False


requires_kernel = pytest.mark.skipif(
    not _kernel_present(),
    reason="match_fused native kernel not built (build_native.py); CI builds it",
)


def _people_df(n_clusters: int = 10, members: int = 3, n_singletons: int = 5) -> pl.DataFrame:
    """Personlike frame with ``n_clusters`` triples (each shares a zip block +
    an identical name so the weighted name matchkey merges them) plus
    ``n_singletons`` rows on their own unique zip block (never merge)."""
    rows: list[dict] = []
    for c in range(n_clusters):
        for _m in range(members):
            rows.append({"name": f"Cluster Person {c}", "zip": f"200{c:02d}"})
    for s in range(n_singletons):
        rows.append({"name": f"Solo Human {s}", "zip": f"900{s:02d}"})
    return pl.DataFrame(rows)


def _covered_config(scorer: str = "jaro_winkler") -> GoldenMatchConfig:
    """Static single-key blocking (zip) + one weighted matchkey (name) -- the
    match_fused-covered shape. auto_split off + quality_weighting off + no
    identity/confidence_majority/lineage_provenance -> config_needs_artifacts
    is False, so the short-circuit is allowed when the flag is set."""
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="name_fuzzy",
                type="weighted",
                threshold=0.85,
                fields=[MatchkeyField(field="name", scorer=scorer, weight=1.0)],
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


def _flag(cfg: GoldenMatchConfig) -> GoldenMatchConfig:
    """Simulate the controller post-step setting ExecutionPlan.use_fused_match."""
    cfg._use_fused_match = True
    return cfg


def _multi_partition(clusters: dict) -> set[frozenset[int]]:
    """Set of member-frozensets over the MULTI-member clusters (cluster ids
    differ between the classic and fused numberings, so compare the partition,
    not the id-keyed dict)."""
    return {
        frozenset(c["members"]) for c in clusters.values() if c["size"] > 1
    }


def _golden_content(g: pl.DataFrame) -> pl.DataFrame:
    """Golden user-value rows, modulo cluster id + confidence (the fused path
    numbers clusters differently and sheds confidence in capacity mode)."""
    cols = [c for c in g.columns if c not in ("__cluster_id__", "__golden_confidence__")]
    return g.select(sorted(cols)).sort(sorted(cols))


def test_config_is_covered_and_artifact_free():
    """Sanity: the parity config is match_fused-covered and needs no artifacts."""
    cfg = _covered_config()
    assert match_fused_ready(cfg) is True
    assert config_needs_artifacts(cfg) is False


@requires_kernel
def test_fused_match_short_circuit_byte_identical(monkeypatch):
    """Flag set + covered + artifact-free -> short-circuit to match_fused; the
    membership partition + golden content are byte-identical to classic, with
    empty scored_pairs + the capacity-mode marker."""
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    df = _people_df()

    classic = run_dedupe_df(df, _covered_config())
    assert classic.get("match_fused_capacity_mode") is not True

    fused = run_dedupe_df(df, _flag(_covered_config()))
    assert fused["match_fused_capacity_mode"] is True
    assert fused["scored_pairs"] == []

    # Membership partition byte-identical.
    assert _multi_partition(fused["clusters"]) == _multi_partition(classic["clusters"])

    # dupes / unique row populations byte-identical.
    assert set(fused["dupes"]["__row_id__"].to_list()) == set(
        classic["dupes"]["__row_id__"].to_list()
    )
    assert set(fused["unique"]["__row_id__"].to_list()) == set(
        classic["unique"]["__row_id__"].to_list()
    )

    # Golden content byte-identical (modulo cluster id + confidence).
    assert fused["golden"] is not None and classic["golden"] is not None
    assert_frame_equal(
        _golden_content(fused["golden"]),
        _golden_content(classic["golden"]),
    )


@requires_kernel
def test_fused_match_declines_uncovered_falls_through(monkeypatch):
    """Flag set but the config is NOT covered (uncovered scorer) -> the kernel
    returns None and the classic path runs unchanged (byte-identical)."""
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    df = _people_df()
    # soundex_match is a valid classic scorer but NOT in the fused scorer set,
    # so match_fused_ready declines and the short-circuit returns None.
    assert match_fused_ready(_covered_config(scorer="soundex_match")) is False

    plain = run_dedupe_df(df, _covered_config(scorer="soundex_match"))
    flagged = run_dedupe_df(df, _flag(_covered_config(scorer="soundex_match")))

    assert flagged.get("match_fused_capacity_mode") is not True
    assert _multi_partition(flagged["clusters"]) == _multi_partition(plain["clusters"])
    assert flagged["golden"] is not None and plain["golden"] is not None
    assert_frame_equal(
        _golden_content(flagged["golden"]), _golden_content(plain["golden"])
    )


@requires_kernel
def test_kill_switch_uses_classic(monkeypatch):
    """GOLDENMATCH_MATCH_FUSED=0 -> the short-circuit declines even with the flag
    set; classic runs byte-identical."""
    df = _people_df()
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    classic = run_dedupe_df(df, _covered_config())

    monkeypatch.setenv("GOLDENMATCH_MATCH_FUSED", "0")
    killed = run_dedupe_df(df, _flag(_covered_config()))
    assert killed.get("match_fused_capacity_mode") is not True
    assert _multi_partition(killed["clusters"]) == _multi_partition(classic["clusters"])
    assert_frame_equal(
        _golden_content(killed["golden"]), _golden_content(classic["golden"])
    )


@requires_kernel
def test_oversized_cluster_excluded_from_golden_like_classic(monkeypatch):
    """With auto_split off + a low max_cluster_size, clusters over the cap are
    flagged oversized: classic (and the fused short-circuit) keep their members in
    dupes but EXCLUDE them from golden. Both paths agree byte-for-byte."""
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    df = _people_df(n_clusters=6, members=3, n_singletons=3)  # size-3 clusters
    cfg = _covered_config()
    cfg.golden_rules.max_cluster_size = 2  # size-3 clusters -> oversized

    classic = run_dedupe_df(df, cfg.model_copy(deep=True))
    fused = run_dedupe_df(df, _flag(cfg.model_copy(deep=True)))
    assert fused["match_fused_capacity_mode"] is True

    # No golden records for any cluster (all multi-member clusters are oversized).
    assert classic["golden"] is None or classic["golden"].height == 0
    assert fused["golden"] is None or fused["golden"].height == 0

    # Oversized members still land in dupes on BOTH paths.
    assert set(fused["dupes"]["__row_id__"].to_list()) == set(
        classic["dupes"]["__row_id__"].to_list()
    )
    assert _multi_partition(fused["clusters"]) == _multi_partition(classic["clusters"])


@requires_kernel
def test_no_flag_runs_classic(monkeypatch):
    """No _use_fused_match flag (no est-RSS pressure) -> classic path, no capacity
    marker. The default posture; the flag is the only thing that routes."""
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    df = _people_df()
    res = run_dedupe_df(df, _covered_config())
    assert res.get("match_fused_capacity_mode") is not True
    assert res["scored_pairs"] != []  # classic path builds scored_pairs
