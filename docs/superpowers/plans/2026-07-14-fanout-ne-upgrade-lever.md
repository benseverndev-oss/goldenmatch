# Fan-out / Negative-Evidence Upgrade Lever Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a risk-gated `fan_out` lever to the Splink migration upgrade pass that suggests
negative-evidence fields (with posterior-estimated `__ne__` weights) and tunes
`golden_rules.max_cluster_size` from reference clusters — and teach the calibration lever
`fs_weight_range` so NE-bearing configs calibrate instead of tripwiring.

**Spec:** `docs/superpowers/specs/2026-07-14-fanout-ne-upgrade-lever-design.md` (read it first; it
pins every semantic decision: posterior definition, gate thresholds, storage schema, guard formula).

**Architecture:** New module `goldenmatch/config/splink_upgrade_fanout.py` holds the lever body
(mirroring the `splink_upgrade_measure.py` precedent); `splink_upgrade.py` registers a thin
lazy-import wrapper as lever `"fan_out"`, ordered between `distance_thresholds` and
`calibration`. `_LeverContext` gains `splink_clusters`/`labels`/`id_column` (previously
measurement-only). The calibration lever's within-block prior re-estimation is extracted to a
shared helper both levers call.

**Tech Stack:** Python 3.12, polars (via `goldenmatch._polars_lazy` ONLY in production modules),
pydantic config models, pytest. No Rust/TS work in this plan.

---

## Environment / repo mechanics (read before Task F0)

- The main checkout `D:\show_case\goldenmatch` sits on an unrelated branch with a dirty tree.
  Work in a NEW worktree off freshly-fetched `origin/main` (Task F0). **NEVER `git stash`**
  (repo-global across worktrees).
- Run worktree Python tests via the MAIN checkout's venv with `PYTHONPATH` pointing at the
  worktree package:
  `PYTHONPATH="<worktree>/packages/python/goldenmatch" POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENMATCH_NATIVE=0 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest <tests> -v`
