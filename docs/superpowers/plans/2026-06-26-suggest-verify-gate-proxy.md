# Config-Suggestion Verify-Gate Proxy Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the config-suggestion raw-vs-live gap by selecting (against the F1 oracle) the unsupervised health proxy with the highest recall at precision 1.0, then flipping it to default — lifting gym `headline_live` from 0.151 toward the 0.555 raw ceiling with zero net-negatives.

**Architecture:** Build an offline bake-off harness in `scripts/suggest_quality/` that, for each `(dataset, perturbation)`, runs raw convergence once and evaluates *every* candidate proxy (as an accept/reject classifier) against ground-truth F1 from the existing oracle helpers. Pure classifier-scoring selects the proxy with max recall subject to zero accepted-harmful fixes, across the full suite plus added adversarial traps. The only production change is enabling a `cap` parameter on one helper and flipping the default proxy in `health.py` to the bake-off winner; the existing env vars stay as rollback.

**Tech Stack:** Python, pytest, the `scripts.suggest_quality` gym/oracle harness (`converge.py`, `oracle.py`, `perturbations.py`, `cli.py`), `goldenmatch.core.suggest.health` proxies, GitHub Actions (`bench-suggest-quality.yml`), the in-tree native config-suggestion kernel under `GOLDENMATCH_SUGGEST_FULL_DIST=1`.

