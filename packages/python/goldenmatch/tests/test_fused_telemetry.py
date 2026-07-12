"""Stage G: fused-routing telemetry markers + the whole-feature parity lock.

Two surfaces carry the fused-routing signal, split by where each datum lives:

  * ``ExecutionPlan.use_fused_match`` + ``rule_name`` (…+fused_match_post_step)
    ride ``history.execution_plan`` -- plan-reachable, so they surface in the
    cross-surface telemetry blob (``serialize_telemetry`` -> ``_execution_plan``).
  * ``golden_fused_used`` + ``match_fused_capacity_mode`` originate in the
    pipeline RESULT dict, which ``serialize_telemetry`` never receives (its
    callers capture only ``(profile, history)`` from ``_LAST_CONTROLLER_RUN``).
    They surface on ``DedupeResult`` instead -- the natural, honest home for a
    run-outcome flag. The telemetry-blob surfacing of those two is deferred
    (would need threading the result dict through every serializer caller, an
    invasive change orthogonal to the controller-decision surface).

G.2 is the whole-feature parity lock: a fused-routed ``dedupe_df`` result is
byte-identical (clusters membership partition + golden) to the classic run, and
under match capacity mode sheds ``scored_pairs`` behind the
``match_fused_capacity_mode`` marker so the tradeoff is never silent.
"""

from __future__ import annotations

import goldenmatch as gm
import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenFieldRule,
    GoldenMatchConfig,
    GoldenRulesConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig_history import RunHistory
from goldenmatch.core.execution_plan import ExecutionPlan
from goldenmatch.web.controller_telemetry import serialize_telemetry
from polars.testing import assert_frame_equal


def _match_kernel_present() -> bool:
    try:
        from goldenmatch.core._native_loader import native_module

        return hasattr(native_module(), "match_fused")
    except Exception:
        return False


def _golden_kernel_present() -> bool:
    try:
        from goldenmatch.core._native_loader import native_module

        return hasattr(native_module(), "golden_fused")
    except Exception:
        return False


requires_match_kernel = pytest.mark.skipif(
    not _match_kernel_present(),
    reason="match_fused native kernel not built (build_native.py); CI builds it",
)
requires_golden_kernel = pytest.mark.skipif(
    not _golden_kernel_present(),
    reason="golden_fused native kernel not built (build_native.py); CI builds it",
)


def _people_df(n_clusters: int = 10, members: int = 3, n_singletons: int = 5) -> pl.DataFrame:
    rows: list[dict] = []
    for c in range(n_clusters):
        for _m in range(members):
            rows.append({"name": f"Cluster Person {c}", "zip": f"200{c:02d}"})
    for s in range(n_singletons):
        rows.append({"name": f"Solo Human {s}", "zip": f"900{s:02d}"})
    return pl.DataFrame(rows)


def _covered_config(scorer: str = "jaro_winkler") -> GoldenMatchConfig:
    """match_fused-covered shape: static zip blocking + one weighted name
    matchkey, auto_split off + quality_weighting off (config_needs_artifacts
    False), so the short-circuit is allowed when the flag is set."""
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


def _slow_golden_config(scorer: str = "jaro_winkler") -> GoldenMatchConfig:
    """Covered config whose golden is NOT _polars_native_eligible (a field_rules
    entry forces the slow / fused-golden arm), still artifact-free."""
    cfg = _covered_config(scorer)
    cfg.golden_rules.field_rules = {"name": GoldenFieldRule(strategy="most_complete")}
    return cfg


def _flag(cfg: GoldenMatchConfig) -> GoldenMatchConfig:
    """Simulate the controller post-step (under est-RSS pressure) setting
    ExecutionPlan.use_fused_match -> config._use_fused_match."""
    cfg._use_fused_match = True
    return cfg


def _multi_partition(clusters: dict) -> set[frozenset[int]]:
    return {frozenset(c["members"]) for c in clusters.values() if c["size"] > 1}


def _golden_content(g) -> pl.DataFrame:
    # v3.0.0: result frames are pa.Table; compare in polars (dev dep).
    if not isinstance(g, pl.DataFrame):
        g = pl.from_arrow(g)
    cols = [c for c in g.columns if c not in ("__cluster_id__", "__golden_confidence__")]
    return g.select(sorted(cols)).sort(sorted(cols))


# ── G.1: telemetry blob surfacing (plan-reachable markers) ──────────────────


def test_serialize_telemetry_surfaces_use_fused_match_and_rule_name():
    """A plan carrying the match-routing marker surfaces use_fused_match AND the
    +fused_match_post_step rule_name through the single cross-surface serializer."""
    history = RunHistory()
    history.execution_plan = ExecutionPlan(
        backend="polars-direct",
        rule_name="simple+fused_match_post_step",
        use_fused_match=True,
    )
    blob = serialize_telemetry(
        profile=None, history=history, committed_config=None,
        source=None, run_name=None, recorded_at=None,
    )
    ep = blob["execution_plan"]
    assert ep is not None
    assert ep["use_fused_match"] is True
    assert "fused_match_post_step" in ep["rule_name"]