- Production modules MUST import polars as `from goldenmatch._polars_lazy import pl` — a bare
  top-level `import polars` reds every zero-polars CI gate (bit PR #1760). Test files may import
  polars directly (repo norm).
- Lint before each commit: `.venv/Scripts/python.exe -m ruff check <touched files>` from the
  worktree package dir.
- `docs/superpowers/` is gitignored: commit spec + plan with `git add -f`.
- Push/PR auth dance: `unset GH_TOKEN`; push via
  `git push "https://x-access-token:$(gh auth token --user benzsevern)@github.com/benseverndev-oss/goldenmatch.git" <branch>`;
  `GH_TOKEN=$(gh auth token --user benzsevern) gh pr create ...`; arm
  `gh pr merge --auto <N>` (merge queue sets the strategy) and STOP — do not poll CI.

**Key existing code (all on origin/main — verify against the worktree, not the stale main checkout):**
- `goldenmatch/config/splink_upgrade.py` — lever registry (`_LEVER_REGISTRY`, `_LEVER_ORDER`),
  `_LeverContext`, `_lever_calibration` (contains the NE tripwire ~line 493 and the inline
  within-block prior re-estimation ~line 632), orchestrator `upgrade_splink_conversion`
  (threads `splink_clusters`/`labels`/`id_column` to measurement only).
- `goldenmatch/config/splink_upgrade_measure.py` — `_resolve_ids(df, id_column)`,
  `_load_reference(...)`, cluster-size helpers.
- `goldenmatch/core/probabilistic.py` — `_ne_fired(row_a, row_b, ne_field)` (line ~466),
  `_ne_scalar_contribution(row_a, row_b, ne, em_result)` (~1509), `fs_weight_range(em, mk)`
  (~1468), `_sample_blocked_pairs(blocks, n_pairs, seed)` (~563),
  `prior_weight(proportion_matched)` (~77), `posterior_from_weight(total_weight, prior_w)` (~88),
  `comparison_vector`, `compute_thresholds`.
- `goldenmatch/core/autoconfig_negative_evidence.py` — `_pick_scorer_for_column(col_name,
  col_type) -> (transforms, scorer)`, `_DEFAULT_NE_THRESHOLD = 0.4`.
- `goldenmatch/core/blocker.py` — `build_blocks(lf, blocking_config)`,
  `collect_blocking_fields(blocking_config)`.
- `goldenmatch/config/schemas.py` — `NegativeEvidenceField` (probabilistic matchkeys REJECT
  `penalty`, accept `penalty_bits` or NEITHER — the lever emits NEITHER), `GoldenRulesConfig`
  (`max_cluster_size=100`, `auto_split=True`).
- Existing tests to mirror style from: `tests/test_splink_upgrade_levers.py`,
  `tests/test_splink_upgrade_measure.py`, `tests/test_fs_ne_e2e.py` (homonym fixture patterns:
  blocks need MIXED identities or EM/posteriors saturate; spread surnames across soundex codes).

## File structure

- Create: `packages/python/goldenmatch/goldenmatch/config/splink_upgrade_fanout.py`
  (lever body: candidate eligibility, risk gate, posterior-weighted NE estimation, guard tuning)
- Modify: `packages/python/goldenmatch/goldenmatch/config/splink_upgrade.py`
  (ctx fields, shared prior helper, registry entry, calibration NE-awareness)
- Create: `packages/python/goldenmatch/tests/test_splink_upgrade_fanout.py` (unit)
- Create: `packages/python/goldenmatch/tests/test_splink_upgrade_fanout_e2e.py` (success bar)
- Modify: `packages/python/goldenmatch/tests/test_splink_upgrade_levers.py`
  (calibration NE-awareness tests; tripwire test replaced; lever-order assertion updated in F1)
- Modify: `packages/python/goldenmatch/goldenmatch/cli/import_splink.py` (help text only)

Module constants (in `splink_upgrade_fanout.py`, all with rationale comments):

```python
_FANOUT_POSTERIOR_CONFIDENT = 0.9   # "confident merge" posterior floor for the risk gate
_FANOUT_MIN_FIRE_RATE = 0.02        # min NE firing rate among confident-merge pairs
_FANOUT_MIN_FIRING_PAIRS = 10       # min absolute firing confident pairs (estimation support)
_FANOUT_MIN_NONNULL = 0.5           # candidate columns sparser than this are not suggested
_FANOUT_MIN_CARDINALITY = 0.5       # mirrors autoconfig NE's _CARDINALITY_THRESHOLD
_FANOUT_MAX_CARDINALITY = 0.999     # a perfect surrogate key has zero shared-identity signal
                                     # (mirrors #721's uniform exact-scorer gate); it would also
                                     # fire on true dups and be dropped by the w>=0 check, but
                                     # excluding it up front keeps findings clean
_FANOUT_RANDOM_PAIRS = 10_000       # u_fire sample size (train_em's random-pair u route scale)
_PROB_CLAMP = 1e-4                  # m/u clamp away from 0/1 before log2
_GUARD_MIN_CAP = 10                 # floor of max(10, 2 * reference_max)
_NE_NAME_PATTERNS = (
    "phone", "mobile", "email", "e-mail", "e_mail", "address", "addr",
    "ssn", "npi", "license", "licence", "passport",
)
```

---

### Task F0: Worktree + branch + commit spec/plan

**Files:** none (repo mechanics)

- [ ] **Step 1:** From `D:\show_case\goldenmatch`: `git fetch origin main` then
  `git worktree add D:\show_case\gm-fanout-lever -b feat/splink-upgrade-fanout-lever origin/main`
- [ ] **Step 2:** Copy the spec + this plan into the worktree (same paths under
  `docs/superpowers/`), `git add -f docs/superpowers/specs/2026-07-14-fanout-ne-upgrade-lever-design.md docs/superpowers/plans/2026-07-14-fanout-ne-upgrade-lever.md`
- [ ] **Step 3:** Commit: `git commit -m "docs: spec + plan for the fan-out/NE upgrade lever"`
- [ ] **Step 4:** Sanity: run the existing lever tests against the worktree via the PYTHONPATH
  pattern above: `pytest tests/test_splink_upgrade_levers.py -x -q` → all pass. If red, STOP and
  investigate before building.

---

### Task F1: Context threading + shared prior helper + registry stub

Thread `splink_clusters`/`labels`/`id_column` into `_LeverContext`; extract the calibration
lever's inline within-block prior re-estimation into `_estimate_within_block_prior`; register
`fan_out` in the registry/order with a lazy-import wrapper; create the new module with ONLY the
bare-settings skip implemented.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/config/splink_upgrade.py`
- Create: `packages/python/goldenmatch/goldenmatch/config/splink_upgrade_fanout.py`
- Create: `packages/python/goldenmatch/tests/test_splink_upgrade_fanout.py`
- Modify: `packages/python/goldenmatch/tests/test_splink_upgrade_levers.py` — the existing
  `test_lever_order_tf_tables_then_distance_then_calibration` (~line 985) asserts the exact
  3-tuple `_LEVER_ORDER`; update it (and any first-occurrence ordering assertion around it) to
  the new 4-lever order as part of THIS task. Its failure before that edit is expected and
  mechanical, not a leak.

- [ ] **Step 1: Failing tests** — in the new test file:
  - `test_fan_out_in_default_lever_order`: `_resolve_levers(None) == ["tf_tables",
    "distance_thresholds", "fan_out", "calibration"]`.
  - `test_fan_out_selectable_alone`: `_resolve_levers({"fan_out"}) == ["fan_out"]`; unknown name
    still raises.
  - `test_fan_out_bare_settings_skip`: build a bare-settings `SplinkConversion` (no em_model —
    mirror the existing bare-settings fixtures in `test_splink_upgrade_levers.py`), run
    `upgrade_splink_conversion(conv, df, levers={"fan_out"}, measure=False)` → exactly one
    `upgrade:fan_out` info finding containing "no imported model"; config unchanged
    (`upgraded_config.model_dump() == baseline model_dump()`).
  - `test_estimate_within_block_prior`: pure-math check —
    `_estimate_within_block_prior([0.0]) == 0.5` (2^0/(1+2^0)); a strongly negative and strongly
    positive weight average to ~0.5; empty list raises ValueError.
  - `test_lever_context_carries_reference_inputs`: `upgrade_splink_conversion(...,
    splink_clusters=<df>, labels=<df>, id_column="rec_id", measure=False)` — assert via a
    monkeypatched fan_out lever that `ctx.splink_clusters`/`ctx.labels`/`ctx.id_column` arrive.
- [ ] **Step 2:** Run: `pytest tests/test_splink_upgrade_fanout.py -v` → FAIL (attribute/name errors).
- [ ] **Step 3: Implement.**
  - `_LeverContext`: add fields `splink_clusters: object | None = None`,
    `labels: object | None = None`, `id_column: str | None = None` (typed loose — they're
    DataFrame-or-path passthroughs, same as the orchestrator signature). Orchestrator passes
    them into the ctx it builds (measurement keeps receiving them as today — no measurement
    change).
  - Extract into `splink_upgrade.py` module level:
    ```python
    def _estimate_within_block_prior(total_weights: list[float]) -> float:
        """Within-block match-rate estimate from model likelihood ratios under an
        equal-odds prior: mean of 2^w/(1+2^w). (Extracted from _lever_calibration;
        see its comment block for the errs-HIGH safety rationale.)"""
        if not total_weights:
            raise ValueError("cannot estimate a within-block prior from zero pairs")
        return sum(posterior_from_weight(w, 0.0) for w in total_weights) / len(total_weights)
    ```
    Replace the inline computation in `_lever_calibration` with a call (keep the big rationale
    comment on the helper). `posterior_from_weight` is function-local-imported today — move that
    import to where the helper needs it (function-local inside the helper, matching module style).
  - New module `splink_upgrade_fanout.py`: docstring citing the spec; constants block;
    `run_fan_out_lever(ctx) -> None` that for now ONLY implements the bare-settings skip:
    ```python
    def run_fan_out_lever(ctx) -> None:
        if ctx.conversion.em_model is None:
            ctx.report.info("upgrade:fan_out", _BARE_SETTINGS_SKIP_MSG_FANOUT, mapped_to=None)
            return
        _suggest_negative_evidence(ctx)   # Task F3
        _tune_cluster_guard(ctx)          # Task F4
    ```
    with both helpers as no-op `pass` stubs for now (NOT NotImplementedError — fan_out is in the
    default lever set from this commit on, and every existing trained-model lever test runs it).
  - In `splink_upgrade.py`: registry wrapper with lazy import (keeps the module import-light,
    mirroring the measurement import):
    ```python
    def _lever_fan_out(ctx: _LeverContext) -> None:
        from goldenmatch.config.splink_upgrade_fanout import run_fan_out_lever
        run_fan_out_lever(ctx)
    ```
    `_LEVER_REGISTRY["fan_out"] = _lever_fan_out`;
    `_LEVER_ORDER = ("tf_tables", "distance_thresholds", "fan_out", "calibration")`.
    Update the `upgrade_splink_conversion` docstring's lever list.
- [ ] **Step 4:** Update the lever-order assertion(s) in `test_splink_upgrade_levers.py` to the
  4-lever tuple, then run new tests → PASS, and `pytest tests/test_splink_upgrade_levers.py
  tests/test_cli_import_splink_upgrade.py -q` → all pass (existing fixtures have no NE candidates,
  so the stub no-ops; any OTHER failure means the registry change leaked).
- [ ] **Step 5:** ruff both touched modules + test file; commit
  `feat(splink-upgrade): register fan_out lever scaffold + shared prior helper`.

---

### Task F2: NE candidate eligibility

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/config/splink_upgrade_fanout.py`
- Test: `packages/python/goldenmatch/tests/test_splink_upgrade_fanout.py`

- [ ] **Step 1: Failing tests** for `_ne_candidates(df, mk, blocking) -> list[_NECandidate]`
  (`_NECandidate` = small dataclass: `column`, `transforms`, `scorer`). Build a df with columns:
  `given_name`, `surname`, `city` (comparison/blocking fields on a probabilistic matchkey),
  `phone` (eligible), `email_null_heavy` (>50% null → excluded), `phone_low_card` (a phone-named
  column with ~3 distinct values → excluded), `ssn` (identity-NAMED but all-unique, ratio 1.0 →
  excluded by `_FANOUT_MAX_CARDINALITY`; a plain `rec_id` surrogate would be excluded by the
  name filter first and never exercise the gate), `notes` (non-identity name → excluded). Cases:
  - returns exactly `phone`, with `(["digits_only"], "exact")` from `_pick_scorer_for_column`;
  - a `phone2` column that IS a comparison field on mk → excluded;
  - a `phone3` column used as a blocking key (in `collect_blocking_fields(blocking)`) → excluded;
  - name matching is case-insensitive (`Phone_Number` eligible).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement.** Pure function; polars via `goldenmatch._polars_lazy`. Cardinality
  ratio = `df[col].n_unique() / max(1, len(df))` on the (already sampled) frame; non-null rate =
  `1 - df[col].null_count() / max(1, len(df))`. Identity-grade = any `_NE_NAME_PATTERNS` substring
  in `col.lower()`. Scorer/transforms from
  `autoconfig_negative_evidence._pick_scorer_for_column(col, "")` (import at module top — it's a
  light module). Exclusions per the spec + constants block.
- [ ] **Step 4:** Run → PASS. Ruff.
- [ ] **Step 5:** Commit `feat(splink-upgrade): fan_out NE candidate eligibility`.

---

### Task F3: Risk gate + posterior-weighted NE estimation + attachment

The core. Fills `_suggest_negative_evidence(ctx)`.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/config/splink_upgrade_fanout.py`
- Test: `packages/python/goldenmatch/tests/test_splink_upgrade_fanout.py`

**Algorithm (all semantics pinned by the spec — deviations need a spec edit):**

```python
def _suggest_negative_evidence(ctx) -> None:
    mk = ctx.upgraded_config.get_matchkeys()[0]
    em = ctx.em_model
    blocking = ctx.upgraded_config.blocking
    if blocking is None:
        warn "skipped: config has no blocking configuration ..." ; return
    # Partial imported model: posteriors need every comparison field covered
    # (mirror _lever_calibration's uncovered-fields warn+skip verbatim; guard
    # tuning is unaffected and still runs after us).
    candidates = _ne_candidates(ctx.df, mk, blocking)
    if not candidates:
        info "no eligible NE candidate columns" ; return
    # Blocked pairs: same route as calibration (build_blocks on __row_id__ lf +
    # _sample_blocked_pairs with _CALIBRATION_MAX_PAIRS / ctx.seed). Also emit the
    # block-size findings here (p50/p95/max over materialized block heights) --
    # spec: findings only, max_block_size untouched.
    if len(pairs) <= _CALIBRATION_MIN_PAIRS:
        warn "skipped: only N blocked candidate pair(s) ..." ; return
    # row_lookup over matchkey fields + candidate columns (extend calibration's pattern)
    # total_weights: regular-field FS sums (same indexed_fields pattern as calibration)
    prior = _estimate_within_block_prior(total_weights)      # shared helper (F1)
    prior_w = prior_weight(prior)
    posteriors = [posterior_from_weight(w, prior_w) for w in total_weights]
    confident = [p >= _FANOUT_POSTERIOR_CONFIDENT for p in posteriors]
    for cand in candidates:
        ne = NegativeEvidenceField(field=cand.column, transforms=cand.transforms,
                                   scorer=cand.scorer, threshold=_DEFAULT_NE_THRESHOLD)
        fired = [_ne_fired(row_lookup[a], row_lookup[b], ne) for (a, b) in pairs]
        n_conf = sum(confident); n_fired_conf = sum(f and c for f, c in zip(fired, confident))
        rate = n_fired_conf / n_conf if n_conf else 0.0
        info finding: measured contradiction rate + counts (ALWAYS, gated or not)
        if n_conf == 0 or rate < _FANOUT_MIN_FIRE_RATE or n_fired_conf < _FANOUT_MIN_FIRING_PAIRS:
            continue   # the info finding above already reports why
        m_fire = clamp(sum(p*f)/sum(p), _PROB_CLAMP, 1-_PROB_CLAMP)   # posterior-weighted
        u_fire = clamp(_random_pair_firing_rate(row_lookup, ne, ctx.seed), ...)
        w_fired = log2(m_fire / u_fire)
        if w_fired >= 0:
            warn "column does not discriminate on this data (w_fired=...) -- dropped" ; continue
        mk.negative_evidence = (mk.negative_evidence or []) + [ne]
        em.m_probs[f"__ne__{cand.column}"] = [m_fire, 1 - m_fire]
        em.u_probs[f"__ne__{cand.column}"] = [u_fire, 1 - u_fire]
        em.match_weights[f"__ne__{cand.column}"] = [w_fired, 0.0]
        info finding: field added, m/u/bits + contradiction rate
```

`_random_pair_firing_rate`: `random.Random(seed)` sampling up to `_FANOUT_RANDOM_PAIRS` pairs of
distinct row ids from `row_lookup` keys (with replacement over pairs is fine; skip a==b), return
mean `_ne_fired`. NO penalty / NO penalty_bits on the emitted field (probabilistic validation
matrix: EM-learned shape).

- [ ] **Step 1: Failing tests.** Synthetic frame (~200 rows) engineered so the imported model is
  hand-built (construct `EMResult` directly with known m/u per field — no EM training in unit
  tests) and blocking on `city` yields mixed blocks:
  - `test_fan_out_adds_ne_when_risk_present`: homonym pairs (same name+city, different phone)
    inside blocks → NE field `phone` appended; `__ne__phone` entries present with
    `match_weights[0] < 0`, `[1] == 0.0`; m/u 2-lists sum to 1 (within clamp); the emitted
    `NegativeEvidenceField` has `penalty is None and penalty_bits is None`; config still
    validates: `GoldenMatchConfig(**upgraded.model_dump())` round-trips.
  - `test_fan_out_no_risk_no_ne`: same frame but phones AGREE on confident pairs → no NE, info
    finding with the measured (near-zero) rate.
  - `test_fan_out_insufficient_support_skips`: risk present but < `_FANOUT_MIN_FIRING_PAIRS`
    firing confident pairs → no NE.
  - `test_fan_out_nondiscriminating_dropped`: phone differs on EVERYTHING (fires on random pairs
    at ≥ the match rate → `w_fired >= 0`) → warn finding, no NE.
  - `test_fan_out_no_blocking_warns`: config with `blocking=None` → warn+skip, no crash.
  - `test_fan_out_partial_model_skips_ne`: drop one comparison field from `em.match_weights` →
    warn+skip (message mirrors calibration's partial-model wording).
  - `test_fan_out_copy_on_write`: baseline `conversion.em_model` has NO `__ne__` keys and
    baseline config has no NE after the pass.
  - `test_fan_out_block_size_findings`: an `upgrade:fan_out` info finding reports block-size
    p50/p95/max.
  - `test_fan_out_runs_under_posterior_mode`: `GOLDENMATCH_FS_CALIBRATED=posterior`
    (monkeypatch env) → NE still suggested (lever is mode-independent).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement per the algorithm block.
- [ ] **Step 4:** Run new tests + full `pytest tests/test_splink_upgrade_fanout.py
  tests/test_splink_upgrade_levers.py -q` → PASS.
- [ ] **Step 5:** Ruff; commit `feat(splink-upgrade): risk-gated posterior-estimated NE suggestion`.

---

### Task F4: Guard tuning

Fills `_tune_cluster_guard(ctx)`.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/config/splink_upgrade_fanout.py`
- Test: `packages/python/goldenmatch/tests/test_splink_upgrade_fanout.py`

Reference priority `labels -> splink_clusters -> skip(info)`. Load via lazy import of
`splink_upgrade_measure._load_reference` and resolve ids via `_resolve_ids(ctx.df,
ctx.id_column)`; when the resolved ids are positional (the `_resolve_ids` fallback) or the
id-join overlap with the reference is zero, skip with an info finding naming `id_column=` (the
`_checked_reference` posture, but info-level — guard tuning is optional, not a measurement
integrity failure). Restricted to joined ids: sizes = reference cluster sizes;
`new_cap = max(_GUARD_MIN_CAP, 2 * max(sizes))`. Create `GoldenRulesConfig()` on the upgraded
config when absent, set `max_cluster_size`, finding reports old → new + max/p99 + which
reference. `_load_reference` failures (bad path/shape) → warn+skip. NOTE: `_resolve_ids` RAISES
`SplinkUpgradeError` on a missing or duplicate-valued explicit `id_column`; the lever contract is
never-fail, so wrap the `_resolve_ids` call in try/except → warn+skip (measurement's later
identical call is separately caught by the orchestrator's try/except and surfaces the same
problem there).

- [ ] **Step 1: Failing tests.**
  - `test_guard_tuned_from_labels`: labels df (`rec_id`,`cluster`) with max true cluster 4 +
    `id_column="rec_id"` → `max_cluster_size == 10` (floor wins over 2×4);
    with max cluster 30 → `60` (2× wins, loosening allowed when > default? here 60 < 100 —
    also add a case with reference max 80 → cap 160 > default 100, asserting the symmetric raise).
  - `test_guard_prefers_labels_over_splink_clusters`: both provided, different maxima → labels win;
    finding names "labels".
  - `test_guard_skips_without_reference`: neither provided → info finding, `golden_rules`
    max_cluster_size untouched (still default).
  - `test_guard_skips_on_unjoinable_ids`: reference ids share nothing with the data ids (or no
    id_column + no id-ish column → positional) → info finding mentioning `id_column`, no change.
  - `test_guard_baseline_untouched`: baseline config's golden_rules unchanged (copy-on-write).
- [ ] **Step 2:** Run → FAIL.  **Step 3:** Implement.  **Step 4:** Run → PASS; ruff.
- [ ] **Step 5:** Commit `feat(splink-upgrade): data-driven max_cluster_size guard tuning`.

---

### Task F5: Calibration lever NE-awareness (tripwire payoff)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/config/splink_upgrade.py` (`_lever_calibration`)
- Test: `packages/python/goldenmatch/tests/test_splink_upgrade_levers.py`

- [ ] **Step 1: Failing tests.**
  - Replace the existing tripwire test (find it: `grep -n "negative_evidence" tests/test_splink_upgrade_levers.py`)
    with `test_calibration_runs_on_ne_bearing_config`: hand-build a conversion whose matchkey
    carries an NE field + `__ne__` model entries (the F3 output shape) → calibration sets
    `link_threshold`/`review_threshold` in (0, 1], no warn finding about NE.
  - `test_calibration_ne_parity_when_never_fires`: same config but the NE column all-null in the
    data (NE never fires; `fs_weight_range` max unchanged, min extended) → thresholds computed;
    assert normalized weights stayed in [0,1] (indirectly: link/review in range) and equal the
    no-NE run's thresholds when the NE weight range contribution is zero
    (`penalty_bits`-free, `w_fired=0.0` entry edge: use `match_weights["__ne__x"]=[0.0, 0.0]`).
  - `test_calibration_uses_fs_weight_range`: monkeypatch `fs_weight_range` to a sentinel raising
    → calibration hits it (proves the hand-rolled sums are gone).
- [ ] **Step 2:** Run → FAIL (tripwire still skips).
- [ ] **Step 3: Implement** in `_lever_calibration`:
  - DELETE the tripwire block (the `if mk.negative_evidence:` warn+skip).
  - Extend `cols` (row_lookup projection) with NE field names:
    `cols += [ne.field for ne in (mk.negative_evidence or []) if ne.field not in cols]`.
  - Per-pair weight: add `sum(_ne_scalar_contribution(row_a, row_b, ne, em) for ne in
    (mk.negative_evidence or []))` (import `_ne_scalar_contribution` in the function-local
    import block).
  - Replace the hand-rolled `max_weight`/`min_weight` sums with
    `min_weight, max_weight = fs_weight_range(em, mk)`. NOTE: `fs_weight_range` iterates ALL
    `mk.fields` (no `__record__` exclusion), but converted configs never emit `__record__`
    pseudo-fields and the existing uncovered-fields guard runs first — leave a one-line comment
    saying exactly that.
- [ ] **Step 4:** Run the whole lever test file → PASS.  Ruff.
- [ ] **Step 5:** Commit `feat(splink-upgrade): calibration lever is NE-aware (fs_weight_range); tripwire removed`.

---

### Task F6: E2E success bar + CLI help text

**Files:**
- Create: `packages/python/goldenmatch/tests/test_splink_upgrade_fanout_e2e.py`
- Modify: `packages/python/goldenmatch/goldenmatch/cli/import_splink.py` (docstring/help for
  `--upgrade` mentioning the fan_out lever; NO behavior change)
- Test: `packages/python/goldenmatch/tests/test_cli_import_splink_upgrade.py` (only if an
  existing help-text assertion breaks — do NOT add Rich-help substring scraping tests)

- [ ] **Step 1: Failing E2E test** (`test_fanout_lever_success_bar`). Build:
  - A Splink settings dict (trained: comparison levels carry `m_probability`/`u_probability`)
    over `given_name`+`surname`+`city` with blocking on `city` — mirror the settings-building
    helpers in `tests/test_from_splink_model_import.py`.
  - A dataframe in the `test_fs_ne_e2e.py` style: N true-duplicate pairs (same person, small
    perturbations, SAME phone) + K homonym traps (distinct people, same name+city, DIFFERENT
    phone) + filler singletons; `rec_id` id column; `labels` df with true cluster ids; blocks
    MUST mix identities (fixture lesson from #1764) and surnames must spread across soundex
    codes (memory: synthetic surname fixtures).
  - `phone` appears in the DATA but NOT in the Splink settings.
  Then: `conv = from_splink(settings)`;
  `res = upgrade_splink_conversion(conv, df, labels=labels_df, id_column="rec_id")`.
  Assert:
  - upgraded matchkey has a phone NE field; `__ne__phone` in `res.em_model.match_weights` with
    negative fired weight; baseline config/model untouched;
  - `res.measurement.vs_labels` present and upgraded pairwise F1 > baseline pairwise F1
    (STRICT — this is the success bar);
  - guard finding present (labels reference) and `max_cluster_size` tuned.
  Mark the test with a generous but bounded runtime expectation (runs two dedupes on a small
  frame; keep the fixture ≤ ~300 rows so the pure-Python NE path stays fast).
- [ ] **Step 2:** Run → FAIL (or, if it unexpectedly PASSES before F3-F5 semantics settle,
  tighten the fixture until baseline demonstrably merges the traps — assert baseline F1 < 1.0
  as a fixture-validity precondition inside the test).
- [ ] **Step 3:** Fix whatever the E2E surfaces (this is the integration shakeout — expect
  fixture tuning, not production rewrites; if production changes ARE needed, keep them within
  the spec's semantics).
- [ ] **Step 4:** CLI help text: extend the `--upgrade` option help to name the four levers.
  Run `pytest tests/test_cli_import_splink_upgrade.py -q` → PASS (stdout byte-compat matters on
  the NON-upgrade path only; help text is fine to change).
- [ ] **Step 5:** Full targeted sweep:
  `pytest tests/test_splink_upgrade_fanout.py tests/test_splink_upgrade_fanout_e2e.py tests/test_splink_upgrade_levers.py tests/test_splink_upgrade_measure.py tests/test_cli_import_splink.py tests/test_cli_import_splink_upgrade.py tests/test_from_splink_api.py -q`
  → all pass. Ruff. Commit `test(splink-upgrade): fan-out lever E2E success bar`.

---

### Task F7: Wild-bench regression check + PR

**Files:** none in-repo (bench assets live in `D:\ER\splink_convert_dogfood`)

- [ ] **Step 1:** Re-run the 3 wild bench pairs through `--upgrade` (see
  `D:\ER\splink_convert_dogfood\bench\run_upgrade_bar.py` — extend/reuse; it compares baseline vs
  upgraded pairwise F1 per pair). Requirement: NO pair's upgraded F1 regresses below its current
  upgraded F1 (0.6328 / 0.7656 / 0.7396). Risk-not-detected no-ops are passes. Record the three
  numbers in the PR body.
- [ ] **Step 2:** If a pair regresses: the lever mis-fired — diagnose (most likely a too-loose
  gate or a bad u_fire estimate), fix within spec semantics, re-run.
- [ ] **Step 3:** Final review pass over the whole branch diff
  (superpowers:subagent-driven-development's final review stage).
- [ ] **Step 4:** Push (auth dance above), open PR titled
  `feat(goldenmatch): fan-out/negative-evidence upgrade lever for Splink migration`, body:
  spec summary + bench numbers + the tripwire-removal note. Arm `gh pr merge --auto` and stop.
- [ ] **Step 5:** After merge: update memory (`project_splink_converter` /
  `project_fs_negative_evidence` open-items) + `D:\Work-Tracking\work-tracker-personal.md`.
