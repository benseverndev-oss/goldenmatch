# SP-C Beta-Frontier Report Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `suggest_substrate_config` report the accept decision across a small beta sweep so the caller sees the precision/recall frontier and picks beta — a free recompute from the two scorecards already in hand.

**Architecture:** One pure helper `_accept_frontier` (recomputes `_score(prop, beta) > _score(base, beta)` per beta), one additive `SuggestResult.accept_frontier` field, computed in `suggest_substrate_config` from the existing `base_sc`/`prop_sc`. The run's `accepted`/`config` (active-beta decision) are untouched. Runner prints the frontier + P/R.

**Tech Stack:** Python stdlib, pytest. No Modal (the beta=1.0 + beta=0.5 SP-C runs already *are* the frontier).

**Spec:** `docs/superpowers/specs/2026-07-02-suggest-beta-frontier-design.md`

**Branch:** `feat/suggest-beta-frontier` (off `origin/main`; SP-C + F-beta both merged).

---

## Files

- **Modify** `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_suggest.py` — add `_accept_frontier`; add `accept_frontier` field to `SuggestResult`; compute + pass it in `suggest_substrate_config`.
- **Modify** `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/run_substrate_suggest.py` — print the frontier + baseline/proposed relational P/R (stdout + md).
- **Modify (tests)** `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_suggest.py` — append frontier tests.

## Test runner (box-safe)

```bash
cd /d/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$PWD:/d/show_case/gg-local-llm/packages/python/goldengraph" PYTHONIOENCODING=utf-8 PYTHONUTF8=1 \
  POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_substrate_suggest.py -q
```

## Ground-truth facts (verified)

- `SuggestResult` is a frozen dataclass with 5 no-default fields: `config, flags, accepted, baseline_scorecard, proposed_scorecard`. The ONLY constructor call is `SuggestResult(winner, flags, accepted, base_sc, prop_sc)` (positional, substrate_suggest.py:118). No other `SuggestResult(...)` call sites in source or tests (tests build via `suggest_substrate_config`).
- `_score(scorecard, *, beta=None)` (keyword-only beta) is imported from `substrate_tuner` — `_score(sc, beta=b)` is valid. On presence=None scorecards `_score` is relational-only (the test `_rel` helper builds presence=None).
- Runner: `b, p = res.baseline_scorecard, res.proposed_scorecard` (line 47); `[suggest]` print (48-50); md block (51-57).
- **Frontier arithmetic (reviewer-verified):** base P=0.8153/R=0.672/f1=0.7368, prop P=0.9323/R=0.545/f1=0.6885 → `{1.0: False, 0.5: True, 0.25: True}` (F_0.5: base 0.782, prop 0.816; F_0.25: base 0.805, prop 0.895).

---

### Task 1: `_accept_frontier` + `SuggestResult.accept_frontier` + wire

**Files:**
- Modify: `erkgbench/substrate_suggest.py`
- Test: `tests/test_substrate_suggest.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_substrate_suggest.py`:

```python
# --- SP-C beta-frontier report ------------------------------------------------------------------------
def _relsc(p, r, f1):
    """A presence=None scorecard with explicit relational P/R/F1 (so _score is relational-only)."""
    return {"presence": None, "relational": {"f1": f1, "recall": r, "precision": p},
            "connectivity": {"coverage": None, "f1": None, "edge_recall": 0.9},
            "coherence": {"components": 1, "largest_fraction": 1.0}}


def test_accept_frontier_flips_at_beta():
    base = _relsc(0.8153, 0.672, 0.7368)     # SP-C smoke baseline
    prop = _relsc(0.9323, 0.545, 0.6885)     # proposed homograph-safe config (beta=0.5 run)
    assert ss._accept_frontier(base, prop) == {1.0: False, 0.5: True, 0.25: True}


def test_accept_frontier_all_true_when_proposed_dominates():
    base = _relsc(0.6, 0.6, 0.6)
    prop = _relsc(0.9, 0.9, 0.9)             # better on both P and R
    assert set(ss._accept_frontier(base, prop).values()) == {True}


def test_accept_frontier_all_false_when_baseline_dominates():
    base = _relsc(0.9, 0.9, 0.9)
    prop = _relsc(0.6, 0.6, 0.6)             # worse on both
    assert set(ss._accept_frontier(base, prop).values()) == {False}


def test_suggest_result_carries_frontier():
    # frontier is ADDITIVE: accepted/config unchanged, accept_frontier == _accept_frontier(base, prop)
    docs = [_Doc("a"), _Doc("b")]
    res = ss.suggest_substrate_config(
        docs, gold=[], qid_aliases=None, profile=_short_profile(),
        build_and_score=_bykey(0.40, 0.70), chat=_fake_chat_homograph)
    # at the default beta (1.0) the proposed (name_ci_type) scored 0.70 > baseline 0.40 -> accepted
    assert res.accepted is True and res.config.xdoc_key == "name_ci_type"
    assert res.accept_frontier == ss._accept_frontier(res.baseline_scorecard, res.proposed_scorecard)
```

(`_Doc`, `_short_profile`, `_bykey`, `_fake_chat_homograph` already exist in the file from the SP-C tests.)

- [ ] **Step 2: Run to verify it fails** — box-safe `-k "accept_frontier or carries_frontier"`. Expected: FAIL — no `_accept_frontier` / no `accept_frontier` field.

- [ ] **Step 3: Implement** in `substrate_suggest.py`:

Add the helper (after `propose_corpus_flags`, before `SuggestResult`):
```python
def _accept_frontier(base_sc, prop_sc, betas=(1.0, 0.5, 0.25)) -> dict:
    """Accept decision (proposed beats baseline) at each beta, recomputed from the two scorecards
    already in hand (no rebuild). beta<1 favors precision -> shows where a precision-improving-but-
    F1-losing config flips to accepted. See the beta-frontier design doc."""
    return {b: _score(prop_sc, beta=b) > _score(base_sc, beta=b) for b in betas}
```

Add the field to `SuggestResult` (6th, no default — all-non-default ordering preserved):
```python
@dataclass(frozen=True)
class SuggestResult:
    config: SubstrateConfig
    flags: CorpusFlags
    accepted: bool
    baseline_scorecard: dict
    proposed_scorecard: dict
    accept_frontier: dict
```

In `suggest_substrate_config`, compute the frontier and pass it (keyword) to the constructor:
```python
    accepted = _score(prop_sc) > _score(base_sc)
    frontier = _accept_frontier(base_sc, prop_sc)
    winner = proposed if accepted else baseline
    if accepted and flags.expect_homographs and flags.entity_type_vocab:
        winner = replace(winner, entity_type_vocab=flags.entity_type_vocab)
    return SuggestResult(winner, flags, accepted, base_sc, prop_sc, accept_frontier=frontier)
```

- [ ] **Step 4: Run to verify passes** — box-safe `-k "accept_frontier or carries_frontier"`. Expected: PASS (4).

- [ ] **Step 5: Run the WHOLE SP-C test file** (existing 13 + F-beta-adjacent + 4 new must all be green — the change is additive):
```bash
cd /d/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$PWD:/d/show_case/gg-local-llm/packages/python/goldengraph" PYTHONIOENCODING=utf-8 PYTHONUTF8=1 \
  POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 /d/show_case/goldenmatch/.venv/Scripts/python.exe \
  -m pytest tests/test_substrate_suggest.py -q      # 13 + 4 = 17 green
```

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_suggest.py \
        packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_suggest.py
git commit -m "feat(erkgbench): SP-C accept_frontier (beta sweep, free recompute; accepted/config unchanged)"
```

---

### Task 2: Runner prints the frontier + P/R

**Files:**
- Modify: `erkgbench/run_substrate_suggest.py` (the print + md block, ~lines 47-57)

- [ ] **Step 1: Add the frontier line to stdout + md**

After `b, p = res.baseline_scorecard, res.proposed_scorecard` (line 47), the print (48-50) becomes:
```python
    print(f"[suggest] accepted={res.accepted} flags={res.flags} "
          f"baseline_F1={b['relational']['f1']:.4f} proposed_F1={p['relational']['f1']:.4f} "
          f"winner_xdoc={res.config.xdoc_key} canon={res.config.entity_type_canon}", flush=True)
    print(f"[suggest] accept_frontier={res.accept_frontier} | "
          f"baseline P/R={b['relational']['precision']:.4f}/{b['relational']['recall']:.4f} "
          f"proposed P/R={p['relational']['precision']:.4f}/{p['relational']['recall']:.4f} "
          f"-> set GOLDENGRAPH_SUBSTRATE_SCORE_BETA to the lowest beta where accept flips True",
          flush=True)
```
And add a frontier line to the `md` block (after the `- winner:` line):
```python
        f"- accept_frontier: `{res.accept_frontier}` (beta<1 favors precision)\n"
        f"- baseline P/R: {b['relational']['precision']:.4f} / {b['relational']['recall']:.4f}  |  "
        f"proposed P/R: {p['relational']['precision']:.4f} / {p['relational']['recall']:.4f}\n"
```

- [ ] **Step 2: Static-check** — box-safe parse + import:
```bash
cd /d/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$PWD:/d/show_case/gg-local-llm/packages/python/goldengraph" POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -c "import ast; ast.parse(open('erkgbench/run_substrate_suggest.py').read()); import erkgbench.run_substrate_suggest; print('ok')"
```
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/run_substrate_suggest.py
git commit -m "feat(erkgbench): suggester runner prints the beta accept_frontier + P/R (pick beta from the frontier)"
```

---

### Task 3: Finish

- [ ] **Step 1: Full green** — `test_substrate_suggest.py` (17) + `test_substrate_tuner.py` (23, the F-beta `_score` is unchanged) box-safe. Confirm no regression.
- [ ] **Step 2: Lint** — `ruff check` the three files; fix findings.
- [ ] **Step 3: PR + arm** — push, open PR base `main`, arm `--auto`, STOP. (No Modal — the frontier is a pure recompute; the beta=1.0/0.5 SP-C runs already demonstrate `{1.0: False, 0.5: True}`.)
- [ ] **Step 4: Memory** — append to `project_goldengraph_local_oss_llm_lane.md`: the auto-beta follow-on shipped as the beta-frontier report (both SP-C follow-ons now closed).

---

## Notes for the implementer

- **Box-safe only.** `test_substrate_suggest.py` + `test_substrate_tuner.py`. No Modal.
- **Additive only.** `accepted`/`config` behavior is unchanged; `accept_frontier` is informational. `test_suggest_result_carries_frontier` is the regression guard.
- **`_relsc` builds presence=None** so `_score` is relational-only and the P/R-only frontier numbers reproduce exactly (a differing presence delta would shift the bool, per the spec note).
- **Float keys 1.0/0.5/0.25** are exact in binary64 — no dict-key collision.
