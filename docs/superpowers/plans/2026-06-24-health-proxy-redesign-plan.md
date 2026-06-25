# Self-Verify Health-Proxy Redesign Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the recall-biased self-verify health proxy with a precision-sensitive `cohesion × coverage` proxy (cohesion = a low-tail intra-cluster edge statistic), gated `legacy`-by-default, and prove on the gym (live recovery rises) + oracle (suggester_precision holds) which cohesion statistic wins.

**Architecture:** All changes in `goldenmatch/core/suggest/health.py`: keep the current formula as `_health_legacy`, add `suggestion_health_cohesion`, and make the public `suggestion_health_from_clusters` a thin env-gated selector (so the adapter call site is unchanged and `legacy` default is byte-identical). The gym/oracle already drive the verify path — a sweep is just env vars.

**Tech Stack:** Python 3.11+ (pure functions over the clusters dict; no native, no pipeline for the unit tests). Validation uses the existing `scripts/suggest_quality` gym + oracle.

**Spec:** `docs/superpowers/specs/2026-06-24-health-proxy-redesign-design.md`

---

## Conventions

- Work from `D:\show_case\goldenmatch\.worktrees\suggest-gym` (branch `feat/suggest-gym`). Local commits only — NO push, NO PR.
- Python: `D:/show_case/goldenmatch/.venv/Scripts/python.exe`, Windows PYTHONPATH separator `;`:
  `export PYTHONPATH="D:/show_case/goldenmatch/.worktrees/suggest-gym/packages/python/goldenmatch;D:/show_case/goldenmatch/.worktrees/suggest-gym"` + `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8`.
- Tasks 1-3 are PURE (no native, no pipeline) — fast. Task 4 runs the gym/oracle (native, minutes).
- Never run the full suite. Commit after each green step. Trailers on every commit:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` / `Claude-Session: https://claude.ai/code/session_01Wz94wngiSXtkxzBPKzqyUy`.

## Grounded facts (from planning instrumentation — build against these)

- The clusters dict (from `EngineResult.clusters` / `_run_config`) per-cluster keys: `members, size, oversized, pair_scores, confidence, bottleneck_pair, cluster_quality`. `pair_scores` is a `dict[(a,b)->score]`. Per-cluster min edge = `min(pair_scores.values())` (or `pair_scores[bottleneck_pair]`).
- On the synthetic `threshold_too_low` case: `cluster_quality` is `"strong"` for ALL clusters and `confidence`/`min-edge` p10 are saturated at 1.000 — so coarse-flag `weak_fraction` and `p10_conf` are DEAD. The signal is the GLOBAL MIN / bottom-tail: conf-min 0.678 (degraded) vs 0.790 (clean); minedge-min 0.657 vs 0.800.
- Verified the product flips correctly with min-edge cohesion: degraded `0.788×0.657=0.518` < clean `0.764×0.800=0.611` → fix KEPT.
- The current legacy proxy EXCLUDES `oversized` clusters. The new cohesion statistic INCLUDES all `size>1` clusters (oversized too) — an oversized cluster IS over-merge and must count against cohesion.

---

## File structure
- Modify only: `packages/python/goldenmatch/goldenmatch/core/suggest/health.py` (legacy body → `_health_legacy`; new `suggestion_health_cohesion` + helpers; selector).
- Test: `packages/python/goldenmatch/tests/test_health_cohesion.py` (new).
- Task 4 appends a findings note to the spec + (if a winner emerges) records it.

---

## Task 1: Cohesion statistics (pure helpers)

**Files:** Modify `health.py`; Test `tests/test_health_cohesion.py`.

- [ ] **Step 1: Failing tests** — over `clusters` dicts built inline:

```python
from goldenmatch.core.suggest.health import (
    _cluster_min_edges, _cohesion_min, _cohesion_mean_bottomk, _cohesion_edge_below_cutoff,
)

def _cl(*min_edges):
    # build a clusters dict where cluster i has a 2-member cluster whose
    # single intra-pair score == min_edges[i]
    return {
        i: {"size": 2, "oversized": False, "members": [2*i, 2*i+1],
            "pair_scores": {(2*i, 2*i+1): e}, "confidence": e,
            "cluster_quality": "strong", "bottleneck_pair": (2*i, 2*i+1)}
        for i, e in enumerate(min_edges)
    }

def test_cluster_min_edges_extracts_per_cluster_min():
    assert sorted(_cluster_min_edges(_cl(0.9, 0.66, 0.8))) == [0.66, 0.8, 0.9]

def test_cohesion_min_is_global_min():
    assert _cohesion_min(_cl(0.9, 0.66, 0.8)) == 0.66

def test_cohesion_mean_bottomk_averages_weakest():
    # k=2 weakest of [0.9,0.66,0.8] -> mean(0.66,0.8)=0.73
    assert abs(_cohesion_mean_bottomk(_cl(0.9, 0.66, 0.8), k=2) - 0.73) < 1e-9

def test_cohesion_edge_below_cutoff_fraction():
    # cutoff 0.75: edges below = {0.66}; 1 of 3 -> 1 - 1/3
    assert abs(_cohesion_edge_below_cutoff(_cl(0.9, 0.66, 0.8), cutoff=0.75) - (1 - 1/3)) < 1e-9

def test_min_edges_includes_oversized_clusters():
    cl = _cl(0.9)
    cl[1] = {"size": 50, "oversized": True, "members": list(range(50)),
             "pair_scores": {(0, 1): 0.4}, "confidence": 0.4, "cluster_quality": "strong",
             "bottleneck_pair": (0, 1)}
    assert min(_cluster_min_edges(cl)) == 0.4  # oversized counted

def test_empty_returns_empty():
    assert _cluster_min_edges({}) == []
```

- [ ] **Step 2: Run → fail.** `... -m pytest packages/python/goldenmatch/tests/test_health_cohesion.py -v`
- [ ] **Step 3: Implement** in `health.py`:

```python
def _cluster_min_edges(clusters: dict) -> list[float]:
    """Per-cluster weakest intra-cluster edge, over all multi-member clusters
    (INCLUDING oversized -- an oversized cluster is over-merge we must penalise)."""
    out: list[float] = []
    for info in clusters.values():
        if info.get("size", 1) <= 1:
            continue
        ps = info.get("pair_scores") or {}
        if isinstance(ps, dict) and ps:
            out.append(min(ps.values()))
        else:
            # fall back to confidence when pair_scores absent
            out.append(float(info.get("confidence", 0.5)))
    return out

def _cohesion_min(clusters: dict) -> float:
    edges = _cluster_min_edges(clusters)
    return min(edges) if edges else 0.0

def _cohesion_mean_bottomk(clusters: dict, k: int = 5) -> float:
    edges = sorted(_cluster_min_edges(clusters))
    if not edges:
        return 0.0
    bottom = edges[: max(1, min(k, len(edges)))]
    return sum(bottom) / len(bottom)

def _cohesion_edge_below_cutoff(clusters: dict, cutoff: float = 0.75) -> float:
    edges = _cluster_min_edges(clusters)
    if not edges:
        return 1.0  # no clusters -> no weak clusters (coverage handles recall collapse)
    below = sum(1 for e in edges if e < cutoff)
    return 1.0 - below / len(edges)
```

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(health): low-tail cohesion statistics`.

---

## Task 2: `suggestion_health_cohesion` (cohesion × saturating coverage)

**Files:** `health.py`; `tests/test_health_cohesion.py` (extend).

- [ ] **Step 1: Failing tests** — the KEY one is the over-merge inversion the legacy proxy gets wrong:

```python
from goldenmatch.core.suggest.health import suggestion_health_cohesion

def test_overmerge_scores_below_clean_at_same_matched_rate():
    # Two configs with the SAME matched_rate (8 records in 4 two-member clusters),
    # but DEGRADED has a weak edge (0.66) where CLEAN is tight (0.80).
    clean = {i: {"size": 2, "oversized": False, "members": [2*i, 2*i+1],
                 "pair_scores": {(2*i, 2*i+1): 0.80}, "confidence": 0.80,
                 "cluster_quality": "strong", "bottleneck_pair": (2*i, 2*i+1)} for i in range(4)}
    degraded = {k: dict(v) for k, v in clean.items()}
    degraded[0] = dict(degraded[0]); degraded[0]["pair_scores"] = {(0, 1): 0.66}
    n = 100
    hc = suggestion_health_cohesion(clean, n)
    hd = suggestion_health_cohesion(degraded, n)
    assert hd < hc, f"over-merged ({hd}) must score below clean ({hc}) -- the legacy bug"

def test_recall_collapse_scores_low():
    # No multi-member clusters -> coverage 0 -> health ~0
    assert suggestion_health_cohesion({}, 100) <= 0.0 + 1e-9

