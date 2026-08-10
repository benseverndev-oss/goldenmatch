# Arrow Seam Hardening Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the arrow-native seam's guardrails match its claims — widen the zero-polars probe past one config, put every polars re-entry idiom under a ratchet, and decide (on measurement, not vibes) whether goldenpipe flips to arrow-canonical.

**Architecture:** Three INDEPENDENT workstreams, each its own PR and exit gate. W1 and W2 are test/gate-only — they add no production behaviour and cannot regress the engine. W3 is a **decision spec**, not an implementation: it ends at a documented go/no-go, because the measured payoff is currently zero and the cost is real.

**Tech Stack:** pytest (subprocess + `sys.meta_path` import blocking), pyarrow, polars (as the thing being fenced out), goldenpipe `Frame` protocol.

---

## Why this exists

PR #2462 fixed a defect where quality-weighted survivorship was silently disabled on the arrow lane. It lived on `main` with `goldenmatch_nopolars` **green and ci-required**.

Two guardrails should have caught it and neither could:

1. **The zero-polars probe proves exactly one config** — `tests/_zero_polars_probe.py`: 5 rows, one `exact` matchkey, `quality=disabled`, `transform=disabled`. Every default-config path is outside it.
2. **The bridge ledger watches one idiom** — `_as_polars_df(` (5 sites). The actual arrow→polars re-entry surface is **71 `pl.from_arrow(` sites across 24 files**, none counted.

This plan closes both, then confronts the structural gap the audit found: the seam is arrow-native inside goldenmatch/goldencheck/goldenflow and polars-canonical in goldenpipe, which is the orchestrator that feeds them.

### Scope check

These are three separate subsystems. They are deliberately **not** stacked — W1 and W2 touch disjoint files and can run in parallel by different workers. W3 depends on neither. If you only have budget for one, do **W1**: it is what would have caught #2462.

---

## File Structure

| File | Workstream | Responsibility |
|---|---|---|
| `packages/python/goldenmatch/tests/_zero_polars_probe.py` | W1 | MODIFY — take a config name via `argv`, run that one case |
| `packages/python/goldenmatch/tests/_zero_polars_cases.py` | W1 | CREATE — the config matrix + the explicit decline ledger |
| `packages/python/goldenmatch/tests/test_zero_polars_gate.py` | W1 | MODIFY — parametrize over the matrix, one subprocess per case |
| `packages/python/goldenmatch/tests/test_bridge_ledger.py` | W2 | MODIFY — add the `pl.from_arrow` ratchet + pragma parser |
| `docs/design/2026-08-10-goldenpipe-arrow-canonical-decision.md` | W3 | CREATE — options, measurement, recommendation |
| `packages/python/goldenpipe/benchmarks/adapter_conversion_probe.py` | W3 | CREATE — measures the thing the decision hinges on |

**Do not** put the W1 config matrix inside `test_zero_polars_gate.py`. The probe subprocess must import the cases WITHOUT importing pytest or the parent `conftest.py` (which imports polars). A standalone `_zero_polars_cases.py` is importable from both sides.

---

## Workstream W1 — Widen the zero-polars probe

**Exit gate:** every config in the matrix either runs polars-free, or is listed in `KNOWN_POLARS_DEPENDENT` with a one-line reason. Silence stops being an option.

