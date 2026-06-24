# Auto-config Probabilistic-Routing Lever Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach auto-config to route probabilistic-shaped datasets (no strong identifier + multiple weak fuzzy fields) to the Fellegi-Sunter path — gated default-off — and give the quality harness a dual-strategy (default vs probabilistic) scorecard column to prove it lifts the right datasets without regressing the others.

**Architecture:** Two phases on one branch. Phase 1 (harness, low-risk): `evaluate_f1` gains a `strategy` arg; `run()` records a parallel `f1_probabilistic` block per ground-truth dataset; `diff.py` floors both; re-bless. Phase 2 (kernel, gated off): a trigger in `_legacy_auto_configure_v0` delegates to the existing `auto_configure_probabilistic_df` when the shape matches and `GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC=1`. The default-flip-to-on is a deferred follow-up, NOT in this plan.

**Tech Stack:** Python, polars, the goldenmatch auto-config kernel (`core/autoconfig.py`, `core/autoconfig_controller.py`), the quality harness (`scripts/autoconfig_quality/`).

**Spec:** `docs/superpowers/specs/2026-06-23-autoconfig-probabilistic-routing-design.md`

---

## Execution model (READ FIRST — box constraint)

Same as the prior harness work: the controller runs all tests **in-session** (not via subagents — this box accumulates zombie Python procs and OOMs); subagent reviewers are **read-only** (ruff / py_compile / read the diff). Branch: `feat/autoconfig-probabilistic-routing` (off `origin/main`, already created). Pinned env for harness runs:
```
PYTHONPATH="D:/show_case/gm-autoconfig-core;D:/show_case/gm-autoconfig-core/packages/python/goldenmatch;D:/show_case/gm-autoconfig-core/packages/python/goldenmatch/scripts" \
GOLDENMATCH_AUTOCONFIG_MEMORY=0 POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 \
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest <target> -q -p no:cacheprovider
```
Kernel tests (`packages/python/goldenmatch/tests/`) run via `uv run pytest <file>` from `packages/python/goldenmatch` (the package is editable-installed). The harness needs `recordlinkage` + `splink` locally for the real datasets (re-add after the post-reformat `uv sync`: `.venv/Scripts/python.exe -m pip install recordlinkage splink`).

---

## File Structure

| File | Responsibility |
| --- | --- |
| `scripts/autoconfig_quality/f1.py` (modify) | `evaluate_f1(..., strategy=)` — `"probabilistic"` forces `auto_configure_probabilistic_df` |
| `scripts/autoconfig_quality/__main__.py` (modify) | record `f1_probabilistic` alongside `f1` for GT datasets |
| `scripts/autoconfig_quality/diff.py` (modify) | floor `f1_probabilistic` like `f1` (shared helper) |
| `scripts/autoconfig_quality/baselines/scorecard.json` (re-bless) | pin both columns |
| `scripts/autoconfig_quality/tests/test_f1.py`, `test_diff.py` (modify) | dual-strategy tests |
| `scripts/autoconfig_quality/README.md` (modify) | document the second strategy column |
| `packages/python/goldenmatch/goldenmatch/core/autoconfig.py` (modify) | `_route_to_probabilistic_enabled()`, `_is_probabilistic_shape()`, the trigger in `_legacy_auto_configure_v0` after `build_matchkeys` (`:3529`) |
| `packages/python/goldenmatch/tests/test_autoconfig_probabilistic_routing.py` (create) | trigger unit + behavioral routing tests |
| `docs-site/goldenmatch/tuning.mdx` (modify) | document `GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC` (docs-staleness gate) |

---

# PHASE 1 — Harness dual-strategy measurement

## Task 1: `evaluate_f1` strategy arg

**Files:** Modify `scripts/autoconfig_quality/f1.py`; Test `scripts/autoconfig_quality/tests/test_f1.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_f1.py`:

```python
def test_evaluate_f1_probabilistic_strategy():
    df, gt = gen_labeled(n_entities=120, seed=7)
    out = evaluate_f1(df, gt, row_cap=None, strategy="probabilistic")
    assert 0.0 <= out["f1"] <= 1.0
    assert set(out) >= {"f1", "precision", "recall", "attribution"}
```

- [ ] **Step 2: Run to verify it fails** → FAIL (`strategy` is not a parameter).

- [ ] **Step 3: Implement** — in `f1.py`, add the `strategy` param and branch the dedupe. Only the dedupe call changes; F1/attribution logic is unchanged:

