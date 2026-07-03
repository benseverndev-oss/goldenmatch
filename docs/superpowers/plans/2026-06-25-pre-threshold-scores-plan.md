# Pre-Threshold Scores Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feed the suggestion kernel the full (pre-threshold) candidate-pair score distribution via a diagnostic threshold-0 re-score in `review_config`, gated `GOLDENMATCH_SUGGEST_FULL_DIST` (default off = byte-identical), so `lower_threshold`/correct rules fire — kernel and rules unchanged.

**Architecture:** Localized to `review_config` in `core/suggest/adapter.py`. The real run still produces `clusters`; when the flag is on, the `scored_pairs` Arrow batch is built from a SECOND run on the same engine with every matchkey threshold forced to 0.0 (same blocking, only the score-emit filter widens). The kernel still receives the REAL config (true threshold) via `config_json`, so `ScoreDiagnostics::from_batch(scored_pairs, threshold, bins)` computes real `mass_above`/`mass_just_below`. Validation is on the gym + oracle.

**Tech Stack:** Python 3.11+ (adapter, polars, pyarrow); the native `suggest_config` kernel (unchanged); the `scripts/suggest_quality` gym/oracle for validation.

**Spec:** `docs/superpowers/specs/2026-06-25-pre-threshold-scores-design.md`

---

## Conventions
- Work from `D:\show_case\goldenmatch\.worktrees\suggest-gym` (branch `feat/suggest-gym`). Local commits only — NO push, NO PR.
- Python: `D:/show_case/goldenmatch/.venv/Scripts/python.exe`, Windows PYTHONPATH `;`:
  `export PYTHONPATH="D:/show_case/goldenmatch/.worktrees/suggest-gym/packages/python/goldenmatch;D:/show_case/goldenmatch/.worktrees/suggest-gym"` + `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8`. Native suggest_config is built in this worktree; behavioral tests RUN it. Never run the full suite. Commit trailers each time:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` / `Claude-Session: https://claude.ai/code/session_01Wz94wngiSXtkxzBPKzqyUy`.

## Grounded facts (from `adapter.py::review_config`)
- Line ~489: `_config = copy.deepcopy(config)`; ~502: `engine = MatchEngine.from_dataframe(df)`; ~505: `result = engine._run_pipeline(df, _config)`; ~511: `scored_pairs = result.scored_pairs`; ~512: `clusters = result.clusters`; ~515: `scored_pairs_batch = _build_scored_pairs_batch(scored_pairs)`.
- `config_json` (~519) is built from `_config` (the REAL thresholds) — the kernel evaluates against the true threshold. The diagnostic run's forced-0 threshold is used ONLY to widen `scored_pairs`; it must NOT touch `config_json`.
- `_run_pipeline` returns pairs `>= threshold` (filtered) — the root cause (comment already at ~571).
- Blocking depends on `config.blocking`, NOT on matchkey thresholds — so forcing thresholds to 0 keeps the candidate set identical; only the emit-filter widens. Reuse the SAME `engine` instance for the diagnostic run.

## File structure
- Modify only: `packages/python/goldenmatch/goldenmatch/core/suggest/adapter.py` (add `_full_dist_enabled()` + `_diagnostic_scored_pairs()`; wire into `review_config`).
- Test: `packages/python/goldenmatch/tests/test_suggest_full_dist.py` (new).
- Task 3 appends a findings note to the spec.

---

## Task 1: `_full_dist_enabled()` + `_diagnostic_scored_pairs()` helpers

**Files:** `adapter.py`; Test `tests/test_suggest_full_dist.py`.

- [ ] **Step 1: Failing tests** (pure — test the diagnostic-config construction + the env flag; no pipeline run):

```python
import copy
from goldenmatch.core.suggest import adapter as A

def _cfg():
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField, BlockingConfig, BlockingKeyConfig,
    )
    mk = MatchkeyConfig(name="person", type="weighted", threshold=0.85, fields=[
        MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0),
        MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0),
    ])
    return GoldenMatchConfig(matchkeys=[mk],
                             blocking=BlockingConfig(strategy="static",
                                                     keys=[BlockingKeyConfig(fields=["last_name"])]))

def test_full_dist_default_off(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_SUGGEST_FULL_DIST", raising=False)
    assert A._full_dist_enabled() is False

def test_full_dist_on_when_1(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_SUGGEST_FULL_DIST", "1")
    assert A._full_dist_enabled() is True

def test_diagnostic_config_forces_all_thresholds_to_zero():
    cfg = _cfg()
    diag = A._zero_threshold_config(cfg)
    assert all(mk.threshold == 0.0 for mk in diag.get_matchkeys())
    # original untouched (immutability)
    assert cfg.get_matchkeys()[0].threshold == 0.85
    # blocking unchanged (candidate set must be identical)
    assert diag.blocking == cfg.blocking
```

