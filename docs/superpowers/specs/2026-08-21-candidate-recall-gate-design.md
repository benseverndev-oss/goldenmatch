# Candidate-recall gate for the suggest-quality scorecard

**Status:** design, not implemented.
**Motivates:** #2633 (candidate-set ceiling), #2635 (vendoring), the #1316 learned-blocking cliff.

## The problem

The suggest-quality scorecard gates `rank_corr`, `suggester_prec` and
`convergence_final_f1`. All three are **downstream of blocking**. None answers
the question that decides how much recall is reachable at all:

> what fraction of true pairs did blocking actually generate?

That gap is why four independent candidate-generation defects survived:

| defect | evidence |
|---|---|
| the auto-suggest candidate pool is matchkey-columns-only, so `year`/`zip` can never be proposed | #2633 — DBLP-ACM ceiling 6.4%, `year` unreachable at any rank |
| the recall estimator is ~26x low on the best key | `title[:5]` true recall 0.982, estimated 0.037 |
| `score x estimated_recall` has no recall floor | picks a 41.6%-recall compound over a 98.2%-recall single key, by 2x |
| learned blocking collapses at >=50k on strong-id shapes | candidate-pair recall 1.0 -> 0.0 (#1316) |

Each shows up downstream only as a diffuse F1 wobble that is easy to attribute
to the scorer. Production candidate-recall on `historical_50k` is **0.8855** —
nearly 12% of true pairs are never generated, and no scoring change can reach
them.

**This gate is a prerequisite, not a nicety.** Every fix in #2633 changes
blocking selection for *every* dataset. You cannot validate a blocking change
against a metric that does not measure blocking.

## The metrics

Two, and they **must gate together**.

### `candidate_recall`

```
|candidate_pairs ∩ gt_pairs| / |gt_pairs|
```

The hard ceiling on recall for the run. Gated on **drop**, same shape as the
existing tolerances.

### `candidate_pairs`

Count of within-block pairs the config generates. Gated on **growth**.

Recall alone is trivially gameable: put every record in one block and
`candidate_recall` is 1.0. That is not hypothetical — it is precisely the
failure mode the parked recall floor produced (22.5x comparisons on dblp_acm,
22.6x on person, for zero measured gain). A recall floor without a cost ceiling
re-introduces it as a *green* gate.

## The implementation insight: do not enumerate candidates

The naive reading — materialise the candidate set, intersect with ground truth —
is O(candidates) and hostile at 50k rows. Neither metric needs it:

- **`candidate_recall`**: iterate `gt_pairs` (thousands, not millions) and test
  whether each pair's two rows share a block key under the committed config.
  O(|gt|) with a row -> block-key-set index built in one pass.
- **`candidate_pairs`**: `sum(n*(n-1)/2)` over block sizes — already exactly what
  `block_analyzer.score_candidate` computes for `total_comparisons`.

So both are cheap on every gated dataset, including `historical_50k`.

## Where it hooks in

`scripts/suggest_quality/oracle.py::evaluate_dataset`, immediately after
`baseline_config = _auto_configure_no_rerank(df)` and before/alongside
`_run_config`. The record already carries `gt_pairs` and is built post-`row_cap`
(the cap truncates `df` and rebuilds `gt_pairs`), so computing there gets the
cap handling for free.

**Derive block keys from the real primitives — `build_blocks` /
`collect_blocking_fields` / `_build_block_key_expr` — never a reimplementation.**
A private copy of block-key derivation would drift from the pipeline and the
gate would certify a fiction. This is the same discipline that makes the
er_matcher basis-parity gate meaningful.

Measure against the **baseline (zero-config) config**: that is what users get.
Measuring the converged config as a second pair of metrics is a reasonable
follow-up, not v1.

## Record and scorecard changes

Two new keys on the `evaluate_dataset` record, flowing through `_build_scorecard`
unchanged (it rounds floats to 6dp generically):

```
candidate_recall: float   # NaN -> null when gt_pairs is empty
candidate_pairs:  int
```

`NaN` for the blocking-shape anchors (`anchor_sparse_zip`, `anchor_shared_email`)
exactly as `baseline_f1` already does — they have no truth, so they must not gate.

## Gate semantics — the one structural change

`_GATE_TOLERANCES` today is a flat `{metric: tolerance}` map and every entry is
interpreted as **"fail if it dropped by more than tolerance"**. `candidate_pairs`
must fail on *growth*, so the map needs a direction:

```python
_GATE_TOLERANCES = {
    "rank_corr":            (0.05,  "drop"),
    "suggester_prec":       (0.05,  "drop"),
    "convergence_final_f1": (0.02,  "drop"),
    "candidate_recall":     (0.02,  "drop"),
    "candidate_pairs":      (0.25,  "growth_ratio"),
}
```

Proposed thresholds, all calibrated-not-derived and expected to be argued with:

- **`candidate_recall` 0.02** mirrors `convergence_final_f1`. A blocking change
  that costs 2pp of reachable recall should be a deliberate re-bless.
- **`candidate_pairs` 0.25 growth ratio** (fail above 1.25x baseline). Deliberately
  loose: it is a blow-up detector, not a budget. It catches the 22x class without
  arguing over a few percent. Tighten once there is a distribution to look at.

`growth_ratio` compares `current / baseline` rather than a difference, because
absolute pair counts span orders of magnitude across the panel.

## What this does NOT do

- **It does not detect scale-cost regressions.** Every suggest-panel dataset is
  small; a 22x comparison blow-up is invisible in wall-clock there. That is why
  the parked recall floor measured byte-identical. Anything touching the blocking
  *objective* still needs `bench-quality-scale` / QIS. This gate catches the
  *shape* of the blow-up (pair count), not its cost at 1M+.
- **It does not fix anything.** It makes four existing defects visible as numbers
  and stops the fifth from landing silently.
- **It inherits config nondeterminism.** Post-#2560 the controller search is
  host-independent, so this should be stable; if a dataset proves flaky, it
  belongs in the existing advisory list rather than being papered over.

## MEASURED 2026-08-22 — the v1 implementation does not yet do the job

Added after implementing the above and running it. Reproduced identically by two
independent paths (the full oracle, and a blocking-stage-only script), so this is
the implementation's behaviour and not a harness artifact.

| dataset | candidate_recall | candidate_pairs | error |
|---|---|---|---|
| `dblp_acm` | — | — | `ColumnNotFoundError: __title_key__` |
| `anchor_person_match` | 1.0000 | 1,712 | — |
| `synthetic` | 1.0000 | 861 | — |
| `ncvr_synthetic` | 1.0000 | 434,530 | — |

**1. It fails on configs that block on a DERIVED column.** `dblp_acm` blocks on
`__title_key__`, which the domain-extraction stage creates. The implementation
calls `build_blocks` on the INPUT frame, but the pipeline blocks on a prepared
one — `auto_fix -> validate -> standardize -> matchkeys -> domain -> precompute
-> block`. There is no single reusable prep seam; those stages are inline in
`_run_dedupe_pipeline`. So the metric is unmeasurable on exactly the dataset that
motivated it.

Fixing it means choosing between two costs, neither free:
- call the same stage functions in the same order — no reimplementation of
  block-key derivation, but a reimplementation of stage ORDER, which can drift
  from the pipeline and leave the metric certifying a fiction one level up;
- capture membership from the pipeline's real blocking run (a `core/bench.py`
  emitter), which is correct by construction but changes goldenmatch core.

