# FS / Lever Enablement — design

**Date:** 2026-08-01
**Status:** Proposed (scoping)
**Author:** perf/quality exploration follow-up

## Problem

GoldenMatch's zero-config auto-config decides the **structural** levers well and per-dataset
(which columns become fields, blocking strategy/passes/anchor, `max_block_size`, pair budget,
backend/plan, weighted-path threshold, FS missing-mode). But an audit of the full lever surface
(config schema + ~100 `GOLDENMATCH_*` env flags) found a systematic gap:

1. **A tier of *quality* levers is stranded** as global env flags or hardcoded constants, so the
   per-dataset decision layer structurally cannot tune them per dataset. Several ship
   **default-OFF despite documented measured wins** — the env flag became the permanent home
   instead of a rollout stage.
2. **The FS (Fellegi-Sunter) path — the default for messy person data since 2026-07-17 — is not
   iterated.** The controller's refit loop (the actual "smarter config" engine, `autoconfig_rules.py`
   `DEFAULT_RULES`) is almost entirely `mk.type == "weighted"`-gated. FS returns early from the v0
   heuristic (`autoconfig.py`) and receives a **single-shot config with no iterative refinement**.
   The path that handles the hardest data is the path the refinement engine skips.

Naming it: this is not one feature. It is a program to **move quality levers from
"global flag / constant" into "per-dataset auto-config decision," and to give the FS path real
refinement.** Because most of the decision *logic already exists* (inert behind a flag or a
constant), the effort is mostly **wiring + validation**, not new algorithms — but the validation is
the real cost, because unlike mechanical perf fixes, every lever here **changes match output**.

## Evidence

### The three ways levers are stranded

| Mechanism | Fix | Examples (file refs are `core/…`) |
|---|---|---|
| **A. Hardcoded constant** | wire the decision to data the controller already has | `link_threshold` = 0.50 (`probabilistic.py:2320`); `levels`/`partial_threshold` fixed by scorer family (`autoconfig.py:5236`); scorer-per-`col_type` fixed map (`autoconfig.py:704`) |
| **B. Env-flag gate (logic exists, inert)** | flip gate from "global env" → "auto-config decides + env override" | `tf_adjustment` (`_tf_adjustment_for`, `autoconfig.py:830`, gated `FS_TF_ADJUSTMENT` off); domain comparators (`FS_DOMAIN_COMPARATORS` off, `autoconfig.py:768`); honorific strip (`FS_STRIP_HONORIFICS` off); blocking-pass pruning (`BLOCKING_PRUNE_PASSES` off); quality-aware blocking (`QUALITY_AWARE_BLOCKING` off) |
| **C. Structural (FS not iterated)** | give FS a refit loop / make controller rules matchkey-type-aware | `autoconfig_rules.py` `DEFAULT_RULES` are weighted-gated; FS never re-enters refinement |

Genuinely data-driven per-dataset today (the bright spots): field admission, blocking
strategy/passes/anchor + `max_block_size` + pair budget, backend/plan (v3 planner), weighted
threshold, and **FS missing-mode** (`_pick_missing_semantics`, `autoconfig.py:5272`) — notably the
exact lever a reverted feature (pair-dedup, #2295→#2304) got wrong; the engine knew the right
answer, the feature just didn't read it.

### The measurement that reshapes the plan

The audit flagged `link_threshold` as the "cheap first win": the fixed 0.50 constant has a
data-driven alternative (`_calibrate_link_threshold`, Otsu-on-training-pairs, `probabilistic.py:2402`)
that is **already wired end-to-end** — it just sits behind `GOLDENMATCH_FS_CALIBRATE_THRESHOLD` (off).
Its docstring cites a measured **+0.04 F1** (historical_50k) / **+0.49** (dblp_acm) on a *simple*
auto-config.

Measured on **today's default config** (`historical_50k`, current auto-config incl. the #2145
orthogonal-anchor blocking), fixed-0.50 vs Otsu-on:

| threshold | F1 | precision | recall | clusters |
|---|---|---|---|---|
| fixed 0.50 (default) | **0.8456** | 0.928 | 0.777 | 11,113 |
| Otsu (flag on) | 0.7826 | **0.768** | 0.798 | 10,131 |

Flipping the "obvious win" **regresses F1 −0.063 and precision −0.16** on the flagship dataset. This
reproduces the docstring's own warning: the Otsu selector calibrates on the EM **training-pair**
distribution, but the actual **scored** population (post-orthogonal-blocking) differs, so the cut is
mis-set. **Enablement here is not "wire it" — it is "harden the selector," and the naive flip is a
real regression.** This single measurement is the justification for the whole program's gating
discipline: the decision levers must be measured on the *current* config, not trusted from a
docstring number taken on an older/simpler one.

