# Auto-config Identifier Pincer (#715) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let high-cardinality identifier columns (npi/email/phone) back exact matchkeys in zero-config auto-config, so healthcare-provider-shape data stops collapsing to a fuzzy-only, mega-block, over-merging config.

**Architecture:** One root cause (the exact-matchkey cost model in `build_matchkeys`). Replace the blanket row-count "Guard 1" with a cardinality band `0.5 <= card < 1.0`, and stop skipping `col_type="identifier"` outright. Blocking already self-corrects once the config is healthy (verification only). Add a regression assertion, docs, and a hard DQbench/quality-gate pre-merge check.

**Tech Stack:** Python 3.12, polars, pytest. Module: `packages/python/goldenmatch/goldenmatch/core/autoconfig.py`. Tests: `packages/python/goldenmatch/tests/`.

**Spec:** `docs/superpowers/specs/2026-06-03-autoconfig-identifier-pincer-design.md`

**Precondition — branch from latest `main`.** PR #718 (merge commit `b2a5914`) already landed BOTH `packages/python/goldenmatch/scripts/repro_issue_715.py` (the synthetic generator + `make_healthcare_df`) and `.github/workflows/repro-issue-715.yml` (the `workflow_dispatch` harness whose "Run #715 repro" step already tees stdout to `repro715.log`). Create the implementation branch off current `main` so both exist. Do NOT branch from any in-flight feature branch (e.g. `chore/sail-*`), which predates the #718 merge and will be missing the workflow.

**Run environment note:** the local Windows box hangs on polars' WMI CPU check and chokes on Unicode console output. For ANY local python run in this plan, prefix with `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8` and run via `.venv/Scripts/python.exe`. Per repo policy, run the FULL pytest suite in CI, not locally; targeted single-file `pytest` runs are fine locally. Kill zombie python with `powershell.exe -Command "Get-Process python | Stop-Process -Force"` if the box starves.

---

## File Structure

- **Modify:** `goldenmatch/core/autoconfig.py`
  - `build_matchkeys` (~`:550-697`): drop Guard 1 (`:644-655`), add the `card >= 1.0` upper-bound surrogate-key skip, remove `"identifier"` from the `:566` skip tuple, update the `_exact_eligible` warning set (`:672-697`).
- **Create:** `tests/test_autoconfig_pincer_715.py` — unit tests for the band + identifier admission + regression assertion + `_healthcare_df` fixture.
- **Modify:** `scripts/repro_issue_715.py` — fix the too-strict verdict (matchkey pincer is the primary signal; blocking is secondary).
- **Modify:** `.github/workflows/repro-issue-715.yml` — add a post-fix assertion step.
- **Create:** `packages/python/goldenmatch/docs/autoconfig-cost-model.md` — user-facing cost-model doc.

---

## Task 1: Cardinality-band exact-matchkey admission in `build_matchkeys`

**Files:**
- Modify: `goldenmatch/core/autoconfig.py:550-697`
- Test: `tests/test_autoconfig_pincer_715.py`

Drive the tests with directly-constructed `ColumnProfile` objects (deterministic `cardinality_ratio`) plus a tiny `df` carrying the same column names. `ColumnProfile(name, dtype, col_type, confidence, sample_values=[], null_rate=0.0, cardinality_ratio=0.0, avg_len=0.0)`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_autoconfig_pincer_715.py
import polars as pl
from goldenmatch.core.autoconfig import ColumnProfile, build_matchkeys


def _df_with(cols):
    # Minimal df so build_matchkeys' df-dependent paths have real columns.
    return pl.DataFrame({c: ["a", "b", "c"] for c in cols})


def _exact_fields(matchkeys):
    return {
        f.field
        for mk in matchkeys if mk.type == "exact"
        for f in mk.fields
    }


def test_email_high_card_large_n_gets_exact_matchkey():
    """email at card 0.7 must back an exact matchkey regardless of row count
    (Guard 1 / df.height > 10000 must no longer fire)."""
    profiles = [
        ColumnProfile("email", "Utf8", "email", 0.9,
                      null_rate=0.3, cardinality_ratio=0.7),
    ]
    df = _df_with(["email"])
    df = pl.concat([df] * 4000)  # ~12000 rows, would trip old Guard 1
    mks = build_matchkeys(profiles, df=df)
    assert "email" in _exact_fields(mks)