def test_serialize_telemetry_default_plan_use_fused_match_false():
    """A classic (non-routed) plan surfaces use_fused_match False -- the marker
    is never silently absent from the blob."""
    history = RunHistory()
    history.execution_plan = ExecutionPlan(
        backend="polars-direct", rule_name="simple",
    )
    blob = serialize_telemetry(
        profile=None, history=history, committed_config=None,
        source=None, run_name=None, recorded_at=None,
    )
    ep = blob["execution_plan"]
    assert ep is not None
    assert ep["use_fused_match"] is False
    assert "fused_match_post_step" not in (ep["rule_name"] or "")


# ── G.1: DedupeResult surfacing (result-dict-origin markers) ────────────────


@requires_golden_kernel
def test_dedupe_result_surfaces_golden_fused_used(monkeypatch):
    """A covered (slow-golden) dedupe_df run genuinely uses the fused golden
    kernel -> DedupeResult.golden_fused_used is True; no match routing ->
    match_fused_capacity_mode is False."""
    monkeypatch.delenv("GOLDENMATCH_GOLDEN_FUSED", raising=False)
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    res = gm.dedupe_df(_people_df(), config=_slow_golden_config())
    assert res.golden_fused_used is True
    assert res.match_fused_capacity_mode is False


@requires_match_kernel
def test_dedupe_result_surfaces_match_capacity_mode(monkeypatch):
    """A fused-match-routed dedupe_df run (flag set) surfaces
    match_fused_capacity_mode True on DedupeResult, and sheds scored_pairs."""
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    res = gm.dedupe_df(_people_df(), config=_flag(_covered_config()))
    assert res.match_fused_capacity_mode is True
    assert res.scored_pairs == []


def test_dedupe_result_classic_both_markers_false(monkeypatch):
    """A classic run (both kill-switches on, no routing flag) surfaces both
    markers False -- the default posture."""
    monkeypatch.setenv("GOLDENMATCH_GOLDEN_FUSED", "0")
    monkeypatch.setenv("GOLDENMATCH_MATCH_FUSED", "0")
    res = gm.dedupe_df(_people_df(), config=_covered_config())
    assert res.golden_fused_used is False
    assert res.match_fused_capacity_mode is False


# ── G.2: the whole-feature parity lock ──────────────────────────────────────


@requires_golden_kernel
def test_end_to_end_golden_routing_parity(monkeypatch):
    """End-to-end through the real dedupe_df pipeline: the fused-golden route
    (golden_fused_used True) is byte-identical to the GOLDENMATCH_GOLDEN_FUSED=0
    classic golden build. Golden is pipeline-local default-on-when-covered, so
    this proves the true pipeline handoff (no controller needed for golden)."""
    df = _people_df()

    monkeypatch.delenv("GOLDENMATCH_GOLDEN_FUSED", raising=False)
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    fused = gm.dedupe_df(df, config=_slow_golden_config())
    assert fused.golden_fused_used is True

    monkeypatch.setenv("GOLDENMATCH_GOLDEN_FUSED", "0")
    classic = gm.dedupe_df(df, config=_slow_golden_config())
    assert classic.golden_fused_used is False

    assert fused.golden is not None and classic.golden is not None
    assert_frame_equal(
        pl.from_arrow(fused.golden).sort("__cluster_id__"),
        pl.from_arrow(classic.golden).sort("__cluster_id__"),
        check_column_order=False,
        check_row_order=False,
    )


@requires_match_kernel
def test_end_to_end_match_capacity_parity_lock(monkeypatch):
    """THE whole-feature parity lock. A covered dedupe_df run under SIMULATED
    pressure (the post-step flag set directly -- zero-config can't route match
    end-to-end because auto-config commits auto_split=True, which
    config_needs_artifacts hard-blocks) routes to fused match (capacity mode),
    and the FINAL result (clusters membership partition + golden) is
    byte-identical to the same run with both fused kill-switches ON (classic).
    scored_pairs empty under capacity mode, marked so it is never silent."""
    df = _people_df()

    # Classic reference: both fused paths off.
    monkeypatch.setenv("GOLDENMATCH_GOLDEN_FUSED", "0")
    monkeypatch.setenv("GOLDENMATCH_MATCH_FUSED", "0")
    classic = gm.dedupe_df(df, config=_covered_config())
    assert classic.match_fused_capacity_mode is False

    # Fused-routed run: flag set (simulated post-step under pressure), both
    # fused paths enabled.
    monkeypatch.delenv("GOLDENMATCH_GOLDEN_FUSED", raising=False)
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    fused = gm.dedupe_df(df, config=_flag(_covered_config()))

    # Routing fired + capacity-mode marker + shed scored_pairs.
    assert fused.match_fused_capacity_mode is True
    assert fused.scored_pairs == []

    # Cluster membership partition byte-identical.
    assert _multi_partition(fused.clusters) == _multi_partition(classic.clusters)

    # dupes / unique row populations byte-identical.
    assert set(fused.dupes["__row_id__"].to_pylist()) == set(
        classic.dupes["__row_id__"].to_pylist()
    )
    assert set(fused.unique.column("__row_id__").to_pylist()) == set(
        classic.unique.column("__row_id__").to_pylist()
    )

    # Golden content byte-identical (modulo cluster id + confidence).
    assert fused.golden is not None and classic.golden is not None
    assert_frame_equal(
        _golden_content(fused.golden), _golden_content(classic.golden)
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