### Measured screening of the "cheap" levers (2026-08-01, the Phase 0 gate's first run)

Ran `scripts/bench_er_headtohead/ab_lever.py` (the Phase 0 gate) over the 5-dataset F1 panel
(person / febrl3 / ncvr_synthetic / dblp_acm / historical_50k), each lever OFF vs ON, on the
*current* default auto-config:

| lever (env) | gate | headline |
|---|---|---|
| `GOLDENMATCH_FS_CALIBRATE_THRESHOLD` (link_threshold) | **FAIL** | historical_50k −0.063 F1 (P −0.16) |
| `GOLDENMATCH_FS_DOMAIN_COMPARATORS` | **FAIL** | historical_50k −0.013 F1; febrl3 +0.0014; else flat |
| `GOLDENMATCH_FS_STRIP_HONORIFICS` | PASS | net-flat; historical_50k **P 0.928→0.957** / R 0.777→0.754 (precision/recall trade) |
| `GOLDENMATCH_BLOCKING_PRUNE_PASSES` | PASS | +0.0000 on every dataset (no-op on this panel) |
| `GOLDENMATCH_FS_TF_ADJUSTMENT` | PASS | +0.0000 on every dataset (no-op on this panel) |

**Finding: there is no clean F1 win among the cheap levers on the validated panel.** Two regress on
a naive default-flip; three pass the gate but are flat/no-op on F1. The audit's optimistic framing
("measured wins held back by a flag") does not survive a multi-dataset gate on the *current* config —
the docstring wins were taken on older/simpler configs. This re-scopes the program away from
"flip stranded flags" and toward **per-dataset decisions** (and FS refinement), which is where the
value actually is. The signals that *do* exist are decision-shaped, not flip-shaped:

- **domain comparators**: `date_diff` *helps* febrl3 (+0.0014) but *hurts* corrupted-DOB historical_50k
  (−0.013). The right enablement is a **per-column decision** (use `date_diff` only where the date
  column is reliable, keep `levenshtein` when corrupted), not a global flip.
- **honorific stripping**: a genuine **precision** lever (+0.029 P on historical_50k) that trades
  recall — a candidate for a precision-starved *decision*, not a blanket default.
- **blocking-pass pruning / tf_adjustment**: perf / skewed-frequency levers whose value is at scale
  or on shapes not in this panel — F1-neutral here, so not F1-enablement candidates.

## The plan

### Phase 0 — the enablement gate (prereq, non-negotiable)

Every lever here changes output, so nothing lands without a gate. Stand up a one-command
**"A/B a lever"** runner over the `bench_er_headtohead` panel (historical_50k, febrl3/4, synthetic,
dblp_acm/scholar, amazon_google) **plus** `qis_gate` scale-neutrality (50K→5M). Most of this exists
(the `bench-probabilistic.yml` `panel-v1-v2` lane); formalize it as *the* enablement gate: a lever
flips its default only after the panel shows **no per-dataset F1 regression** on the *current* config
and `qis_gate` shows scale-neutrality. **This is the real cost of the program** — the validation
cycles, not the code.

Effort: **S/M**, 1 PR. Blocks everything below.

### Phase 1 — constants → data-driven

- **`link_threshold` — M/L, medium-HIGH risk (revised up by the measurement).** Not a wiring task:
  the Otsu-on-training path is already wired and *regresses* on the default config (evidence above).
  The hardening work is one of:
  1. calibrate on the **actual scored-pair distribution** (the `compute_thresholds(scored_weights=…)`
     percentile branch at `probabilistic.py:2303` exists but is *never fed* the scored pairs —
     plumb the post-scoring weight distribution back), which matches the real population instead of
     the training sample; and/or
  2. a **precision-health guard** that rejects a calibrated cut which would drop the training-pair
     precision proxy below the fixed-cut baseline (fail-safe to 0.50).
  Gate on the full panel — this lever is the canary for the whole program.
- **`levels` / `partial_threshold` from the score histogram — M.** Choose bands from the score
  distribution instead of the fixed 2/3 + 0.8/0.9. Higher blast radius (every field); gate carefully.

### Phase 2 — per-dataset DECISIONS, not flag-flips (revised by the screening)

