# Precision-Anchor Threshold Raise (#1319 PR2b) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A controller rule that raises a name-only weighted matchkey's threshold to 0.9 when the
measured #1207 over-merge shape is present, closing #1319 (and, with the bar held, #1207).

**Spec:** `docs/superpowers/specs/2026-07-15-precision-anchor-threshold-raise-design.md` — READ
FIRST. It pins the five trigger conditions (incl. the concrete mechanisms), the single-shot 0.9
remedy, the copy-on-write template, and the registration position.

**Architecture:** One new `rule_*` function in `core/autoconfig_rules.py` + registration; one new
unit-test file; one controller-integration test; a scratchpad success-bar re-measure.

**Tech Stack:** Python 3.12, pytest.

---

## Environment / repo mechanics

- **PREREQUISITE:** PR #1782 (bucket tf_freqs fix) must be MERGED before the worktree is cut —
  the success bar depends on it. Check `gh pr view 1782 --json state` first; if still open, wait
  for the queue (do NOT stack).
- NEW worktree `D:\show_case\gm-pr2b`, branch `feat/1319-precision-anchor-raise` off
  freshly-fetched `origin/main`. **NEVER `git stash`.**
- Tests via main venv + worktree PYTHONPATH (Git Bash):
  `cd /d/show_case/gm-pr2b/packages/python/goldenmatch && PYTHONPATH="D:/show_case/gm-pr2b/packages/python/goldenmatch" POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENMATCH_NATIVE=0 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest <tests> -q`
- Ruff before commits. `docs/superpowers/` gitignored → `git add -f`. Commit trailers:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01R8MSaGwsjdxzf6Z7Bt3BXs`
- Push/PR: the standard benzsevern token dance; arm `gh pr merge --auto`; STOP.

**Key code (verified on origin/main):**
- `packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py`:
  - The COPY template: `rule_matchkey_demote_high_cardinality_field` (line 1106) — 3-arg
    signature, iterates `current.matchkeys`, shallow `mk.model_copy(update={...})`, rebuilds the
    matchkeys list via identity (`new_mk if m is mk else m`), `current.model_copy(update=...)`,
    returns `(new_cfg, PolicyDecision(rule_name=..., rationale=..., config_diff=...))`.
  - The CTX template: `rule_sparse_match_expand` (line 1067) — 4-arg signature with
    `ctx: IndicatorContext | None = None`, `if ctx is None: return None` (line 1078).
  - `DEFAULT_RULES` (line 1245): ordered list with per-entry comments; insert the new rule
    directly BEFORE `rule_sparse_match_expand` (currently last), with a comment in the list's
    voice (e.g. `# 17 NEW #1319: raise name-only weighted threshold on over-merge shape (before sparse-expand's loosen)`).
  - Module constants convention: `_DEMOTE_CARD_THRESHOLD`-style names near the top of the rule's
    section.
- `ColumnPrior` (`core/complexity_profile.py:44`): `identity_score` attr;
  `ctx.column_priors: dict[str, ColumnPrior]`.
- `profile.scoring.mass_above_threshold` (`complexity_profile.py:368`).
- Matchkey shapes: `mk.type` ("weighted"/"exact"), `mk.fields` (`MatchkeyField.field`/`.scorer`/
  `.tf_freqs`), `mk.threshold`.
- The controller-integration + success-bar fixture: the #1319 harness at
  `C:/Users/bsevern/AppData/Local/Temp/claude/D--show-case-goldenmatch/2af6f77e-d6b2-41c0-ad88-08d7fd3f306e/scratchpad/1319/measure_1319.py`
  (its `build_fixture()`-equivalent builds the 2600-row common-name/strong-email frame; READ it
  to lift the fixture shape for the in-repo integration test at a smaller row count).
  Post-#1782 baseline on that harness: bucket ON P 0.0305, strangers 15,058, mass 1.0.
- Existing controller-run test patterns: `tests/test_autoconfig_regressions.py` (`_person_df`,
  rerank-off pattern, reading `result.postflight_report.controller_history`).

## File structure

- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py` (the rule +
  constants + DEFAULT_RULES entry)
- Create: `packages/python/goldenmatch/tests/test_precision_anchor_1319.py` (trigger matrix +
  controller integration)

---

### Task P0: Verify prerequisite + worktree

- [ ] **Step 1:** `unset GH_TOKEN; GH_TOKEN=$(gh auth token --user benzsevern) gh pr view 1782 --repo benseverndev-oss/goldenmatch --json state -q .state` → must print MERGED. If OPEN, STOP and wait (report back).
- [ ] **Step 2:** `git fetch origin main -q && git worktree add /d/show_case/gm-pr2b -b feat/1319-precision-anchor-raise origin/main`; confirm `git -C /d/show_case/gm-pr2b log --oneline -3` shows the #1782 merge.
- [ ] **Step 3:** Copy spec+plan into the worktree docs/superpowers/{specs,plans}; `git add -f`; commit `docs: spec + plan for the #1319 precision-anchor threshold raise` (+ trailers).

### Task P1: The rule + trigger-matrix unit tests (TDD)

**Files:** Modify `core/autoconfig_rules.py`; Create `tests/test_precision_anchor_1319.py`.

- [ ] **Step 1: Failing tests** — the trigger matrix, calling the rule DIRECTLY with hand-built
  `ComplexityProfile`/config/`RunHistory`/ctx fixtures (mirror how other rule unit tests build
  them — grep `rule_sparse_match_expand` or `rule_matchkey_demote` in tests/ for the fixture
  idiom; a faked ctx whose `column_priors` is a plain dict of stub objects with
  `identity_score` attrs is fine if IndicatorContext is heavy to construct — read
  `IndicatorContext` first and prefer the real thing when cheap):
  - all five conditions satisfied → returns `(new_cfg, decision)`; new_cfg's weighted matchkey
    threshold == 0.9; INPUT config unmutated (its threshold still 0.8); decision.rule_name set;
    rationale mentions the shape.
  - mass 0.94 → None. Weighted mk containing one non-name scorer (e.g. `jaro_winkler` address
    field, the NCVR shape) → None. No exact matchkey with identity_score >= 0.75 (either no
    exact mk, or its field's prior below 0.75, or field missing from column_priors) → None.
    No `tf_freqs` on any name field → None. Exactly ONE of two name fields carrying the table →
    FIRES (the measured fixture shape: only last_name carries tf_freqs; the ANY-field
    formulation is load-bearing). threshold already 0.9 → None (convergence).
    `ctx is None` → None.
  - Registration: `DEFAULT_RULES.index(rule_precision_anchor_threshold_raise) == DEFAULT_RULES.index(rule_sparse_match_expand) - 1`.
- [ ] **Step 2:** Run → FAIL (rule doesn't exist).
- [ ] **Step 3: Implement** in autoconfig_rules.py (section constants + the rule; mirror the
  demote rule's structure and docstring voice):
  ```python
  _ANCHOR_NAME_SCORERS = {"name_freq_weighted_jw", "given_name_aliased_jw"}
  _ANCHOR_IDENTITY_MIN = 0.75
  _ANCHOR_MASS_MIN = 0.95
  _ANCHOR_RAISED_THRESHOLD = 0.9
  ```
  Rule skeleton: 4-arg signature (ctx optional, None → return None); find the first weighted
  matchkey with non-empty fields, ALL scorers in `_ANCHOR_NAME_SCORERS`, threshold not None and
  `< _ANCHOR_RAISED_THRESHOLD`, and ANY field with truthy `tf_freqs`; require
  `profile.scoring.mass_above_threshold >= _ANCHOR_MASS_MIN`; require any exact matchkey with a
  field whose `ctx.column_priors.get(field)` has `identity_score >= _ANCHOR_IDENTITY_MIN`;
  remedy via the demote rule's shallow model_copy pattern setting
  `{"threshold": _ANCHOR_RAISED_THRESHOLD}`; PolicyDecision rationale citing the measured
  evidence shape (mass, the name-only fields, the anchor field) and #1319. Docstring: the
  measurement numbers (P 0.009 pre-#1782 / 0.0305 post-#1782 → 0.987 raised to 0.9 on the
  crafted fixture — state BOTH baselines), why the trigger is the config shape not mass alone
  (healthy NCVR reads mass 1.0), why tf_freqs is required (identical names without the table
  clear any threshold), why single-fire converges, the parked-B1 history (designed shape never
  emitted by the controller — this rule fires on what the controller actually commits), AND the
  policy's implicit gate: HeuristicRefitPolicy.decide() returns None on an overall-GREEN profile
  (autoconfig_policy.py:78), so this rule structurally cannot fire on healthy commits — the
  crafted fixture measures overall_health YELLOW, which is why rules run there. Register in DEFAULT_RULES before sparse_match_expand.
- [ ] **Step 4:** Matrix passes; `pytest tests/test_autoconfig_rules.py -q` (or wherever
  existing rule tests live — locate first) still green; ruff clean.
- [ ] **Step 5:** Commit `feat(autoconfig): precision-anchor threshold raise on the #1207 over-merge shape (#1319)` (+ trailers).

### Task P2: Through-the-real-controller integration test

**Files:** extend `tests/test_precision_anchor_1319.py`.

- [ ] **Step 1: Failing test** `test_rule_fires_through_real_controller`: build the #1319
  fixture shape IN-REPO at reduced scale (~400-600 rows: a common-name pool with unique strong
  emails + planted dups — lift the generator logic from the scratchpad harness, keep it
  deterministic and fast; heed the repo fixture gotchas: emails unique per person so the email
  column profiles as an identity anchor). Run `auto_configure_df(df)` (with
  `GOLDENMATCH_AUTOCONFIG_MEMORY=0` via monkeypatch env) → assert the committed config's
  weighted matchkey threshold == 0.9 AND the rule's decision appears in
  `_LAST_CONTROLLER_RUN`/`controller_history.decisions` (rule_name match). Also assert the
  weighted mk's fields all carry name scorers (fixture-validity guard: if auto-config stops
  emitting the shape, the test must say so rather than vacuously pass).
  NOTE the parked-B1 lesson in the test docstring: this test exists because the previous rule
  passed unit fixtures and could never fire on real controller output.
- [ ] **Step 2:** FAIL (threshold stays 0.8). **Step 3:** No production change expected beyond
  P1 — if the rule does NOT fire through the controller, DIAGNOSE (which condition fails on the
  real profile/ctx?) and fix the RULE within spec semantics; report prominently if a spec
  condition proves unfireable on real output (that's the B1 failure mode recurring — STOP and
  escalate rather than loosening silently).
- [ ] **Step 4:** Green + adjacent controller tests (`tests/test_autoconfig_regressions.py -q`)
  still green. **Step 5:** Commit `test(autoconfig): precision-anchor fires through the real controller` (+ trailers).

### Task P3: Success bar + PR + close-out

- [ ] **Step 1:** Re-run the #1319 Leg-A harness against THIS worktree (PYTHONPATH swap), flag
  default (on): expect precision ~0.99, recall 1.0, threshold 0.9 in the committed config.
  Record exact numbers. Run the NCVR control (`measure_1319.py b on`) TWICE — once with
  PYTHONPATH at current main (gm-train330 after fetch) and once at this worktree — and compare
  the committed configs (thresholds identical; the rule did not fire). RE-DERIVE the main-side
  run rather than trusting the stored `leg_b_on.json` (it predates the #1782 merge).
- [ ] **Step 2:** Targeted sweep: the new test file + `tests/test_autoconfig_regressions.py` +
  the rules test file + `tests/test_autoconfig_blocking_union_1207.py` +
  `tests/test_tf_name_weighting_1207.py` → green.
- [ ] **Step 3:** Push; PR `feat(goldenmatch): precision-anchor threshold raise -- closes the #1207 over-merge (#1319)`
  with the before/after numbers (P 0.009 → measured, R 1.0, NCVR unaffected), `Closes #1319`
  and `Closes #1207` (the umbrella's observation 1 lives on in #1316/#1317 — say so in the
  body). Arm auto-merge, STOP.
- [ ] **Step 4:** After merge: post the close-out numbers as a final comment on #1319 and #1207
  (they auto-close via the PR); delete the parked branch `feat/1207-pr2b-precision-anchor`
  (local only — `git branch -D`); update memory (`project_1207_autoconfig_blocking_union`) +
  work tracker.