def test_identifier_high_card_gets_exact_matchkey():
    """npi-shaped identifier at card 0.62 must back an exact matchkey
    (col_type='identifier' must no longer be skipped outright)."""
    profiles = [
        ColumnProfile("npi", "Utf8", "identifier", 0.9,
                      null_rate=0.38, cardinality_ratio=0.62),
    ]
    mks = build_matchkeys(profiles, df=_df_with(["npi"]))
    assert "npi" in _exact_fields(mks)


def test_surrogate_key_card_1_excluded():
    """matching_id at card 1.0 is a per-record surrogate key -> NO exact
    matchkey (upper bound of the band)."""
    profiles = [
        ColumnProfile("matching_id", "Utf8", "identifier", 0.9,
                      null_rate=0.0, cardinality_ratio=1.0),
    ]
    mks = build_matchkeys(profiles, df=_df_with(["matching_id"]))
    assert "matching_id" not in _exact_fields(mks)


def test_low_card_still_excluded_megacluster_guard_intact():
    """A low-card column (0.3) must STILL be excluded (mega-cluster guard)."""
    profiles = [
        ColumnProfile("status", "Utf8", "identifier", 0.9,
                      null_rate=0.0, cardinality_ratio=0.3),
    ]
    mks = build_matchkeys(profiles, df=_df_with(["status"]))
    assert "status" not in _exact_fields(mks)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `POLARS_SKIP_CPU_CHECK=1 .venv/Scripts/python.exe -m pytest tests/test_autoconfig_pincer_715.py -v` (from `packages/python/goldenmatch`)
Expected: `test_email_*`, `test_identifier_*` FAIL (no exact matchkey produced); `test_surrogate_*`, `test_low_card_*` may already pass.

- [ ] **Step 3: Remove `"identifier"` from the matchkey skip**

At `autoconfig.py:566`, change:
```python
        if p.col_type in ("numeric", "date", "identifier", "year"):
            continue  # skip non-matchable columns (year is blocking-only)
```
to:
```python
        if p.col_type in ("numeric", "date", "year"):
            continue  # skip non-matchable columns (year is blocking-only).
            # identifier columns ARE matchable: a real shared identifier
            # (NPI/SSN/MRN) backs an exact matchkey, gated below by the
            # cardinality band. Per-record surrogate keys (card==1.0) are
            # excluded by the upper bound. See #715.
```

- [ ] **Step 4: Replace Guard 1 with the surrogate-key upper bound**