```python
def evaluate_f1(
    df: pl.DataFrame, gt_pairs: set, row_cap: int | None = 20_000,
    strategy: str = "default",
) -> dict[str, Any]:
    """Full dedupe -> F1/P/R + attribution. strategy='probabilistic' forces the
    Fellegi-Sunter config (auto_configure_probabilistic_df); 'default' uses the
    zero-config dedupe_df path (which reflects the routing lever when enabled)."""
    if row_cap is not None and df.height > row_cap:
        df = df.head(row_cap)
        gt_pairs = {(a, b) for a, b in gt_pairs if a < row_cap and b < row_cap}
    if strategy == "probabilistic":
        from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
        result = goldenmatch.dedupe_df(df, config=auto_configure_probabilistic_df(df))
    else:
        result = goldenmatch.dedupe_df(df)
    ev = evaluate_clusters(result.clusters, gt_pairs).summary()
    # ... (unchanged from here: emitted, _candidate_pairs scale guard, attr_out, return)
```

- [ ] **Step 4: Run the test** (pinned env, needs `recordlinkage`/`splink` only for the real datasets — gen_labeled needs neither) → PASS. Re-run the existing `test_f1.py` tests → still PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/autoconfig_quality/f1.py scripts/autoconfig_quality/tests/test_f1.py
git commit -m "feat(quality): evaluate_f1 strategy arg (default | probabilistic)"
```

---

## Task 2: `run()` records `f1_probabilistic`

**Files:** Modify `scripts/autoconfig_quality/__main__.py`

- [ ] **Step 1:** In `run()`, after the existing `rec["f1"] = evaluate_f1(...)` block (inside `if not fast_only and gt:`), add a parallel probabilistic block with the same try/except shape:

```python
        if not fast_only and gt:
            try:
                rec["f1"] = evaluate_f1(df, gt, row_cap=effective_row_cap(d, row_cap))
            except Exception as e:
                rec["error"] = str(e)
            try:
                rec["f1_probabilistic"] = evaluate_f1(
                    df, gt, row_cap=effective_row_cap(d, row_cap), strategy="probabilistic")
            except Exception as e:
                rec["error_probabilistic"] = str(e)  # informational; floored only when present
```

(The `del loaded, df, gt; gc.collect()` at the end of the loop already frees both runs' intermediates.)

- [ ] **Step 2: Verify by a targeted report** (one small dataset, fast):

```bash
... -m scripts.autoconfig_quality report --datasets anchor_person_match --native 0
```
Expect both an `f1` and an `f1_probabilistic` row in the diff (vs the current baseline, which has no probabilistic block → shows as a new value).

- [ ] **Step 3: Commit**

```bash
git add scripts/autoconfig_quality/__main__.py
git commit -m "feat(quality): record f1_probabilistic strategy per GT dataset"
```

---

## Task 3: `diff.py` floors `f1_probabilistic`

**Files:** Modify `scripts/autoconfig_quality/diff.py`; Test `scripts/autoconfig_quality/tests/test_diff.py`

The real-dataset branch currently floors only `c.get("f1",{}).get("f1")`. Factor the floor check into a helper and call it for both blocks.

- [ ] **Step 1: Write the failing test** — append to `tests/test_diff.py`:

```python
def test_real_f1_probabilistic_floored():
    base = {"datasets": {"hist": {"kind": "real",
        "f1": {"f1": 0.46}, "f1_probabilistic": {"f1": 0.82}}}}
    # probabilistic drop beyond tol -> FAIL
    cur = {"datasets": {"hist": {"kind": "real",
        "f1": {"f1": 0.46}, "f1_probabilistic": {"f1": 0.70}}}}
    _, verdict = diff_scorecards(cur, base, tolerance=0.01)
    assert verdict == "FAIL"
    # both within tol -> PASS
    cur2 = {"datasets": {"hist": {"kind": "real",
        "f1": {"f1": 0.455}, "f1_probabilistic": {"f1": 0.815}}}}
    _, verdict2 = diff_scorecards(cur2, base, tolerance=0.01)
    assert verdict2 == "PASS"