- [ ] **Step 2: Run → fail.** `... -m pytest packages/python/goldenmatch/tests/test_suggest_full_dist.py -v`
- [ ] **Step 3: Implement** in `adapter.py` (near the other env helpers like `_verify_enabled_by_env`):

```python
def _full_dist_enabled() -> bool:
    """When True, source the kernel's scored_pairs from a threshold-0 diagnostic
    run (full pre-threshold distribution) instead of the threshold-filtered run.
    Default OFF -> byte-identical to current behavior."""
    return os.environ.get("GOLDENMATCH_SUGGEST_FULL_DIST", "0").strip().lower() in {"1", "true", "on"}

def _zero_threshold_config(config):
    """Deep-copy the config with every matchkey threshold forced to 0.0. Used
    ONLY to widen the diagnostic scored_pairs run; blocking (the candidate set) is
    unchanged. Never mutates the input."""
    diag = copy.deepcopy(config)
    try:
        for mk in diag.get_matchkeys():
            if getattr(mk, "threshold", None) is not None:
                mk.threshold = 0.0
    except Exception:
        logger.debug("_zero_threshold_config: failed to zero thresholds", exc_info=True)
    return diag

def _diagnostic_scored_pairs(engine, df, config):
    """Run the SAME engine at threshold 0 to capture the full candidate-pair
    score distribution (the sub-threshold tail). Returns scored_pairs; clusters
    discarded. Falls back to None on failure (caller keeps the filtered pairs)."""
    try:
        diag_cfg = _zero_threshold_config(config)
        diag_result = engine._run_pipeline(df, diag_cfg)
        return diag_result.scored_pairs
    except Exception:
        logger.debug("_diagnostic_scored_pairs: diagnostic run failed", exc_info=True)
        return None
```

If a Pydantic validator rejects exactly `0.0` for `threshold`, use a tiny epsilon (`1e-9`) and note it. (Confirm during this task by constructing `_zero_threshold_config(_cfg())` — the test above asserts `== 0.0`; if construction raises, switch to `1e-9` and update the assertion.)

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(suggest): full-dist env flag + zero-threshold diagnostic helpers`.

---

## Task 2: Wire the diagnostic distribution into `review_config`

**Files:** `adapter.py`; Test `tests/test_suggest_full_dist.py` (extend, native-guarded).

- [ ] **Step 1: Failing behavioral tests** (native-guarded — copy the `_suggest_available()` guard from `tests/test_suggest_oracle_smoke.py`; set `os.environ.setdefault("POLARS_SKIP_CPU_CHECK","1")` at module top):

```python
# Uses ncvr_synthetic (deterministic, damage-capable) via the gym loader.
def _ncvr():
    from scripts.suggest_quality.datasets import _ncvr_synthetic
    df, gt = _ncvr_synthetic()
    return df.with_row_index("__row_id__")

def test_full_dist_off_is_unchanged(monkeypatch):
    # Default off: review_config emits the same (current) suggestions.
    monkeypatch.delenv("GOLDENMATCH_SUGGEST_FULL_DIST", raising=False)
    from goldenmatch.core.suggest import review_config
    from scripts.suggest_quality.oracle import _auto_configure_no_rerank
    df = _ncvr(); cfg = _auto_configure_no_rerank(df)
    sugg = review_config(df, cfg, verify=False)
    assert all(s.kind == "raise_threshold" for s in sugg)  # the known-buggy current behavior

def test_full_dist_on_fires_lower_threshold_on_too_high(monkeypatch):
    # The misfire fix: on a too-HIGH threshold, the kernel must now see
    # mass_just_below > 0 and emit lower_threshold.
    monkeypatch.setenv("GOLDENMATCH_SUGGEST_FULL_DIST", "1")
    from goldenmatch.core.suggest import review_config
    from scripts.suggest_quality.oracle import _auto_configure_no_rerank
    from scripts.suggest_quality.perturbations import get as getp
    df = _ncvr(); ceil = _auto_configure_no_rerank(df)
    too_high = getp("threshold_too_high").apply(ceil)
    sugg = review_config(df, too_high, verify=False)
    kinds = {s.kind for s in sugg}
    assert "lower_threshold" in kinds, f"expected lower_threshold, got {kinds}"