At `autoconfig.py:644-655`, DELETE the row-count guard block:
```python
        # Skip exact matchkeys for large datasets — exact matchkeys do a full
        # self-join which is O(N^2) without blocking. ...
        if scorer == "exact" and df is not None and df.height > 10000:
            reason = f"dataset has {df.height} rows; exact self-join is O(N^2)"
            logger.warning(...)
            skipped_exact.append((p.name, reason))
            continue
```
and REPLACE with the upper-bound surrogate-key skip:
```python
        # Exact matchkeys are a Polars hash self-join (find_exact_matches),
        # not a nested loop, and do not pass through fuzzy blocking. Their
        # cost is the number of emitted equal-pairs, bounded by cardinality:
        # a high-cardinality column emits few pairs and is both cheap and
        # mega-cluster-safe. So there is NO row-count guard here (the old
        # df.height > 10000 guard mismodeled the cost and orphaned real
        # identifiers -- see #715). The mega-cluster risk is the OPPOSITE
        # shape (low cardinality), already caught by the >= 0.5 gate above.
        #
        # Upper bound: a perfectly-unique column (card == 1.0) is a
        # per-record surrogate key (e.g. a row PK). It is never shared, so an
        # exact match emits zero pairs and asserts no real identity. Exclude
        # it for config hygiene.
        if scorer == "exact" and p.cardinality_ratio >= 1.0:
            reason = (
                f"cardinality_ratio={p.cardinality_ratio:.4f} >= 1.0 "
                f"— perfectly-unique surrogate key, no shared identity to match"
            )
            logger.info(
                "Skipping exact matchkey for '%s' (%s).", p.name, reason,
            )
            skipped_exact.append((p.name, reason))
            continue
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `POLARS_SKIP_CPU_CHECK=1 .venv/Scripts/python.exe -m pytest tests/test_autoconfig_pincer_715.py -v`
Expected: all four PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig.py packages/python/goldenmatch/tests/test_autoconfig_pincer_715.py
git commit -m "fix(autoconfig): admit high-card identifiers to exact matchkeys (#715)

Drop the blanket df.height>10000 Guard 1 (mismodeled a hash self-join as
O(N^2)) and the col_type=='identifier' skip. Admit exact matchkeys on the
cardinality band 0.5 <= card < 1.0: lower bound = existing mega-cluster
guard, upper bound excludes perfectly-unique surrogate keys.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Update the "all exact-eligible excluded" aggregate warning

**Files:**
- Modify: `goldenmatch/core/autoconfig.py:672-697`
- Test: `tests/test_autoconfig_pincer_715.py`

The `_exact_eligible` set (`:672-676`) currently excludes `identifier`, so the degradation warning under-counts. Since identifier now backs exact matchkeys, include it.

- [ ] **Step 1: Write failing test**

```python
def test_aggregate_warning_counts_identifier(caplog):
    """When every exact-eligible column (incl. identifier) is excluded, the
    aggregate warning must count identifier columns too."""
    import logging
    profiles = [
        # low-card identifier -> excluded by the >=0.5 gate
        ColumnProfile("npi", "Utf8", "identifier", 0.9,
                      null_rate=0.0, cardinality_ratio=0.2),
        ColumnProfile("name", "Utf8", "name", 0.9,
                      null_rate=0.0, cardinality_ratio=0.5),
    ]
    with caplog.at_level(logging.WARNING, logger="goldenmatch.core.autoconfig"):
        build_matchkeys(profiles, df=_df_with(["npi", "name"]))
    msgs = " ".join(r.message for r in caplog.records)
    assert "exact-eligible" in msgs and "npi" in msgs
```

- [ ] **Step 2: Run to verify it fails**

Run: `POLARS_SKIP_CPU_CHECK=1 .venv/Scripts/python.exe -m pytest tests/test_autoconfig_pincer_715.py::test_aggregate_warning_counts_identifier -v`
Expected: FAIL (npi not in the eligible set, so warning omits it).

- [ ] **Step 3: Include identifier in the eligible set**

At `autoconfig.py:672-676`, change the exclusion tuple:
```python
    _exact_eligible = [
        p for p in profiles
        if p.col_type not in ("numeric", "date", "identifier", "description")
        and _SCORER_MAP.get(p.col_type, (None,))[0] == "exact"
    ]
```
to:
```python
    _exact_eligible = [
        p for p in profiles
        if p.col_type not in ("numeric", "date", "description")
        and _SCORER_MAP.get(p.col_type, (None,))[0] == "exact"
    ]
```
(`identifier` maps to `exact` in `_SCORER_MAP`, so removing it from the exclusion makes it counted.)

- [ ] **Step 4: Run to verify it passes**

Run: `POLARS_SKIP_CPU_CHECK=1 .venv/Scripts/python.exe -m pytest tests/test_autoconfig_pincer_715.py::test_aggregate_warning_counts_identifier -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig.py packages/python/goldenmatch/tests/test_autoconfig_pincer_715.py
git commit -m "fix(autoconfig): count identifier columns in exact-eligible warning (#715)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: End-to-end regression — healthcare shape commits a healthy config (Component 3 + Component 2 verification)

**Files:**
- Modify: `tests/test_autoconfig_pincer_715.py`

This is the load-bearing behavioral assertion: the full `auto_configure_df` path on healthcare shape must produce >= 1 exact matchkey on an identifier column AND retain a bounded blocking key, at a row count above the old Guard 1 threshold but small enough to run in unit-test time (~15K rows). Use `confidence_required=False` so the test exercises the committed config directly without the REFUSE_AT_N raise (which only triggers at >= 100K anyway).

Reuse the synthetic generator already in `scripts/repro_issue_715.py` (`make_healthcare_df`). Import it, or copy it into a `_healthcare_df` helper in the test module (sibling to `_person_df`/`_gate_test_df` in `test_autoconfig_regressions.py`). Prefer importing to stay DRY:

- [ ] **Step 1: Write the failing regression test**

```python
def test_healthcare_shape_commits_exact_matchkey_and_blocking():
    """#715 regression: healthcare-provider shape must auto-configure to a
    config with >= 1 exact matchkey on an identifier-ish column AND a bounded
    blocking key — not the fuzzy-only, mega-block collapse."""
    import sys
    from pathlib import Path
    sys.path.insert(
        0, str(Path(__file__).parent.parent / "scripts"),
    )
    from repro_issue_715 import make_healthcare_df
    from goldenmatch.core.autoconfig import auto_configure_df

    df = make_healthcare_df(15_000)  # above old Guard 1 (10000), fast
    cfg = auto_configure_df(df, confidence_required=False)

    mks = cfg.get_matchkeys()
    exact_fields = {
        f.field for mk in mks if mk.type == "exact" for f in mk.fields
    }
    assert exact_fields & {"npi", "email", "phone_number"}, (
        f"expected an exact matchkey on an identifier column, got {exact_fields}"
    )

    # Blocking retained and bounded (Component 2 verification).
    blocking = cfg.blocking
    assert blocking is not None and blocking.keys, (
        "expected blocking to be retained, got none"
    )
```

- [ ] **Step 2: Run to verify it fails (pre-fix) or passes (post-Task-1)**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest tests/test_autoconfig_pincer_715.py::test_healthcare_shape_commits_exact_matchkey_and_blocking -v`
Expected after Tasks 1-2: PASS. If the blocking assertion FAILS, that confirms the controller-commit gap (spec Component 2) — STOP and surface it; do not patch `build_blocking` blindly.

Note: `auto_configure_df` may enable `rerank=True` on weighted matchkeys, which loads a HF cross-encoder. If the test errors on a model download, follow the offline pattern in `tests/test_autoconfig_regressions.py::test_dedupe_df_interaction_all_three_fixes_together` (build config, set `mk.rerank = False`). The assertions here only read matchkeys/blocking, so a download is unlikely to trigger, but guard if it does.

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/tests/test_autoconfig_pincer_715.py
git commit -m "test(autoconfig): #715 regression — healthcare shape gets exact MK + blocking

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Fix the repro script verdict + add post-fix assertion to the workflow

**Files:**
- Modify: `scripts/repro_issue_715.py`
- Modify: `.github/workflows/repro-issue-715.yml`

The script's verdict required BOTH zero exact matchkeys AND zero blocking; the matchkey pincer is the primary signal. Reframe it to report the matchkey-side pincer (no exact matchkeys) as the verdict, and show blocking separately.

- [ ] **Step 1: Reframe the verdict in `scripts/repro_issue_715.py`**

Replace the `# -- Verdict --` block so the primary signal is "zero exact matchkeys despite identifier-eligible columns present" and blocking is reported as context, not part of the pass/fail. Post-fix, the script should print `PINCER RESOLVED` when exact matchkeys ARE produced.