The screening killed the original "flip stranded flags for free wins" framing — none is a clean win.
So Phase 2 is not "flip the gate to default-on"; it is "make auto-config **decide** the lever
per-dataset (or per-column) from the profile, gated." One decision per PR, each through the gate:

- **Per-column domain comparators (the tractable first real decision).** Emit `date_diff` for a date
  column only when it is *reliable* (low corruption / high parse rate), else keep `levenshtein`;
  emit `numeric_diff`/`geo_haversine` where numeric/coord columns parse cleanly. This targets the
  febrl3 win without the historical_50k regression the global flip caused. Requires a per-column
  reliability signal from the profiler (parse rate / corruption score already computed by GoldenCheck
  indicators).
- **Honorific stripping as a precision decision** — apply when the config is precision-starved
  (name-only fuzzy, high over-merge risk), not as a blanket default. Data-gated on the same signals
  the controller's precision-anchor rule already uses.
- **FS negative evidence — M (bigger).** `promote_negative_evidence` currently *skips* probabilistic
  matchkeys (`autoconfig_negative_evidence.py:148`); the FS scorer already honors NE fields, so this
  is "add an FS-appropriate promotion," not new scoring. Gate hard (NE changes precision/recall
  balance directly).
- **blocking-pass pruning / tf_adjustment** — de-prioritized for the *F1* program (no-op on the
  panel). Their value is perf (fewer pairs) / skewed-frequency precision; revisit under the perf
  track or at scale, not here.

### Phase 3 — structural: FS gets refinement (the real answer, L, spec-first)

Today FS is single-shot. Two options:
- **3a (cheaper, partial):** make the *blocking* refit rules matchkey-type-agnostic — most already
  mutate `blocking`, which FS uses — so at least blocking is iterated on FS.
- **3b (the answer):** a dedicated FS refit loop keyed on signals that already exist in
  `ComplexityProfile` (score-histogram dip → threshold/levels; precision signal → tf_adjustment;
  date/numeric detection → comparator upgrade). This is where the Phase 1–2 levers become *iterated*
  decisions.

Gated hardest: iteration adds planning wall (the "smarter config costs wall to decide" tension), so
it must prove net-positive at scale, not just on a fixed sample.

### Sequencing

```
P0 gate harness ─┐
P1 link_threshold─┼─ each ships independently, each gated on the current config
P2 flag-flips ×5–6┘   (domain comparators first — additive/scale-neutral; then perf-dual levers)
        ↓ (levers now reachable + individually validated)
P3 FS refit loop ── spec-first epic, consumes the P1/P2 levers as iterated decisions
```

Rough sizing: **P0** 1 PR · **P1** 1–2 PRs (higher risk than first thought) · **P2** ~6 PRs (small
code, gated) · **P3** a design doc + 3–5 PRs.

## Non-goals / guardrails

- **Not inventing new levers** — this is about *reaching existing ones*. New comparators/strategies
  are separate work.
- **Leave frontier-neutral levers alone** — calibration mode (`FS_CALIBRATED`) is documented
  F1-neutral; stays env-only, no auto-config decision needed.
- **Keep every env flag as an override** — the flag becomes the escape hatch, never the
  decision-maker. Precedence stays: explicit config field > env override > auto-config decision >
  constant default.
- **One lever per PR through the gate.** The reason these are stranded is "not hardened for the
  controller"; batching flips reproduces exactly that risk.
- **Measure on the *current* config, not a docstring.** The `link_threshold` measurement shows a
  docstring win from a simpler config can be a live regression today.

## Open questions

1. `link_threshold`: is scored-pair-distribution calibration (option 1) enough, or is the
   precision-health guard (option 2) also required? Prototype both against the panel.
2. Phase 3: dedicated FS refit loop vs. generalizing the weighted rules — how much of the existing
   rule set is genuinely type-agnostic vs. needs FS-specific signals?
3. Does a central lever/flag registry with decision provenance (surfaced on `RunHistory.decisions`)
   pay for itself as part of P0, or is it a separate cleanup? ~100 flags are read inline today with
   no registry.

## Appendix — measurement reproduction

`historical_50k`, `auto_configure_probabilistic_df` + `dedupe_df`, one host:

```
GOLDENMATCH_FS_CALIBRATE_THRESHOLD=0  →  F1 0.8456  P 0.9280  R 0.7766  (11,113 clusters)
GOLDENMATCH_FS_CALIBRATE_THRESHOLD=1  →  F1 0.7826  P 0.7682  R 0.7976  (10,131 clusters)
```
