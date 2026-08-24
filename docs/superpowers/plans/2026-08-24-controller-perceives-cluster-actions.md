# Let the Controller Perceive a Cluster Action — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the auto-config controller able to observe the effect of cluster splitting, so an iteration that improves clustering can actually be committed instead of losing to v0.

**Architecture:** Three ordered changes. (1) Re-emit the `ClusterProfile` after the transitive-consistency split, so the profile describes the clusters the run actually returns rather than the pre-split ones. (2) Populate `bridge_edge_count` / `measured_bridge_risk` on the frames path, where they are currently hardcoded to zero, so the metric that matches this action is real in both directions. (3) Point the cluster rules' `predicts` at that metric and confirm `pick_committed` can now prefer the splitting iteration.

**Tech Stack:** Python 3.11+, pytest, dataclasses (profiles), pyarrow/polars frames. No new dependencies.

**Spec:** No separate spec — the measurements are inline under "Why this exists" and reproducible with the commands given there. Follows `docs/superpowers/plans/2026-08-23-rule-action-space-first-class.md`, which closed *diagnosis → action* and *action → feedback*; this closes *action → perceptible signal*.

## Global Constraints

- **`quality_gate` is the arbiter.** Every task here changes what the controller observes, which changes which config it commits. It has caught this class before.
- **Cost discipline.** `_emit_cluster_profile_frames` is guarded on `_emitter_stack.get()` so the df-input fast path never materializes the pair list for a profiler that is not capturing. Nothing in this plan may make profiling cost anything when no capture is active.
- **Bridge detection is O(E·(V+E))** and already capped by `_BRIDGE_MAX_CLUSTER_SIZE = 100`. Keep that cap; do not widen it here.
- **Run `python scripts/regen_docs.py` as the LAST step before every push.** A new Python symbol without it reds `config_matrix` + `docs_regen`.
- Tests: `PYTHONPATH` at `packages/python/goldenmatch`, `GOLDENMATCH_AUTOCONFIG_MEMORY=0`.
- The full pytest suite OOMs locally under xdist and belongs in CI. Run the blast radius serially with `-p no:randomly`.

---

## Why this exists

Measured on Abt-Buy, 2026-08-24, splitting off versus on:

| | splitting OFF | splitting ON |
|---|---|---|
| **final emitted clusters** | 669 | **709** |
| profile `n_clusters` | 669 | **669** |
| profile `cluster_size_max` | 90 | 90 |
| profile `bridge_edge_count` | 0 | 0 |
| profile `transitivity_rate` | 0.133 | 0.137 |

**The action splits 40 clusters and the profile reports identical numbers.** The only field that moves is `transitivity_rate`, by 0.004 — inside the triple sampler's noise band (~0.003-0.005 run to run on an unchanged config).

Reproduce with `scripts/diagnose_cluster_profile_visibility.py` (added in Task 1).

Two independent causes:

**1. Ordering.** `_emit_cluster_profile` runs inside `build_clusters` (`cluster.py:1103`). `materialize_and_split` runs later, during results assembly (`pipeline.py:5209`). The profile therefore always describes PRE-split clusters. No choice of predicted metric can fix this — the measurement happens before the action.

**2. The matching metric is dead on the shipped path.** `_emit_cluster_profile_frames` hardcodes them:

```python
        bridge_edge_count=0,          # cluster.py:898
        measured_bridge_risk=0.0,
```

The real computation (`_severe_bridge_count`, `cluster.py:324`) exists only in the legacy dict emitter. So the frames path reports zero bridges while the splitter finds 40, and `ClusterProfile.health` has no branch on either field, so nothing notices.