**Worktree:** `D:/show_case/goldenmatch/.worktrees/verify-gate` (branch `feat/suggest-verify-gate-proxy`, stacked on `feat/suggest-gym` / PR #1271). Run commands from the worktree root. Python: `D:/show_case/goldenmatch/.venv/Scripts/python.exe`; prefix `POLARS_SKIP_CPU_CHECK=1` if polars import hangs. Pure-function tests need neither native nor FULL_DIST.

**Spec:** `docs/superpowers/specs/2026-06-26-suggest-verify-gate-proxy-design.md`

**Execution route:** Tasks 1-4 and 6 are local TDD/edits. Tasks 5 and 7 run the heavy FULL_DIST bake-off and re-bless in CI (matching the threshold project's route; CI commits the re-blessed baseline). Task 6's winner value comes from Task 5's CI output, so Task 6 is filled in after Task 5 runs.

---

## Key facts the implementer needs (verified against source)

- **The gate** (`adapter.py:651`): after applying a suggestion and re-running, keep it iff `cand_health >= baseline_health - _VERIFY_EPS` (`_VERIFY_EPS = 1e-6`). `baseline` = the config *before* this suggestion (the current step's config), not the original.
- **Proxies live in `health.py`:** `_health_legacy(clusters, n_records)` (line 108, the recall-biased default), `suggestion_health_cohesion` (271) = `_select_cohesion(clusters) * _coverage(clusters, n_records)`. Cohesion statistic env-selectable via `GOLDENMATCH_SUGGEST_COHESION` ∈ {`min_edge` (default, `_cohesion_min`), `mean_bottomk_edge` (`_cohesion_mean_bottomk`), `edge_below_cutoff_fraction` (`_cohesion_edge_below_cutoff`)}. `_coverage(clusters, n_records)` uses module constant `_COVERAGE_CAP = 0.30` (line 250) — **no env/param today**. `suggestion_health_from_clusters` (198) reads `GOLDENMATCH_SUGGEST_HEALTH` ∈ {`legacy` (default), `cohesion`} at line 203.
- **Harness helpers to REUSE (DRY):** `oracle._auto_configure_no_rerank(df)`, `oracle._run_config(df, config) -> (clusters, scored_pairs)`, `oracle._compute_f1(clusters, scored, gt_pairs) -> float`. `converge.converge_unsupervised(df, config, *, step_cap=5, verify=...)`. `metrics.DAMAGE_EPS = 0.005`. The gym (`gym.run_catalog`) shows the load→ceiling→per-perturbation structure to mirror.
- **`review_config(df, config, verify=False)`** returns the kernel's ranked suggestions WITHOUT the gate; `apply_suggestion(config, s)` returns a new config. Suggestion has `.id` and `.kind`.
- **Cluster dicts** have `members`, `size`, `confidence`, `pair_scores` (the proxies read these). `n_records = df.height`.
- **Datasets** with no `gt_pairs` (blocking anchors) and `no_damage` perturbations must be skipped exactly as `gym.run_catalog`/`evaluate_perturbation` do.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `scripts/suggest_quality/bakeoff.py` | Candidate-proxy registry; pure classifier-scoring + selection; the raw per-fix evaluation loop + catalog runner | **Create** |
| `scripts/suggest_quality/tests/test_bakeoff.py` | Unit tests for the proxy registry + classifier scoring + selection (pure, no native) | **Create** |
| `scripts/suggest_quality/perturbations.py` | Add two adversarial perturbations (near-valley, over-merge trap) | **Modify** |
| `scripts/suggest_quality/tests/test_perturbations.py` | Tests for the two adversarial perturbations | **Modify** |
| `scripts/suggest_quality/cli.py` | Add `bakeoff` mode (report the per-proxy precision/recall table + winner) | **Modify** |
| `.github/workflows/bench-suggest-quality.yml` | Enable FULL_DIST for the `bakeoff` mode too | **Modify** |
| `packages/python/goldenmatch/goldenmatch/core/suggest/health.py` | Add optional `cap` param to `_coverage`; flip the default proxy to the bake-off winner | **Modify** |

---

## Task 1: Bake-off proxy registry (TDD, local)

**Files:** Create `scripts/suggest_quality/bakeoff.py`; Create `scripts/suggest_quality/tests/test_bakeoff.py`

### Background
The registry enumerates every candidate proxy as a `(name, callable(clusters, n_records) -> float)`. We compose them directly from the `health.py` primitives so the bake-off does NOT depend on env vars. Coverage-cap variation needs a cap-parametrized coverage — Task 6 adds the `cap` param to `health._coverage`; until then, compose coverage inline in the harness so Task 1 is self-contained and order-independent.

- [ ] **Step 1: Write the failing test**

Create `scripts/suggest_quality/tests/test_bakeoff.py`:

```python
"""Unit tests for the verify-gate proxy bake-off (pure functions, no native)."""
from scripts.suggest_quality import bakeoff


def test_build_proxies_includes_legacy_and_cohesion_variants():
    proxies = dict(bakeoff.build_proxies())
    # legacy + the three cohesion statistics at the default cap, at minimum.
    assert "legacy" in proxies
    assert "cohesion_min_edge" in proxies
    assert "cohesion_mean_bottomk_edge" in proxies
    assert "cohesion_edge_below_cutoff_fraction" in proxies
    # every value is callable(clusters, n_records) -> float
    for name, fn in proxies.items():
        val = fn({}, 0)
        assert isinstance(val, float)


def test_legacy_proxy_matches_health_legacy():
    from goldenmatch.core.suggest import health
    proxies = dict(bakeoff.build_proxies())
    clusters = {1: {"size": 2, "members": [0, 1], "confidence": 0.9, "pair_scores": {(0, 1): 0.9}}}
    assert proxies["legacy"](clusters, 4) == health._health_legacy(clusters, 4)
```

- [ ] **Step 2: Run to verify it fails**

Run: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/suggest_quality/tests/test_bakeoff.py -v`
Expected: FAIL — `ModuleNotFoundError: scripts.suggest_quality.bakeoff` / `AttributeError: build_proxies`.

- [ ] **Step 3: Create `bakeoff.py` with the registry**

Create `scripts/suggest_quality/bakeoff.py`:

```python
"""Verify-gate proxy bake-off.

Offline harness that scores every candidate unsupervised health proxy as an
accept/reject classifier against ground-truth F1, to pick the proxy with the
highest recall at zero accepted-harmful fixes. See
docs/superpowers/specs/2026-06-26-suggest-verify-gate-proxy-design.md.

Pure functions (registry + scoring + selection) at the top are import-safe and
unit-tested; the raw per-fix evaluation loop defers all goldenmatch imports.
"""
from __future__ import annotations

from typing import Callable

# Candidate coverage caps to sweep (the default 0.30 plus a tighter/looser pair).
_COVERAGE_CAPS: tuple[float, ...] = (0.30, 0.15, 0.50)
_EPS: float = 1e-6  # mirrors adapter._VERIFY_EPS


def _coverage_with_cap(clusters: dict, n_records: int, cap: float) -> float:
    """Saturating coverage with an explicit cap (mirrors health._coverage)."""
    if n_records <= 0:
        return 0.0
    n_matched = sum(i.get("size", 2) for i in clusters.values() if i.get("size", 1) > 1)
    return min((n_matched / n_records) / cap, 1.0)


def build_proxies() -> list[tuple[str, Callable]]:
    """Enumerate candidate proxies as (name, fn(clusters, n_records) -> float)."""
    from goldenmatch.core.suggest import health  # local import keeps module light

    # Keys MUST equal the production GOLDENMATCH_SUGGEST_COHESION values that
    # health._select_cohesion recognizes (min_edge / mean_bottomk_edge /
    # edge_below_cutoff_fraction), so the winning proxy name maps 1:1 to the
    # default we flip in Task 6 -- no name translation, no silent fall-through.
    cohesion_stats = {
        "min_edge": health._cohesion_min,
        "mean_bottomk_edge": lambda c: health._cohesion_mean_bottomk(c, health._COHESION_BOTTOMK),
        "edge_below_cutoff_fraction": lambda c: health._cohesion_edge_below_cutoff(c, health._COHESION_CUTOFF),
    }

    proxies: list[tuple[str, Callable]] = [
        ("legacy", lambda c, n: float(health._health_legacy(c, n))),
    ]
    for stat_name, stat_fn in cohesion_stats.items():
        for cap in _COVERAGE_CAPS:
            suffix = "" if cap == 0.30 else f"_cap{int(cap * 100)}"
            name = f"cohesion_{stat_name}{suffix}"
            proxies.append(
                (name, (lambda c, n, sf=stat_fn, cp=cap: float(sf(c) * _coverage_with_cap(c, n, cp))))
            )
    return proxies
```

(The default-cap names are `cohesion_min_edge`/`cohesion_mean_bottomk_edge`/`cohesion_edge_below_cutoff_fraction`; cap variants get a `_cap15`/`_cap50` suffix. The stat portion of any winner name is therefore a valid `GOLDENMATCH_SUGGEST_COHESION` value verbatim. `build_proxies` does a local `health` import — fine for the unit test since `health.py` is pure-python and imports no native kernel.)

- [ ] **Step 4: Run to verify it passes**

Run: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/suggest_quality/tests/test_bakeoff.py -v`
Expected: PASS.

- [ ] **Step 5: Lint**

Run: `ruff check scripts/suggest_quality/bakeoff.py scripts/suggest_quality/tests/test_bakeoff.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add scripts/suggest_quality/bakeoff.py scripts/suggest_quality/tests/test_bakeoff.py
git commit -m "feat(suggest-bakeoff): candidate proxy registry"
```

---

## Task 2: Classifier scoring + selection (TDD, local)

**Files:** Modify `scripts/suggest_quality/bakeoff.py`; Modify `scripts/suggest_quality/tests/test_bakeoff.py`

### Background
A bake-off "row" is one `(dataset, perturbation, step, proxy)` evaluation with: `accept` (bool, `proxy_delta >= -eps`), `f1_delta` (float). Define `is_harmful = f1_delta < 0` and `is_real_win = f1_delta > 0` (a tie is neutral — neither). The hard constraint is **zero accepted-harmful** rows; among proxies that satisfy it, maximize recall.

- [ ] **Step 1: Write the failing test**

Append to `scripts/suggest_quality/tests/test_bakeoff.py`:

```python
def _row(proxy, accept, f1_delta, dataset="d", pert="p", step=0):
    return {"proxy": proxy, "accept": accept, "f1_delta": f1_delta,
            "dataset": dataset, "perturbation": pert, "step": step}


def test_score_proxy_precision_and_recall():
    rows = [
        _row("A", accept=True, f1_delta=0.2),    # accepted real win
        _row("A", accept=False, f1_delta=0.1),   # missed win
        _row("A", accept=True, f1_delta=0.0),    # accepted neutral (not harmful)
    ]
    s = bakeoff.score_proxy([r for r in rows if r["proxy"] == "A"])
    assert s["n_accepted"] == 2
    assert s["n_accepted_harmful"] == 0
    assert s["n_real_wins"] == 2
    assert s["recall"] == 0.5          # 1 of 2 real wins accepted
    assert s["precision_safe"] == 1.0  # no accepted harmful


def test_select_best_disqualifies_accepted_harmful_then_maxes_recall():
    rows = [
        # A: accepts a harmful fix -> disqualified despite high recall
        _row("A", accept=True, f1_delta=-0.3),
        _row("A", accept=True, f1_delta=0.2),
        # B: safe, recall 1/2
        _row("B", accept=True, f1_delta=0.2),
        _row("B", accept=False, f1_delta=0.1),
        # C: safe, recall 2/2
        _row("C", accept=True, f1_delta=0.2),
        _row("C", accept=True, f1_delta=0.1),
    ]
    winner, table = bakeoff.select_best(rows)
    assert winner == "C"
    assert table["A"]["n_accepted_harmful"] == 1
    assert table["C"]["recall"] == 1.0


def test_select_best_returns_none_when_all_disqualified():
    rows = [_row("A", accept=True, f1_delta=-0.1)]
    winner, table = bakeoff.select_best(rows)
    assert winner is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/suggest_quality/tests/test_bakeoff.py -v`
Expected: FAIL — `AttributeError: score_proxy` / `select_best`.

- [ ] **Step 3: Add the scoring/selection functions**

Append to `scripts/suggest_quality/bakeoff.py`:

```python
def score_proxy(rows: list[dict]) -> dict:
    """Classifier metrics for one proxy's rows (already filtered to that proxy)."""
    accepted = [r for r in rows if r["accept"]]
    real_wins = [r for r in rows if r["f1_delta"] > 0]
    accepted_harmful = [r for r in accepted if r["f1_delta"] < 0]
    accepted_wins = [r for r in accepted if r["f1_delta"] > 0]
    n_accepted = len(accepted)
    return {
        "n_rows": len(rows),
        "n_accepted": n_accepted,
        "n_accepted_harmful": len(accepted_harmful),
        "n_real_wins": len(real_wins),
        # precision_safe: fraction of accepts that were not harmful (1.0 if none accepted)
        "precision_safe": 1.0 if n_accepted == 0 else (n_accepted - len(accepted_harmful)) / n_accepted,
        # recall: fraction of real wins that were accepted (nan if no real wins)
        "recall": (len(accepted_wins) / len(real_wins)) if real_wins else float("nan"),
    }


def select_best(rows: list[dict]) -> tuple[str | None, dict]:
    """Pick the proxy with max recall among those with ZERO accepted-harmful rows.

    Returns (winner_name_or_None, {proxy: score_dict}). Tie-break: higher
    n_accepted, then lexical name (deterministic).
    """
    by_proxy: dict[str, list[dict]] = {}
    for r in rows:
        by_proxy.setdefault(r["proxy"], []).append(r)
    table = {name: score_proxy(rs) for name, rs in by_proxy.items()}

    eligible = [name for name, s in table.items() if s["n_accepted_harmful"] == 0]

    def _key(name: str):
        s = table[name]
        rec = s["recall"]
        rec = -1.0 if rec != rec else rec  # nan -> worst
        return (rec, s["n_accepted"], _neg_lex(name))

    winner = max(eligible, key=_key) if eligible else None
    return winner, table


def _neg_lex(name: str) -> tuple:
    """Lexically-smaller name wins ties (so max() prefers it): negate codepoints."""
    return tuple(-ord(c) for c in name)
```

- [ ] **Step 4: Run to verify it passes**

Run: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/suggest_quality/tests/test_bakeoff.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Lint + commit**

```bash
ruff check scripts/suggest_quality/bakeoff.py scripts/suggest_quality/tests/test_bakeoff.py
git add scripts/suggest_quality/bakeoff.py scripts/suggest_quality/tests/test_bakeoff.py
git commit -m "feat(suggest-bakeoff): classifier scoring + precision-constrained selection"
```

---

## Task 3: Raw per-fix evaluation loop + catalog runner (local)

**Files:** Modify `scripts/suggest_quality/bakeoff.py`

### Background
This is the genuinely-new integration code the spec flagged: neither `converge_unsupervised(verify=False)` (no per-step clusters/F1) nor the oracle loop (runs `verify=True`) gives per-fix candidate clusters + F1 in raw mode. We mirror the gym's catalog structure (`gym.run_catalog`) and the oracle's per-step loop, reusing `oracle._auto_configure_no_rerank`, `oracle._run_config`, `oracle._compute_f1`. All goldenmatch imports are deferred inside the functions (matches the oracle pattern, keeps the pure-function tests light). No unit test here — it needs the native kernel + pipeline; it is exercised end-to-end by the CI bake-off (Task 5). Keep it small and readable.

- [ ] **Step 1: Add the loop + catalog runner**

Append to `scripts/suggest_quality/bakeoff.py`:

```python
_STEP_CAP = 5


def bakeoff_dataset(df, gt_pairs, degraded_config, proxies) -> list[dict]:
    """Raw greedy convergence over `degraded_config`, emitting one row per
    (applied fix x proxy). Advances the RAW path (apply top suggestion each
    step regardless of any gate). Defers goldenmatch imports."""
    import copy  # noqa: PLC0415
    from goldenmatch.core.suggest import apply_suggestion, review_config  # noqa: PLC0415
    from scripts.suggest_quality.oracle import _compute_f1, _run_config  # noqa: PLC0415

    n = df.height
    rows: list[dict] = []
    current = copy.deepcopy(degraded_config)
    cur_clusters, cur_scored = _run_config(df, current)
    f1_current = _compute_f1(cur_clusters, cur_scored, gt_pairs)
    applied_ids: set = set()

    for step in range(_STEP_CAP):
        suggestions = review_config(df, current, verify=False)
        if not suggestions:
            break
        top = suggestions[0]
        if top.id in applied_ids:
            break
        applied_ids.add(top.id)

        candidate = apply_suggestion(current, top)
        cand_clusters, cand_scored = _run_config(df, candidate)
        f1_cand = _compute_f1(cand_clusters, cand_scored, gt_pairs)
        f1_delta = f1_cand - f1_current

        for proxy_name, proxy_fn in proxies:
            delta = proxy_fn(cand_clusters, n) - proxy_fn(cur_clusters, n)
            rows.append({
                "proxy": proxy_name,
                "step": step,
                "kind": getattr(top, "kind", None),
                "accept": delta >= -_EPS,
                "proxy_delta": delta,
                "f1_delta": f1_delta,
            })

        # advance raw path
        current, cur_clusters, cur_scored, f1_current = (
            candidate, cand_clusters, cand_scored, f1_cand
        )
    return rows


def run_bakeoff_catalog(datasets, perturbations, proxies) -> list[dict]:
    """Mirror gym.run_catalog: load -> ceiling -> per-damaging-perturbation
    raw bake-off. Each emitted row carries dataset + perturbation. Never raises."""
    import logging  # noqa: PLC0415
    import math  # noqa: PLC0415

    import polars as pl  # noqa: PLC0415

    from scripts.suggest_quality.metrics import DAMAGE_EPS  # noqa: PLC0415
    from scripts.suggest_quality.oracle import (  # noqa: PLC0415
        _auto_configure_no_rerank, _compute_f1, _run_config,
    )

    log = logging.getLogger(__name__)
    out: list[dict] = []

    for dataset in datasets:
        try:
            loaded = dataset.loader()
        except Exception as exc:
            log.warning("bakeoff: loader failed for %r: %s", dataset.name, exc)
            loaded = None
        if loaded is None:
            continue
        df, gt_pairs = loaded
        if not gt_pairs:
            continue
        if "__row_id__" not in df.columns:
            df = df.with_row_index("__row_id__").with_columns(pl.col("__row_id__").cast(pl.Int64))

        try:
            ceiling = _auto_configure_no_rerank(df)
            cc, cs = _run_config(df, ceiling)
            f1_ceiling = _compute_f1(cc, cs, gt_pairs)
        except Exception as exc:
            log.warning("bakeoff: ceiling failed for %r: %s", dataset.name, exc)
            continue

        for pert in perturbations:
            try:
                if not pert.applies_to(ceiling):
                    continue
                degraded = pert.apply(ceiling)
                dc, ds = _run_config(df, degraded)
                f1_degraded = _compute_f1(dc, ds, gt_pairs)
                if math.isnan(f1_degraded) or math.isnan(f1_ceiling):
                    continue
                if f1_ceiling - f1_degraded < DAMAGE_EPS:
                    continue  # no_damage: nothing to recover, skip
                rows = bakeoff_dataset(df, gt_pairs, degraded, proxies)
            except Exception as exc:
                log.warning("bakeoff: %r/%r failed: %s", dataset.name, pert.name, exc, exc_info=True)
                continue
            for r in rows:
                r["dataset"] = dataset.name
                r["perturbation"] = pert.name
                out.append(r)
    return out
```

- [ ] **Step 2: Verify imports resolve (no run)**

Run: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -c "import scripts.suggest_quality.bakeoff as b; print(b.run_bakeoff_catalog, b.bakeoff_dataset)"`
Expected: prints the two function objects (module imports cleanly; the deferred imports inside are not triggered).

- [ ] **Step 3: Lint + commit**

```bash
ruff check scripts/suggest_quality/bakeoff.py
git add scripts/suggest_quality/bakeoff.py
git commit -m "feat(suggest-bakeoff): raw per-fix evaluation loop + catalog runner"
```

---

## Task 4: Adversarial perturbations (TDD, local)

**Files:** Modify `scripts/suggest_quality/perturbations.py`; Modify `scripts/suggest_quality/tests/test_perturbations.py`

### Background
Two additive perturbations that create precision traps a good proxy must reject. They follow the exact mutator pattern (deep-copy, `_primary_weighted_mk` guard, immutable) and reuse `_applies_threshold_too_high` where the precondition matches.
- `near_valley_threshold`: nudge the primary threshold to **just below** the valley (`max(0.50, threshold - 0.05)`) so the kernel is tempted to lower further into the sub-threshold tail (a precision-losing fix). A recall-biased proxy accepts it; a good proxy rejects.
- `over_merge_bait`: lower the primary threshold a large step (`max(0.50, threshold - 0.30)`) to induce over-merge; the recall-biased `matched_rate` reward is exactly what this baits.

**Honest note for the implementer:** these are best-effort traps. Whether the kernel actually emits a *harmful* fix on them (and so exercises the precision bar) is only known from the CI bake-off (Task 5). If a trap comes back with no accepted-harmful row for any proxy, record that in the Task 5 findings — it means the trap didn't bite, not that selection is wrong. Do NOT assert kernel behavior in the unit test; assert only catalog membership + apply behavior + immutability (mirroring the existing `test_perturbations.py`).

- [ ] **Step 1: Write the failing tests**

Append to `scripts/suggest_quality/tests/test_perturbations.py` (reuse the existing `_config_with_threshold` helper in that file):

```python
def test_near_valley_threshold_nudges_just_below():
    cfg = _config_with_threshold(0.80)
    out = perturbations._apply_near_valley_threshold(cfg)
    assert out.get_matchkeys()[0].threshold == 0.75   # 0.80 - 0.05
    assert cfg.get_matchkeys()[0].threshold == 0.80   # input untouched


def test_over_merge_bait_lowers_hard_floor_050():
    cfg = _config_with_threshold(0.70)
    out = perturbations._apply_over_merge_bait(cfg)
    assert out.get_matchkeys()[0].threshold == 0.50   # max(0.50, 0.70 - 0.30)
    assert cfg.get_matchkeys()[0].threshold == 0.70


def test_adversarial_perturbations_in_catalog():
    names = {p.name for p in CATALOG}
    assert {"near_valley_threshold", "over_merge_bait"} <= names
```

- [ ] **Step 2: Run to verify it fails**

Run: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/suggest_quality/tests/test_perturbations.py -v`
Expected: FAIL — missing `_apply_near_valley_threshold` / `_apply_over_merge_bait` / catalog entries.

- [ ] **Step 3: Add the mutators** (after `_applies_threshold_too_high`, ~line 137 in `perturbations.py`)

```python
def _apply_near_valley_threshold(config: GoldenMatchConfig) -> GoldenMatchConfig:
    """Adversarial: nudge the primary threshold JUST BELOW the valley (-0.05,
    floor 0.50) so the dip rule is tempted to lower further into the tail -- a
    precision-losing fix a good proxy must reject."""
    new = copy.deepcopy(config)
    mk = _primary_weighted_mk(new)
    if mk is None:
        return new
    mk.threshold = max(0.50, mk.threshold - 0.05)
    return new


def _apply_over_merge_bait(config: GoldenMatchConfig) -> GoldenMatchConfig:
    """Adversarial: lower the primary threshold a large step (-0.30, floor 0.50)
    to induce over-merge -- baits a recall-biased proxy that rewards matched_rate."""
    new = copy.deepcopy(config)
    mk = _primary_weighted_mk(new)
    if mk is None:
        return new
    mk.threshold = max(0.50, mk.threshold - 0.30)
    return new
```

- [ ] **Step 4: Append catalog entries** (after the `threshold_far_too_high` entry in `CATALOG`)

```python
    Perturbation(
        name="near_valley_threshold",
        expected_rule="lower_threshold",
        builds_on_existing_rule=True,
        description=(
            "ADVERSARIAL precision trap: nudge the primary threshold just below "
            "the score valley so the dip rule is tempted to over-lower into the "
            "sub-threshold tail. A good health proxy must REJECT the fix."
        ),
        applies_to=_applies_threshold_too_high,
        apply=_apply_near_valley_threshold,
    ),
    Perturbation(
        name="over_merge_bait",
        expected_rule="lower_threshold",
        builds_on_existing_rule=True,
        description=(
            "ADVERSARIAL precision trap: lower the primary threshold a large "
            "step to induce over-merge. Baits a recall-biased proxy that rewards "
            "matched_rate; a good proxy must REJECT the over-merging fix."
        ),
        applies_to=_applies_threshold_too_high,
        apply=_apply_over_merge_bait,
    ),
```

- [ ] **Step 5: Run to verify it passes; lint; commit**

```bash
POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/suggest_quality/tests/test_perturbations.py -v
ruff check scripts/suggest_quality/perturbations.py scripts/suggest_quality/tests/test_perturbations.py
git add scripts/suggest_quality/perturbations.py scripts/suggest_quality/tests/test_perturbations.py
git commit -m "feat(suggest-gym): adversarial near-valley + over-merge perturbations"
```

Expected pytest: PASS (existing + 3 new tests).

---

## Task 5: `bakeoff` CLI mode + workflow wiring; run it in CI (local edits, then CI dispatch)

**Files:** Modify `scripts/suggest_quality/cli.py`; Modify `.github/workflows/bench-suggest-quality.yml`

### Background
The `bakeoff` mode is a REPORT (prints the per-proxy precision/recall table + the selected winner). No bless, no commit — the winner is read from the run log and fed into Task 6. The dip/raise fixes only appear under FULL_DIST, so the workflow must enable FULL_DIST for `bakeoff` too (currently only `gym*` modes get it via `startsWith(inputs.mode, 'gym')`).

- [ ] **Step 1: Add `bakeoff` to the CLI mode choices + dispatch**

In `cli.py`, add `"bakeoff"` to the `choices` list at `main()` (the `mode` argument, ~line 216). Then, right after the gym-modes early branch (~line 236-238), add:

```python
    if args.mode == "bakeoff":
        native_version, git_sha = _gather_meta()
        return _run_bakeoff_mode(names, native_version, git_sha)
```

- [ ] **Step 2: Add `_run_bakeoff_mode`** (near `_run_gym_mode`, ~line 496 in `cli.py`)

```python
def _run_bakeoff_mode(dataset_names, native_version, git_sha) -> int:
    """Run the verify-gate proxy bake-off and print the per-proxy table + winner."""
    from scripts.suggest_quality.bakeoff import (  # noqa: PLC0415
        build_proxies, run_bakeoff_catalog, score_proxy, select_best,
    )
    from scripts.suggest_quality.datasets import REGISTRY  # noqa: PLC0415
    from scripts.suggest_quality.perturbations import CATALOG  # noqa: PLC0415

    # Same native guard as _run_gym_mode.
    try:
        from goldenmatch.core._native_loader import native_module  # noqa: PLC0415
        _mod = native_module()
        if _mod is None or not hasattr(_mod, "suggest_config"):
            print("bakeoff requires the native suggest_config kernel.\n"
                  "Build it:  uv run python scripts/build_native.py")
            return 1
    except Exception as exc:
        print(f"bakeoff: native loader error: {exc}")
        return 1

    datasets = [d for d in REGISTRY if dataset_names is None or d.name in dataset_names]
    proxies = build_proxies()
    rows = run_bakeoff_catalog(datasets, CATALOG, proxies)
    winner, table = select_best(rows)

    print("verify-gate proxy bake-off")
    print(f"  native={native_version}  sha={git_sha[:12] if git_sha != 'unknown' else 'unknown'}")
    print(f"  {len(rows)} (fix x proxy) rows over {len({(r['dataset'], r['perturbation']) for r in rows})} damaging pairs")
    print()
    hdr = f"  {'proxy':<34} {'accepted':>8} {'acc_harm':>8} {'real_wins':>9} {'precision':>9} {'recall':>7}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for name in sorted(table):
        s = table[name]
        rec = "   n/a" if s["recall"] != s["recall"] else f"{s['recall']:6.3f}"
        flag = "  <-- DISQUALIFIED (accepts harmful)" if s["n_accepted_harmful"] else ""
        print(f"  {name:<34} {s['n_accepted']:>8} {s['n_accepted_harmful']:>8} "
              f"{s['n_real_wins']:>9} {s['precision_safe']:>9.3f} {rec:>7}{flag}")
    print()
    if winner is None:
        print("  WINNER: none -- no proxy achieved zero accepted-harmful fixes. "
              "Consider Phase B (valley-margin proxy).")
    else:
        print(f"  WINNER: {winner}  (max recall at zero accepted-harmful)")
    return 0
```

- [ ] **Step 3: Verify the CLI parses the new mode (local, no native needed for the arg check)**

Run: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -c "from scripts.suggest_quality.cli import main; import sys; sys.argv=['x']; main(['bakeoff','--datasets','__none__'])"`
Expected: it reaches `_run_bakeoff_mode` (may print the native-guard message or an empty table locally; the point is no argparse error on `bakeoff`).

- [ ] **Step 4: Enable FULL_DIST for `bakeoff` in the workflow**

In `.github/workflows/bench-suggest-quality.yml`, the first run step's env (added in PR #1271) is:
```yaml
          GOLDENMATCH_SUGGEST_FULL_DIST: ${{ startsWith(inputs.mode, 'gym') && '1' || '0' }}
```
Change it to also enable for `bakeoff`:
```yaml
          GOLDENMATCH_SUGGEST_FULL_DIST: ${{ (startsWith(inputs.mode, 'gym') || inputs.mode == 'bakeoff') && '1' || '0' }}
```

- [ ] **Step 5: Validate YAML + commit**

```bash
D:/show_case/goldenmatch/.venv/Scripts/python.exe -c "import yaml; yaml.safe_load(open('.github/workflows/bench-suggest-quality.yml')); print('YAML OK')"
git add scripts/suggest_quality/cli.py .github/workflows/bench-suggest-quality.yml
git commit -m "feat(suggest-bakeoff): bakeoff CLI mode + FULL_DIST workflow wiring"
git push origin feat/suggest-verify-gate-proxy
```

- [ ] **Step 6: Dispatch the bake-off in CI (under FULL_DIST) and read the winner**

Auth: `gh auth status` should show `benzsevern` active (unset `GH_TOKEN` if it shadows). Then:

```bash
gh workflow run bench-suggest-quality.yml --ref feat/suggest-verify-gate-proxy \
  -f mode=bakeoff -f datasets=synthetic,ncvr_synthetic
sleep 5
RID=$(gh run list --workflow=bench-suggest-quality.yml --branch feat/suggest-verify-gate-proxy --event workflow_dispatch --limit 1 --json databaseId -q '.[0].databaseId')
gh run watch "$RID" --exit-status
gh run view "$RID" --log | grep -E "proxy|WINNER|DISQUALIFIED|precision|recall" | tail -40
```

Expected: the run prints the per-proxy table and a `WINNER:` line. **Record the winner name + its precision/recall, and the full table, for Task 6 and the findings.** If `WINNER: none`, go to the contingent Phase B task before Task 6.

> Notes:
> - The `bakeoff`-mode dispatch run ALSO executes the workflow's unconditional second step (`gym-gate`) after the bakeoff step. At Task 5 `health.py` is not yet flipped (still `legacy`) and `--datasets synthetic,ncvr_synthetic` matches the committed baseline scope, so gym-gate should pass. But if that step reds for any reason, the `WINNER:` line is still in the bakeoff step's log — grep it regardless; `gh run watch --exit-status` returning nonzero does not invalidate the bake-off result.
> - The push of Task 5 also triggers a push-event run that gym-gates under FULL_DIST against the existing baseline — same transient as the threshold project; cancel it (`gh run list ... --event push --limit 1` then `gh run cancel`) if you want a clean board. It is not the source of truth.

---

## Task 6: Flip the default proxy to the winner (local; winner from Task 5)

**Files:** Modify `packages/python/goldenmatch/goldenmatch/core/suggest/health.py`; Modify `scripts/suggest_quality/tests/test_bakeoff.py` (or a small `health` test)

### Background
Fill in `<WINNER>` from Task 5. Two sub-cases:
- **Winner is `legacy`:** no flip needed — document the bake-off result in findings and skip to Task 7 (re-bless still confirms the number). (Unlikely given the recall bias, but possible.)
- **Winner is a cohesion variant:** flip `suggestion_health_from_clusters` default to `cohesion`, set the default `GOLDENMATCH_SUGGEST_COHESION` to the winning statistic, and — only if the winner used a non-0.30 cap — add the `cap` param to `_coverage` plus a `GOLDENMATCH_SUGGEST_COVERAGE_CAP` env read.

- [ ] **Step 1: (If a non-0.30 cap won) add the `cap` param to `_coverage`**

In `health.py`, change `_coverage` (line 255) to accept an optional cap, backward-compatible:

```python
def _coverage(clusters: dict, n_records: int, cap: float | None = None) -> float:
    if n_records <= 0:
        return 0.0
    if cap is None:
        cap = _coverage_cap_from_env()
    n_matched = sum(i.get("size", 2) for i in clusters.values() if i.get("size", 1) > 1)
    return min((n_matched / n_records) / cap, 1.0)


def _coverage_cap_from_env() -> float:
    raw = os.environ.get("GOLDENMATCH_SUGGEST_COVERAGE_CAP", "").strip()
    try:
        return float(raw) if raw else _COVERAGE_CAP
    except ValueError:
        return _COVERAGE_CAP
```

(If the 0.30 cap won, skip this step — `_coverage` is unchanged.)

- [ ] **Step 2: Write the failing test for the new default**

Add to `scripts/suggest_quality/tests/test_bakeoff.py` (a default-posture assertion; `<WINNER_MODE>` = `cohesion`, `<WINNER_STAT>` = the winning statistic):

```python
def test_default_health_proxy_is_the_bakeoff_winner(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_SUGGEST_HEALTH", raising=False)
    monkeypatch.delenv("GOLDENMATCH_SUGGEST_COHESION", raising=False)
    from goldenmatch.core.suggest import health
    # Over-merge clusters: legacy (recall-biased) scores HIGH (matched_rate 0.8,
    # conf 0.9 -> 0.72 - 0.26 hhi penalty = 0.46); cohesion scores LOW (weak
    # min edge 0.4). Tight clustering: legacy 0.2*0.95 = 0.19; cohesion high
    # (min edge 0.95). So legacy INVERTS the right ordering; a precision-sensitive
    # default does not.
    over_merged = {1: {"size": 8, "members": list(range(8)), "confidence": 0.9,
                       "pair_scores": {(0, 1): 0.4}}}
    tight = {1: {"size": 2, "members": [0, 1], "confidence": 0.95,
                 "pair_scores": {(0, 1): 0.95}}}
    # Under the winning (precision-sensitive) default, tight must score >= over_merged.
    assert health.suggestion_health_from_clusters(tight, 10) >= \
           health.suggestion_health_from_clusters(over_merged, 10)
```

Run it: expect **FAIL** while the default is still `legacy` -- legacy gives `tight=0.19 < over_merged=0.46` (it rewards the 8-member over-merge's higher matched_rate), so `tight >= over_merged` is False. This confirms the test is a genuine RED before the flip, not a no-op.

- [ ] **Step 3: Flip the default**

In `health.py:203`, change the default mode read so the winner is default while the env still overrides:

```python
    mode = os.environ.get("GOLDENMATCH_SUGGEST_HEALTH", "<WINNER_MODE>").strip().lower()
```

If the winner is a cohesion variant, also flip the cohesion-statistic default at `_select_cohesion` (line 263):

```python
    which = os.environ.get("GOLDENMATCH_SUGGEST_COHESION", "<WINNER_STAT>").strip().lower()
```

**Deriving `<WINNER_STAT>` from the winner name (no translation needed):** because Task 1 named the proxies with the production stat values, the stat is the winner name with the `cohesion_` prefix and any `_capNN` suffix stripped. E.g. winner `cohesion_mean_bottomk_edge` -> `<WINNER_STAT>=mean_bottomk_edge` (a value `_select_cohesion` recognizes); winner `cohesion_min_edge_cap15` -> `<WINNER_STAT>=min_edge` AND a non-default cap (Step 1 applies: set `GOLDENMATCH_SUGGEST_COVERAGE_CAP` default to `0.15`). Do NOT pass a bakeoff-only short name — confirm the chosen `<WINNER_STAT>` is one of `min_edge`/`mean_bottomk_edge`/`edge_below_cutoff_fraction`, or `_select_cohesion` silently falls through to `min_edge`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/suggest_quality/tests/test_bakeoff.py -v`
Expected: PASS.

- [ ] **Step 5: Guard against unrelated breakage — run the suggest health/adapter tests**

Run: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_suggest_full_dist.py -q`
Expected: PASS (or, if a test pinned the old `legacy` default behavior, update it to set `GOLDENMATCH_SUGGEST_HEALTH=legacy` explicitly and note why). Report any test that encoded the old default as a concern.

- [ ] **Step 6: Lint + commit**

```bash
ruff check packages/python/goldenmatch/goldenmatch/core/suggest/health.py
git add packages/python/goldenmatch/goldenmatch/core/suggest/health.py scripts/suggest_quality/tests/test_bakeoff.py
git commit -m "feat(suggest): default verify-gate proxy -> <WINNER> (bake-off winner)"
```

---

## Task 7: Re-bless under the new default + validate (CI dispatch)

**Files:** `scripts/suggest_quality/baselines/gym_scorecard.json` (regenerated + committed by CI)

### Background
With the default flipped, the gym's live convergence now uses the winning proxy. Re-bless (CI, FULL_DIST, commits the baseline back — the `mode=gym-bless` path shipped in PR #1271) and confirm `headline_live` rose with zero net-negatives. This is the real end-to-end validation the spec requires (the classifier was only the selection signal).

- [ ] **Step 1: Push, then dispatch the re-bless**

```bash
git push origin feat/suggest-verify-gate-proxy
# (optional) cancel the transient push-triggered run as in the threshold project
gh workflow run bench-suggest-quality.yml --ref feat/suggest-verify-gate-proxy \
  -f mode=gym-bless -f datasets=synthetic,ncvr_synthetic
sleep 5
RID=$(gh run list --workflow=bench-suggest-quality.yml --branch feat/suggest-verify-gate-proxy --event workflow_dispatch --limit 1 --json databaseId -q '.[0].databaseId')
gh run watch "$RID" --exit-status
```

Expected: bless + gym-gate pass under FULL_DIST; CI commits the re-blessed `gym_scorecard.json`.

- [ ] **Step 2: Pull and verify headline_live rose + zero net-negatives**

```bash
git pull --ff-only origin feat/suggest-verify-gate-proxy
D:/show_case/goldenmatch/.venv/Scripts/python.exe -c "import json; d=json.load(open('scripts/suggest_quality/baselines/gym_scorecard.json')); print('headline_live', d['headline_live'], 'headline_raw', d['headline_raw'])"
```

Expected: `headline_live` up from 0.151 toward `headline_raw`. **Read the headline carefully — it is now blended:** the two adversarial traps (Task 4) are `builds_on_existing_rule=True`, so they enter the `built_ok` headline mean, and a *correctly-rejected* trap contributes ~0 live recovery by design. So the headline mean is diluted by the traps even under a perfect proxy; do not expect it to reach raw. Compare `recovery_pct_live` **per pair** on the non-adversarial recovery perturbations (`threshold_too_low`, `threshold_far_too_high`, etc.) — those are where live recovery should rise toward raw.

**Zero net-negatives check (the real guarantee):** inspect every built-rule pair's `recovery_pct_live`. None should be negative (the live path must not have *accepted* a harmful fix) — this explicitly includes the two trap pairs, whose live recovery should be ~0 (rejected), never negative. If any built-rule pair's live recovery went negative, STOP — the real run surfaced a net-negative the classifier missed; revert the flip to the next candidate (or Phase B) and re-run. The authoritative evidence is this per-pair check plus the Task 5 bakeoff precision table, NOT the blended headline number.

- [ ] **Step 3: Record findings**

Append a `## Findings (verify-gate proxy, <date>)` section to `docs/superpowers/specs/2026-06-26-suggest-verify-gate-proxy-design.md` with: the per-proxy precision/recall table from Task 5, the chosen winner, the before/after `headline_live` (0.151 -> X), the zero-net-negative confirmation, and whether the adversarial traps actually bit (and if not, say so). Commit + push:

```bash
git add docs/superpowers/specs/2026-06-26-suggest-verify-gate-proxy-design.md
git commit -m "docs(suggest): record verify-gate proxy bake-off findings"
git push origin feat/suggest-verify-gate-proxy
```

---

## Contingent Task B: Valley-margin proxy (only if Task 5 winner is `none`)

Do this ONLY if Task 5 reported `WINNER: none` (no existing proxy achieved zero accepted-harmful at useful recall).

**Files:** Modify `packages/python/goldenmatch/goldenmatch/core/suggest/health.py` (add the proxy); Modify `scripts/suggest_quality/bakeoff.py` (`build_proxies` adds the candidate).

- [ ] **Step 1:** Add a `suggestion_health_valley_margin(clusters, n_records)` to `health.py`: a precision-sensitive score = separation between kept-match intra-cluster edges and the implied sub-threshold tail (reuse the dip-arc valley computation; clusters carry `pair_scores`). Add it under a new `GOLDENMATCH_SUGGEST_HEALTH=valley_margin` mode branch.
- [ ] **Step 2:** Add it to `bakeoff.build_proxies()` as a new candidate.
- [ ] **Step 3:** Re-run Task 5 (the CI bake-off) including the new candidate; if it now wins at zero accepted-harmful, proceed to Task 6 with `<WINNER>=valley_margin`. If still `none`, surface to the user — the existing proxies + a margin proxy could not close the gap safely, which is itself a finding (the gate may need the two-sided architecture, Approach C, a separate design).

---

## Done criteria (from the spec)

- [ ] Bake-off harness exists and emits a per-proxy precision/recall table over the full + adversarial suite (Tasks 1-3, 5).
- [ ] A proxy is selected with zero accepted-harmful fixes and the highest recall; the choice + numbers are recorded (Task 5 + Task 7 findings).
- [ ] Adversarial near-valley + over-merge perturbations are in the catalog (additive) and included in selection + validation (Task 4).
- [ ] Default flipped (`health.py`), rollback env preserved (Task 6).
- [ ] Re-blessed gym shows per-pair `recovery_pct_live` rising toward raw on the non-adversarial recovery perturbations, with **zero net-negatives** on every built-rule pair (traps included, ~0 not negative), confirmed by the real live run (Task 7). (The blended `headline_live` rises too but is diluted by the correctly-rejected traps — the per-pair check + bakeoff precision table are the authoritative evidence.)
- [ ] If no existing proxy cleared the bar, Phase B (valley-margin proxy) was added and re-evaluated before any flip (Contingent Task B).

## Out of scope (from the spec)

- Changing the gate architecture (still apply -> re-run -> compare proxy).
- Building new suggestion *rules* (blocking-pass, field-weight).
- Auto-applying suggestions in the default pipeline.
- Flipping `FULL_DIST` default-on globally.
- Real-world-headroom hunting on messier datasets.
- Improving zero-config near-ceiling (layer 1).