```

- [ ] **Step 2: Run to verify it fails** → FAIL (probabilistic drop not gated; verdict PASS).

- [ ] **Step 3: Implement** — in `diff.py`'s `real` branch, extract the existing f1-floor logic into a local helper and call it for `"f1"` and `"f1_probabilistic"`. The helper reproduces the current rule (FAIL if `cur < base - tol`; the crash/absent sub-cases stay as-is for the primary `f1`; for `f1_probabilistic`, absent-with-baseline-present is WARN "not measured", mirroring the existing fast-only treatment). Keep `f1` as the primary block; `f1_probabilistic` is a second floored block.

- [ ] **Step 4: Run** `test_diff.py` → all PASS (10+ existing + new) + ruff.

- [ ] **Step 5: Commit**

```bash
git add scripts/autoconfig_quality/diff.py scripts/autoconfig_quality/tests/test_diff.py
git commit -m "feat(quality): floor the f1_probabilistic strategy block"
```

---

## Task 4: Re-bless + the dual-strategy evidence

**Files:** Re-bless `scripts/autoconfig_quality/baselines/scorecard.json`; Modify `README.md`

- [ ] **Step 1: Ensure real-dataset deps** — `recordlinkage` + `splink` installed locally (Task env note).

- [ ] **Step 2: Re-bless** (memory-off, native-0, routing flag UNSET so `f1` stays the deterministic default):

```bash
... -m scripts.autoconfig_quality bless --native 0
```

- [ ] **Step 3: Inspect the new baseline** — confirm each GT dataset now has BOTH `f1` (default) and `f1_probabilistic`. Record the det-vs-prob table (this is the evidence artifact). Expect historical_50k `f1`≈0.466 / `f1_probabilistic`≈0.82-0.83; febrl3 + ncvr_synthetic where prob is ≤ default (the no-regression cases). Note the EM ±0.004 wobble: if a dataset's `f1_probabilistic` sits within 0.01 of the blessed value run-to-run, the floor+tolerance absorbs it; if any wobbles more, lower THAT floor by hand a hair below the observed min and note why.

- [ ] **Step 4: Gate green + harness suite**

```bash
... -m scripts.autoconfig_quality gate --native 0           # verdict: PASS
... -m pytest scripts/autoconfig_quality/tests/ -q           # all PASS
```

- [ ] **Step 5: README** — add the "two strategies" column to the corpus docs (default = what dedupe_df decides / reflects the routing lever; probabilistic = forced FS), and the det-vs-prob table as the lever-evidence snapshot.

- [ ] **Step 6: Commit**

```bash
git add scripts/autoconfig_quality/baselines/scorecard.json scripts/autoconfig_quality/README.md
git commit -m "feat(quality): re-bless with dual-strategy (default + probabilistic) floors"
```

---

# PHASE 2 — Kernel routing lever (gated default-off)

## Task 5: Trigger predicate + env helper

**Files:** Modify `packages/python/goldenmatch/goldenmatch/core/autoconfig.py`; Create `packages/python/goldenmatch/tests/test_autoconfig_probabilistic_routing.py`

- [ ] **Step 1: Write the failing test** — `tests/test_autoconfig_probabilistic_routing.py`:

```python
from goldenmatch.core.autoconfig import _is_probabilistic_shape
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.profiling import ColumnProfile  # adjust import to where ColumnProfile lives


def _prof(name, col_type, card=0.5):
    return ColumnProfile(name=name, dtype="Utf8", col_type=col_type,
                         confidence=0.9, null_rate=0.0, cardinality_ratio=card, avg_len=10)


def test_probabilistic_shape_no_identifier_two_fuzzy():
    profiles = [_prof("first_name", "name"), _prof("surname", "name"), _prof("dob", "date")]
    mks = [
        MatchkeyConfig(name="exact_dob", type="exact", fields=[MatchkeyField(field="dob")]),
        MatchkeyConfig(name="fuzzy", type="weighted", threshold=0.8,
                       fields=[MatchkeyField(field="first_name"), MatchkeyField(field="surname")]),
    ]
    assert _is_probabilistic_shape(mks, profiles) is True


def test_identifier_backed_exact_blocks_routing():
    profiles = [_prof("ssn", "identifier", card=0.99), _prof("first_name", "name"), _prof("surname", "name")]
    mks = [
        MatchkeyConfig(name="exact_ssn", type="exact", fields=[MatchkeyField(field="ssn")]),
        MatchkeyConfig(name="fuzzy", type="weighted", threshold=0.8,
                       fields=[MatchkeyField(field="first_name"), MatchkeyField(field="surname")]),
    ]
    assert _is_probabilistic_shape(mks, profiles) is False  # surviving identifier exact matchkey


def test_too_few_fuzzy_fields_no_route():
    profiles = [_prof("first_name", "name")]
    mks = [MatchkeyConfig(name="fuzzy", type="weighted", threshold=0.8,
                          fields=[MatchkeyField(field="first_name")])]
    assert _is_probabilistic_shape(mks, profiles) is False
