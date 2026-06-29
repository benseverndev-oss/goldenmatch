# #1207 PR2 — Data-driven TF name weighting + precision-anchor controller rule

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the weighted auto-config path from over-merging on common names — (A) make `name_freq_weighted_jw` downweight agreements on high-frequency name values using a per-dataset frequency table (identical "John Smith" must score below identical rare names), and (B) when the controller sees the everything-matches pathology (`mass_above_threshold` ≈ 1.0), re-anchor the committed config off names and onto the high-identity-score fields (email/npi/phone).

**Architecture:** Two independent parts. **Part A (→ PR2a):** extract `_build_tf_tables`'s per-value-frequency computation into a shared helper; add a `tf_freqs` table to `MatchkeyField`; thread it through `_fuzzy_score_matrix` into `NameFreqWeightedJW.score_matrix`/`score_pair` so the downweight is data-driven and applies across the whole JW range (static census fallback when absent); populate it at auto-config time for `name_freq_weighted_jw` fields. **Part B (→ PR2b):** add `rule_precision_anchor_on_mass_collapse` to the controller's heuristic refit rules — fires on `scoring.mass_above_threshold >= ~0.95`, demotes name-weighted matchkey field weights and promotes strong-id fields (by `identity_score`); returns no change when no strong-id exists (the existing RED → `ControllerNotConfidentError` posture then stands).

**Tech Stack:** Python 3.11+, Polars, NumPy, rapidfuzz, pytest. No new deps.

**Scope:** PR2 of the #1207 staged rollout. PR1 (per-identifier blocking union) shipped via #1315. Spec: `docs/superpowers/specs/2026-06-28-1207-weighted-autoconfig-blocking-tf-anchor-design.md`. **Parts A and B are independent and SHOULD ship as two PRs (PR2a then PR2b)** — do Part A first; Part B does not depend on it.

---

## Posture / rollout (READ FIRST)

