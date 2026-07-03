# Precision-Aware F-beta `_score` Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the shared `_score` accept/rank scalar precision-tunable (F-beta, env-driven, default 1.0 = current F1) so a config that improves precision at acceptable recall can win — closing the loop the SP-C smoke opened.

**Architecture:** One-function change to `substrate_tuner._score` (add module-level `import os`; F-beta on the relational axis from an env beta, `beta==1.0` short-circuits to the stored `relational.f1` for bit-identical backward-compat). Both consumers (SP-B2 `run_staged` argmax, SP-C `suggest` accept) pick up the env beta with no call-site change. The SP-C homograph runner gains a `--score-beta` (default 0.5) so the confirming smoke runs precision-favoring.

**Tech Stack:** Python stdlib, pytest.

**Spec:** `docs/superpowers/specs/2026-07-02-precision-aware-score-design.md`

**Branch:** `feat/precision-aware-score` (off `origin/main`; SP-B2 `_score` + SP-C runner both on main — no rebase needed).

---

## Files

- **Modify** `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_tuner.py` — add `import os` at module top; rewrite `_score`.
- **Modify** `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/run_substrate_suggest.py` — add `--score-beta` (default 0.5), set the env var before `suggest_substrate_config`.
- **Modify** `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_tuner.py` — F-beta unit tests (append).

## Test runner (box-safe)

```bash
cd /d/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$PWD:/d/show_case/gg-local-llm/packages/python/goldengraph" PYTHONIOENCODING=utf-8 PYTHONUTF8=1 \
  POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_substrate_tuner.py -q
```

## Ground-truth facts (verified)

