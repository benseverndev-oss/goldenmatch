# Stage-2-A: MuSiQue Lever-Ranking Instrument — Design

**Status:** Approved (brainstorm), pending implementation plan.
**Date:** 2026-06-29
**Context:** goldengraph real-corpus (stage-2) quality. Follows the stage-1 honest-null
(open-vocab predicate clustering closed) and the support_recall wiring (#1295).

## Goal

Produce a **trustworthy, meaningful-N ranking** of the three real MuSiQue failure modes —
entity-extraction recall, multi-hop retrieval, synthesis — so the *next* sub-project targets
the dominant lever. This sub-project **measures and ranks; it adds no graph/retrieval/synthesis
capability.**

## Why this, not a feature build

The first real-corpus measurements reframed stage-2 twice, by measurement:

1. **Engineered win does not transfer.** MuSiQue N=12 (7B, open extraction): answer_match
   **0.083** vs engineered 0.60–0.67; support_recall **0.70**.
2. **Predicate-vocab discovery is not the lever.** Discovery config = 0.0 / support 0.66 —
   no help (slightly worse: relabeling noise on real prose's large vocab).
3. **Answer-representation (literal-attrs) is not the lever — measured-dead.** `LITERAL_ATTRS=1`:
   answer_match unchanged (0.083), support_recall **worse** (0.70 → 0.65). Per-question it
   *hurt* the cases it should help: `$72,641` went `in_ball=True` → unreachable; `the Politburo`
   went `in_graph=True` → `False` (extra attribute extraction perturbed extraction and dropped
   the entity).

What the N=12 localize trace actually shows: the EXTRACTION bucket is **not** "non-entity answers"
— it is mostly **entities the 7B never extracted from dense prose** (Lana Wood, Rooster Cogburn,
the Politburo). The failures spread roughly evenly across three real problems:

- **Entity-extraction recall** on dense real prose (`in_graph=False`).
- **Multi-hop retrieval / broken chains** (`in_graph=True`, `in_ball=False`).
- **Synthesis** (`in_ball=True`, wrong answer written).

At N=12 each bucket is 3–5 questions — too small to rank confidently, and the obvious cheap lever
(literal-attrs) is now measured-dead. So the correct next step is a fair-metric, meaningful-N
**ranking run**, not a speculative build.

## Architecture

Three small, independently-testable pieces.

### 1. Answer-normalization extension (`metrics.py`)

The existing `metrics._normalize` already lowercases, strips **all** punctuation (so `$` and `,`
are gone — `$72,641` ≡ `72641` *today*), strips articles, and collapses whitespace. The remaining
fairness gaps it does **not** handle:

- **Date word-order**: `11 February 1929` vs `February 11, 1929` vs `1929-02-11`.
- **Times**: `5am` vs `5 a.m.` vs `5 AM`.
- **Number-words**: `100` vs `one hundred`.

Add a layered canonicalization on top of `_normalize`, with **deliberately narrow** parse scope to
keep the false-positive surface small:

- **Dates → ISO `YYYY-MM-DD`** when a *full* day/month/year parses (so `11 February 1929`,
  `February 11, 1929`, and `1929-02-11` all canonicalize identically). A **bare year** (`1929`) is
  left as the year token — *not* expanded — so it is never forced to match a full date; the existing
  containment in `answer_match` already lets a bare-year gold match the year token inside a full date
  where that is genuinely correct, and we do not invent the reverse.
- **Times → a single canonical form** (`5am`, `5 a.m.`, `5 AM` → `5am`); hour[:minute] + am/pm only.
- **Number-words ↔ digits → small integer cardinals only**, via a **fixed lookup** (zero..twenty,
  thirty..hundred and the obvious tens). **No** compound parsing (`twenty-one`), **no** decimals
  (`1.5 million`), **no** large-magnitude words (`million`/`billion`), **no** ordinals. Anything
  outside the lookup falls through untouched.

**Fail-soft**: the date/time/number rules fire *only* on a successful parse within these narrow
bounds; any failure (or anything out of scope) falls through to the existing `_normalize` output, so
an un-parseable string canonicalizes exactly as today.

**Load-bearing coupling (the reason this is required, not cosmetic):** the localize buckets are
computed *via* `metrics.answer_match` (`harness.py:153` `in_graph`, `harness.py:155` `in_ball`).
Unfair normalization therefore **mis-categorizes the buckets** — a date answer present in the graph
but format-mismatched falsely reads `in_graph=False` and is counted as EXTRACTION when it is really
retrieval/synthesis. Fairer normalization is required for the ranking to be correct.

Pure functions, unit-tested, no LLM.

### 2. Bigger-N ranking run

MuSiQue **N≈50**, open extraction, `LITERAL_ATTRS` **off** (measured-dead), auto mode. The harness
already emits per-question `EXTRACTION / RETRIEVAL-BROKEN-CHAIN / SYNTHESIS`; aggregate into a
confident distribution. Run via the existing `scripts/distill/modal_bench.py --corpus musique`.

### 3. Verdict report

A committed markdown alongside the other bench reports: the fair-metric `answer_match`, the
**actual per-bucket counts** (the raw N per bucket, so the confidence claim is data-backed rather
than assuming an even three-way split), and a **ranked recommendation** for the next sub-project.
States confidence honestly (see Risks).

## Data flow

```
run_qa_e2e (--corpus musique, N≈50)
  → harness scores answer_match with the EXTENDED normalizer
  → localize categorizes each question (now fairly) into a bucket
  → aggregate bucket counts
  → verdict report (committed)
```

## Error handling

- Normalization is **fail-soft**: date/time/number-word rules fire only on a successful parse;
  any failure falls through to the existing `_normalize` output. No new exceptions on the scoring
  path.
- The Modal run uses the established detach + spawn + volume + Monitor pattern (box OOM-reaps the
  local CLI ~1 min in).

## Testing

- **Normalizer unit tests** (pure, box-safe, the TDD core):
  - *Should now match*: `11 February 1929` ≡ `February 11, 1929` ≡ `1929-02-11` (all → ISO);
    `5am` ≡ `5 a.m.`; `100` ≡ `one hundred`.
  - *Must still NOT match*: `1928` ≠ `1929`; `Lana Wood` ≠ `Natalie Wood`; `100` ≠ `1000`;
    a bare year `1929` ≠ a full date `11 February 1929` (gold-full direction: answering only the
    year is incomplete — the narrow rule must not invent this match).
  - *Out-of-scope falls through untouched*: `twenty-one`, `1.5 million`, `third` are left as-is
    (no compound/decimal/large-magnitude/ordinal parsing) — locks the narrow number-word boundary.
  - *Fall-through*: `"the Politburo"` normalizes exactly as today (regression-locked against
    the current `_normalize`).
- **Bucket-coupling test**: a graph containing `February 11, 1929` reads `in_graph=True` for a
  gold of `11 February 1929` — proving the normalization flows into localize categorization, not
  just the headline.
- **Engineered no-regression**: a small engineered e2e must hold its answer_match — the fairness
  change is additive, not a silent rescore.
- **Integration validation** = the N≈50 run itself.

## Scope / YAGNI

- **No LLM-judge** (deferred; deterministic normalization first).
- **No literal-attrs** (measured-dead).
- **No graph/retrieval/synthesis feature work** — this sub-project only measures and ranks. The
  lever-fix is the next sub-project, scoped from this verdict.
- **Normalization stays minimal**: dates, times, number-words only. No general semantic equivalence
  (`NYC` ≡ `New York City`), no unit conversion — real but rare on MuSiQue; add only if the verdict
  shows they are material.
- **N≈50, not 100**: balances signal against the ~60-min Modal cap (the literal-attrs run hit
  ~36 min at N=12; open extraction is lighter, but N=50 of real prose is the practical ceiling for
  one job).

## Risks

- **Sample size**: even N≈50 gives ~15–25 questions per bucket — enough to *rank* the levers, not
  to split hairs between close seconds. The verdict states confidence, does not over-claim.
- **Normalization over-reach**: an over-eager date/number parser could make wrong answers match
  (false positives). Mitigated by the explicit non-equivalence guard tests (`1928 ≠ 1929`,
  `100 ≠ 1000`) and the fail-soft fall-through.
- **7B non-determinism**: live-7B extraction varies run-to-run (same caveat as every live run);
  the bucket *distribution* at N≈50 is stable enough to rank even if individual questions flip.

## Files

- Modify: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/metrics.py`
  (date/time/number-word canonicalization layered on `_normalize`).
- Create: a normalizer unit-test file under the bench `tests/`.
- Create: the verdict report (committed markdown).
- Validation: existing `scripts/distill/modal_bench.py` (`--corpus musique`, no code change).
