# Auto-config lever coverage: probabilistic + qgram + ANN (#491) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make three unreachable auto-config levers selectable from the auto surface — the `probabilistic` matchkey type (controller heuristic + optimizer candidate), the `qgram` scorer (heuristic + optimizer), and `ann` blocking (heuristic gate).

**Architecture:** Three independent levers. Lever A (probabilistic) reuses the existing `MatchkeyTypeSwap` edit in the optimizer (A1) and adds a conservative, quality-gated controller refit rule (A2, ship-or-defer). Lever B (qgram) adds a short-code shape refinement in `build_matchkeys` (B1) + the optimizer scorer family (B2). Lever C (ann) adds a strictly-gated branch in `build_blocking`.

**Tech Stack:** Python 3.12, polars, pytest. Files in `packages/python/goldenmatch/goldenmatch/core/`: `autoconfig.py`, `autoconfig_rules.py`, `config_optimizer.py`, `config_edits.py`, `config/schemas.py`.

**Spec:** `docs/superpowers/specs/2026-06-04-autoconfig-491-lever-coverage-design.md`

**Precondition — branch from latest `main`** (has #715/#720/#723, and #678/#458 once merged). Create `feat/491-lever-coverage` off `origin/main`.

**Run environment:** prefix every local python/pytest with `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8` and use the worktree `.venv/Scripts/python.exe`. `uv` lives at `C:/Users/bsevern/AppData/Local/Programs/Python/Python312/Scripts/uv.exe` (not on PATH — use the absolute path for `uv sync`). Run ONLY targeted test files locally (full suite OOMs; CI runs it). Kill zombie python: `powershell.exe -Command "Get-Process python | Stop-Process -Force"`. pyright reporting "polars could not be resolved" is env noise — judge NEW errors vs baseline.

---

## File Structure
- **Modify** `goldenmatch/core/autoconfig.py`: `build_matchkeys` scorer-selection (B1 short-code → qgram, via a refinement near the `_refdata_refine_matchkey_field` hook ~592-609); `build_blocking` (C: new `ann` branch).
- **Modify** `goldenmatch/core/config_optimizer.py`: `CoordinateDescentProposer` scorer family (~334, add `qgram` for B2) + a new `mktype` family wiring `MatchkeyTypeSwap` (A1).
- **Modify** `goldenmatch/core/autoconfig_rules.py`: new `rule_select_probabilistic_matchkey` + `DEFAULT_RULES` (A2).
- **Modify** `goldenmatch/config/schemas.py` (NOT under `core/`): `VALID_SCORERS` (~21-25, add `qgram` — Task 0).
- **Read-only ref** `goldenmatch/core/config_edits.py:167` (`MatchkeyTypeSwap`), `goldenmatch/config/schemas.py:~380` (`BlockingConfig.ann_column/ann_model/ann_top_k`, `strategy="ann"` Literal ~366).
- **Create** `tests/test_autoconfig_491_levers.py` (all lever unit + reachability tests).

---

## Task 0: B0 — implement the qgram scorer (PREREQUISITE for Task 1/2)

**Files:** Modify `goldenmatch/core/scorer.py` (single dispatch ~89, matrix dispatch ~419, + two new helpers); Modify `goldenmatch/config/schemas.py` (`VALID_SCORERS` ~21-25); Test `tests/test_autoconfig_491_levers.py`

**Why:** `qgram` is not a scorer today (it's a lossy `qgram:N` transform; dice/jaccard are bloom-only). Must add a real char-n-gram similarity scorer before Tasks 1/2 can route to it.

- [ ] **Step 1: failing test**
```python
def test_qgram_scorer_similarity():
    from goldenmatch.core.scorer import score_pair  # or the single-score entry; READ scorer.py:80-95 for the real entry
    # identical -> 1.0; disjoint -> 0.0; transposition-ish -> high but < 1
    assert score_pair("ABC123", "ABC123", scorer="qgram") == 1.0
    assert score_pair("ABC123", "XYZ789", scorer="qgram") < 0.2
    assert 0.4 < score_pair("ABC123", "ABC132", scorer="qgram") < 1.0
```
(READ `scorer.py:80-95` to find the actual single-score function name + signature; adapt the import/call. The `_dice_score_single`/`_jaccard_score_single` pair at ~526-536 is the structural template — but those read bloom hex; yours reads raw strings.)
- [ ] **Step 2:** run, confirm FAIL (qgram not a valid scorer → ValueError, or unknown scorer).
- [ ] **Step 3: implement**
  - Add `"qgram"` to `VALID_SCORERS` in `goldenmatch/config/schemas.py` (~21-25).
  - Add `_qgram_score_single(val_a, val_b, n=3)`: pad each with `##...##`, build the FULL set of length-n substrings (NOT the transform's `[:5]` truncation), return `len(A & B)/len(A | B)` (0.0 if both empty / union empty). Wire into the single dispatch (`scorer.py` ~89, beside dice/jaccard).
  - Add `_qgram_score_matrix(values)`: NxN, same metric (can be a straightforward loop or vectorized set ops). Wire into the matrix dispatch (~419).
  - Confirm no native/Rust dispatch hijacks `qgram` (the note at ~317-319 shows dice/jaccard matrix stays Python; keep qgram Python too).
- [ ] **Step 4:** run, pass.
- [ ] **Step 5: commit** `feat(scorer): add qgram char-n-gram similarity scorer (#491)`.

---

## Task 1: B1 — route short-code columns to qgram (heuristic)

**Files:** Modify `goldenmatch/core/autoconfig.py` (`build_matchkeys` scorer path); Test `tests/test_autoconfig_491_levers.py`

READ `build_matchkeys` (~550-700), `_SCORER_MAP` (~503), and the `_refdata_refine_matchkey_field` refinement hook (~592-609) first. The short-code refinement slots in alongside that hook: after col_type/scorer is chosen, if the column matches a short-code shape, override the matchkey field's scorer to `qgram` (now a valid scorer from Task 0).

- [ ] **Step 1: failing test** — construct `ColumnProfile`s for a short-code column (e.g. `sku`, `col_type="string"` or `"identifier"`, `avg_len≈6`, `cardinality_ratio≈0.7`, alphanumeric `sample_values` like `"A1B2C3"`) and a name column. Assert `build_matchkeys` produces a matchkey field with `scorer="qgram"` for `sku` and NOT for the name column.
```python
def test_short_code_column_gets_qgram():
    profiles = [
        ColumnProfile("sku","Utf8","string",0.9, sample_values=["A1B2C3","X9Y8Z7","Q2W3E4"],
                      null_rate=0.0, cardinality_ratio=0.7, avg_len=6.0),
        ColumnProfile("first_name","Utf8","name",0.9, sample_values=["james","mary"],
                      null_rate=0.0, cardinality_ratio=0.02, avg_len=5.0),
    ]
    mks = build_matchkeys(profiles, df=_df_with(["sku","first_name"]))
    scorers = {f.field: f.scorer for mk in mks for f in mk.fields}
    assert scorers.get("sku") == "qgram"
    assert scorers.get("first_name") != "qgram"
```
- [ ] **Step 2:** run, confirm FAIL (qgram never emitted today).
- [ ] **Step 3: implement** a `_is_short_code(p: ColumnProfile) -> bool` helper (avg_len in ~3..12, cardinality_ratio >= a threshold e.g. 0.3, col_type in {"string","identifier"} and NOT name/email/phone/zip/date, sample_values look alphanumeric/code-like — check a mix of letters+digits or uniform short length). In `build_matchkeys`, after the scorer is resolved for a fuzzy field, if `_is_short_code(p)` and scorer is a generic string scorer (token_sort/ensemble), set scorer to `"qgram"` (valid as of Task 0). Add a comment tagging `#491`.
- [ ] **Step 4:** run, pass. Re-run a couple of existing `build_matchkeys` tests (`tests/test_refdata_autoconfig.py`) to confirm no regression on name/address columns.
- [ ] **Step 5: commit** `feat(autoconfig): emit qgram for short-code columns (#491)`.

---

## Task 2: B2 — qgram in the optimizer scorer family

**Files:** Modify `goldenmatch/core/config_optimizer.py` (~334); Test `tests/test_autoconfig_491_levers.py`

- [ ] **Step 1: failing test** — assert `qgram` is in `CoordinateDescentProposer`'s scorer family / appears as a candidate scorer. Inspect the family constant directly:
```python
def test_optimizer_scorer_family_includes_qgram():
    from goldenmatch.core.config_optimizer import CoordinateDescentProposer
    # the scorer family is the `scorers` tuple (instance attr `_scorers`, default
    # literal at config_optimizer.py:328-335) -- READ to confirm and assert against
    # the real symbol (NOT `_SCORER_FAMILY`, which does not exist).
    assert "qgram" in CoordinateDescentProposer()._scorers
```
(READ config_optimizer.py:328-376 to confirm the exact attribute/local name; adapt the test + the constructor call to the real signature.)
- [ ] **Step 2:** run, confirm FAIL.
- [ ] **Step 3: implement** — add `"qgram"` to the scorer family list (~334), with the same `#491` comment style as the levenshtein/soundex entries.
- [ ] **Step 4:** run, pass.
- [ ] **Step 5: commit** `feat(optimizer): add qgram to scorer candidate family (#491)`.

---

## Task 3: A1 — probabilistic candidate in the deterministic optimizer

**Files:** Modify `goldenmatch/core/config_optimizer.py` (`CoordinateDescentProposer` `_FAMILIES`/`_edits`); ref `config_edits.py:167` (`MatchkeyTypeSwap`); Test `tests/test_autoconfig_491_levers.py`

READ `config_optimizer.py:307-446` (`CoordinateDescentProposer`, its `_FAMILIES`, `_edits`, `propose`) and `config_edits.py:167` (`MatchkeyTypeSwap`, `_PERTURBABLE_TYPES = ("weighted","probabilistic")`, its `.apply`). `MatchkeyTypeSwap` is already LLM-deserialized but not imported into the optimizer.

- [ ] **Step 1: failing test** — given a `SearchState` whose config has a `weighted` matchkey, assert `CoordinateDescentProposer.propose` yields at least one candidate whose matchkey type is `probabilistic`.
```python
def test_optimizer_proposes_probabilistic_candidate():
    # build a minimal SearchState with a weighted matchkey (read SearchState shape first)
    cands = CoordinateDescentProposer(...).propose(state)
    types = {mk.type for _label, cfg in cands for mk in cfg.get_matchkeys()}
    assert "probabilistic" in types
```
(READ `SearchState` + how existing families construct candidates to build the fixture correctly.)
- [ ] **Step 2:** run, confirm FAIL.
- [ ] **Step 3: implement** — import `MatchkeyTypeSwap`; add a `mktype` family to `CoordinateDescentProposer` that, for each `weighted` matchkey in the state, emits a `MatchkeyTypeSwap(...weighted→probabilistic)` candidate (and optionally the reverse for a probabilistic matchkey). Apply via the same `edit.apply(base)` path the other families use. Label candidates so the report attributes the move (e.g. `"mktype:probabilistic"`).
- [ ] **Step 4:** run, pass.
- [ ] **Step 5: commit** `feat(optimizer): propose weighted↔probabilistic matchkey-type swaps (#491)`.

---

## Task 4: A2 — conservative controller rule (ship-or-defer)

**Files:** Modify `goldenmatch/core/autoconfig_rules.py` (new rule + `DEFAULT_RULES` ~1086); Test `tests/test_autoconfig_491_levers.py`

READ an existing rule (e.g. `rule_unimodal_scoring` ~240 or `rule_recall_gap_suspected` ~656) for the exact signature `(profile, current, history) -> proposal | None` and how a rule edits matchkeys + returns its proposal/decision. Read `ComplexityProfile.scoring` for `dip_statistic` / `mass_above_threshold`.

- [ ] **Step 1: failing tests** —
  - FIRES: profile with a weighted matchkey of ≥3 graded fuzzy fields + recall-limited scoring (low `dip_statistic`/`mass_above_threshold`) + no exact-anchor matchkey → rule returns a proposal converting the weighted matchkey to `probabilistic`.
  - DOES NOT FIRE: (a) a config with an exact-anchor matchkey present (DQbench-like), (b) a 2-field weighted matchkey (below the ≥3 threshold), (c) healthy scoring.
```python
def test_rule_selects_probabilistic_on_target_shape(): ...
def test_rule_skips_when_exact_anchor_present(): ...
def test_rule_skips_small_weighted_or_healthy(): ...
```
- [ ] **Step 2:** run, confirm FAIL (no such rule).
- [ ] **Step 3: implement** `rule_select_probabilistic_matchkey(profile, current, history) -> tuple[GoldenMatchConfig, PolicyDecision] | None` (READ an existing rule for the exact 2-tuple return + `PolicyDecision` shape): guard on the three trigger conditions (≥3 graded fuzzy fields in a weighted mk; recall-limited scoring via `profile.scoring.dip_statistic` / `mass_above_threshold`; no exact matchkey in `current`); when all hold, return `(new_config, PolicyDecision(...))` swapping that weighted matchkey to `type="probabilistic"` (m/u train at dedupe time). **INSERT the rule into `DEFAULT_RULES` BEFORE `rule_recall_gap_suspected`** — do NOT append: `test_autoconfig_policy.py:1182` asserts `idx_recall_gap == len(DEFAULT_RULES) - 2` ("recall_gap second-to-last, sparse_match_expand last"), which a tail append breaks. Tag `#491`.
  - **Bump ALL SIX `len(DEFAULT_RULES) == 15` assertions to `== 16`** in `tests/test_autoconfig_policy.py` (lines ~439, 622, 838, 1162, 1303, 1551 — grep `len(DEFAULT_RULES)` to find them all), and confirm the positional invariant at ~1182 still holds after the insert.
- [ ] **Step 4:** run the new tests + `tests/test_autoconfig_policy.py` + `tests/test_autoconfig_rules.py`, all pass.
- [ ] **Step 5: commit** `feat(autoconfig): rule_select_probabilistic_matchkey (conservative, #491)`.

---

## Task 5: C — ANN blocking branch (strictly gated)

**Files:** Modify `goldenmatch/core/autoconfig.py` (`build_blocking` ~1309); ref `config/schemas.py:362` (`BlockingConfig.ann_column/ann_model/ann_top_k`); Test `tests/test_autoconfig_491_levers.py`

READ `build_blocking` (signature takes `n_rows_full`), the embedding-column/scorer detection already used for embedding scorers, and `BlockingConfig` ann_* fields. `ANNBlocker` raises if `ann_column` unset (`blocker.py:658`).

- [ ] **Step 1: failing tests** —
  - EMITS ann: profiles include an embedding-bearing column/scorer AND `n_rows_full >= ANN_MIN_ROWS` → `build_blocking` returns `strategy="ann"` with `ann_column` set.
  - NEVER ann without embeddings: profiles with no embedding column → strategy is the normal one, never `"ann"` (the safety invariant).
  - below scale: embeddings present but `n_rows_full < ANN_MIN_ROWS` → not ann.
- [ ] **Step 2:** run, confirm FAIL.
- [ ] **Step 3: implement** — add an early branch in `build_blocking`: detect embedding presence (an embedding/record_embedding matchkey field, or an embedding-designated column — reuse the existing detection); define `ANN_MIN_ROWS` (default ~100_000, env-overridable `GOLDENMATCH_ANN_MIN_ROWS`); when both hold, return `BlockingConfig(strategy="ann", ann_column=<the embedding column>, ann_model=<default>, ann_top_k=<default>, ...)`. The gate MUST be conjunctive — no embeddings ⇒ never ann. Tag `#491`.
- [ ] **Step 4:** run, pass. Re-run `tests/test_autoconfig.py` blocking tests for no regression.
- [ ] **Step 5: commit** `feat(autoconfig): auto-select ANN blocking with embeddings at scale (#491)`.

---

## Task 6: Reachability tests (the #491 acceptance)

**Files:** Test `tests/test_autoconfig_491_levers.py`

- [ ] **Step 1:** add explicit reachability assertions tying back to #491's acceptance: qgram reachable via `build_matchkeys` (Task 1 covers); probabilistic reachable via BOTH the controller rule (Task 4) AND the optimizer candidate set (Task 3); ann reachable via `build_blocking` (Task 5). A small grouped test module-docstring referencing #491.
- [ ] **Step 2:** run the whole `tests/test_autoconfig_491_levers.py` — all green.
- [ ] **Step 3: commit** `test(autoconfig): #491 lever-reachability assertions`.

---

## Task 7: Quality gate + ship-or-defer decision (HARD GATE)

**Files:** none (validation) — may revert Task 4.

A2 (and B1) change default zero-config selection. This is the central risk.

- [ ] **Step 1:** run the in-house #528 quality gate (CI lane on the PR) — confirm green.
- [ ] **Step 2:** run DQbench T1/T2/T3 (local `~/.dqbench`, system Python312 dqbench CLI per `project_issue_489_zerolabel_gate`) + the NCVR + Febrl3 benchmarks. Capture before/after.
- [ ] **Step 3: DECISION** — if NCVR/Febrl3/DQbench regress vs baseline: **revert Task 4** (`rule_select_probabilistic_matchkey` + its `DEFAULT_RULES` entry + the `len(DEFAULT_RULES)` assert), leaving probabilistic reachable via the optimizer only (A1) — still satisfies #491. Record the numbers + the decision in the PR. If no regression: keep A2.

---

## Task 8: PR + CI + land

- [ ] **Step 1:** ruff clean + pyright (no NEW errors) on all changed files.
- [ ] **Step 2:** push (`benzsevern` auth dance), open PR to `main` linking #491; paste reachability test results + Task 7 benchmark numbers + the A2 keep/defer decision.
- [ ] **Step 3:** wait `python (goldenmatch)` + `ci-required` green; merge `--squash --delete-branch` (update-branch if behind).
- [ ] **Step 4:** comment on #491 mapping each lever to its delivery (qgram heuristic+optimizer, probabilistic controller-rule[kept/deferred]+optimizer, ann gate); close #491 if all acceptance met (or re-scope to any deferred piece).