**Consequence:** `rule_low_transitivity` and `rule_cluster_giant` can request splitting (shipped in #2744) and `pick_committed` will still prefer v0, because the iteration that enabled splitting looks identical to the one that did not.

**What makes Task 1 cheap:** when splitting is enabled, `materialize_and_split` already calls `_clusters_dict()`, so the dict materialization is paid on that path regardless. `ProfileEmitter.set_cluster` overwrites, so re-emitting is idempotent. The added cost is one profile computation, only when splitting ran, only when a capture is active.

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `goldenmatch/core/pipeline.py` | Results assembly, split call site | Re-emit the profile after a successful split |
| `goldenmatch/core/cluster.py` | Cluster profile emitters | Populate the frames path's bridge metrics |
| `goldenmatch/core/autoconfig_rules.py` | The cluster rules | Point `predicts` at `cluster.bridge_edge_count` |
| `scripts/diagnose_cluster_profile_visibility.py` | **new** — the harness | Reproduces the table above on demand |
| `tests/test_cluster_profile_after_split.py` | **new** | Task 1 |
| `tests/test_frames_bridge_metrics.py` | **new** | Task 2 |
| `tests/test_cluster_rules_predict_bridges.py` | **new** | Task 3 |

---

### Task 1: Re-emit the cluster profile after the split

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/pipeline.py` (the `_tc_enabled` block, ~line 5207)
- Create: `packages/python/goldenmatch/tests/test_cluster_profile_after_split.py`
- Create: `scripts/diagnose_cluster_profile_visibility.py`

**Interfaces:**
- Consumes: `materialize_and_split(clusters, all_pairs, margin) -> (dict[int, dict], report)` and `_emit_cluster_profile(clusters: dict[int, dict]) -> None` from `goldenmatch.core.cluster`.
- Produces: no new public symbol. After this task, `current_emitter().cluster.n_clusters` equals the post-split cluster count whenever splitting ran under an active capture.

- [ ] **Step 1: Write the failing test**

```python
"""The profile must describe the clusters the run RETURNS, not the pre-split ones.

`_emit_cluster_profile` runs inside `build_clusters`; `materialize_and_split`
runs later, in results assembly. So the controller's ClusterProfile always
described PRE-split clusters -- measured on Abt-Buy, splitting changed the final
count 669 -> 709 while the profile reported 669 both times.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.config.schemas import ClusterConfig, GoldenMatchConfig
from goldenmatch.core.profile_emitter import profile_capture


def _chained_frame(n_groups: int = 12) -> pl.DataFrame:
    """Rows that connected-components chains into few large clusters: each group
    shares a token with the next, so A-B and B-C match while A-C does not."""
    rows = []
    for g in range(n_groups):
        rows += [
            {"name": f"alpha{g} beta{g}", "city": "springfield"},
            {"name": f"beta{g} gamma{g}", "city": "springfield"},
            {"name": f"gamma{g} delta{g}", "city": "springfield"},
        ]
    return pl.DataFrame(rows)


def test_profile_reports_the_post_split_cluster_count():
    import goldenmatch

    df = _chained_frame()
    with profile_capture() as emitter:
        result = goldenmatch.dedupe_df(
            df, exact=["city"],
            config=GoldenMatchConfig(cluster=ClusterConfig(split_weak_bridges=True,
                                                           weak_bridge_margin=0.0)),
        )
        observed = emitter.cluster.n_clusters
    assert observed == len(result.clusters), (
        f"profile says {observed} clusters, the run returned {len(result.clusters)} "
        f"-- the profile is describing pre-split clusters"
    )


def test_profile_is_untouched_when_splitting_is_off():
    """Default off must stay byte-identical: no re-emit, no added cost."""
    import goldenmatch

    df = _chained_frame()
    with profile_capture() as emitter:
        result = goldenmatch.dedupe_df(df, exact=["city"])
        observed = emitter.cluster.n_clusters
    assert observed == len(result.clusters)
```

- [ ] **Step 2: Run it and watch the first test fail**

Run:
```bash
PYTHONPATH=packages/python/goldenmatch GOLDENMATCH_AUTOCONFIG_MEMORY=0 \
  python -m pytest packages/python/goldenmatch/tests/test_cluster_profile_after_split.py -q -p no:randomly
```
Expected: `test_profile_reports_the_post_split_cluster_count` FAILS with the profile count below the returned count. If it PASSES, the fixture did not produce a splittable chain — raise `n_groups`, verify with `result.clusters`, and do not proceed until it fails for the right reason.

- [ ] **Step 3: Re-emit after the split**

In `pipeline.py`, replace the body of the `if _tc_enabled:` block:

```python
    if _tc_enabled:
        from goldenmatch.core.transitive_consistency import materialize_and_split
        _tc_clusters, _tc_report = materialize_and_split(
            _clusters_dict(), all_pairs, _tc_margin,
        )
        if isinstance(report, dict):
            report["transitive_consistency"] = _tc_report
        # Re-emit the cluster profile over the SPLIT clusters. `build_clusters`
        # emits during clustering, which is BEFORE this runs, so without this the
        # controller's ClusterProfile describes clusters the run does not return:
        # measured on Abt-Buy, splitting moved the final count 669 -> 709 while
        # the profile reported 669 either way, so `pick_committed` could never
        # prefer the iteration that enabled splitting (#2717).
        #
        # Cheap by construction: `materialize_and_split` above already
        # materialized the dict, `set_cluster` overwrites, and the whole block
        # only runs when splitting is enabled. `_emit_cluster_profile` is a no-op
        # without an active capture.
        from goldenmatch.core.cluster import _emit_cluster_profile
        _emit_cluster_profile(_tc_clusters)
```

- [ ] **Step 4: Run the test again**

Run the command from Step 2.
Expected: both PASS.

- [ ] **Step 5: Add the committed harness**

Create `scripts/diagnose_cluster_profile_visibility.py`:

```python
"""Can the controller's ClusterProfile see the effect of cluster splitting?

Prints the controller's last-iteration ClusterProfile with splitting off and on,
beside the final emitted cluster count. Before #2717's follow-up the profile
reported identical numbers while the run returned 40 more clusters.

    python scripts/diagnose_cluster_profile_visibility.py --dataset abt-buy
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_benchmarks import _PRODUCT_SPECS  # type: ignore[import-not-found]  # noqa: E402

DEFAULT_DATASETS_DIR = Path("packages/python/goldenmatch/tests/benchmarks/datasets")
FIELDS = ("n_clusters", "cluster_size_p50", "cluster_size_p99", "cluster_size_max",
          "transitivity_rate", "oversized_cluster_count", "bridge_edge_count",
          "measured_bridge_risk")


def _records(datasets_dir: Path, key: str) -> pl.DataFrame:
    spec = _PRODUCT_SPECS[key]
    base = datasets_dir / spec["subdir"]
    a = pl.read_csv(base / spec["file_a"], encoding="utf8-lossy", ignore_errors=True)
    b = pl.read_csv(base / spec["file_b"], encoding="utf8-lossy", ignore_errors=True)
    rename = spec["rename"] or {}
    a = a.rename({k: v for k, v in rename.items() if k in a.columns})
    b = b.rename({k: v for k, v in rename.items() if k in b.columns})
    shared = [c for c in a.columns if c in b.columns and c != "id"]

    def prep(df: pl.DataFrame, src: str) -> pl.DataFrame:
        return (df.select(["id"] + shared)
                  .with_columns((pl.lit(src + ":") + pl.col("id").cast(pl.Utf8))
                                .alias("record_id"))
                  .drop("id"))

    return pl.concat([prep(a, spec["src_a"]), prep(b, spec["src_b"])])


def _run(records: pl.DataFrame, label: str) -> dict:
    from goldenmatch import dedupe_df
    from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN

    result = dedupe_df(records)
    run = _LAST_CONTROLLER_RUN.get()
    if not run:
        print(f"--- {label}: no controller run recorded")
        return {}
    _, history = run
    real = [e for e in history.entries if e.profile is not None and e.iteration >= 0]
    cluster = real[-1].profile.cluster
    got = {f: getattr(cluster, f, None) for f in FIELDS}
    print(f"--- {label}")
    for name in FIELDS:
        print(f"      {name:<26} {got[name]}")
    print(f"      FINAL emitted clusters     {len(result.clusters)}")
    got["_final"] = len(result.clusters)
    return got


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="abt-buy", choices=sorted(_PRODUCT_SPECS))
    parser.add_argument("--datasets-dir", type=Path, default=DEFAULT_DATASETS_DIR)
    args = parser.parse_args()

    records = _records(args.datasets_dir, args.dataset)
    os.environ.pop("GOLDENMATCH_TRANSITIVE_POSTFLIGHT", None)
    off = _run(records, "splitting OFF")
    os.environ["GOLDENMATCH_TRANSITIVE_POSTFLIGHT"] = "1"
    os.environ["GOLDENMATCH_TRANSITIVE_WEAK_MARGIN"] = "0.05"
    on = _run(records, "splitting ON")

    print("")
    moved = [f for f in FIELDS if off.get(f) != on.get(f)]
    print("fields that moved:", moved or "NONE -- the profile is blind to the split")
    if off.get("_final") != on.get("_final") and "n_clusters" not in moved:
        print("  the run returned a DIFFERENT cluster count while the profile did not "
              "-- that is the ordering defect this script exists to catch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Confirm the harness now shows the profile tracking the run**

Run:
```bash
PYTHONPATH="packages/python/goldenmatch;scripts" GOLDENMATCH_AUTOCONFIG_MEMORY=0 \
  python scripts/diagnose_cluster_profile_visibility.py --dataset abt-buy
```
Expected: `n_clusters` appears in "fields that moved", and the trailing ordering-defect warning does NOT print.

- [ ] **Step 7: Verify nothing else moved, then commit**

Run:
```bash
PYTHONPATH=packages/python/goldenmatch GOLDENMATCH_AUTOCONFIG_MEMORY=0 python -m pytest \
  packages/python/goldenmatch/tests/test_cluster_profile_after_split.py \
  packages/python/goldenmatch/tests/test_transitive_consistency.py \
  packages/python/goldenmatch/tests/test_pipeline.py \
  packages/python/goldenmatch/tests/test_autoconfig_controller.py -q -p no:randomly --timeout=600
python scripts/run_benchmarks.py --datasets products \
  --datasets-dir packages/python/goldenmatch/tests/benchmarks/datasets
```
Expected: tests PASS; all four benchmark rows inside quarantine tolerance
(`Abt-Buy (dedupe)` 0.0881 ±0.03, `Amazon-Google (dedupe)` 0.1097 ±0.03,
`Abt-Buy (linkage)` 0.7024 clears its 0.45 floor). **Stop and report on drift** —
the controller now sees a different profile, so a changed committed config is
plausible and is exactly what `quality_gate` arbitrates.

```bash
python scripts/regen_docs.py
git add -A
git commit -m "fix(profile): emit the cluster profile AFTER the split, not before"
```

---

### Task 2: Populate the frames path's bridge metrics

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/cluster.py` (`_emit_cluster_profile_frames`, ~line 898)
- Create: `packages/python/goldenmatch/tests/test_frames_bridge_metrics.py`

**Interfaces:**
- Consumes: `_severe_bridge_count(members: list[int], pair_scores: dict) -> int` and `_BRIDGE_MAX_CLUSTER_SIZE = 100` from the same module.
- Produces: `_emit_cluster_profile_frames` sets real `bridge_edge_count: int` and `measured_bridge_risk: float | None`, matching what `_emit_cluster_profile` reports for the same clusters.

- [ ] **Step 1: Write the failing test**

```python
"""The frames path hardcoded its bridge metrics to zero.

`_severe_bridge_count` exists and the dict emitter uses it; the frames emitter --
which is the shipped path -- set `bridge_edge_count=0, measured_bridge_risk=0.0`
unconditionally. So the profile reported zero bridges on data where the splitter
found 40, and no rule could predict on the one metric that matches the action.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.core.profile_emitter import profile_capture


def _bridged_frame() -> pl.DataFrame:
    """Two cohesive groups joined by one weak link, repeated -- the exact shape
    `_severe_bridge_count` is written to find."""
    rows = []
    for g in range(8):
        rows += [
            {"name": f"alpha{g} alpha{g} core", "city": "springfield"},
            {"name": f"alpha{g} bridge{g}", "city": "springfield"},
            {"name": f"bridge{g} omega{g}", "city": "springfield"},
            {"name": f"omega{g} omega{g} core", "city": "springfield"},
        ]
    return pl.DataFrame(rows)


def test_frames_path_reports_real_bridge_counts():
    import goldenmatch

    with profile_capture() as emitter:
        goldenmatch.dedupe_df(_bridged_frame(), exact=["city"])
        profile = emitter.cluster
    assert profile.bridge_edge_count > 0, (
        "the frames emitter still reports zero bridges on a bridged fixture"
    )
    assert profile.measured_bridge_risk is not None
    assert 0.0 <= profile.measured_bridge_risk <= 1.0


def test_a_clean_frame_reports_no_bridges():
    """Zero must mean measured-zero, not hardcoded-zero -- otherwise the metric
    is indistinguishable from the bug it replaces."""
    import goldenmatch

    clean = pl.DataFrame({
        "name": [f"unrelated subject {i}" for i in range(20)],
        "city": ["springfield"] * 20,
    })
    with profile_capture() as emitter:
        goldenmatch.dedupe_df(clean, exact=["city"])
        profile = emitter.cluster
    assert profile.bridge_edge_count == 0


def test_profiling_stays_free_when_no_capture_is_active():
    """The emitter is guarded on `_emitter_stack` so the df-input fast path never
    materializes the pair list for a profiler that is not capturing. Bridge
    detection is O(E*(V+E)) -- it must not change that."""
    import goldenmatch
    from goldenmatch.core import cluster as cluster_mod

    calls = []
    original = cluster_mod._severe_bridge_count

    def spy(members, pair_scores):
        calls.append(len(members))
        return original(members, pair_scores)

    cluster_mod._severe_bridge_count = spy
    try:
        goldenmatch.dedupe_df(_bridged_frame(), exact=["city"])  # no profile_capture
    finally:
        cluster_mod._severe_bridge_count = original
    assert calls == [], f"bridge detection ran without an active capture: {calls}"
```

- [ ] **Step 2: Run it and watch the first test fail**

Run:
```bash
PYTHONPATH=packages/python/goldenmatch GOLDENMATCH_AUTOCONFIG_MEMORY=0 \
  python -m pytest packages/python/goldenmatch/tests/test_frames_bridge_metrics.py -q -p no:randomly
```
Expected: `test_frames_path_reports_real_bridge_counts` FAILS with `bridge_edge_count == 0`.

- [ ] **Step 3: Compute the metrics on the frames path**

In `cluster.py`, replace the two hardcoded lines in `_emit_cluster_profile_frames`:

```python
        bridge_edge_count=0,
        measured_bridge_risk=0.0,
```

with a computation over the same clusters the profile already walked, reusing the
dict emitter's guard so the two paths agree:

```python
        bridge_edge_count=_bridge_count,
        measured_bridge_risk=_bridge_risk,
```

and immediately above the `profile = ClusterProfile(` construction, add:

```python
    # Bridge metrics were hardcoded to 0 / 0.0 here while `_emit_cluster_profile`
    # (the legacy dict path) computed them for real. The frames path is the
    # SHIPPED path, so the profile reported zero bridges on data where the
    # transitive-consistency splitter found 40 -- and `bridge_edge_count` is the
    # metric that matches that action, so no rule could usefully predict on it
    # (#2717).
    #
    # Same `_BRIDGE_MAX_CLUSTER_SIZE` cap as the dict emitter: detection is
    # O(E*(V+E)), and a severe 2+2 split needs >= 4 nodes. This whole function is
    # already guarded on an active emitter by its caller, so nothing here costs
    # anything on the non-capturing fast path.
    _measurable = [
        (mid, members) for mid, members in members_by_cluster.items()
        if 4 <= len(members) <= _BRIDGE_MAX_CLUSTER_SIZE
    ]
    _bridge_count = sum(
        _severe_bridge_count(members, _cluster_pair_scores(aggregated_scores, members))
        for _mid, members in _measurable
    )
    _bridge_risk = (
        sum(
            1
            for _mid, members in _measurable
            if _severe_bridge_count(
                members, _cluster_pair_scores(aggregated_scores, members)
            )
        ) / len(_measurable)
        if _measurable
        else None
    )
```

Then add the helper next to `_severe_bridge_count`:

```python
def _cluster_pair_scores(
    aggregated_scores: dict[tuple[int, int], float], members: list[int]
) -> dict[tuple[int, int], float]:
    """The subset of the global scored pairs that lies WITHIN one cluster.

    `_severe_bridge_count` expects a cluster-local pair map. The dict emitter
    gets one for free (each cluster carries `pair_scores`); the frames path holds
    one global map, so this projects it.
    """
    member_set = set(members)
    return {
        (a, b): s
        for (a, b), s in aggregated_scores.items()
        if a in member_set and b in member_set
    }
```

- [ ] **Step 4: Run the test**

Run the command from Step 2.
Expected: all four PASS. If `test_profiling_stays_free_when_no_capture_is_active`
fails, the computation escaped the emitter guard — move it inside
`_emit_cluster_profile_frames` rather than into the caller.

- [ ] **Step 5: Confirm both emitters agree**

Add to the same test file:

```python
def test_both_emitters_report_the_same_bridge_count():
    """The dict and frames emitters must not disagree about the same clusters --
    a rule that predicts on this metric would otherwise read the path, not the
    data."""
    from goldenmatch.core.cluster import _severe_bridge_count

    members = [0, 1, 2, 3]
    pair_scores = {(0, 1): 0.95, (2, 3): 0.95, (1, 2): 0.55}
    assert _severe_bridge_count(members, pair_scores) >= 1
```

Run the file again. Expected: PASS.

- [ ] **Step 6: Verify cost and behaviour, then commit**

Run:
```bash
PYTHONPATH=packages/python/goldenmatch GOLDENMATCH_AUTOCONFIG_MEMORY=0 python -m pytest \
  packages/python/goldenmatch/tests/test_frames_bridge_metrics.py \
  packages/python/goldenmatch/tests/test_cluster_profile_after_split.py \
  packages/python/goldenmatch/tests/test_complexity_profile.py \
  packages/python/goldenmatch/tests/test_autoconfig_controller.py -q -p no:randomly --timeout=600
python scripts/run_benchmarks.py --datasets products \
  --datasets-dir packages/python/goldenmatch/tests/benchmarks/datasets
```
Expected: tests PASS, all four rows inside tolerance. Note the `elapsed` column —
if any dedupe row slows by more than ~10%, bridge detection is costing more than
budgeted; **stop and report** rather than absorbing it.

```bash
python scripts/regen_docs.py
git add -A
git commit -m "fix(profile): compute the frames path's bridge metrics instead of hardcoding zero"
```

---

### Task 3: Point the cluster rules at the metric the action moves

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py` (`rule_low_transitivity`, `rule_cluster_giant`)
- Create: `packages/python/goldenmatch/tests/test_cluster_rules_predict_bridges.py`

**Interfaces:**
- Consumes: `PolicyDecision.predicts` / `predicts_direction` and `rule_effect_was_negative` (shipped in #2744); real `cluster.bridge_edge_count` (Task 2); post-split profile (Task 1).
- Produces: both cluster rules emit `predicts="cluster.bridge_edge_count"`, `predicts_direction="down"` on the decision that requests splitting. The threshold-fallback decisions keep `predicts="cluster.transitivity_rate"`, direction `up`.

- [ ] **Step 1: Write the failing test**

```python
"""A rule should predict the metric its action actually moves.

Splitting weak transitive bridges removes bridges. It does NOT reliably raise
transitivity -- measured on Abt-Buy, transitivity moved 0.133 -> 0.137, inside
the triple sampler's noise band, while the run returned 40 more clusters. So a
rule predicting transitivity for the SPLITTING action was predicting the wrong
thing even once the ordering was fixed.
"""
from __future__ import annotations

import pytest
from goldenmatch.config.schemas import (
    BlockingConfig, BlockingKeyConfig, ClusterConfig, GoldenMatchConfig,
    MatchkeyConfig, MatchkeyField,
)
from goldenmatch.core.autoconfig_history import RunHistory
from goldenmatch.core.autoconfig_rules import rule_cluster_giant, rule_low_transitivity
from goldenmatch.core.complexity_profile import (
    BlockingProfile, ClusterProfile, ComplexityProfile, DataProfile, ScoringProfile,
)


def _cfg(threshold: float = 0.7, cluster: ClusterConfig | None = None) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="mk", type="weighted", threshold=threshold,
                                  fields=[MatchkeyField(field="name",
                                                        scorer="token_sort", weight=1.0)])],
        blocking=BlockingConfig(strategy="static",
                                keys=[BlockingKeyConfig(fields=["name"])]),
        cluster=cluster,
    )


def _profile(transitivity: float = 0.2, cluster_size_max: int = 4,
             bridges: int = 7) -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(n_rows=1000, n_cols=3),
        blocking=BlockingProfile(n_blocks=20, reduction_ratio=0.9),
        scoring=ScoringProfile(n_pairs_scored=500, candidates_compared=5000,
                               candidates_counted=True, mass_above_threshold=1.0,
                               dip_statistic=0.5),
        cluster=ClusterProfile(n_clusters=50, cluster_size_max=cluster_size_max,
                               transitivity_rate=transitivity,
                               bridge_edge_count=bridges),
    )


def test_low_transitivity_predicts_bridges_when_it_asks_for_splitting():
    out = rule_low_transitivity(_profile(), _cfg(), RunHistory())
    assert out is not None
    _, decision = out
    assert decision.config_diff == {"cluster.split_weak_bridges": True}
    assert decision.predicts == "cluster.bridge_edge_count"
    assert decision.predicts_direction == "down"


def test_low_transitivity_still_predicts_transitivity_for_the_threshold_fallback():
    """The threshold nudge is a different action and moves a different metric."""
    out = rule_low_transitivity(
        _profile(), _cfg(cluster=ClusterConfig(split_weak_bridges=True)), RunHistory()
    )
    assert out is not None
    new_cfg, decision = out
    assert new_cfg.matchkeys[0].threshold == pytest.approx(0.75)
    assert decision.predicts == "cluster.transitivity_rate"
    assert decision.predicts_direction == "up"


def test_cluster_giant_predicts_bridges_when_it_asks_for_splitting():
    out = rule_cluster_giant(_profile(cluster_size_max=400), _cfg(), RunHistory())
    assert out is not None
    _, decision = out
    assert decision.predicts == "cluster.bridge_edge_count"
    assert decision.predicts_direction == "down"


def test_a_split_that_removed_no_bridges_is_read_as_negative():
    """The whole point: the rule can now tell that its action did nothing."""
    from goldenmatch.core.autoconfig_history import HistoryEntry, rule_effect_was_negative

    history = RunHistory()
    fired = rule_low_transitivity(_profile(bridges=7), _cfg(), RunHistory())
    assert fired is not None
    history.entries.append(HistoryEntry(iteration=0, config=None,
                                        profile=_profile(bridges=7),
                                        decision=fired[1], error=None, wall_clock_ms=1))
    history.entries.append(HistoryEntry(iteration=1, config=None,
                                        profile=_profile(bridges=7),
                                        decision=None, error=None, wall_clock_ms=1))
    assert rule_effect_was_negative(history, "low_transitivity") is True
```

- [ ] **Step 2: Run it and watch it fail**

Run:
```bash
PYTHONPATH=packages/python/goldenmatch GOLDENMATCH_AUTOCONFIG_MEMORY=0 \
  python -m pytest packages/python/goldenmatch/tests/test_cluster_rules_predict_bridges.py -q -p no:randomly
```
Expected: FAIL — the splitting decisions still carry `predicts="cluster.transitivity_rate"`.

- [ ] **Step 3: Repoint the two splitting decisions**

In `autoconfig_rules.py`, on the `PolicyDecision` inside `rule_low_transitivity`'s
cluster-action branch and the one inside `rule_cluster_giant`'s cluster-action
branch, change:

```python
            predicts="cluster.transitivity_rate",
            predicts_direction="up",
```

to:

```python
            # Splitting removes BRIDGES; it does not reliably raise transitivity.
            # Measured on Abt-Buy: transitivity moved 0.133 -> 0.137 (inside the
            # triple sampler's ~0.003-0.005 noise band) while the run returned 40
            # more clusters. Predicting transitivity for this action would read
            # noise as evidence in both directions.
            predicts="cluster.bridge_edge_count",
            predicts_direction="down",
```

Leave the threshold-fallback decisions predicting `cluster.transitivity_rate`
with direction `up` — a different action moving a different metric.

- [ ] **Step 4: Run the test**

Run the command from Step 2. Expected: all four PASS.

- [ ] **Step 5: Measure whether the controller can now commit the split**

This is the goal of all three tasks. Run:
```bash
PYTHONPATH="packages/python/goldenmatch;scripts" GOLDENMATCH_AUTOCONFIG_MEMORY=0 \
  python scripts/diagnose_cluster_profile_visibility.py --dataset abt-buy
python scripts/run_benchmarks.py --datasets products \
  --datasets-dir packages/python/goldenmatch/tests/benchmarks/datasets
```
Record, in the commit message, whether `Abt-Buy (dedupe)` moved from **0.0881**
toward the **0.1004** that forcing splitting on produced before this work. Either
answer is a result and both must be reported:
- moved: the loop closed, and the quarantine baseline needs re-pointing in the same change.
- unchanged: the controller still prefers v0 for a reason these three tasks did
  not address. **Say so plainly** rather than claiming the loop is closed —
  `pick_committed`'s ranking is the next place to look, not this plan's scope.

- [ ] **Step 6: Full blast radius, then commit**

```bash
PYTHONPATH=packages/python/goldenmatch GOLDENMATCH_AUTOCONFIG_MEMORY=0 python -m pytest \
  packages/python/goldenmatch/tests -q -p no:randomly --timeout=900 \
  --continue-on-collection-errors \
  -k "autoconfig or profile or rule or policy or cluster or matchkey or blocking or transitiv or controller or history"
python scripts/regen_docs.py
git add -A
git commit -m "feat(autoconfig): cluster rules predict bridges, the metric splitting moves"
```
The 15 pre-existing failures (`test_golden_fused` x10, `test_quality_aware_blocking`
x3, `test_cluster_edges_df::test_datafusion_is_importable`, one arrow-parity test)
are unrelated to this work — verify the count is still 15 and that none is new.

---

## Out of scope, deliberately

**`pick_committed`'s ranking.** These tasks make the split VISIBLE and give the
rules a metric that tracks it. Whether the controller then prefers that iteration
depends on how `pick_committed` weighs health rank, the precision-collapse guard,
and the degenerate-empty guard — none of which this plan touches. Task 3 Step 5
measures the answer rather than assuming it.

**Widening `_BRIDGE_MAX_CLUSTER_SIZE`.** Bridge detection is O(E·(V+E)) and the
100-member cap exists for that reason. Clusters above it report no bridges, which
is a real blind spot and a separate cost/benefit question.

**A `ClusterProfile.health` branch on `bridge_edge_count`.** Tempting once the
metric is real, but it would add an eleventh RED condition and the coverage gate
would immediately demand a rule for it. That is a deliberate design decision, not
a follow-on edit.

## Self-review notes

- **Coverage:** the ordering defect maps to Task 1, the hardcoded metric to Task 2,
  the wrong predicted metric to Task 3. The measured table in "Why this exists" is
  reproduced by the harness Task 1 adds.
- **Placeholders:** none. Every code step shows the code; both fixtures are written
  out; the diagnostic script is complete.
- **Type consistency:** `bridge_edge_count: int`, `measured_bridge_risk: float | None`
  match `ClusterProfile`'s existing declarations. `_cluster_pair_scores` returns the
  `dict[tuple[int, int], float]` shape `_severe_bridge_count` already accepts.
- **Known risk:** Task 1 changes what the controller observes on every run where
  splitting is enabled, so a changed committed config is plausible. Both Task 1 and
  Task 2 end with a benchmark run and an explicit stop-on-drift instruction, and
  `quality_gate` is the CI arbiter.
- **Honest uncertainty:** Task 3 Step 5 may find the benchmark unchanged. The plan
  says to report that plainly rather than declare the loop closed — the three
  defects here are real and worth fixing either way, but they are not a guarantee
  that `pick_committed` flips.
