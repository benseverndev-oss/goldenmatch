# Suggester Gym — Degraded-Config Recovery + Corruption Sweep — Design

**Date:** 2026-06-24
**Status:** Approved (brainstorm); pending plan
**Author:** Ben Severn (with Claude)
**Follows:** `2026-06-24-config-suggestion-kernel-design.md` (Plan 1, PR #1267)

## Problem & vision

Plan 1 shipped the config-suggestion engine + a `scripts/suggest_quality` oracle
benchmark. But the benchmark immediately exposed a measurement problem: on every
labeled dataset, **zero-config is already near-ceiling** (DBLP-ACM 0.964 — above
the hand-tuned 0.918; Febrl3 0.944; NCVR 0.972; the bench's `synthetic`/
`ncvr_synthetic` 0.98–0.99). So the suggester correctly stays mostly quiet and
there is **almost no headroom to measure intelligence against.** If we make a
rule smarter today, the bench can't tell — every dataset is already solved.

That is a chicken-and-egg block on "make the suggester optimal": to iterate
intelligence we first need scenarios where a config is *suboptimal*, so a smarter
suggester has something real to recover. That is also the suggester's actual
product value — "the user has a config that isn't zero-config-optimal, and the
suggester improves it."

**This sub-project builds the gym:** a measurement harness that manufactures
controllable headroom and turns "did the suggester get smarter or dumber" into a
CI-gated number. The new rules (blocking-pass, field-weight) are a *follow-up*
spec, built and proven against this gym.

### Scope

In scope: the measurement gym only —
1. A **perturbation catalog**: damage a known-good zero-config along one axis,
   measure how much F1 the suggester recovers (`recovery%`).
2. A **corruption sweep**: synthetic data at rising noise where zero-config
   itself weakens; measure suggester lift vs. corruption level.
3. `recovery%` metric, a `gym` CLI command, and a CI-gated `gym_scorecard.json`.

Out of scope (follow-up specs):
- The new rules themselves (blocking-pass add/drop; field-weight adjustment +
  its per-field-score pipeline plumbing). The gym defines *forward-compatible*
  perturbations for them now so they are gradeable the day they land.
- Naive-template realism as the *primary* signal (Approach B) — one B-style
  scenario is folded in, but the controlled catalog (Approach A) leads.
- Per-level oracle ceiling for the corruption sweep (expensive grid search;
  v1 measures lift-over-zero-config).

## Approach

Three approaches were considered:

- **A — Perturbation catalog over zero-config (chosen).** Start from the
  known-good zero-config, apply named single-axis perturbations, run the
  suggester to convergence, grade `recovery%` toward the zero-config ceiling.
  Each perturbation maps to the rule that should fix it → a per-rule unit test
  of intelligence. Forward-compatible: perturbations for unbuilt rules score ~0%
  today and become the next spec's acceptance bar. Plus a corruption sweep.
- **B — Naive/template starting configs.** Realistic starting point, but a naive
  config has many problems at once → muddy attribution, noisy multi-step
  convergence, no clean per-rule signal. Rejected as the lead; one scenario
  folded in for realism.
- **C — Combinatorial perturbation + rule ablation.** Most rigorous (rule
  interactions + marginal contribution) but combinatorial cost; overkill for a
  v1 gym. Deferred.

**Decision: A**, with 1–2 B-style realism scenarios folded in. A gives the clean
per-rule signal that makes rule-iteration tractable and is forward-compatible
with the blocking/weight rules we are about to build.

## Architecture & placement

The gym **extends `scripts/suggest_quality/`** — same machinery (dataset
loaders, `review_config`, `apply_suggestion`, the convergence loop,
`core/evaluate.py::evaluate_pairs`, `MatchEngine.from_dataframe`), pointed at a
new question. New / changed modules:

- **`perturbations.py`** (new) — the catalog. Each entry:
  `Perturbation(name, expected_rule, applies_to(config)->bool,
  apply(config)->config, description)`. Pure, no I/O, unit-testable.
- **`gym.py`** (new) — the recovery-eval loop + the corruption sweep driver.
- **`metrics.py`** (extend) — add `recovery_pct(f1_degraded, f1_recovered,
  f1_ceiling)`.
- **`datasets.py`** (extend) — a corruption-sweep generator (synthetic person
  data at rising noise; reuses the existing `gen_labeled`/Febrl-style path).
- **`cli.py`** (extend) — a `gym` subcommand; fold recovery metrics into
  `bless`/`gate`.

**Required refactor — extract `converge`.** The oracle's greedy convergence loop
(apply top suggestion → re-run → recompute → stop when no positive *unsupervised*
lift) currently lives inline in `oracle.py::evaluate_dataset`. The gym needs the
exact same loop starting from a *degraded* config, so extract it to a shared
`converge(df, config) -> (final_config, trail)` that both the oracle and the gym
call. Keeps "how the suggester behaves" in one place.

**The key invariant:** the suggester runs **fully unsupervised** (convergence
decisions use the self-verify health proxy, never labels). The gym uses
ground-truth F1 only to *grade* the result. We measure real recovery without
changing how the suggester operates — the gym is an honest mirror of production.

**Separate scoreboard:** a new `baselines/gym_scorecard.json`, distinct from the
oracle's `scorecard.json`. Different questions — oracle: "does it suggest well
from zero-config?" (low headroom); gym: "does it recover a *damaged* config?"
(high, controllable headroom). Both CI-gated.

## The perturbation catalog

Each perturbation is a named single-axis damage to the zero-config, tagged with
the rule that should reverse it. `applies_to(config)` gates per dataset (can't
drop a blocking pass that isn't there); a perturbation that doesn't actually hurt
F1 is flagged `no_damage` and excluded from recovery scoring.

**Damage whose fixing rule exists today (these prove the gym works):**

| Name | Mutation | Expected rule |
|---|---|---|
| `threshold_too_low` | primary matchkey threshold −0.15 (clamp ≥ floor) | threshold-raise |
| `threshold_too_high` | primary threshold +0.10 | threshold-lower |
| `bad_freetext_scorer` | set an address/name field scorer → `token_sort` | scorer-swap |
| `missing_negative_evidence` | drop an NE field zero-config would add | negative-evidence |

**Damage whose fixing rule is NOT built yet (forward-compatible targets, ~0%
recovery today):**

| Name | Mutation | Expected rule (future) |
|---|---|---|
| `dropped_blocking_pass` | remove a recall-critical pass, leave one coarse key | blocking-pass-add |
| `flattened_weights` | set all field weights equal | field-weight |
| `skewed_weight` | over-weight a weak/noisy field | field-weight |

**Realism scenario (multi-problem, lower attribution):**
- `naive_single_fuzzy` — replace the whole config with a minimal one-fuzzy-
  matchkey / default-threshold config a user might hand-write; graded against the
  zero-config ceiling.

Every gym record captures **whether the expected rule actually fired** (not just
whether F1 recovered) — separating "F1 recovered" from "the *right* lever moved",
which catches lucky recoveries via the wrong rule.

## Recovery loop & the recovery% metric

For each `(dataset with ground truth, applicable perturbation)`:

1. **Ceiling:** `ceiling_config` = zero-config; run + score → `F1_ceiling`.
2. **Damage:** `degraded_config = perturbation.apply(ceiling_config)`; run +
   score → `F1_degraded`.
3. **Damage check:** if `F1_ceiling − F1_degraded < ε` (≈0.005) → record
   `no_damage`, skip recovery scoring.
4. **Recover:** `recovered_config, trail = converge(df, degraded_config)` (shared
   unsupervised loop).
5. **Score:** run + score `recovered_config` → `F1_recovered`.
6. **Record:** `recovery_pct = (F1_recovered − F1_degraded) /
   (F1_ceiling − F1_degraded)` (100% = fully undone, >100% = beat zero-config,
   <0% = made it worse — self-verify should prevent this, the gym catches it);
   `expected_rule_fired` (did any applied suggestion carry the expected rule's
   kind/target); `n_applied`, applied kinds, the three F1s.

**Metric** (`metrics.py`, pure + unit-tested): `recovery_pct(...)` returns `nan`
when the denominator ≤ ε (no-damage, excluded from aggregates); does NOT clamp
(overshoot >100% and negatives are meaningful and reported as-is).

**Aggregation / headline:**
- Per-perturbation recovery% (mean across datasets where it caused damage).
- Per-rule rollup (mean over that rule's perturbations) — the "is this rule
  smart?" number.
- **Headline gym score** = mean recovery% over damaging perturbations **whose
  fixing rule exists**. Unbuilt-rule perturbations are reported separately as
  standing targets, NOT in the headline (else the score is permanently ~50% and
  can't move).

The `expected_rule_fired` flag keeps it honest: high recovery% with the *wrong*
rule firing is a yellow flag, not a win.

## The corruption sweep

Complements the catalog: where the catalog recovers *injected* damage, the sweep
tests **data so noisy that zero-config itself weakens**.

- **Generation:** reuse the synthetic person generator at rising corruption
  levels `[0.0, 0.1, 0.2, 0.3, 0.4]` (typo / transposition / dropped-token noise
  on name/address). Ground truth known by construction; deterministic seed.
- **Per level:** zero-config → `F1_zeroconfig`; `converge(df, zero_config)` →
  `F1_recovered`; record `lift = F1_recovered − F1_zeroconfig` + applied
  suggestions.
- **Headline:** the lift curve vs. corruption level — does the suggester's value
  *grow* as data degrades (where users actually need help), and stay ≥ 0
  everywhere (self-verify holding)?
- **v1 scope guard:** no per-level hand-tuned/oracle ceiling (expensive grid
  search). v1 measures lift over zero-config; a per-level ceiling is a clean
  later add if the lift curve says we need it.

## Reporting, bless & gate

**`gym` subcommand** prints two boards:
- **Catalog board:** rows of `(dataset, perturbation)` with
  `F1_ceiling/degraded/recovered`, `recovery%`, `expected_rule_fired` ✓/✗,
  `n_applied`; a per-rule rollup; the headline gym score. Unbuilt-rule
  perturbations print in a separate "standing targets (rule not built)" section.
- **Sweep board:** `corruption_level | F1_zeroconfig | F1_recovered | lift` +
  the lift-curve summary.

**bless/gate** (separate `gym_scorecard.json`): `bless` writes per-
`(dataset,perturbation)` recovery% + per-rule rollup + sweep lift-per-level + the
headline. `gate` fails (exit 1) on a **recovery regression** — a built rule's
recovery% dropping > tolerance (~5%), the headline dropping, or any sweep level's
lift going negative (self-verify regression). Reuses the Plan-1 gate semantics
(zero-eval guard → fail; missing-blessed → fail). This is the payoff: "the
suggester got smarter or dumber" becomes a CI-gated number.

**CI:** extend `bench-suggest-quality.yml` to also run `gym gate` (sibling step
or job), same `large-new-64GB` + native-build + symbol-assert setup.

## Testing

- **Pure unit tests (no native):** `recovery_pct` edges (no-damage→nan,
  overshoot>100%, negative); each perturbation's `apply()` produces the intended
  mutation (e.g. `threshold_too_low` actually lowers the threshold;
  `bad_freetext_scorer` sets `token_sort` on a free-text field) and `applies_to`
  gates correctly; the corruption generator produces rising-noise datasets with
  valid ground truth.
- **Integration (native-guarded skip):** `gym` runs end-to-end on `synthetic`;
  assert at least one **built-rule** perturbation (e.g. `bad_freetext_scorer` or
  `threshold_too_low`) shows recovery% meaningfully > 0 **with
  `expected_rule_fired = True`** — proving the gym measures intelligence on a real
  rule, not just plumbing. Assert the unbuilt-rule perturbations record ~0%
  without erroring.
- **Determinism:** fixed seeds; `GOLDENMATCH_AUTOCONFIG_MEMORY=0`; same posture
  as the oracle.

## Done criteria

- `python -m scripts.suggest_quality.cli gym` prints the catalog + sweep boards
  with real numbers; the headline gym score reflects only built-rule
  perturbations.
- At least one built-rule perturbation demonstrably recovers (>0%,
  `expected_rule_fired`), proving the gym measures intelligence.
- Unbuilt-rule perturbations (`dropped_blocking_pass`, `flattened_weights`,
  `skewed_weight`) record their ~0% recovery as the standing target for the
  next spec — without erroring.
- `gym bless` + `gym gate` wired with a `gym_scorecard.json`; `gate` fails on
  recovery regression / negative sweep lift; CI runs it.
- No change to suggester runtime behavior — the gym only reads and grades.

## Open questions for planning

- Confirm the existing synthetic generator (`gen_labeled` / the Febrl-style path
  in `datasets.py` / `tests/generate_synthetic.py`) exposes a corruption-intensity
  knob, or whether the sweep needs a thin corruption-injection wrapper over a
  clean synthetic base.
- Confirm `converge` can be cleanly extracted from `oracle.py::evaluate_dataset`
  without changing the oracle's measured output (the oracle scorecard must stay
  byte-stable after the refactor — gate it).
- Exact zero-config build path the gym perturbs from (`auto_configure_df` with
  rerank disabled, matching the oracle's baseline builder) so ceiling F1 matches
  the oracle's baseline_f1 for shared datasets.