def test_under_merge_below_balanced_peak():
    # tight but tiny coverage (1 cluster of 2 in 100 records) scores below a
    # config with many tight clusters (good coverage)
    tiny = {0: {"size": 2, "oversized": False, "members": [0, 1],
                "pair_scores": {(0, 1): 0.95}, "confidence": 0.95,
                "cluster_quality": "strong", "bottleneck_pair": (0, 1)}}
    full = {i: {"size": 2, "oversized": False, "members": [2*i, 2*i+1],
                "pair_scores": {(2*i, 2*i+1): 0.95}, "confidence": 0.95,
                "cluster_quality": "strong", "bottleneck_pair": (2*i, 2*i+1)} for i in range(20)}
    assert suggestion_health_cohesion(tiny, 100) < suggestion_health_cohesion(full, 100)
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement:**

```python
import os

# Coverage saturates: normal matched_rate -> ~1.0 (so cohesion drives the
# over-merge decision), but recall collapse (matched_rate -> 0) drops it.
_COVERAGE_CAP: float = 0.30
# Bottom-k count for mean_bottomk_edge.
_COHESION_BOTTOMK: int = 5
# Edge cutoff for edge_below_cutoff_fraction.
_COHESION_CUTOFF: float = 0.75

def _coverage(clusters: dict, n_records: int) -> float:
    if n_records <= 0:
        return 0.0
    n_matched = sum(i.get("size", 2) for i in clusters.values()
                    if i.get("size", 1) > 1)  # include oversized for coverage too
    matched_rate = n_matched / n_records
    return min(matched_rate / _COVERAGE_CAP, 1.0)

def _select_cohesion(clusters: dict) -> float:
    which = os.environ.get("GOLDENMATCH_SUGGEST_COHESION", "min_edge").strip().lower()
    if which == "mean_bottomk_edge":
        return _cohesion_mean_bottomk(clusters, _COHESION_BOTTOMK)
    if which == "edge_below_cutoff_fraction":
        return _cohesion_edge_below_cutoff(clusters, _COHESION_CUTOFF)
    return _cohesion_min(clusters)  # default

def suggestion_health_cohesion(clusters: dict, n_records: int) -> float:
    """Precision-sensitive health proxy: cohesion (low-tail intra-cluster edge)
    x saturating coverage. Over-merge concentrates damage in a few weak edges
    -> low-tail cohesion drops -> health drops (the legacy mean washed this out).
    Cohesion statistic is env-selectable (GOLDENMATCH_SUGGEST_COHESION) for the
    gym sweep; default min_edge. NO ground truth."""
    if n_records <= 0:
        return -1.0
    return _select_cohesion(clusters) * _coverage(clusters, n_records)
```

- [ ] **Step 4: Run → PASS** (esp. the over-merge inversion test). If `test_under_merge_below_balanced_peak` fails because coverage saturates both to 1.0, that means `_COVERAGE_CAP` is too low to distinguish — the tiny config (matched 2/100=0.02 -> coverage 0.067) vs full (40/100=0.4 -> 1.0) should already differ; confirm.
- [ ] **Step 5: Commit** `feat(health): cohesion x saturating-coverage proxy`.

---

## Task 3: Env-gated selector (legacy default byte-identical)

**Files:** `health.py`; `tests/test_health_cohesion.py` (extend).

- [ ] **Step 1: Failing tests:**

```python
import importlib
from goldenmatch.core.suggest import health as H

def test_legacy_is_default_and_byte_identical(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_SUGGEST_HEALTH", raising=False)
    cl = {0: {"size": 3, "oversized": False, "members": [0, 1, 2],
              "pair_scores": {(0, 1): 0.9, (1, 2): 0.8}, "confidence": 0.85,
              "cluster_quality": "strong", "bottleneck_pair": (1, 2)}}
    # public selector with default == the legacy implementation exactly
    assert H.suggestion_health_from_clusters(cl, 100) == H._health_legacy(cl, 100)

def test_cohesion_env_routes_to_new_formula(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_SUGGEST_HEALTH", "cohesion")
    cl = {0: {"size": 2, "oversized": False, "members": [0, 1],
              "pair_scores": {(0, 1): 0.66}, "confidence": 0.66,
              "cluster_quality": "strong", "bottleneck_pair": (0, 1)}}
    assert H.suggestion_health_from_clusters(cl, 100) == H.suggestion_health_cohesion(cl, 100)
```

