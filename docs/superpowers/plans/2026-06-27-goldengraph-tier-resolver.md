# GoldenGraph slice 4b (tier -> resolver) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make 4a's `UnifiedPlan` executable: a `resolver_for_tier(tier) -> Resolver` factory + `plan_resolver(workload) -> (UnifiedPlan, Resolver)` so the chosen tier selects the resolver `ingest` uses, with a free deterministic gate that FUZZY out-resolves EXACT on the engineered universe.

**Architecture:** Refactor `goldengraph/resolve.py` into `_fuzzy_resolve(use_context)` + `_exact_resolve` (exact `(name,typ)` grouping; `resolve` stays the `use_context=True` default). Add `resolver_for_tier`/`plan_resolver` to `goldengraph/unified.py`. A new `erkgbench/qa_e2e/tier_eval.py` gate measures resolution-recall (same-concept distinct-surface merge fraction) per tier over `dataset/concepts.jsonl` and asserts `FUZZY_recall - EXACT_recall >= MARGIN`.

**Tech Stack:** Python 3.12, pytest, ruff. STACKED on slice 4a (`feat/goldengraph-unified-planner`, PR #1286). Fully deterministic (no opt-in lane). The EXACT/FUZZY resolvers + the recall metric need `goldenmatch` (for `_record_key` + `dedupe_df`) -> run in the goldengraph-pipeline lane (which installs goldenmatch); gate-shape tests on a hand-built result are goldenmatch-free.

**Spec:** `docs/superpowers/specs/2026-06-27-goldengraph-tier-resolver-design.md`

---

## Conventions for every task

- Worktree: `D:\show_case\gg-tier-resolver`, branch `feat/goldengraph-tier-resolver` (stacked on 4a).
- Bench dir (where `erkgbench/...` paths are rooted) = `packages/python/goldenmatch/benchmarks/er-kg-bench`.
- `PYEXE="D:/show_case/goldenmatch/.venv/Scripts/python.exe"`. ALWAYS set `POLARS_SKIP_CPU_CHECK=1` (goldenmatch imports polars -> WMI hang without it).
- goldengraph tests: `cd D:/show_case/gg-tier-resolver && POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="packages/python/goldengraph" "$PYEXE" -m pytest packages/python/goldengraph/tests/test_resolve_tiers.py -v`. (These import goldenmatch via `_record_key`; the local `.venv` has it. If a local goldenmatch import hangs/zombies, rely on the goldengraph-pipeline lane -- but it should work with POLARS_SKIP_CPU_CHECK=1.)
- er-kg-bench wheel-free gate-shape: from the bench dir, `PYTHONPATH="$(pwd);D:/show_case/gg-tier-resolver/packages/python/goldengraph"`.
- The resolution-recall metric needs goldenmatch dedupe_df -> verified in the goldengraph-pipeline lane.
- Ruff-clean per commit.
- Verified reuse facts: `resolve.py` -- `resolve(mentions)` body (polars frame {name,type,[context]} -> `gm.dedupe_df` -> groups via `result.clusters`); `ResolvedEntity(local_id, canonical_name, typ, surface_names, record_keys, member_idx)`; `_record_key(name, typ)` (imports goldenmatch). `extract.Mention(name, typ, context="")`. `ingest.Resolver = Callable[[list[Mention]], list[ResolvedEntity]]`. `unified.py` (4a): `ResolutionTier` StrEnum (EXACT/FUZZY/FUZZY_CONTEXT), `plan_resolution`, `profile_workload`, `UnifiedPlan`. `dataset/concepts_loader.load_concepts(path) -> list[Concept(concept, canonical_id, entity_type, context, variants)]`, `Variant(surface, failure_class)`. Concepts path = `bench_root / "dataset" / "concepts.jsonl"` (bench_root via `Path(__file__).resolve().parents[2]` from a qa_e2e module -- mirror `engineered._load_entities`).
- Commit footer:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Sc5aGSaVBTWBWpytNH99QE
  ```

## File structure

- Modify `packages/python/goldengraph/goldengraph/resolve.py` -- `_fuzzy_resolve(use_context)`, `_exact_resolve`, `resolve` delegates.
- Modify `packages/python/goldengraph/goldengraph/unified.py` -- `resolver_for_tier`, `plan_resolver`.
- Create `packages/python/goldengraph/tests/test_resolve_tiers.py` -- EXACT grouping + resolver_for_tier + plan_resolver.
- Create `erkgbench/qa_e2e/tier_eval.py` -- `resolution_recall(tier)` + `TierResult` + gate + render.
- Create `erkgbench/qa_e2e/run_tier_eval.py` -- CLI -> TIER.md.
- Create `erkgbench/qa_e2e/.../tests/test_qa_tier.py` -- wheel-free gate-shape.
- Modify `.github/workflows/goldengraph-pipeline.yml` -- tier gate step + upload (after "Upload UNIFIED.md").
- Modify `.github/workflows/bench-er-kg.yml` -- add `tests/test_qa_tier.py` to the pure-Python list.

---

## Task 1: resolve.py tier refactor (`_fuzzy_resolve` + `_exact_resolve`)

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/resolve.py`
- Test: `packages/python/goldengraph/tests/test_resolve_tiers.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/python/goldengraph/tests/test_resolve_tiers.py
"""Slice 4b tier resolvers -- EXACT grouping + resolver factory (needs goldenmatch for _record_key)."""
from __future__ import annotations

from goldengraph.extract import Mention
from goldengraph.resolve import _exact_resolve


def test_exact_resolve_merges_identical_separates_variants():
    ms = [Mention("Apple", "org"), Mention("Apple", "org"), Mention("Apple Inc", "org")]
    ents = _exact_resolve(ms)
    # exact (name,typ): {Apple:[0,1]} + {Apple Inc:[2]} -> 2 entities; the two "Apple" merge
    assert len(ents) == 2
    by_members = {tuple(e.member_idx): e for e in ents}
    assert (0, 1) in by_members and by_members[(0, 1)].surface_names == ["Apple"]
    assert (2,) in by_members and by_members[(2,)].surface_names == ["Apple Inc"]


def test_exact_resolve_empty():
    assert _exact_resolve([]) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="packages/python/goldengraph" "$PYEXE" -m pytest packages/python/goldengraph/tests/test_resolve_tiers.py -v`
Expected: FAIL (`ImportError: cannot import name '_exact_resolve'`).

- [ ] **Step 3: Write minimal implementation** (in `resolve.py`)

Rename the current `resolve` body to `_fuzzy_resolve(mentions, *, use_context: bool)`, changing ONLY the context-column guard, and make `resolve` delegate; add `_exact_resolve`:

```python
def _fuzzy_resolve(mentions: list[Mention], *, use_context: bool) -> list[ResolvedEntity]:
    """<existing resolve() docstring/body> -- the context column is included only when
    use_context AND any mention carries context."""
    if not mentions:
        return []
    import goldenmatch as gm
    import polars as pl

    cols = {"name": [m.name for m in mentions], "type": [m.typ for m in mentions]}
    if use_context and any(m.context for m in mentions):
        cols["context"] = [m.context for m in mentions]
    df = pl.DataFrame(cols)
    result = gm.dedupe_df(df)
    # ... the EXISTING grouping + ResolvedEntity construction, unchanged ...
    return out


def _exact_resolve(mentions: list[Mention]) -> list[ResolvedEntity]:
    """Group mentions by EXACT (name, typ) -- distinct surfaces never merge. Deterministic (no
    dedupe_df). record_keys via the SAME _record_key as the fuzzy path (cross-doc parity)."""
    if not mentions:
        return []
    groups: dict[tuple[str, str], list[int]] = {}
    for i, m in enumerate(mentions):
        groups.setdefault((m.name, m.typ), []).append(i)
    out: list[ResolvedEntity] = []
    for local_id, grp in enumerate(sorted(groups.values(), key=min)):
        rep = min(grp, key=lambda i: (-len(mentions[i].name), i))
        out.append(
            ResolvedEntity(
                local_id=local_id,
                canonical_name=mentions[rep].name,
                typ=mentions[rep].typ,
                surface_names=sorted({mentions[i].name for i in grp}),
                record_keys=sorted({_record_key(mentions[i].name, mentions[i].typ) for i in grp}),
                member_idx=sorted(grp),
            )
        )
    return out


def resolve(mentions: list[Mention]) -> list[ResolvedEntity]:
    """Backward-compatible default: fuzzy name+type+context (FUZZY_CONTEXT)."""
    return _fuzzy_resolve(mentions, use_context=True)
```

VERIFY: with `use_context=True` the guard `use_context and any(m.context)` == the old `any(m.context)`, so `resolve` is byte-identical to before. Run the existing goldengraph resolve tests (e.g. `test_retrieval.py` / `test_ingest_cross_doc_link.py`) to confirm no regression.

- [ ] **Step 4: Run to verify it passes**

Run: `... pytest packages/python/goldengraph/tests/test_resolve_tiers.py -v` -> PASS.
Run the existing resolve-dependent tests: `... pytest packages/python/goldengraph/tests/test_ingest_cross_doc_link.py -v` -> still PASS (regression check). `ruff check resolve.py` -> clean.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/resolve.py packages/python/goldengraph/tests/test_resolve_tiers.py
git commit -m "feat(goldengraph): split resolve into _fuzzy_resolve(use_context) + _exact_resolve"
```

---

## Task 2: resolver_for_tier + plan_resolver (`unified.py`)

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/unified.py`
- Test: `packages/python/goldengraph/tests/test_resolve_tiers.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_resolve_tiers.py
from goldengraph import unified
from goldengraph.resolve import _exact_resolve as _exact

_PREDS = {"works_at", "located_in", "acquired", "authored", "part_of"}


def test_resolver_for_tier_returns_distinct():
    assert unified.resolver_for_tier(unified.ResolutionTier.EXACT) is _exact
    f = unified.resolver_for_tier(unified.ResolutionTier.FUZZY)
    fc = unified.resolver_for_tier(unified.ResolutionTier.FUZZY_CONTEXT)
    assert callable(f) and callable(fc) and f is not fc


def test_exact_tier_resolver_groups_exactly():
    r = unified.resolver_for_tier(unified.ResolutionTier.EXACT)
    ents = r([Mention("Apple", "org"), Mention("Apple Inc", "org")])
    assert len(ents) == 2  # distinct surfaces stay separate under EXACT


def test_plan_resolver_capability_returns_fuzzy_resolver():
    plan, resolver = unified.plan_resolver(["List all entities that Metaphone works at."], predicates=_PREDS)
    assert plan.resolution_tier is unified.ResolutionTier.FUZZY
    assert callable(resolver)


def test_plan_resolver_lookup_returns_exact_resolver():
    plan, resolver = unified.plan_resolver(["what is Soundex?"], predicates=_PREDS)
    assert plan.resolution_tier is unified.ResolutionTier.EXACT
    assert resolver is _exact
```

- [ ] **Step 2: Run to verify it fails**

Run: `... pytest packages/python/goldengraph/tests/test_resolve_tiers.py -k "resolver_for_tier or plan_resolver or exact_tier" -v`
Expected: FAIL (`AttributeError: resolver_for_tier`).

- [ ] **Step 3: Write minimal implementation** (append to `unified.py`)

```python
def resolver_for_tier(tier: ResolutionTier):
    """Map a ResolutionTier to a Resolver (the ingest `resolver=` callable)."""
    from .resolve import _exact_resolve, _fuzzy_resolve

    if tier is ResolutionTier.EXACT:
        return _exact_resolve
    if tier is ResolutionTier.FUZZY:
        return lambda ms: _fuzzy_resolve(ms, use_context=False)
    return lambda ms: _fuzzy_resolve(ms, use_context=True)  # FUZZY_CONTEXT


def plan_resolver(queries, *, predicates=None, llm_classifier=None):
    """The executable join: 4a's plan -> the resolver ingest should use.
    plan_resolution emits EXACT or FUZZY (FUZZY == the slice-D-measured tier); FUZZY_CONTEXT is
    reachable via resolver_for_tier but not auto-selected (its win on this corpus isn't measured)."""
    plan = plan_resolution(profile_workload(queries, predicates=predicates, llm_classifier=llm_classifier))
    return plan, resolver_for_tier(plan.resolution_tier)
```

- [ ] **Step 4: Run to verify it passes**

Run: `... pytest packages/python/goldengraph/tests/test_resolve_tiers.py -v` -> PASS. `ruff check unified.py` -> clean.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/unified.py packages/python/goldengraph/tests/test_resolve_tiers.py
git commit -m "feat(goldengraph): resolver_for_tier + plan_resolver (executable join)"
```

---

## Task 3: resolution-recall gate (`tier_eval.py`) + CLI

**Files:**
- Create: `erkgbench/qa_e2e/tier_eval.py`, `erkgbench/qa_e2e/run_tier_eval.py`
- Test: `tests/test_qa_tier.py`

- [ ] **Step 1: Write the failing wheel-free gate-shape test**

```python
# tests/test_qa_tier.py
"""Slice 4b tier gate -- wheel-free gate shape."""
from __future__ import annotations

from erkgbench.qa_e2e import tier_eval as te


def test_gate_passes_on_good_result():
    res = te.TierResult(fuzzy_recall=0.40, exact_recall=0.0, n_pairs=300)
    assert te.gate_exit_code(res) == 0


def test_gate_fails_when_fuzzy_not_better():
    res = te.TierResult(fuzzy_recall=0.03, exact_recall=0.0, n_pairs=300)  # gap < MARGIN
    assert te.gate_exit_code(res) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd <bench dir> && PYTHONPATH="$(pwd);<goldengraph>" POLARS_SKIP_CPU_CHECK=1 "$PYEXE" -m pytest tests/test_qa_tier.py -v`
Expected: FAIL (`ModuleNotFoundError: tier_eval`).

- [ ] **Step 3: Write minimal implementation**

```python
# erkgbench/qa_e2e/tier_eval.py
"""Slice 4b gate: resolution-recall per tier over the engineered universe. Feed each concept's
DISTINCT surfaces (deduped -- the dataset plants cross_document_exact byte-identical variants) as
Mentions to resolver_for_tier(tier); measure the fraction of same-concept DISTINCT-surface PAIRS
merged into one resolved group. FUZZY merges string-close variants EXACT cannot. Needs goldenmatch
(dedupe_df via the FUZZY resolver) -> goldengraph-pipeline lane.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from goldengraph.unified import ResolutionTier, resolver_for_tier

MARGIN = 0.10  # FUZZY_recall - EXACT_recall must be >= this (frozen from the measured run)


def _concept_surface_mentions():
    """Returns (mentions, gold) where gold[i] = canonical_id of mention i. One Mention per DISTINCT
    surface per concept (typ = entity_type)."""
    from dataset.concepts_loader import load_concepts  # type: ignore

    from goldengraph.extract import Mention

    bench_root = Path(__file__).resolve().parents[2]
    concepts = load_concepts(bench_root / "dataset" / "concepts.jsonl")
    mentions, gold = [], []
    for c in concepts:
        surfaces = list(dict.fromkeys([c.concept] + [v.surface for v in c.variants]))
        for s in surfaces:
            mentions.append(Mention(s, c.entity_type))
            gold.append(c.canonical_id)
    return mentions, gold


def resolution_recall(tier: ResolutionTier) -> tuple[float, int]:
    """Fraction of same-concept distinct-surface pairs the tier's resolver places in one group.
    Returns (recall, n_pairs)."""
    mentions, gold = _concept_surface_mentions()
    ents = resolver_for_tier(tier)(mentions)
    group_of: dict[int, int] = {}
    for g, e in enumerate(ents):
        for i in e.member_idx:
            group_of[i] = g
    # same-concept pairs
    by_concept: dict[str, list[int]] = {}
    for i, c in enumerate(gold):
        by_concept.setdefault(c, []).append(i)
    merged = total = 0
    for idxs in by_concept.values():
        for a, b in combinations(idxs, 2):
            total += 1
            if group_of.get(a) == group_of.get(b):
                merged += 1
    return (merged / total if total else 0.0), total


@dataclass
class TierResult:
    fuzzy_recall: float
    exact_recall: float
    n_pairs: int


def evaluate_assertions(res: TierResult):
    gap = res.fuzzy_recall - res.exact_recall
    return [
        (f"FUZZY out-resolves EXACT on the universe (fuzzy {res.fuzzy_recall:.3f} - exact {res.exact_recall:.3f} = {gap:.3f} >= {MARGIN}; {res.n_pairs} pairs)",
         gap >= MARGIN, True),
    ]


def gate_exit_code(res: TierResult) -> int:
    return 1 if any(h and not ok for _l, ok, h in evaluate_assertions(res)) else 0


def run_tier_deterministic() -> TierResult:
    fr, n = resolution_recall(ResolutionTier.FUZZY)
    er, _ = resolution_recall(ResolutionTier.EXACT)
    return TierResult(fuzzy_recall=fr, exact_recall=er, n_pairs=n)


def render_tier_md(res: TierResult) -> str:
    lines = [
        "# GoldenGraph tier-resolver gate (slice 4b, no LLM)",
        "",
        "4b makes 4a's UnifiedPlan executable: resolution_tier -> the resolver ingest uses. This gate",
        "proves the tiers resolve DIFFERENTLY -- FUZZY merges variant surfaces EXACT cannot (same-",
        "concept distinct-surface merge recall on the engineered universe). The build->capability link",
        "is slice-D's dial scorecard + 4a (reused).",
        "",
        f"- FUZZY resolution-recall: {res.fuzzy_recall:.3f}",
        f"- EXACT resolution-recall: {res.exact_recall:.3f}  (distinct surfaces never exact-merge -> ~0)",
        f"- same-concept distinct-surface pairs: {res.n_pairs}",
        "",
        "## verdicts",
        "",
    ]
    for label, ok, _h in evaluate_assertions(res):
        lines.append(f"- [{'PASS' if ok else 'FAIL'}] {label}")
    return "\n".join(lines) + "\n"
```

```python
# erkgbench/qa_e2e/run_tier_eval.py
"""CLI: slice 4b tier-resolver gate; write TIER.md, exit non-zero on a HARD failure. Needs goldenmatch."""
from __future__ import annotations

import argparse
import sys

from .tier_eval import gate_exit_code, render_tier_md, run_tier_deterministic


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GoldenGraph tier-resolver gate")
    ap.add_argument("--out-md", default="TIER.md")
    ap.parse_args(argv)
    res = run_tier_deterministic()
    md = render_tier_md(res)
    with open(res and "TIER.md", "w", encoding="utf-8") as fh:  # NOTE: use args.out_md (see fix below)
        fh.write(md)
    sys.stdout.write(md)
    return gate_exit_code(res)
```

FIX the CLI `main` to use `args = ap.parse_args(argv)` and `open(args.out_md, ...)` (the snippet above
has a deliberate typo placeholder; write it correctly: parse args, `with open(args.out_md, "w", ...)`).

- [ ] **Step 4: Run the wheel-free gate-shape test + ruff + MEASURE recall**

Run: `... pytest tests/test_qa_tier.py -v` (gate-shape; PASS). `ruff check tier_eval.py run_tier_eval.py`.
Then MEASURE on the real universe (needs goldenmatch; local with POLARS_SKIP_CPU_CHECK=1, else read from
the first CI TIER.md): `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="$(pwd);<goldengraph>" "$PYEXE" -c "from erkgbench.qa_e2e.tier_eval import run_tier_deterministic as r; print(r())"`. Confirm `fuzzy_recall - exact_recall >= 0.10` and freeze `MARGIN` below the measured gap with headroom. **If FUZZY ~= EXACT (gap < ~0.05), STOP and surface to Ben** -- the tier distinction is too weak on this corpus to gate (per the spec's STOP clause).

- [ ] **Step 5: Commit**

```bash
git add erkgbench/qa_e2e/tier_eval.py erkgbench/qa_e2e/run_tier_eval.py tests/test_qa_tier.py
git commit -m "feat(er-kg-bench): tier-resolver resolution-recall gate (FUZZY > EXACT on the universe)"
```

---

## Task 4: CI wiring

**Files:**
- Modify: `.github/workflows/goldengraph-pipeline.yml`, `.github/workflows/bench-er-kg.yml`

- [ ] **Step 1: Add the pipeline gate step** after the **"Upload UNIFIED.md"** step:

```yaml
      - name: Tier-resolver gate (deterministic, key-free)
        # Slice 4b: 4a's UnifiedPlan made executable (resolution_tier -> the resolver ingest uses).
        # Proves the tiers resolve differently -- FUZZY merges variant surfaces EXACT cannot
        # (same-concept distinct-surface merge recall on the engineered universe). Needs goldenmatch.
        working-directory: packages/python/goldenmatch/benchmarks/er-kg-bench
        env:
          POLARS_SKIP_CPU_CHECK: "1"
        run: |
          python -m pytest tests/test_qa_tier.py -v
          python -m erkgbench.qa_e2e.run_tier_eval --out-md TIER.md

      - name: Upload TIER.md
        if: ${{ always() }}
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v4
        with:
          name: goldengraph-tier
          path: packages/python/goldenmatch/benchmarks/er-kg-bench/TIER.md
          if-no-files-found: ignore
```

- [ ] **Step 2: Add the wheel-free gate-shape test to bench-er-kg.yml** -- append `tests/test_qa_tier.py` to the pure-Python pytest list (it currently ends with `tests/test_qa_unified.py -v"`).

- [ ] **Step 3: Validate yaml + commit**

```bash
python -c "import yaml; [yaml.safe_load(open(f)) for f in ['.github/workflows/goldengraph-pipeline.yml','.github/workflows/bench-er-kg.yml']]; print('yaml ok')"
git add .github/workflows/goldengraph-pipeline.yml .github/workflows/bench-er-kg.yml
git commit -m "ci(er-kg-bench): wire slice 4b tier-resolver gate"
```

---

## Final verification (before finishing the branch)

- [ ] `... pytest packages/python/goldengraph/tests/test_resolve_tiers.py -v` -> PASS (goldenmatch present).
- [ ] er-kg-bench `tests/test_qa_tier.py` -> PASS (wheel-free gate-shape).
- [ ] Existing resolve-dependent goldengraph tests still PASS (regression: `resolve` unchanged).
- [ ] `ruff check` on all modified/created .py -> clean.
- [ ] Use superpowers:finishing-a-development-branch: push (benzsevern account), open PR. **Base:
  `feat/goldengraph-unified-planner` if #1286 is still open; if #1286 merged first, rebase
  `--onto origin/main <slice-4a-tip>` and target main** (slice-4a tip = the commit this branch started
  at; `git log --oneline` to find it). Watch the `goldengraph-pipeline` tier gate green BEFORE arming
  `gh pr merge --auto`. Freeze `MARGIN` from the measured TIER.md if needed; record memory.
- [ ] If FUZZY does NOT out-recall EXACT by `MARGIN`, surface to Ben (the tier distinction is too weak
  on this corpus) -- do not loosen the gate.

## Known unknowns to resolve during implementation (call out, don't guess)

- The MEASURED FUZZY resolution-recall on the deduped universe (Task 3 Step 4) -- many variants are
  abbreviations (`LSH`/`WCC`/`EM`) name-only fuzzy won't merge, so FUZZY recall may be modest. Freeze
  `MARGIN` below the measured gap; if the gap is < ~0.05, STOP (the corpus can't gate this tier
  distinction) and surface to Ben.
- Confirm the existing `resolve` callers (ingest_corpus, test_ingest_cross_doc_link, test_retrieval)
  still pass after the `_fuzzy_resolve(use_context=True)` delegation (byte-identical default).
- Whether local goldenmatch import (via `_record_key`) runs cleanly under POLARS_SKIP_CPU_CHECK=1; if
  it hangs/zombies on this Windows box, run only the gate-shape test locally and rely on the
  goldengraph-pipeline lane for the resolver-invoking tests.
- `run_tier_eval.py` `main` -- write it correctly (parse args; `open(args.out_md, ...)`); the plan
  snippet flags a placeholder typo to fix.