```

- [ ] **Step 2: Run → fail** (`test_full_dist_on_...` fails: still emits raise_threshold).
- [ ] **Step 3: Implement** — in `review_config`, after `scored_pairs = result.scored_pairs` / `clusters = result.clusters` (~511-515), replace the `scored_pairs_batch` line with:

```python
    scored_pairs = result.scored_pairs
    clusters = result.clusters

    # When full-dist is on, source the score distribution from a threshold-0
    # diagnostic run so the kernel sees the FULL (pre-threshold) distribution --
    # otherwise mass_above is always 1.0 (only >= threshold pairs are returned).
    # clusters/column_signals still come from the REAL run; config_json below still
    # carries the REAL threshold, so the kernel evaluates against the true cutoff.
    pairs_for_kernel = scored_pairs
    if _full_dist_enabled():
        diag_pairs = _diagnostic_scored_pairs(engine, df, _config)
        if diag_pairs is not None:
            pairs_for_kernel = diag_pairs

    # -- Build Arrow batches --
    scored_pairs_batch = _build_scored_pairs_batch(pairs_for_kernel)
    clusters_batch = _build_clusters_batch(clusters)
    column_signals_batch = _build_column_signals_batch(df, _config, clusters)
```

CRITICAL: `config_json = json.dumps(_config_summary(_config), ...)` MUST stay built from `_config` (REAL thresholds), NOT the diagnostic config. Do not touch that line.

- [ ] **Step 4: Run → PASS** (both tests). If `test_full_dist_on_fires_lower_threshold_on_too_high` still fails, instrument: confirm `diag_pairs` actually contains pairs below the too_high threshold (print min score) — if not, the threshold-0 run isn't widening (check the zero-threshold config took effect / blocking didn't change the candidate set).
- [ ] **Step 5: Commit** `feat(suggest): review_config sources full pre-threshold distribution under FULL_DIST`.

---

## Task 3: Gym/oracle validation + findings (run/record)

**Files:** append `## Findings (full-dist, <date from git log>)` to `docs/superpowers/specs/2026-06-25-pre-threshold-scores-design.md`. No pytest. Native runs, minutes each.

- [ ] **Step 1: Baseline (FULL_DIST off) gym** — confirm the starting point (raise-everywhere):
  `GOLDENMATCH_SUGGEST_FULL_DIST=0 ... -m scripts.suggest_quality.cli gym --datasets synthetic,ncvr_synthetic`. Record per-perturbation rule_fired + raw/live recovery + the two headlines.
- [ ] **Step 2: FULL_DIST on gym** — `GOLDENMATCH_SUGGEST_FULL_DIST=1 ... gym --datasets synthetic,ncvr_synthetic`. Record the same. CHECK: does `threshold_too_high` now fire `lower_threshold`? does `bad_freetext_scorer` recovery climb out of negative? does `gym score (raw)` rise from −225.9%?
- [ ] **Step 3: Oracle no-harm** — `GOLDENMATCH_SUGGEST_FULL_DIST=1 ... report --datasets synthetic,ncvr_synthetic`. Record `suggester_precision` per dataset (must stay ~1.0).
- [ ] **Step 4: Unifying bonus check** — re-run the cohesion sweep WITH full-dist:
  `GOLDENMATCH_SUGGEST_FULL_DIST=1 GOLDENMATCH_SUGGEST_HEALTH=cohesion GOLDENMATCH_SUGGEST_COHESION=min_edge ... gym ...` + the matching oracle `report`. Does the de-saturated `mass_above` now let cohesion (or even legacy) thread the dual gate — live recovery up AND suggester_precision ~1.0? Record honestly whatever happens.
- [ ] **Step 5: Record findings** — append a table to the spec: per (variant = off / full-dist / full-dist+cohesion), the threshold_too_high rule fired, raw + live recovery headlines, and oracle suggester_precision. State whether the rule-misfire is resolved and whether the bonus check rescues the proxy. If full-dist does NOT fire lower_threshold or precision regresses, say so honestly — do not fake a pass.
- [ ] **Step 6: Commit** `docs(pre-threshold): full-dist gym/oracle findings`.

Note: the default-on flip (`FULL_DIST=0`→`1`) is OUT of scope (separate evidence-backed change). This plan ends with the fix proven behind the flag + findings recorded.

---

## Done criteria
- `_full_dist_enabled()` + `_zero_threshold_config()` + `_diagnostic_scored_pairs()` land in `adapter.py`; `FULL_DIST=0` default is byte-identical (`test_full_dist_off_is_unchanged` green).
- With `FULL_DIST=1`, `review_config` emits `lower_threshold` on a too-high-threshold config (the misfire fixed, observably).
- The gym findings record: rule-misfire resolved (lower_threshold fires, raw recovery climbs), oracle precision held, plus the bonus cohesion re-sweep result — honestly, whatever the numbers.
- Kernel, rules, pipeline UNCHANGED. `config_json` still carries the real threshold. Default behavior unchanged.