```

- [ ] **Step 2: Run to verify it fails** (`cd packages/python/goldenmatch && uv run pytest tests/test_autoconfig_probabilistic_routing.py -q`) → FAIL (`_is_probabilistic_shape` undefined). Fix the `ColumnProfile`/`MatchkeyConfig` import paths if the test errors on import (grep for `class ColumnProfile` / `class MatchkeyConfig` to confirm modules).

- [ ] **Step 3: Implement** — in `autoconfig.py` (near the other `_*_enabled` helpers):

```python
def _route_to_probabilistic_enabled() -> bool:
    """Auto-route to Fellegi-Sunter when the dataset is probabilistic-shaped.
    Default OFF (2026-06-23). Enable: GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC=1."""
    return os.environ.get("GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC", "0").lower() in (
        "1", "true", "yes", "on", "enabled",
    )


def _is_probabilistic_shape(matchkeys, profiles) -> bool:
    """Probabilistic shape = no SURVIVING exact matchkey backed by an identifier-typed
    column + >=2 fuzzy (weighted) fields. Keys on the EMITTED matchkeys (not raw
    profiles), so a ceiling-excluded identifier column (e.g. a perfectly-unique
    surrogate id) correctly counts as 'no surviving identifier matchkey'."""
    col_type = {p.name: p.col_type for p in profiles}
    exact_fields = [f.field for mk in matchkeys if mk.type == "exact" for f in mk.fields]
    has_strong_id = any(col_type.get(fld) == "identifier" for fld in exact_fields)
    fuzzy_field_count = sum(len(mk.fields) for mk in matchkeys if mk.type == "weighted")
    return (not has_strong_id) and fuzzy_field_count >= 2
```

- [ ] **Step 4: Run the test** → PASS. ruff/py_compile.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig.py packages/python/goldenmatch/tests/test_autoconfig_probabilistic_routing.py
git commit -m "feat(autoconfig): probabilistic-shape trigger + route-enabled flag"
```

---

## Task 6: Wire the routing into `_legacy_auto_configure_v0`

**Files:** Modify `packages/python/goldenmatch/goldenmatch/core/autoconfig.py`; Test (same file as Task 5)

- [ ] **Step 1: Write the failing behavioral test** — append to `test_autoconfig_probabilistic_routing.py`:

```python
import os
import polars as pl
import goldenmatch
from goldenmatch.core.autoconfig import auto_configure_df


def _bio_df():
    # No identifier column; several fuzzy fields with realistic collisions.
    import random
    rng = random.Random(7)
    first = ["Jon", "Jane", "Bill", "Mary", "Tom", "Sue", "Ed", "Ann"]
    last = ["Smith", "Jones", "Brown", "Lee", "Clark", "Hall"]
    rows = [{"first_name": rng.choice(first), "surname": rng.choice(last),
             "dob": f"19{rng.randint(50,99)}-0{rng.randint(1,9)}-1{rng.randint(0,9)}",
             "city": rng.choice(["Raleigh", "Durham", "Cary"])} for _ in range(200)]
    return pl.DataFrame(rows)


def test_routing_off_is_deterministic(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC", raising=False)
    cfg = auto_configure_df(_bio_df())
    assert all(mk.type != "probabilistic" for mk in cfg.matchkeys)


def test_routing_on_emits_probabilistic(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC", "1")
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    df = _bio_df()
    cfg = auto_configure_df(df)
    assert any(mk.type == "probabilistic" for mk in cfg.matchkeys)
    # and the full default path runs without raising + produces clusters
    r = goldenmatch.dedupe_df(df)
    assert len(r.clusters) >= 1
```

(`auto_configure_df` is the controller entry point the default `dedupe_df` uses; assert at the config level so no heavy run is needed beyond the one small `dedupe_df`.)

- [ ] **Step 2: Run to verify it fails** → `test_routing_on_emits_probabilistic` FAILs (config stays deterministic; routing not wired).

- [ ] **Step 3: Implement** — in `_legacy_auto_configure_v0`, immediately after `matchkeys = build_matchkeys(profiles, df=df, multi_source=multi_source)` (`autoconfig.py:3529`):

```python
    matchkeys = build_matchkeys(profiles, df=df, multi_source=multi_source)

    # Probabilistic routing (gated, default-off): a probabilistic-shaped dataset
    # (no surviving identifier-backed exact matchkey + >=2 fuzzy fields) is better
    # served by the Fellegi-Sunter path. Delegate to auto_configure_probabilistic_df
    # (it builds the diversified FS blocking that lifts recall) and return directly.
    if (not multi_source and _route_to_probabilistic_enabled()
            and _is_probabilistic_shape(matchkeys, profiles)):
        return auto_configure_probabilistic_df(df, llm_provider=llm_provider)
```