- [ ] **Step 2: Run → fail** (no `_health_legacy` yet).
- [ ] **Step 3: Implement:** rename the CURRENT body of `suggestion_health_from_clusters` to `_health_legacy(clusters, n_records)` (verbatim — do not change a character of the computation), then make the public function a selector:

```python
def suggestion_health_from_clusters(clusters: dict, n_records: int) -> float:
    """Public health proxy used by review_config's verify gate. Env-selectable:
    GOLDENMATCH_SUGGEST_HEALTH = 'legacy' (default, the shipped formula) or
    'cohesion' (the precision-sensitive redesign). Default keeps PR #1267
    behavior byte-identical."""
    mode = os.environ.get("GOLDENMATCH_SUGGEST_HEALTH", "legacy").strip().lower()
    if mode == "cohesion":
        return suggestion_health_cohesion(clusters, n_records)
    return _health_legacy(clusters, n_records)
```

Keep the module docstring + `suggestion_health` (scored-pairs variant) untouched.

- [ ] **Step 4: Run → PASS.** Also run the existing suggest suite to confirm legacy default unchanged:
  `... -m pytest packages/python/goldenmatch/tests/test_suggest_verify.py packages/python/goldenmatch/tests/test_suggest_adapter.py -q` (native-gated; should still pass with default legacy).
- [ ] **Step 5: Commit** `feat(health): env-gated legacy/cohesion selector`.

---

## Task 4: Gym sweep + oracle gate + findings note

**Files:** append a `## Findings (gym sweep)` section to the spec `docs/superpowers/specs/2026-06-24-health-proxy-redesign-design.md`.

This is a run/record task (no pytest). Goal: pick the cohesion statistic that maximizes live recovery while the oracle holds suggester_precision ~1.0.

- [ ] **Step 1: Baseline (legacy) gym** — confirm the starting point:
  `GOLDENMATCH_SUGGEST_HEALTH=legacy ... -m scripts.suggest_quality.cli gym --datasets synthetic,ncvr_synthetic`
  Record the headline `gym score (live)` (expected ~0%, the bug).
- [ ] **Step 2: Sweep cohesion** — for each `STAT in {min_edge, mean_bottomk_edge, edge_below_cutoff_fraction}`:
  `GOLDENMATCH_SUGGEST_HEALTH=cohesion GOLDENMATCH_SUGGEST_COHESION=$STAT ... -m scripts.suggest_quality.cli gym --datasets synthetic,ncvr_synthetic`
  Record per STAT: `threshold_too_low` `recovery_pct_live` + `expected_rule_fired_live` on both datasets, and the headline `gym score (live)`.
- [ ] **Step 3: Oracle no-harm check** — for the best-recovery STAT (and legacy):
  `GOLDENMATCH_SUGGEST_HEALTH=cohesion GOLDENMATCH_SUGGEST_COHESION=$BEST ... -m scripts.suggest_quality.cli report --datasets synthetic,ncvr_synthetic`
  Record `suggester_precision` per dataset. MUST stay ~1.0 (no net-negative reintroduced). If it drops, that STAT is disqualified — pick the next that satisfies precision ≈ 1.0.
- [ ] **Step 4: Record findings** — append a table to the spec: `(STAT, live recovery synthetic, live recovery ncvr, rule_fired, suggester_precision)` for legacy + the three stats, and state the winner + whether the dual gate (recovery up AND precision held) is met. If NO stat threads both constraints, record that honestly and recommend escalation to Approach C (weak-pseudo-labels) — do NOT fake a pass.
- [ ] **Step 5: Commit** `docs(health-proxy): gym-sweep findings + winning cohesion statistic`.

Note: the default-on flip (`legacy`→`cohesion`) and re-blessing the gym baseline with the new proxy are OUT of scope here (separate evidence-backed change). This plan ends with the proxy proven behind the flag + the winner recorded.

---

## Done criteria
- `suggestion_health_cohesion` + 3 cohesion statistics + env selector land in `health.py`; `legacy` default is byte-identical (selector test + existing suggest suite green).
- The over-merge inversion test passes (over-merged dict scores below clean at equal matched_rate) — the exact legacy bug, fixed.
- The gym sweep is run; the findings note records live recovery rising from ~0% on `threshold_too_low` for the winning statistic with the right rule firing, AND oracle suggester_precision ~1.0 — or an honest "no stat threads both → escalate to C".
- No default behavior change; `oracle.py` and the kernel untouched.