**Design note — why a decline ledger and not just "make everything pass":** some paths genuinely need polars today (goldencheck's `apply_fixes` is polars-native; the `[polars]` wall optimizations are opt-in by design). Forcing them all green would mean either porting goldencheck's fixer or deleting real features. The ledger makes each decline a **declared, reviewable line** instead of an untested gap — the same trick the bridge ledger uses.

### Task 1: Extract the config matrix

**Files:**
- Create: `packages/python/goldenmatch/tests/_zero_polars_cases.py`

- [ ] **Step 1: Write the case module**

```python
"""Config matrix for the zero-polars gate.

Importable WITHOUT pytest and WITHOUT the parent conftest (which imports
polars), because the probe subprocess imports this under a polars import block.

Each case is (name, builder) where builder() -> (pa.Table, GoldenMatchConfig).
Keep fixtures TINY -- this runs one subprocess per case in CI.
"""
from __future__ import annotations

from typing import Any, Callable

# Configs that legitimately cannot run polars-free TODAY. Each entry needs a
# reason. Adding one is a review conversation; removing one is a win.
# Format: name -> why.
KNOWN_POLARS_DEPENDENT: dict[str, str] = {
    "quality_autofix": (
        "goldencheck.engine.fixer.apply_fixes takes a pl.DataFrame; the arrow "
        "lane degrades to scan-only (see tests/test_quality_no_polars.py)."
    ),
}


def _tiny_people() -> Any:
    import pyarrow as pa

    # 60 rows: clears goldencheck's fuzzy thresholds (_MIN_ROWS=50,
    # _MIN_DISTINCT=3) so quality-weighting cases actually get signal.
    names, cities, emails = [], [], []
    for i in range(30):
        if i < 15:
            city_a = city_b = "California"
        elif i < 24:
            city_a = city_b = "Texas"
        elif i < 29:
            city_a = city_b = "Nevada"
        else:
            city_a, city_b = "Californa", "California"
        names += [f"person{i}", f"person{i}"]
        cities += [city_a, city_b]
        emails += [f"p{i}@x.com", f"p{i}@x.com"]
    return pa.table({"name": names, "city": cities, "email": emails})


def case_exact() -> tuple[Any, Any]:
    """The original probe case -- kept so the matrix is a superset."""
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        QualityConfig, TransformConfig,
    )
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="k", type="exact", fields=[MatchkeyField(field="name")],
        )],
        quality=QualityConfig(mode="disabled"),
        transform=TransformConfig(mode="disabled"),
    )
    return _tiny_people(), cfg


def case_default_prep() -> tuple[Any, Any]:
    """Quality + transform at their DEFAULTS (both are default-ON). This is the
    config a real `pip install goldenmatch` user gets, and the one the original
    single-case probe never exercised."""
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
    )
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="k", type="exact", fields=[MatchkeyField(field="name")],
        )],
    )
    return _tiny_people(), cfg


CASES: dict[str, Callable[[], tuple[Any, Any]]] = {
    "exact": case_exact,
    "default_prep": case_default_prep,
}
```

- [ ] **Step 2: Commit the scaffold**

```bash
git add packages/python/goldenmatch/tests/_zero_polars_cases.py
git commit -m "test(gate): extract zero-polars config matrix + decline ledger"
```

### Task 2: Make the probe case-driven

**Files:**
- Modify: `packages/python/goldenmatch/tests/_zero_polars_probe.py`

- [ ] **Step 1: Replace the hardcoded config with an argv-selected case**

Keep the existing polars-blocking prelude verbatim (it is the load-bearing part). Replace the config construction and the `run_dedupe` call with:

```python
import sys

case_name = sys.argv[1] if len(sys.argv) > 1 else "exact"

from _zero_polars_cases import CASES  # noqa: E402

tbl, cfg = CASES[case_name]()

import goldenmatch.core.pipeline as P  # noqa: E402

res = P.run_dedupe_df(tbl, cfg)
assert res is not None

assert "polars" not in sys.modules, (
    f"case {case_name!r} imported polars: "
    f"{[m for m in sys.modules if m.startswith('polars')]}"
)
print("ZERO-POLARS OK")
```

The probe's directory must be on `sys.path` for `_zero_polars_cases` to import — the gate already sets `PYTHONPATH`, add the tests dir there in Task 3.

- [ ] **Step 2: Run the ORIGINAL case to prove no behaviour change**

Run: `python tests/_zero_polars_probe.py exact`
Expected: prints `ZERO-POLARS OK`, exit 0.

- [ ] **Step 3: Run the NEW case and watch it tell you something**

Run: `python tests/_zero_polars_probe.py default_prep`
Expected: **unknown** — this is the point of the exercise. Record the actual result.
- If it passes: default-config prep is genuinely polars-free. Good.
- If it fails on `polars in sys.modules`: you have found a real gap. Do NOT silence it by adding `default_prep` to `KNOWN_POLARS_DEPENDENT` without reading the traceback first — the leak may be a one-line `isinstance(x, pl.DataFrame)` on a hot path, which is a fix, not a decline.

- [ ] **Step 4: Commit**

```bash
git add packages/python/goldenmatch/tests/_zero_polars_probe.py
git commit -m "test(gate): probe takes a case name instead of one hardcoded config"
```

### Task 3: Parametrize the gate over the matrix

**Files:**
- Modify: `packages/python/goldenmatch/tests/test_zero_polars_gate.py`

- [ ] **Step 1: Write the parametrized test**

```python
from _zero_polars_cases import CASES, KNOWN_POLARS_DEPENDENT


@pytest.mark.parametrize("case_name", sorted(CASES))
@pytest.mark.parametrize("native", ["0", "1"], ids=["pure", "native"])
def test_zero_polars_across_config_matrix(case_name, native):
    """Every covered config runs polars-free on BOTH lanes.

    One subprocess PER CASE so a leak is attributed to the exact config that
    caused it -- a single shared process would only tell you 'something leaked'.
    """
    if case_name in KNOWN_POLARS_DEPENDENT:
        pytest.skip(f"declared polars-dependent: {KNOWN_POLARS_DEPENDENT[case_name]}")

    env = dict(os.environ)
    tests_dir = Path(__file__).parent
    env["PYTHONPATH"] = os.pathsep.join(
        [str(tests_dir.parent), str(tests_dir), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    env["GOLDENMATCH_NATIVE_GATE"] = native
    proc = subprocess.run(
        [sys.executable, str(_PROBE), case_name],
        capture_output=True, text=True, env=env, timeout=300,
    )
    assert proc.returncode == 0, (
        f"case={case_name} lane={native}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr[-2000:]}"
    )
    assert "ZERO-POLARS OK" in proc.stdout


def test_decline_ledger_entries_are_real_cases():
    """A decline must name a case that EXISTS -- otherwise the ledger rots into
    a list of excuses for configs nobody runs."""
    unknown = set(KNOWN_POLARS_DEPENDENT) - set(CASES)
    assert not unknown, f"declined cases not in the matrix: {unknown}"
```

Delete the old single-case `test_arrow_lane_exact_dedupe_imports_zero_polars` — `case_exact` supersedes it. Keep `test_cli_import_zero_polars` untouched; it tests a different invariant (import-time, not run-time).

- [ ] **Step 2: Run the gate**

Run: `pytest tests/test_zero_polars_gate.py -v`
Expected: `exact` passes on both lanes. `default_prep` result per Task 2 Step 3.

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/tests/test_zero_polars_gate.py
git commit -m "test(gate): run the zero-polars probe across the config matrix"
```

### Task 4: Grow the matrix

Add cases one at a time, **each as its own commit**, running the probe after each. Expect some to fail — a failure here is the deliverable, not a setback.

- [ ] `fuzzy` — a `fuzzy` matchkey (exercises the scorer, where the #2250 lane divergence lived)
- [ ] `probabilistic` — a `probabilistic` matchkey + EM (the FS path)
- [ ] `weighted` — `weighted` matchkey with per-field scorers/weights
- [ ] `golden_strategies` — non-default survivorship (`most_recent`, `source_priority`)
- [ ] `quality_weighting` — `quality_weighting=True` with the dirty-city fixture (regression pin for #2462)
- [ ] `zero_config` — no matchkeys at all, auto-config picks (the most-used entry point)
- [ ] `bucket_backend` — `partitioned_block_scoring` / bucket scorer

For each: if it leaks polars, read the traceback. Fix if it is a stray `isinstance`/`pl.` on the covered path; declare in `KNOWN_POLARS_DEPENDENT` with a reason if it is a genuine polars-only feature. **Never** declare without reading the traceback.

- [ ] **Final step: CI budget check**

Run: `pytest tests/test_zero_polars_gate.py --durations=0`
Expected: total under ~90s. Cases × 2 lanes × subprocess startup adds up. If over, drop the `native` axis for cases whose kernels are not lane-sensitive rather than cutting cases.

---

## Workstream W2 — Put every polars re-entry idiom under a ratchet

**Exit gate:** `pl.from_arrow` counts are pinned per-file and may only go DOWN; a new file entering the list fails the build; each surviving site is either pinned or explicitly justified inline.

**Design note — why a ratchet and not exact equality:** `_as_polars_df` has 5 sites, so exact equality is cheap to maintain. `pl.from_arrow` has **71 across 24 files**. Exact equality there would fail on every unrelated refactor and get muted within a month. A ratchet (`found <= expected`, no new files) keeps the pressure one-directional without the churn.

**Design note — why the pragma:** not every `pl.from_arrow` is a defect. Some sit inside polars-lane-only code, where converting to polars is the *correct* behaviour. Counting those as debt makes the number meaningless. A `# polars-lane:` pragma forces each site to declare which it is, and the counter skips declared ones — so the ledger number tracks *undeclared* re-entry, which is the thing that actually needs to fall.

### Task 5: Seed the baseline

**Files:**
- Modify: `packages/python/goldenmatch/tests/test_bridge_ledger.py`

- [ ] **Step 1: Generate the current per-file counts**

Run:
```bash
cd packages/python/goldenmatch
grep -rn "pl\.from_arrow(" --include=*.py goldenmatch/ \
  | grep -v "^goldenmatch/core/frame.py" \
  | cut -d: -f1 | sed 's|^goldenmatch/||' | sort | uniq -c | sort -rn
```

Expected at time of writing (verify — it drifts):

```
17 distributed/clustering.py     10 core/cluster.py        6 distributed/scoring.py
 5 backends/fs_out_of_core.py     4 core/pairs.py          3 distributed/golden.py
 3 core/pipeline.py               3 core/golden.py         2 identity/fingerprint_batch.py
 2 identity/block_index.py        2 core/quality.py        2 core/blocker.py
 1 x12 more files                                          TOTAL 71
```

`core/frame.py` is excluded as a **forward-looking** guard, not to mask anything: it currently has **zero** `pl.from_arrow` calls (verified). The exclusion exists because it IS the seam — if the `PolarsFrame` backend ever needs the idiom, that is its legitimate home and should not read as debt. Verify the zero still holds when you seed the baseline; if it has grown, find out why before excluding it.

- [ ] **Step 2: Write the failing ratchet test**

```python
# Undeclared arrow->polars re-entry, per file. RATCHET: may only go DOWN.
# A site that is CORRECT (polars-lane-only code) should carry an inline
# `# polars-lane: <reason>` pragma -- the counter skips those, so declaring a
# site is how you remove it from this number honestly.
EXPECTED_FROM_ARROW: dict[str, int] = {
    # <paste Step 1 output here>
}

_FROM_ARROW = re.compile(r"pl\.from_arrow\(")
_PRAGMA = re.compile(r"#\s*polars-lane:")


def _count_from_arrow(text: str) -> int:
    return sum(
        1 for line in text.splitlines()
        if _FROM_ARROW.search(line) and not _PRAGMA.search(line)
    )


def test_from_arrow_ratchet():
    found: dict[str, int] = {}
    for py in PKG.rglob("*.py"):
        rel = py.relative_to(PKG).as_posix()
        if rel == "core/frame.py":
            continue  # the seam's own polars backend
        n = _count_from_arrow(py.read_text(encoding="utf-8"))
        if n > 0:
            found[rel] = n

    new_files = set(found) - set(EXPECTED_FROM_ARROW)
    assert not new_files, (
        "NEW file re-entering polars via pl.from_arrow: "
        f"{sorted(new_files)}. Port via the seam, or mark the site "
        "`# polars-lane: <reason>` if it is polars-lane-only code."
    )

    regressed = {
        f: (found[f], EXPECTED_FROM_ARROW[f])
        for f in found
        if found[f] > EXPECTED_FROM_ARROW[f]
    }
    assert not regressed, f"pl.from_arrow count went UP (file: got, allowed): {regressed}"

    improved = {
        f: (found.get(f, 0), EXPECTED_FROM_ARROW[f])
        for f in EXPECTED_FROM_ARROW
        if found.get(f, 0) < EXPECTED_FROM_ARROW[f]
    }
    assert not improved, (
        "You retired sites -- thank you. Now ratchet the ledger DOWN "
        f"(file: got, stale expectation): {improved}"
    )
```

- [ ] **Step 3: Run it to verify it fails**

Run: `pytest tests/test_bridge_ledger.py::test_from_arrow_ratchet -v`
Expected: FAIL — `EXPECTED_FROM_ARROW` is empty, so all 24 files report as "NEW file re-entering polars".

- [ ] **Step 4: Paste the real baseline in, re-run**

Run: `pytest tests/test_bridge_ledger.py -v`
Expected: PASS (both ledger tests).

- [ ] **Step 5: Prove the ratchet actually bites**

Temporarily add a `pl.from_arrow(x)` line to any listed file, re-run, confirm FAIL with the "went UP" message. Then revert. **Do not skip this** — a ledger that cannot fail is decoration.

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/tests/test_bridge_ledger.py
git commit -m "test(ledger): ratchet pl.from_arrow re-entry (71 sites, 24 files)"
```

### Task 6: Declare the legitimate sites

- [ ] Walk `distributed/clustering.py` (17) and `core/cluster.py` (10) — the two biggest. For each site, decide: polars-lane-only (add `# polars-lane: <reason>`) or real debt (leave counted).
- [ ] Ratchet `EXPECTED_FROM_ARROW` down by however many you declared.
- [ ] Commit per file, not per site.

This is deliberately the LAST task in W2 and it is open-ended. The ratchet is valuable the moment it lands; the classification is ongoing hygiene.

---

## Workstream W3 — goldenpipe arrow-canonical: DECISION, not implementation

**This workstream produces a document and a measurement. It does not change the engine.**

Stopping at a decision is the point. The audit found goldenpipe is polars-canonical *by explicit design* — `models/frame.py` says "Arrow-CAPABLE, not Arrow-MANDATORY", `PipelineContext.df` is a `pl.DataFrame`, `LocalFrame.polars()` returns the backing frame **by reference**, and `arrow_batches()` is documented "not called on the in-process path". That is a coherent design someone chose, backed by a measurement. Overturning it needs a better reason than tidiness.

### The measured facts (from `docs/design/2026-07-06-goldenpipe-stage0-findings.md`)

| | 2176 ms run | 4806 ms run |
|---|---|---|
| handoff: CSV re-read | 3.5 ms · 0.2% | 5.7 ms · 0.1% |
| handoff: full-df Utf8 cast | 1.6 ms · 0.1% | 2.2 ms · 0.0% |
| **handoff total** | **5.1 ms · 0.2%** | **7.9 ms · 0.2%** |

Wall is **~99% per-stage kernel compute**; `goldenmatch.dedupe` alone is **~75%**.

**There is no wall upside.** Anyone proposing this flip on performance grounds has not read Stage 0. The case, if there is one, is:

- one substrate end-to-end (no per-column `.to_arrow()` at `adapters/match.py:172`)
- no dtype drift across the boundary (the `pl.Utf8` cast at `adapters/match.py:40` is a whole-frame coercion)
- drops the last hard `polars` dep in the orchestrator (install size, supply chain)

Against:

- `LocalFrame.polars()` by-reference is load-bearing and explicitly why Stage 0 did not regress
- 41 call sites read `ctx.df` / `.polars()` across 7 adapters
- `DuckDBFrame` (Phase C) already covers the engine-resident case, which was the actual scaling motivation
- zero measured perf win

### Task 7: Measure the one thing that is NOT yet measured

Stage 0 measured the *handoff*. It did **not** measure the per-column conversion the match adapter does today. That is the number that separates option B from "do nothing".

**Files:**
- Create: `packages/python/goldenpipe/benchmarks/adapter_conversion_probe.py`

- [ ] **Step 1: Write the probe**

Measure, at 100K / 1M / 10M rows, median of 5, fresh process per size:
1. wall of `{c: ctx.df[c].to_arrow() for c in needed}` (`adapters/match.py:172`)
2. wall of the whole-frame `pl.Utf8` cast (`adapters/match.py:40`)
3. total pipeline wall for the same run

Report each as a percentage of total wall.

- [ ] **Step 2: Run it**

Per @feedback_no_local_scale_benchmarks: **do not run 10M locally.** Dispatch to CI (`runs-on: large-new-64GB`, per @feedback_bench_default_runner). 100K/1M locally is fine for a smoke check.

- [ ] **Step 3: Record results in the decision doc**

### Task 8: Write the decision document

**Files:**
- Create: `docs/design/2026-08-10-goldenpipe-arrow-canonical-decision.md`

- [ ] Document the three options with the Task 7 numbers filled in:

**Option A — Do nothing; document the boundary as intentional.**
Add a note to `models/frame.py` stating the polars-canonical choice is deliberate and cross-referencing Stage 0. Cost: ~0. Leaves the suite with two substrates.

**Option B — Additive `ArrowFrame` impl; adapters prefer arrow when the source is arrow.**
Add a third `Frame` impl beside `LocalFrame`/`DuckDBFrame`. `PipeContext.frame` already abstracts this — the protocol does not change. Adapters that receive an arrow-backed frame skip the per-column conversion. `LocalFrame` stays polars-backed, so Stage 0 is untouched. Cost: moderate, contained to the adapters. **Recommended if Task 7 shows conversion > ~2% of wall.**

**Option C — Full flip: arrow-canonical `PipeContext`, `polars()` becomes a compat shim, polars → extra.**
Cost: 41 call sites, 7 adapters, plus goldenanalysis/infermap (which also hard-dep polars and sit in the same tier). **Only justified if the suite commits to removing polars from the orchestration tier entirely** — which is a product decision about install footprint, not an engineering one.

- [ ] **Recommendation to record:** if Task 7 returns < 2%, recommend **A** and stop. The consistency argument is real but it is an aesthetic preference at that cost, and it is Ben's call to make explicitly rather than mine to smuggle in as cleanup. If it returns > 2%, recommend **B**. Do not recommend **C** without a separate product decision on the polars footprint across goldenpipe + goldenanalysis + infermap together — flipping goldenpipe alone leaves two of three tier-2 packages still polars-canonical, which buys inconsistency at full price.

- [ ] **Step: Commit and STOP.**

```bash
git add docs/design/2026-08-10-goldenpipe-arrow-canonical-decision.md \
        packages/python/goldenpipe/benchmarks/adapter_conversion_probe.py
git commit -m "docs(goldenpipe): arrow-canonical decision spec + conversion measurement"
```

Do not open a PR that implements B or C off the back of this. Bring the numbers to Ben.

---

## Ordering and effort

| | Workstream | Effort | Risk | Catches #2462-class bugs? |
|---|---|---|---|---|
| 1 | W1 probe matrix | ~1 day | none (test-only) | **yes, directly** |
| 2 | W2 from_arrow ratchet | ~half day + ongoing | none (test-only) | yes, prevents new ones |
| 3 | W3 decision | ~half day + CI bench | none (doc only) | no |

W1 first. It is the one that would have caught the bug that started this.

## Cross-cutting notes

- **Worktree test skew** (@reference_py_worktree_test_native_skew): the repo `.venv` resolves workspace members as editable installs pointing at whatever checkout it was created from. A worktree run MUST set `PYTHONPATH` to the worktree's sibling packages or it silently tests a stale goldencheck. #2462 hit exactly this (installed goldencheck was 1.4.0 vs 3.4.0 on main). `_run_polars_blocked` in `test_quality_no_polars.py` now prepends rather than clobbers — copy that idiom, don't reinvent it.
- **`.columns` is a silent-false trap** (@feedback_no_readd_evicted_polars): polars `.columns` returns NAMES, pyarrow `.columns` returns ChunkedArrays. A ported `if "col" not in df.columns:` guard does not raise on a `pa.Table` — it quietly evaluates True and the function fail-opens forever. That was #2462's root cause. Use `.column_names`. Grep for `.columns` before declaring any port done.
- **Do not run the full pytest suite locally** (@feedback_avoid_full_suite_oom) — xdist OOMs the box. Targeted files only; CI runs the rest.