```python
    print("=== VERDICT ===")
    print(f"  exact matchkeys produced: {len(exact_mks)}  fields={sorted(_exact_fields(matchkeys))}")
    print(f"  blocking keys: {has_blocking}")
    identifier_eligible = [
        p.name for p in profiles
        if p.col_type in ("identifier", "email", "phone")
        and 0.5 <= p.cardinality_ratio < 1.0
    ]
    print(f"  identifier-eligible (0.5<=card<1.0): {identifier_eligible}")
    if identifier_eligible and len(exact_mks) == 0:
        print("  >>> PINCER PRESENT: identifier-eligible columns produced ZERO exact matchkeys.")
    elif identifier_eligible and exact_mks:
        print("  >>> PINCER RESOLVED: identifier columns now back exact matchkeys.")
    else:
        print("  >>> inconclusive at this shape.")
```
(Add a small `_exact_fields(matchkeys)` helper mirroring the test's.)

- [ ] **Step 2: Add a post-fix assertion step to `.github/workflows/repro-issue-715.yml`**

The workflow already exists on `main` (from #718) and its "Run #715 repro" step already tees stdout to `repro715.log`. Add a NEW step immediately after it (do not recreate the workflow or the run step) that fails the job if the pincer is still present:
```yaml
      - name: Assert pincer resolved
        working-directory: packages/python/goldenmatch
        run: |
          if grep -q "PINCER PRESENT" repro715.log; then
            echo "::error::#715 pincer still present — identifier columns produced no exact matchkeys"
            exit 1
          fi
          grep -q "PINCER RESOLVED" repro715.log && echo "pincer resolved" || echo "::warning::inconclusive verdict"
```

- [ ] **Step 3: Run the repro locally to confirm RESOLVED**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe packages/python/goldenmatch/scripts/repro_issue_715.py 20000`
Expected: `PINCER RESOLVED` (email/npi now back exact matchkeys).

- [ ] **Step 4: Commit**

```bash
git add packages/python/goldenmatch/scripts/repro_issue_715.py .github/workflows/repro-issue-715.yml
git commit -m "test(autoconfig): repro #715 verdict reframed to matchkey pincer + CI assert

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: User-facing cost-model doc (Component 4)

**Files:**
- Create: `packages/python/goldenmatch/docs/autoconfig-cost-model.md`

- [ ] **Step 1: Write the doc**

Cover, in plain prose with a short example:
- Why exact matchkeys are gated by **cardinality**, not row count (hash self-join, cost = emitted equal-pairs).
- The admission band `0.5 <= card < 1.0` and what each bound protects against (mega-clusters below 0.5; useless surrogate keys at 1.0).
- That high-cardinality identifiers (NPI/email/phone) anchor exact matchkeys and do NOT need blocking.
- The blocking block-size cap (`max_safe_block`) + compound fallback for the fuzzy matchkey.
- Override env vars: `GOLDENMATCH_BLOCKING_MAX_RATIO`, `POLARS_SKIP_CPU_CHECK` (Windows local-run note).
- ASCII only (repo convention; no em dashes).

- [ ] **Step 2: Commit**

```bash
git add packages/python/goldenmatch/docs/autoconfig-cost-model.md
git commit -m "docs(autoconfig): add exact-matchkey cardinality cost-model page (#715)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Pre-merge validation — DQbench + in-house quality gate (HARD GATE)

**Files:** none (validation run)

Dropping Guard 1 changes behavior for ANY large dataset with a high-card email/identifier column. This is the primary mega-cluster risk. It MUST be measured before merge.

- [ ] **Step 1: Run the in-house backend-parity quality gate (#528)**

This runs in CI on the PR (the `python (goldenmatch)` lane / quality gate). Confirm it stays green. If a local run is needed, use the gate entry point referenced in `project_bucket_native_scoring_win` / `#528`.

- [ ] **Step 2: Run DQbench T1/T2/T3**

DQbench datasets are local at `~/.dqbench`; the CLI runs under system Python312 (see `project_issue_489_zerolabel_gate`). Capture the composite + per-tier F1 before and after the change. The adversarial same-email/same-id collision shape (T3) is the closest analog to the mega-cluster risk — watch T3 precision specifically.

- [ ] **Step 3: Record the verdict in the PR description**

Paste the before/after DQbench composite + T1/T2/T3 and the quality-gate result. **Do not merge if T3 precision regresses materially** — if it does, the cardinality band alone is insufficient and the band's upper/lower bounds (or a collision-rate check) need revisiting before merge.

---

## Task 7: Open PR, run full CI, land

**Files:** none

- [ ] **Step 1: Push branch + open PR** to `benseverndev-oss/goldenmatch` base `main` (auth: `gh auth switch --user benzsevern` before push; switch back to `benzsevern-mjh` after). PR body links #715 and pastes the repro `PINCER RESOLVED` output + Task 6 DQbench numbers.
- [ ] **Step 2: Wait for `python (goldenmatch)` lane + `ci-required` green** (the lane runs ~14 min). Poll robustly (treat empty `gh` output as "keep waiting").
- [ ] **Step 3: Dispatch `repro-issue-715.yml` on the PR branch** to confirm the at-scale post-fix assertion passes.
- [ ] **Step 4: Squash-merge `--delete-branch`** once green (branch must be up to date with main; `gh pr update-branch` if behind).
- [ ] **Step 5: Comment on #715** with the root-cause summary (the pincer + hash-join cost model) and the fix, and close it.