**2. Where it does work, it has no discriminating power.** All three
loadable datasets score exactly 1.0000 — they are synthetic corpora with clean
blocking keys, so that is plausible rather than suspicious, but the consequence
is that a 0.02 drop-floor could never fire on any of them. `candidate_pairs`
does look alive (434,530 comparisons for 2,500 true pairs on `ncvr_synthetic`).

**This invalidates the Rollout assumption below.** Step 1 assumed advisory-first
would produce a distribution to calibrate thresholds against. The actual
distribution is `{1.0, 1.0, 1.0, error}`, which calibrates nothing. The metric is
therefore NOT close to gating, and should not be promoted on the strength of a
green advisory run.

What survives: the pure `candidate_metrics` helper and its tests are correct and
regime-independent; the two-metrics-together constraint and the
do-not-enumerate construction are unaffected; and the failure is *recorded* on
`candidate_error` rather than swallowed, so the gap is visible in the scorecard
rather than latent.

One incidental confirmation: the error message enumerates the frame's columns as
`["__row_id__", "id", "title", "authors", "venue", "year"]`. `year` IS present on
the raw frame — independent corroboration of #2633's premise that it is available
and simply never proposed, because the candidate pool is matchkey-only.

## Rollout

1. Land the metrics as **advisory** (reported, non-gating) for one cycle. This
   costs nothing and produces the distribution needed to sanity-check the two
   thresholds against real spread rather than a guess.
2. Bless, then promote to gating.
3. Only then start #2633 — with a metric that can actually see what it changes.

Step 1 matters: blessing a threshold picked from a single observation is how the
`historical_50k` bench blessed an over-merge that a capped subset hid.

## Test plan

`scripts/suggest_quality/tests/`:

- `candidate_recall == 1.0` on a fixture where blocking is a single all-rows block.
- `candidate_recall` strictly drops when a blocking key is narrowed to exclude a
  known true pair (the direction the gate exists to catch).
- `candidate_pairs` matches the `sum(n*(n-1)/2)` identity on a hand-built block set.
- Empty-`gt_pairs` anchors emit `None`, and the gate skips them rather than
  treating them as a regression to 0.
- `growth_ratio` fires above the ratio and stays silent below it.
- A block-key-derivation drift guard: for one dataset, the metric's block
  assignment equals `build_blocks`' own, so a future change to either is caught.

## Sizing

Small. Two record keys, one `_GATE_TOLERANCES` shape change, one comparison
branch in `_cmd_gate`, one helper in `oracle.py`, six tests. The intellectual
content is the pair-of-metrics constraint and the do-not-enumerate trick; the
code is modest.