- [ ] **Step 4: Run the test.** If `test_routing_on_emits_probabilistic` PASSES — good (the controller commits the probabilistic config from `_initial_config` unchanged; its refit rules are weighted-specific and don't alter the matchkey type). If it FAILS because the controller loop mangles or rejects the probabilistic config (e.g. raises ControllerNotConfidentError or refits it away), do Step 4b.

- [ ] **Step 4b (contingency only if Step 4 shows the controller mishandles the prob config):** add a short-circuit in `autoconfig_controller.py` `run()` — after `_initial_config(...)` returns, if `any(mk.type == "probabilistic" for mk in config_v0.matchkeys)`, commit it directly and skip the iterative refit loop (use the existing early-return/commit pattern). Add a test asserting no refit occurs on a probabilistic initial config.

- [ ] **Step 5: Run** the full routing test file → PASS. ruff/py_compile.

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig.py packages/python/goldenmatch/tests/test_autoconfig_probabilistic_routing.py
git commit -m "feat(autoconfig): route probabilistic-shaped data to Fellegi-Sunter (gated off)"
```

---

## Task 7: Docs + flag-on validation (the proof)

**Files:** Modify `docs-site/goldenmatch/tuning.mdx`; no baseline change (lever ships off)

- [ ] **Step 1: Document the flag** — add `GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC` (default `0`) to `tuning.mdx`: what it does, the trigger shape, that it's a behavior change shipping default-off pending the corpus proof. (Touching tuning.mdx satisfies the docs-staleness CI gate that fires on new `GOLDENMATCH_*` tokens.)

- [ ] **Step 2: Validation run (flag ON, NOT a bless)** — prove the lever fires + lifts the right dataset, leaving the committed baseline (flag-off) untouched:

```bash
GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC=1 ... \
  -m scripts.autoconfig_quality report --native 0
```
Expect the `f1` (default) row for historical_50k to JUMP from ~0.466 toward its `f1_probabilistic` ~0.82 (routing fired), while datasets with a strong identifier / where `f1_probabilistic < f1` show NO `f1` change (not routed). Capture this table for the PR — it's the evidence the trigger routes the right datasets and only those.

- [ ] **Step 3:** If Step 2 shows a misroute (a dataset where `f1` dropped because it was wrongly routed and `f1_probabilistic < f1`), tighten `_is_probabilistic_shape` (e.g. switch the identifier test to a cardinality threshold, or raise the fuzzy-field floor) and re-run until only the right datasets route. Record the final trigger rationale.

- [ ] **Step 4: Commit**

```bash
git add docs-site/goldenmatch/tuning.mdx
git commit -m "docs(autoconfig): document GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC"
```

---

## Task 8: Final review + PR

- [ ] **Step 1:** Read-only final reviewer over `main..HEAD` (box constraint: no execution). Address Critical/Important findings in-session, re-review.
- [ ] **Step 2:** Assemble the PR: the dual-strategy baseline table (flag-off) + the flag-on validation table (historical_50k jumps, others stable). State clearly: lever ships **default-off**; the default-flip is a deferred follow-up gated on a broader-than-historical_50k regression sweep.
- [ ] **Step 3:** Push (benzsevern account: `unset GH_TOKEN; gh auth switch --user benzsevern`; create PR via `gh api ... pulls` if GraphQL is rate-limited). Arm `gh pr merge <N> --repo benseverndev-oss/goldenmatch --auto` (merge queue sets strategy — no `--squash`). Then STOP.

---

## Verification & sequencing notes

- **Phases are separable.** Phase 1 (Tasks 1-4) is harness-only and independently valuable (the det-vs-prob view); Phase 2 (Tasks 5-7) is the gated kernel change. Both land on one branch/PR since Phase 2 is default-off (safe) and its validation uses Phase 1's column.
- **No default behavior change ships here.** With the flag off, `dedupe_df` output is byte-identical to today; the only committed change to the default path is dormant code behind an env check. The harness `f1` (default) floors are unchanged from the corpus baseline; `f1_probabilistic` floors are new.
- **The trigger is empirically tuned (Task 7 Step 3), not assumed.** `col_type=="identifier"` is the v1 signal; the flag-on corpus run is the validator and the regression guard.
- **EM non-determinism:** ±0.004 wobble, recall stable; the 0.01 floor tolerance absorbs it; bless conservatively if any dataset wobbles wider.
- **YAGNI:** two strategies only; no EM-seed/model_path work; the default-flip and the broader regression sweep are a deferred follow-up, explicitly out of scope.
```