Both parts ship **default-on**, but each carries a **kill-switch env var** (matching this repo's autoconfig-lever convention — `GOLDENMATCH_NOISE_AWARE_SCORERS`, `GOLDENMATCH_QUALITY_AWARE_BLOCKING`, etc.) so a benchmark regression can be reverted without a code change:
- Part A: `GOLDENMATCH_TF_NAME_WEIGHTING` (default on; `0`/`false` → today's static-census behavior).
- Part B: `GOLDENMATCH_AUTOCONFIG_PRECISION_ANCHOR` (default on; `0` → rule never fires).

**This is an ACCURACY change.** Part A intentionally lowers the score of identical common-name agreements, which moves precision AND recall. The real guard is the standing CI quality gates — **#528 `synthetic_benchmarks` parity and the DQbench non-regression (composite ≥ 91.04)**, plus Febrl/DBLP-ACM/NCVR. **These cannot be run on the dev box** (OOM + dataset access); they run in CI. So: implement with local unit/proxy tests, rely on the CI gates to validate no regression, and if a gate regresses, flip the relevant kill-switch default to off in the same PR (or before merge) rather than shipping a regression. Do NOT claim a measured accuracy win locally — the local tests prove the MECHANISM (common < rare), not the benchmark delta.

## Environment / how to run tests

- Worktree: `D:\show_case\goldenmatch\.worktrees\1207-pr2-tf-anchor`, branch `feat/1207-pr2-tf-anchor` (off main; PR1 is independent so no dependency).
- **DO NOT run the full pytest suite** (OOM-prone). Run only targeted files/nodes.
- Run a node (from `packages/python/goldenmatch`): `POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 PYTHONIOENCODING=utf-8 /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest <path> -v`. (The `.venv` is shared at repo root; from a worktree use the ABSOLUTE path `/d/show_case/goldenmatch/.venv/Scripts/python.exe` — the relative `../../../.venv` is wrong from a worktree.)
- Lint touched files: `... -m ruff check <files>` (CI enforces E9/F63/F7; also fix F401/F811/I001 you introduce).

## Key facts from the integration map (verify against live code before trusting)

- `NameFreqWeightedJW` is in `refdata/scorer.py` (~:54-122). `score_pair(self, val_a, val_b)` and `score_matrix(self, values)`; constants `_BORDERLINE_LOW=0.70`, `_BORDERLINE_HIGH=0.95`, `_COMMON_NAME_FLOOR=0.6`. Today it only re-weights in `[0.70, 0.95)` using static census `surname_idf`. Registered via `register_scorers()` (~:218) as a SINGLE shared `PluginRegistry` instance — so do NOT make the instance carry per-run state; pass the table per-call instead.
- Hot path: `core/scorer.py::_fuzzy_score_matrix(values, scorer_name, ...)` (~:574-579) does `plugin = PluginRegistry.instance().get_scorer(scorer_name)` then `matrix_fn = getattr(plugin, "score_matrix", None); matrix_fn(values)`. Caller `find_fuzzy_matches` (~:1212) does `_fuzzy_score_matrix(values, f.scorer, model_name=...)` where `f` is the `MatchkeyField`. **This is where the per-field `tf_freqs` must be passed in.**
- `ScorerPlugin` base in `plugins/base.py` defines `score_pair`/`score_matrix`. Extend signatures backward-compatibly with an optional keyword (`tf_freqs=None`); other plugins ignore it.
- `_build_tf_tables(df, mk)` in `core/probabilistic.py` (~:924-966) computes `tf_freqs: dict[field -> {value -> rel_freq}]` + `tf_collision`, applying `MatchkeyField.transforms` before counting. Extract the per-field value-frequency loop into a shared helper; keep `_build_tf_tables` behavior identical.
- `MatchkeyField` / `MatchkeyConfig` in `config/schemas.py`: field has `field`, `scorer`, `weight`, `transforms`; `tf_adjustment: bool=False` already exists (FS path). Add `tf_freqs: dict[str, float] | None = None` (per-field value→rel-freq for the weighted scorer).
- Controller: `profile.scoring.mass_above_threshold` (ScoringProfile in `core/complexity_profile.py`). Refit rules in `core/autoconfig_rules.py` follow `def rule_X(profile, current, history, ctx=None) -> tuple[GoldenMatchConfig, PolicyDecision] | None`; they read column priors via `ctx.column_priors.get(col)` (`.identity_score`), build a new config, return `(new_cfg, PolicyDecision(rule_name=..., rationale=..., config_diff=...))`. Rules are dispatched by `HeuristicRefitPolicy` (find its rule list/order in `autoconfig_rules.py` or `autoconfig_policy.py`). `RunHistory.pick_committed()` + `precision_collapse_floor=0.9` (`core/autoconfig_history.py`) demote RED entries with `mass_above_threshold > 0.9` to rank 3 — so the re-anchored config must drop mass below 0.9 to be committable. RED → `ControllerNotConfidentError` at `df.height >= 100_000` lives in `core/autoconfig_controller.py` (`REFUSE_AT_N`).
- Tests: scorer tests `tests/test_scorer.py`; rule tests `tests/test_autoconfig_rules.py`; controller tests `tests/test_autoconfig*.py`. TS port has `name_freq_weighted_jw` (`packages/typescript/goldenmatch/src/core/scorer.ts`, test `tests/unit/name-freq-weighted-jw.test.ts`) — PR2 parity decision in Part C.

## File Structure

- Create: `packages/python/goldenmatch/goldenmatch/core/tf_tables.py` — shared per-value frequency helper (one responsibility).
- Modify: `core/probabilistic.py` (`_build_tf_tables` delegates to the helper — no behavior change).
- Modify: `config/schemas.py` (add `MatchkeyField.tf_freqs`).
- Modify: `refdata/scorer.py` (`NameFreqWeightedJW` data-driven path).
- Modify: `core/scorer.py` (`_fuzzy_score_matrix` threads `tf_freqs`; `find_fuzzy_matches` passes `f.tf_freqs`).
- Modify: `core/autoconfig.py` (populate `tf_freqs` for `name_freq_weighted_jw` fields in `build_matchkeys`).
- Modify: `core/autoconfig_rules.py` (+ policy registry) for Part B.
- Create: `tests/test_tf_name_weighting_1207.py` (Part A), `tests/test_precision_anchor_1207.py` (Part B).

---

# PART A — Data-driven TF name weighting (→ PR2a)

### Task A1: Extract the shared per-value frequency helper

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/core/tf_tables.py`
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py` (`_build_tf_tables`)
- Test: `packages/python/goldenmatch/tests/test_tf_name_weighting_1207.py`

- [ ] **Step 1: Failing test for the helper**

```python
"""#1207 PR2a: data-driven TF name weighting."""
from __future__ import annotations
import polars as pl
from goldenmatch.core.tf_tables import value_frequencies


def test_value_frequencies_relative_and_transformed():
    df = pl.DataFrame({"last_name": ["Smith", "smith", "SMITH", "Zelinski", None, ""]})
    freqs = value_frequencies(df, "last_name", transforms=["lowercase", "strip"])
    # 3 "smith" + 1 "zelinski" over 4 non-empty -> 0.75 / 0.25; null+"" dropped
    assert abs(freqs["smith"] - 0.75) < 1e-9
    assert abs(freqs["zelinski"] - 0.25) < 1e-9
    assert "" not in freqs and None not in freqs
```

(The test uses transforms `["lowercase", "strip"]` — confirm both are in `VALID_SIMPLE_TRANSFORMS` (`config/schemas.py`) before relying on the assertion; they're standard, but a typo'd transform name would fail the test for the wrong reason.)

- [ ] **Step 2: Run → fail** (`... -m pytest tests/test_tf_name_weighting_1207.py -k value_frequencies -v`) — ImportError.

- [ ] **Step 3: Implement `value_frequencies`**

```python
"""Per-value relative-frequency tables shared by the FS TF-adjustment and the
weighted name scorer's data-driven downweight (#1207 PR2a)."""
from __future__ import annotations
import polars as pl


def value_frequencies(
    df: pl.DataFrame, field: str, transforms: list[str] | None = None,
) -> dict[str, float]:
    """Relative frequency of each transformed non-empty value in ``field``.

    Mirrors the counting in probabilistic._build_tf_tables: applies the same
    transforms, drops None/empty, returns {value -> count/total}. Empty dict
    when the column is absent or all-empty."""
    from goldenmatch.utils.transforms import apply_transforms

    if field not in df.columns:
        return {}
    counts: dict[str, int] = {}
    total = 0
    for v in df[field].to_list():
        if v is None:
            continue
        s = str(v)
        if transforms:
            s = apply_transforms(s, transforms)
        if s is None or s == "":
            continue
        counts[s] = counts.get(s, 0) + 1
        total += 1
    if total == 0:
        return {}
    return {val: c / total for val, c in counts.items()}
```

- [ ] **Step 4: Run → pass.**

- [ ] **Step 5: Refactor `_build_tf_tables` to delegate (no behavior change)**

In `probabilistic.py::_build_tf_tables`, replace the inline per-field counting loop with `freqs = value_frequencies(df, f.field, f.transforms)`; keep the `tf_collision[field] = sum(p*p for p in freqs.values())` and the `if not freqs: continue` / `(None, None)` semantics identical.

- [ ] **Step 6: Run the FS TF tests to confirm no regression**

Run: `... -m pytest tests/test_probabilistic.py -k "tf or term_freq or build_tf" -q` (find the actual TF test names first with `grep -rin "tf_freqs\|_build_tf_tables\|tf_adjustment" tests/`). Expected: green. If no dedicated test exists, run the broader `tests/test_probabilistic.py -q`.

- [ ] **Step 7: Commit** — `feat(tf): #1207 shared value_frequencies helper; probabilistic delegates`

---

### Task A2: Data-driven downweight in `NameFreqWeightedJW`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/config/schemas.py` (`MatchkeyField`)
- Modify: `packages/python/goldenmatch/goldenmatch/refdata/scorer.py` (`NameFreqWeightedJW`)
- Test: `packages/python/goldenmatch/tests/test_tf_name_weighting_1207.py`

- [ ] **Step 1: Add `tf_freqs` to `MatchkeyField`**

In `config/schemas.py`, add to `MatchkeyField`: `tf_freqs: dict[str, float] | None = None` with a comment: "#1207 PR2a: per-dataset value->relative-frequency table for `name_freq_weighted_jw`; when present the scorer downweights agreements on high-frequency values across the whole JW range (data-driven), else falls back to static census IDF in the borderline zone."

- [ ] **Step 2: Failing scorer tests (mechanism: common < rare; static fallback unchanged)**

```python
import numpy as np
from goldenmatch.refdata.scorer import NameFreqWeightedJW

def test_tf_downweights_identical_common_below_identical_rare():
    s = NameFreqWeightedJW()
    tf = {"smith": 0.5, "zelinski": 0.001}   # Smith common, Zelinski rare
    # identical common name pair vs identical rare name pair
    common = s.score_matrix(["smith", "smith"], tf_freqs=tf)[0, 1]
    rare = s.score_matrix(["zelinski", "zelinski"], tf_freqs=tf)[0, 1]
    assert common < rare
    assert rare >= 0.99            # rare identical ~ full credit
    assert common <= 0.75          # common identical materially downweighted

def test_tf_absent_is_todays_static_behavior():
    s = NameFreqWeightedJW()
    # identical names with no tf table -> static path returns plain jw (1.0)
    m = s.score_matrix(["smith", "smith"])     # no tf_freqs
    assert abs(m[0, 1] - 1.0) < 1e-6
```

- [ ] **Step 3: Run → fail** (`score_matrix() got unexpected keyword 'tf_freqs'`).

- [ ] **Step 4: Implement the data-driven path**

Add a module-level rarity helper + extend both methods. Keep the static path byte-identical when `tf_freqs is None`.

```python
def _tf_rarity(value: str | None, tf_freqs: dict[str, float], log_ref: float) -> float:
    """Rarity in [0,1]: 1.0 for a once-seen / unseen value, ~0 for the most
    common. log_ref = -log(min observed freq) normalizes a singleton to 1.0."""
    if value is None or value == "":
        return 1.0
    f = tf_freqs.get(value)
    if f is None or f <= 0.0:
        return 1.0                      # unseen at scoring time -> treat as rare
    import math
    if log_ref <= 0.0:
        return 1.0
    return max(0.0, min(1.0, math.log(1.0 / f) / log_ref))
```

In `score_pair`, BEFORE the existing static block, add:
```python
        if tf_freqs:
            import math
            log_ref = -math.log(min(tf_freqs.values())) if tf_freqs else 0.0
            ra = _tf_rarity(val_a, tf_freqs, log_ref)
            rb = _tf_rarity(val_b, tf_freqs, log_ref)
            weight = _COMMON_NAME_FLOOR + (1.0 - _COMMON_NAME_FLOOR) * ((ra + rb) / 2.0)
            return jw * weight          # data-driven: applies across the WHOLE jw range
```
(Signature becomes `def score_pair(self, val_a, val_b, *, tf_freqs: dict[str, float] | None = None)`; compute `jw = JaroWinkler.similarity(val_a, val_b)` first, handle the None-input guard as today.)

In `score_matrix(self, values, *, tf_freqs=None)`, after computing the base `jw` matrix, add a data-driven branch that SHORT-CIRCUITS the static branch:
```python
        if tf_freqs:
            import math
            log_ref = -math.log(min(tf_freqs.values())) if tf_freqs else 0.0
            rarity = np.array([_tf_rarity(v if v else None, tf_freqs, log_ref) for v in clean],
                              dtype=np.float32)
            mean_r = (rarity[:, None] + rarity[None, :]) / 2.0
            weight = _COMMON_NAME_FLOOR + (1.0 - _COMMON_NAME_FLOOR) * mean_r
            return (jw * weight).astype(np.float32)   # whole-range, no zone gating
        # ... existing static-census path unchanged below ...
```
Apply `tf_freqs` transforms-consistently: the table is keyed on TRANSFORMED values, and `score_matrix`/`score_pair` receive values that have ALREADY had matchkey transforms applied by the pipeline (confirm in `find_fuzzy_matches` — the `values` passed are post-transform). If they are NOT pre-transformed, the table must be built on the RAW values instead (Task A4 controls this — keep both sides consistent and add a comment stating the assumption).

- [ ] **Step 5: Run → pass** (both new tests + existing scorer tests: `... -m pytest tests/test_scorer.py tests/test_tf_name_weighting_1207.py -q`). The static-fallback test guarantees no change when `tf_freqs is None`.

- [ ] **Step 6: Commit** — `feat(scorer): #1207 data-driven TF downweight in name_freq_weighted_jw (static fallback)`

---

### Task A3: Thread `tf_freqs` through the hot path

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/scorer.py` (`_fuzzy_score_matrix`, `find_fuzzy_matches`)
- Modify: `packages/python/goldenmatch/goldenmatch/plugins/base.py` (`ScorerPlugin.score_matrix`/`score_pair` optional `tf_freqs`)
- Test: `packages/python/goldenmatch/tests/test_tf_name_weighting_1207.py`

- [ ] **Step 1: Failing integration test**

```python
def test_fuzzy_score_matrix_passes_tf_freqs():
    from goldenmatch.core.scorer import _fuzzy_score_matrix
    tf = {"smith": 0.5, "zelinski": 0.001}
    common = _fuzzy_score_matrix(["smith", "smith"], "name_freq_weighted_jw", tf_freqs=tf)[0, 1]
    rare = _fuzzy_score_matrix(["zelinski", "zelinski"], "name_freq_weighted_jw", tf_freqs=tf)[0, 1]
    assert common < rare
```

- [ ] **Step 2: Run → fail** (`_fuzzy_score_matrix() got unexpected keyword 'tf_freqs'`).

- [ ] **Step 3: Implement backward-compatible threading**

- In `plugins/base.py`, change the base `score_matrix`/`score_pair` signatures to accept `*, tf_freqs: dict[str, float] | None = None` (default None, ignored by default impls). This keeps every existing plugin valid.
- In `core/scorer.py::_fuzzy_score_matrix`, add a keyword param `tf_freqs: dict[str, float] | None = None`. When calling `matrix_fn`, pass `tf_freqs` only if the plugin accepts it — use `inspect.signature` (the repo already introspects signatures for back-compat in the controller) OR a `try/except TypeError` fallback:
```python
        try:
            matrix = np.asarray(matrix_fn(values, tf_freqs=tf_freqs), dtype=np.float32)
        except TypeError:
            matrix = np.asarray(matrix_fn(values), dtype=np.float32)
```
- In `find_fuzzy_matches` (~:1212), pass the field's table: `_fuzzy_score_matrix(values, f.scorer, model_name=..., tf_freqs=getattr(f, "tf_freqs", None))`.

- [ ] **Step 4: Run → pass** (the A3 test + re-run `tests/test_scorer.py -q` for no regression).

- [ ] **Step 5: Commit** — `feat(scorer): #1207 thread per-field tf_freqs into fuzzy score_matrix`

---

### Task A4: Populate `tf_freqs` at auto-config time

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig.py` (where weighted matchkeys are built — find `build_matchkeys` and the `name_freq_weighted_jw` selection site; grep `name_freq_weighted_jw` in autoconfig.py)
- Test: `packages/python/goldenmatch/tests/test_tf_name_weighting_1207.py`

- [ ] **Step 1: Failing e2e test**

```python
import polars as pl
from goldenmatch.core.autoconfig import auto_configure_df

def test_autoconfig_populates_tf_freqs_for_name_scorer(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_TF_NAME_WEIGHTING", "1")
    # many common surnames + a few rare; first names present so name matchkeys form
    import random
    rng = random.Random(7)
    commons = ["Smith", "Jones", "Brown"]; rares = [f"Rare{i}" for i in range(50)]
    rows = [{"first_name": rng.choice(["John","Jane","Mary"]),
             "last_name": rng.choice(commons) if rng.random() < 0.7 else rng.choice(rares),
             "city": rng.choice(["Springfield","Madison"])} for _ in range(800)]
    df = pl.DataFrame(rows)
    cfg = auto_configure_df(df)
    # find any weighted matchkey field using name_freq_weighted_jw -> it carries tf_freqs
    fields = [f for mk in cfg.get_matchkeys() for f in (mk.fields or [])]
    tf_fields = [f for f in fields if getattr(f, "scorer", None) == "name_freq_weighted_jw"]
    assert tf_fields, "expected a name_freq_weighted_jw field on this person shape"
    assert any(getattr(f, "tf_freqs", None) for f in tf_fields)

def test_kill_switch_disables_tf_population(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_TF_NAME_WEIGHTING", "0")
    # same df; with kill-switch off, no tf_freqs populated
    ... (build same df) ...
    cfg = auto_configure_df(df)
    fields = [f for mk in cfg.get_matchkeys() for f in (mk.fields or [])]
    assert all(getattr(f, "tf_freqs", None) is None for f in fields)
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement population**

In `autoconfig.py`, after weighted matchkeys are built (where a field's `scorer` is set to `name_freq_weighted_jw`), add a gated post-pass: for each weighted matchkey field whose `scorer == "name_freq_weighted_jw"`, set `field.tf_freqs = value_frequencies(df, field.field, field.transforms)` — using the SAME df the rest of auto-config profiles on, and the SAME transforms the scorer will see (keep consistent with the Task A2 transform assumption). Gate behind `_tf_name_weighting_enabled()` (default True unless `GOLDENMATCH_TF_NAME_WEIGHTING` in `{"0","false","no","off"}`), mirroring `_noise_aware_scorers_enabled` style already in the file. Skip when the table would be empty or trivial (e.g. < 2 distinct values).

**Standardization-consistency check (load-bearing — verified concern from plan review):** the table is built from the auto-config `df` with `field.transforms`, but the pipeline may apply column STANDARDIZATION (a separate step from matchkey transforms) before scoring. If the name field is standardized upstream, the scored values won't match the table's keys (built on un-standardized values) and the downweight silently no-ops. In Task A4's e2e test, **assert the mechanism actually bites end-to-end**, not just that `tf_freqs` is populated: e.g. score two records that share an identical common surname and two that share an identical rare surname through the FULL configured path (`dedupe_df(df, config=cfg)` with weighted-matchkey `rerank=False` per the repo's offline-CI pattern, or `score_pair_df`) and assert the common-surname pair's score is lower. If standardization breaks key alignment, either build `tf_freqs` AFTER standardization (from the standardized column) or document that `name_freq_weighted_jw` fields are not auto-standardized. Confirm which, and make the e2e test prove the downweight reaches scoring.

- [ ] **Step 4: Run → pass** (both tests).

- [ ] **Step 5: Commit** — `feat(autoconfig): #1207 populate tf_freqs for name_freq_weighted_jw fields (GOLDENMATCH_TF_NAME_WEIGHTING)`

---

### Task A5: Regression + lint (Part A)

- [ ] **Step 1: Targeted regressions**

Run (NOT the full suite): `... -m pytest tests/test_scorer.py tests/test_autoconfig.py tests/test_autoconfig_regressions.py tests/test_probabilistic.py -q`. Expected: all pass. The DQbench / Febrl / DBLP-ACM / NCVR accuracy gates run in CI (`.github/workflows/`); they are the real validation that the data-driven downweight doesn't regress — you cannot run them locally.

- [ ] **Step 2: Lint** the touched files (scorer.py, refdata/scorer.py, autoconfig.py, schemas.py, tf_tables.py, probabilistic.py).

- [ ] **Step 3: Commit** any test-list/lint fixes — `test(tf): #1207 Part A regression + lint`

> **Handoff for PR2a:** Part A is independently shippable. Take it to a PR here (see Part C steps, scoped to Part A files) OR continue to Part B and split at PR-open time.

---

# PART B — Precision-anchor controller rule (→ PR2b)

### Task B1: `rule_precision_anchor_on_mass_collapse`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py` (new rule + register in the heuristic rule list)
- Test: `packages/python/goldenmatch/tests/test_precision_anchor_1207.py`

- [ ] **Step 1: Read the rule + policy machinery first**

`grep -n "def rule_" core/autoconfig_rules.py` and find the `HeuristicRefitPolicy` rule sequence (in `autoconfig_rules.py` or `autoconfig_policy.py`). Read `rule_blocking_key_swap` and `rule_demote_clustered_identity` in full for the exact `(profile, current, history, ctx)` → `(new_cfg, PolicyDecision)` contract, how `ctx.column_priors[col].identity_score` is read, and how a new `GoldenMatchConfig` is built (copy + mutate matchkey field weights). Note the `_near_dup_locked`/GREEN-health veto patterns.

- [ ] **Step 2: Failing unit tests**

```python
# Build a minimal ComplexityProfile with scoring.mass_above_threshold = 0.98,
# a GoldenMatchConfig whose weighted matchkey weights names heavily, and an
# IndicatorContext whose column_priors rate email/npi identity_score ~0.95 and
# name ~0.3. Assert the rule:
#  (a) fires (returns a new cfg + decision) when a strong-id field exists,
#      and the new cfg's name field weight is reduced while email/npi weight is raised;
#  (b) returns None when mass_above_threshold < 0.95;
#  (c) returns None when NO field has identity_score >= 0.75 (nothing to anchor on).
```
Write three tests building these fixtures directly (mirror the fixture style in `tests/test_autoconfig_rules.py`).

- [ ] **Step 3: Run → fail** (rule not defined / not imported).

- [ ] **Step 4: Implement the rule**

```python
_PRECISION_ANCHOR_MASS = 0.95
_ANCHOR_IDENTITY_MIN = 0.75

def rule_precision_anchor_on_mass_collapse(
    profile: ComplexityProfile, current: GoldenMatchConfig, history: RunHistory,
    ctx: IndicatorContext | None = None,
) -> tuple[GoldenMatchConfig, PolicyDecision] | None:
    """#1207 PR2b: the everything-matches pathology. When scoring mass piles
    above threshold (mass_above_threshold >= 0.95), a name-weighted config is
    over-merging on common names. Re-anchor: demote name-weighted matchkey
    fields, promote high-identity-score fields (email/npi/phone). If no such
    field exists, return None and let the existing RED/refuse posture stand."""
    import os
    if os.environ.get("GOLDENMATCH_AUTOCONFIG_PRECISION_ANCHOR", "1").lower() in {"0","false","no","off"}:
        return None
    if profile.scoring.mass_above_threshold < _PRECISION_ANCHOR_MASS:
        return None
    if ctx is None:
        return None
    # collect strong-id fields present in the data with high identity prior
    strong = {col for col, cp in ctx.column_priors.items()
              if getattr(cp, "identity_score", 0.0) >= _ANCHOR_IDENTITY_MIN}
    if not strong:
        return None                      # nothing to anchor on -> existing refuse stands
    new_cfg = current.model_copy(deep=True)   # or the repo's clone idiom — confirm
    changed = False
    for mk in new_cfg.get_matchkeys():
        if mk.type != "weighted" or not mk.fields:
            continue
        for f in mk.fields:
            is_name = (f.scorer in {"name_freq_weighted_jw", "given_name_aliased_jw"}
                       or (ctx.column_priors.get(f.field) and ctx.column_priors[f.field].identity_score < 0.5))
            if f.field in strong:
                # promote the precision anchor
                if (f.weight or 1.0) < 3.0:
                    f.weight = 3.0; changed = True
            elif is_name:
                # demote name-only signal
                if (f.weight or 1.0) > 1.0:
                    f.weight = 1.0; changed = True
    if not changed:
        return None
    decision = PolicyDecision(
        rule_name="precision_anchor_on_mass_collapse",
        rationale=(f"mass_above_threshold={profile.scoring.mass_above_threshold:.2f} "
                   f">= {_PRECISION_ANCHOR_MASS}; re-anchored onto strong-id fields {sorted(strong)} "
                   f"and demoted name-weighted fields"),
        config_diff={},
    )
    return new_cfg, decision
```
Then REGISTER it in the `HeuristicRefitPolicy` rule list/order (match how the other rules are registered — likely an ordered list the policy iterates). Place it where a precision-collapse remedy belongs (before generic threshold-loosening rules so it pre-empts them). Confirm `model_copy`/clone idiom and the exact weight field name against the live schema.

- [ ] **Step 5: Run → pass** (all three unit tests).

- [ ] **Step 6: Commit** — `feat(autoconfig): #1207 precision-anchor refit rule on mass collapse (GOLDENMATCH_AUTOCONFIG_PRECISION_ANCHOR)`

---

### Task B2: Controller integration test

**Files:**
- Test: `packages/python/goldenmatch/tests/test_precision_anchor_1207.py`

- [ ] **Step 1: Failing integration test**

Build a small (< 5k rows so no 100k refuse, no learned-blocking takeover) person dataset with: common-name collisions across distinct people, a strong id (email) present and discriminating, so a name-weighted v0 config over-merges (mass_above_threshold high) but an email-anchored config is clean. Run `auto_configure_df(df)` and assert the committed config does NOT lean on names (the email/strong-id field has the higher weight), and that controller telemetry/history records the `precision_anchor_on_mass_collapse` rule firing.

```python
def test_controller_reanchors_off_names_on_mass_collapse():
    df = _common_name_with_email_anchor_df()   # local fixture, ~2-3k rows
    cfg = auto_configure_df(df)
    weights = {f.field: (f.weight or 1.0) for mk in cfg.get_matchkeys()
               if mk.type == "weighted" for f in (mk.fields or [])}
    # email (strong id) should out-weigh last_name after re-anchoring
    if "email" in weights and "last_name" in weights:
        assert weights["email"] >= weights["last_name"]
    # rule fired (read controller history)
    from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN
    run = _LAST_CONTROLLER_RUN.get()
    assert run is not None
    _profile, history = run
    assert any("precision_anchor" in d.rule_name for d in history.decisions)
```
(Confirm `_LAST_CONTROLLER_RUN` access + `history.decisions[].rule_name` against the live code; the map says decisions carry `rule_name`. If the fixture doesn't trip mass-collapse, tune name-commonality/email-discrimination until v0 does over-merge — verify by asserting the rule fired.)

- [ ] **Step 2: Run → fail / iterate the fixture until the rule genuinely fires and the assertion holds.** Do NOT weaken the assertion to pass; shape the fixture so the pathology is real.

- [ ] **Step 3: Commit** — `test(autoconfig): #1207 controller re-anchors off names on mass collapse`

---

### Task B3: Regression + lint (Part B)

- [ ] **Step 1:** `... -m pytest tests/test_autoconfig_rules.py tests/test_autoconfig.py tests/test_autoconfig_regressions.py tests/test_precision_anchor_1207.py -q` → all pass. (DQbench/Febrl/NCVR via CI.)
- [ ] **Step 2:** Lint touched files.
- [ ] **Step 3: Commit** — `test(autoconfig): #1207 Part B regression + lint`

---

# PART C — Docs + parity + PR(s)

### Task C1: Docs

- [ ] CHANGELOG entry under `## [Unreleased]` (ASCII, no em-dashes) describing PR2a (data-driven TF name downweight, default-on, kill-switch `GOLDENMATCH_TF_NAME_WEIGHTING`, CI-gated) and PR2b (precision-anchor rule, `GOLDENMATCH_AUTOCONFIG_PRECISION_ANCHOR`). Note both are accuracy changes validated by the CI quality gates.
- [ ] One bullet each in `packages/python/goldenmatch/CLAUDE.md` `## Auto-Config` section: the `tf_freqs`/`value_frequencies` seam + the whole-range data-driven downweight vs static fallback; and the `mass_above_threshold >= 0.95` re-anchor rule + its kill-switch + the "no strong id -> refuse stands" behavior.
- [ ] Commit — `docs(autoconfig): #1207 PR2 changelog + CLAUDE.md notes`

### Task C2: TS parity decision

- [ ] The TS port has `name_freq_weighted_jw` (static parity). PR2a adds a data-driven `tf_freqs` table; PR2b is controller-only (the TS port may not have the introspective controller — confirm). Decision: **defer TS parity to a follow-up issue** (porting `tf_freqs` threading + the rule is substantial; CI typecheck-gates TS, no local toolchain). File a follow-up issue noting the Python-vs-TS gap PR2 opens (mirror PR1's #1317). Put the one-liner in the PR body.

### Task C3: Open PR(s) + arm auto-merge

- [ ] **Recommended: two PRs.** Open PR2a (Part A commits) first; after it's green, rebase/branch Part B and open PR2b. If keeping one PR, open it for the whole branch. Use the `benzsevern` account: `unset GH_TOKEN; gh auth switch --user benzsevern`, push, then `GH_TOKEN=$(gh auth token --user benzsevern) gh pr create ...` and `gh pr merge <N> --auto` (the merge queue owns the squash strategy). End PR body with the Claude Code footer. Then STOP — do not poll CI.

---

## Done criteria for PR2

- **PR2a:** `value_frequencies` shared helper; `MatchkeyField.tf_freqs`; `NameFreqWeightedJW` downweights identical common names below identical rare names when a table is present, byte-identical static behavior when absent; `tf_freqs` threaded through the vectorized hot path; auto-config populates it for name scorers behind `GOLDENMATCH_TF_NAME_WEIGHTING` (default on); targeted regressions green; CI accuracy gates green (the real validation) or kill-switch flipped off if they regress.
- **PR2b:** `rule_precision_anchor_on_mass_collapse` fires at `mass_above_threshold >= 0.95`, re-anchors onto strong-id fields, no-ops without one; registered in the heuristic policy; unit + controller-integration tests green; targeted regressions green.
- Docs updated; TS-parity follow-up filed; PR(s) opened with auto-merge armed.