- `substrate_tuner.py` module-top imports are `from __future__`, `from dataclasses import dataclass, field, replace`, `from goldengraph.config import ...`, `from erkgbench.substrate_eval import LEVER_AXIS_MAP`. **No `import os`** (it's function-local in `_reset_llm_state`) → MUST add at module top.
- Current `_score(scorecard)` = `scorecard["relational"]["f1"]` (+ `presence["coverage"]` when presence not None). Single positional arg. Call sites: `run_staged` `max(rounds, key=lambda rr: _score(rr.scorecard))` and `substrate_suggest` `_score(prop_sc) > _score(base_sc)`.
- `run_substrate_suggest.py` already `import os`; argparse has `--homograph/--ambiguity/--out-md`; sets `os.environ[...]` before `suggest_substrate_config(corpus.documents, ...)`.
- **F-beta:** `(1+b²)·P·R / (b²·P + R)`. Verified flips: base(P=.8153,R=.672) vs prop(P=.8916,R=.540) → beta=0.5: 0.782 vs **0.789** (prop wins); beta=0.25: 0.805 vs **0.859**.

---

### Task 1: F-beta `_score`

**Files:**
- Modify: `erkgbench/substrate_tuner.py` (add `import os`; rewrite `_score`)
- Test: `tests/test_substrate_tuner.py` (append)

- [ ] **Step 1: Write the failing tests** — append to `tests/test_substrate_tuner.py`:

```python
# --- precision-aware F-beta _score --------------------------------------------------------------------
def _rel(p, r, f1, *, presence=None):
    """A scorecard with explicit relational P/R/F1 (F-beta reads P/R; beta==1 uses the STORED f1)."""
    return {"presence": None if presence is None else {"coverage": presence},
            "relational": {"f1": f1, "recall": r, "precision": p},
            "connectivity": {"coverage": None, "f1": None, "edge_recall": 0.9},
            "coherence": {"components": 1, "largest_fraction": 1.0}}


def test_score_beta1_equals_stored_f1():
    # f1 sentinel (0.99) deliberately != 2PR/(P+R)=0.5 -> beta==1 must return the STORED f1, not recompute
    sc = _rel(0.5, 0.5, 0.99)
    assert st._score(sc, beta=1.0) == 0.99


def test_score_beta_half_favors_precision():
    base = _rel(0.8153, 0.672, 0.7368)     # the SP-C smoke baseline
    prop = _rel(0.8916, 0.540, 0.6723)     # the proposed homograph-safe config
    # default (F1): baseline wins (the bug we're fixing)
    assert st._score(base, beta=1.0) > st._score(prop, beta=1.0)
    # beta<1 (favor precision): proposed wins
    assert st._score(prop, beta=0.5) > st._score(base, beta=0.5)


def test_score_beta_reads_env():
    import os
    sc = _rel(0.9, 0.5, 0.643)
    prev = os.environ.get("GOLDENGRAPH_SUBSTRATE_SCORE_BETA")
    try:
        os.environ["GOLDENGRAPH_SUBSTRATE_SCORE_BETA"] = "0.25"
        assert st._score(sc) == st._score(sc, beta=0.25)          # env beta applied
        assert st._score(sc) != st._score(sc, beta=1.0)           # and it's NOT F1
    finally:
        if prev is None:
            os.environ.pop("GOLDENGRAPH_SUBSTRATE_SCORE_BETA", None)
        else:
            os.environ["GOLDENGRAPH_SUBSTRATE_SCORE_BETA"] = prev


def test_score_beta_zero_denom_safe():
    sc = _rel(0.0, 0.0, 0.0)
    assert st._score(sc, beta=0.5) == 0.0                          # denom 0 -> 0.0, no ZeroDivisionError


def test_score_presence_still_additive():
    sc = _rel(0.8, 0.6, 0.686, presence=0.5)
    # F_0.5(0.8,0.6) + presence 0.5
    fbeta = (1 + 0.25) * 0.8 * 0.6 / (0.25 * 0.8 + 0.6)
    assert abs(st._score(sc, beta=0.5) - (fbeta + 0.5)) < 1e-9
```

(`st` is the existing `from erkgbench import substrate_tuner as st` import at the top of the file.)

- [ ] **Step 2: Run to verify it fails** — box-safe `-k "score_beta or score_presence_still"`. Expected: FAIL — `_score()` got an unexpected keyword argument `beta` (and/or NameError on env path).

- [ ] **Step 3: Implement**

Add `import os` at the module top of `substrate_tuner.py` (after `from __future__ import annotations`, before the `from dataclasses` line):
```python
import os
```

Replace `_score` (currently `relational.f1 + presence.coverage`):
```python
def _score(scorecard: dict, *, beta: float | None = None) -> float:
    """Round-ranking / accept scalar: F-beta of relational P/R (+ presence.coverage when present).
    beta<1 favors PRECISION, >1 recall, ==1 is F1. Default beta from GOLDENGRAPH_SUBSTRATE_SCORE_BETA
    (1.0). At beta==1.0 the STORED relational.f1 is used (bit-identical to the prior F1-only behavior).
    Both consumers (run_staged argmax, suggest accept) read this via the env by default."""
    if beta is None:
        beta = float(os.environ.get("GOLDENGRAPH_SUBSTRATE_SCORE_BETA", "1.0") or "1.0")
    rel = scorecard["relational"]
    if beta == 1.0:
        f = rel["f1"]
    else:
        p, r, b2 = rel["precision"], rel["recall"], beta * beta
        denom = b2 * p + r
        f = (1.0 + b2) * p * r / denom if denom > 0 else 0.0
    s = f
    presence = scorecard.get("presence")
    if presence is not None:
        s += presence["coverage"]
    return s
```

- [ ] **Step 4: Run to verify passes** — box-safe `-k "score_beta or score_presence_still"`. Expected: PASS (5).

- [ ] **Step 5: Run the WHOLE tuner test file** (the existing SP-B2 tests must stay green at the default beta):
```bash
cd /d/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$PWD:/d/show_case/gg-local-llm/packages/python/goldengraph" PYTHONIOENCODING=utf-8 PYTHONUTF8=1 \
  POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 /d/show_case/goldenmatch/.venv/Scripts/python.exe \
  -m pytest tests/test_substrate_tuner.py -q      # 18 existing + 5 new = 23 green
```
Also run the SP-C suggest tests (they call `_score` indirectly via accept): `-m pytest tests/test_substrate_suggest.py -q` (13 green). Both must be unchanged — beta defaults to 1.0.

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_tuner.py \
        packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_tuner.py
git commit -m "feat(erkgbench): precision-aware F-beta _score (env GOLDENGRAPH_SUBSTRATE_SCORE_BETA, default 1.0)"
```

---

### Task 2: `--score-beta` on the SP-C homograph runner

**Files:**
- Modify: `erkgbench/run_substrate_suggest.py`

- [ ] **Step 1: Add the arg + set the env** — in `main()`:

After `ap.add_argument("--out-md", ...)`:
```python
    ap.add_argument("--score-beta", type=float, default=0.5,
                    help="F-beta for the accept metric; <1 favors precision (the homograph win). "
                         "0.5 accepts the SP-C precision win the F1 default (1.0) hid.")
```
After `args = ap.parse_args()` (near the other `os.environ` sets, before `suggest_substrate_config`):
```python
    os.environ["GOLDENGRAPH_SUBSTRATE_SCORE_BETA"] = str(args.score_beta)
```

- [ ] **Step 2: Static-check** the runner still parses + imports box-safe:
```bash
cd /d/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$PWD:/d/show_case/gg-local-llm/packages/python/goldengraph" POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -c "import ast; ast.parse(open('erkgbench/run_substrate_suggest.py').read()); import erkgbench.run_substrate_suggest; print('ok')"
```
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/run_substrate_suggest.py
git commit -m "feat(erkgbench): --score-beta on the SP-C suggester runner (default 0.5, precision-favoring)"
```

---

### Task 3: Finish + Modal verification

- [ ] **Step 1: Full green** — the whole `test_substrate_tuner.py` (23) + `test_substrate_suggest.py` (13) box-safe. Confirm no regression.
- [ ] **Step 2: Lint** — `ruff check` the three files; fix findings.
- [ ] **Step 3: Modal verification** (needs Infisical Modal creds; see `feedback_infisical_usage`). Re-run the SP-C suggest smoke precision-favoring:
  ```
  modal run --detach scripts/distill/modal_bench.py --eval suggest --n 3 --spawn \
    --opts $'GOLDENGRAPH_LLM_SEED=42\nGOLDENGRAPH_SUBSTRATE_SCORE_BETA=0.5'
  ```
  Poll `results/suggest_3_goldengraph-qwen2.5-7b-instruct.md`. **Expected:** `accepted=True`, winner `name_ci_type` (+canon), the proposed config (P 0.815→0.892) now beats baseline on F_0.5. (The runner already sets `--score-beta 0.5` by default; the `--opts` env set is belt-and-suspenders / makes the beta explicit in the run record.)
  - **If still `accepted=False`:** read the actual P/R — if the recall cost is larger than the smoke's (build variance despite the reset), a lower beta (0.25) may be needed; record the actual numbers, don't force it.
- [ ] **Step 4: Verdict addendum** — append a short section to `docs/superpowers/reports/2026-07-02-suggester-smoke-verdict.md` (or a new `2026-07-02-precision-aware-score-verdict.md`) with the beta=0.5 result (accepted flips true; the precision win is now rewarded).
- [ ] **Step 5: PR + arm** — push, open PR base `main`, arm `--auto`, STOP.
- [ ] **Step 6: Memory** — append to `project_goldengraph_local_oss_llm_lane.md`: precision-aware F-beta shipped; SP-C precision win now accepted at beta<1.

---

## Notes for the implementer

- **Box-safe only.** Run `tests/test_substrate_tuner.py` + `tests/test_substrate_suggest.py`. No full suite.
- **`beta == 1.0` MUST use the stored `relational.f1`** (not the recomputed harmonic mean) so the default is bit-for-bit unchanged — this is what keeps every existing SP-B2/SP-C test green.
- **`import os` at module top is load-bearing** — without it `_score`'s env read (which runs on EVERY default call, since `beta is None` → env lookup) raises `NameError`. Don't rely on the function-local `os` in `_reset_llm_state`.
- **Env-test hygiene:** `test_score_beta_reads_env` restores the env var in a `finally` — a leak would flip the default beta for the other tests in the file and break the SP-B2 tests.
