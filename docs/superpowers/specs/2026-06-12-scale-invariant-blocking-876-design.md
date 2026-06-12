# Scale-Invariant Blocking Selection (#876) — Design

**Date:** 2026-06-12 · **Issue:** #876 · **Branch:** feat/510-quality-invariant-scale · **Status:** approved (brainstorm), pre-plan

## Problem

The #510 scale audit proved that, beyond ~1M rows, both a precision drift and a
super-linear slowdown trace to one cause: auto-config's blocking selection is not
scale-invariant. The frozen config blocks on `zip` alone; `zip` is
bounded-cardinality (~100K distinct — real US zips are ~40K), so its block size
grows ∝ N and the candidate-pair count grows ∝ N²/cardinality (≈3B pairs at 25M).

Two concrete failures in `core/autoconfig.py::build_blocking` (line 1582):

1. **The gate is on MAX BLOCK SIZE, not total pairs.** `_pass_is_bounded`
   (#715) accepts a key whose projected max block ≤ `max_safe_block`, and
   `max_safe_block = max(1000, min(10000, N//200))` *scales up with N*. At 100M a
   uniform key with a 1000-row block passes (1000 < 10000) yet generates
   `100K blocks × C(1000,2) ≈ 5e10` candidate pairs. Per-block OOM safety and
   scale-invariance are different concerns; the gate only enforces the former.
2. **When a key IS dropped, the fallback is degenerate.** Told the true scale
   (`n_rows_full ≥ 1M`), `build_blocking` drops `zip` and falls through to `id` —
   the unique surrogate key (block size 1 → *zero* candidate pairs → finds
   nothing). There is no surrogate guard and no compound/multi-pass fallback in
   this path.

Plus a harness gap: the #510 frozen config is built at the 1K oracle, so
`build_blocking` runs with `n_rows_full = 1000` and sees `zip` as block-size-5
(safe). The config is then *applied* at 100M, where it explodes. The projection
never saw the target scale.

## Goal

Blocking selection that keeps the candidate-pair count **linear in N** (constant
pairs-per-row) while preserving recall, so the same config is correct from 1K to
200M. Validate by rebuilding the #510 frozen config, re-running the quality ladder
to confirm linear wall scaling + flat precision, and completing the cluster tier
(25M→200M). Must not regress the #491/#715 benchmark datasets (NCVR / DQbench /
Febrl), which use full-DOB date anchors and small blocks.

## Design

### 1. Total-candidate-pairs budget (the new scale gate)

A blocking **option** (a single key, a compound, or a multi-pass union) is
*scale-safe* iff its projected total candidate pairs at full N is within a linear
budget:

```
projected_pairs(option, full_n) <= K * full_n        # K = pairs-per-row budget, a CONSTANT
```

- For a (near-)uniform key, `projected_pairs ≈ full_n * (proj_block - 1) / 2`
  where `proj_block = project_max_block_size(sample_block, df.height, full_n)`.
  So the budget reduces to `proj_block <= 2K + 1` — an avg-block cap that does
  NOT scale with N (unlike `max_safe_block`). For a multi-pass union, sum the
  passes' projected pairs.
- `K` is a constant (default chosen so a productive block stays in a healthy
  range — see "Constants"), exposed as a tunable. This is the scale-invariance
  knob, kept **separate** from `max_safe_block` (which stays the per-block OOM
  guard, unchanged, so #715's scorer-matrix protection is untouched).
- A scale-safe option must ALSO satisfy the existing per-block `max_safe_block`
  gate (both must hold).

### 2. Surrogate / unproductive exclusion

Drop blocking candidates whose projected full-N cardinality ≈ `full_n` (block
size ~1 → ~0 candidate pairs). Mirror the exact-matchkey surrogate guard
(`cardinality_ratio >= 1.0`, autoconfig.py:813): a column that is unique-per-row
is a useless blocking key. This removes the `id` fall-through. A key is
*productive* iff projected block ≥ 2 (it generates at least some pairs); the
selector prefers keys that cover recall, not just any productive key.

### 3. Bounded compound

When no single key is both scale-safe and recall-covering, refine a coarse key
into a compound by AND-ing a discriminating token, so block size lands in
`[productive, scale-safe]`:

- Candidate refinements: a coarse anchor (`zip`/`geo`) × a name token
  (`first-syllable` / first-K-chars of a name column, or `soundex(name)`).
- Pick the refinement whose projected compound block size is the largest that
  still satisfies the total-pairs budget (largest block = best recall within
  budget). Reuses `_build_compound_blocking` (autoconfig.py:1060); this design
  adds the *selection* of which refinement, gated by §1.
- Recall rationale: corruption is mostly mid-string, so a `zip + first-syllable`
  compound still co-locates a cluster's corrupted variants (they share zip and a
  stable name prefix) while shrinking blocks from N/100K to N/(100K·24).

### 4. Capped multi-pass union

Emit several independent single-key passes (e.g. `zip`, a name key, a
`soundex(name)` key), each **sub-blocked or dropped** so its own projected pairs
fit a per-pass share of the budget, unioned. Multi-pass trades more total pairs
for broader recall coverage (a pair clean on ANY pass is caught). Each pass is
gated by §1 at its budget share; passes that can't be made scale-safe even with
sub-blocking are dropped (logged).

### 5. Budget-driven selector

Given the productive, surrogate-filtered candidates, build the option set:
single keys (§1-safe ones), the best bounded compound (§3), and the capped
multi-pass union (§4). Choose by **maximizing projected recall coverage subject
to `projected_pairs ≤ K·full_n`**:

- Prefer a single scale-safe key if one covers recall (cheapest).
- Else prefer the bounded compound if it covers recall within budget.
- Else the capped multi-pass union.
- If nothing fits the budget (pathological), emit the degenerate/empty config so
  the controller refuses (existing behavior) rather than shipping a pair-bomb —
  never fall through to a surrogate.

Carried on `BlockingConfig` via the existing `max_total_comparisons` field set to
`K·full_n` (so the runtime also enforces the budget) plus the chosen
keys/passes.

### 6. Harness / API change

- Add a public keyword `n_rows_full: int | None = None` to
  `auto_configure_df` (autoconfig.py:2372), threaded to the controller →
  `_initial_config` → `_legacy_auto_configure_v0` → `build_blocking` (the
  internal plumbing already exists; only the public entry point lacks it).
- `quality_invariant_scale.build_frozen_config` passes
  `n_rows_full = <target ladder scale>` (e.g. 200_000_000) so the frozen config
  is built FOR the scale it will be applied at. Document the value.

## Constants

- `K` (pairs-per-row budget): default such that the projected avg block stays
  in a recall-healthy but scale-safe range. Start at a value giving avg block
  ~O(100) (e.g. `K = 50`, block ≤ ~101) and tune against the benchmark recall +
  the #510 ladder; expose via env/arg. Rationale: small enough that total pairs
  stay linear at 200M, large enough that a compound block (~42 for
  zip+syllable) and the benchmark blocks (name+DOB, tiny) pass.
- `max_safe_block`: unchanged (per-block OOM guard).

## Components & boundaries

- `_project_pairs(fields, df, full_n) -> int` — new pure helper (sample block →
  full-N total pairs). Testable standalone.
- `_blocking_option_scale_safe(option, full_n, K) -> bool` — the §1 gate.
- Surrogate filter — a predicate on profiles.
- Compound-refinement selection — extends the existing compound builder.
- Multi-pass capping — extends the existing pass logic.
- Selector — orchestrates the above; one function, clear inputs/outputs.
- `auto_configure_df(n_rows_full=...)` + `build_frozen_config(n_rows_full=...)`.

## Testing

- **Unit (fast, no dedupe):** `build_blocking` on synthetic profiles —
  (a) a bounded-cardinality uniform key (zip-like) at large `n_rows_full` is NOT
  emitted as a sole key (it's refined into a compound or multi-pass);
  (b) a unique surrogate (`id`-like) is never the blocking key;
  (c) `_project_pairs` math on hand cases; (d) the selector picks the cheapest
  scale-safe option; (e) a name+DOB benchmark-shaped profile still yields the
  same small-block key (no regression).
- **No-regression:** `test_autoconfig.py`, `test_autoconfig_491_levers.py`,
  `test_autoconfig_regressions.py` green; the heavier `test_autoconfig_benchmarks`
  (NCVR/DQbench/Febrl) recall not regressed (run where datasets exist).
- **#510 integration:** rebuild the frozen config with `n_rows_full=200M`; assert
  its blocking is a bounded compound / capped multi-pass (NOT sole-`zip`, NOT
  `id`); the 1K band test still in range.
- **Scale validation (the payoff):** re-run the ladder 1K→10M and confirm (i)
  wall scales ~linearly (not super-linear) and (ii) precision is flat (no 10M
  drift); then run the cluster tier 25M→200M on GCP and confirm it completes in
  practical time with flat F1.

## Risks

- **Benchmark recall regression** — the new gate could drop a pass the #491/#715
  datasets relied on. Mitigate: the gate only *adds* a total-pairs constraint and
  a compound/multi-pass fallback; small-block benchmark keys are unaffected (they
  pass the budget trivially). The benchmark suite is the guard.
- **`K` mis-tuned** — too small starves recall, too large lets the explosion
  back in. Tune against both the benchmark recall and the #510 ladder; it's an
  exposed knob.
- **Compound recall on corrupted prefixes** — if corruption hits the refining
  token (first-syllable), recall dips. Multi-pass (a soundex pass) backs it up;
  the ladder's recall column is the check.
- **200M cost/feasibility** — generation is in-memory (RAM-heavy); 200M may need
  the distributed pipeline or a big-mem box. Size from 50M first; drop 200M loudly
  if it doesn't fit a sane window.

## Done criteria

- [ ] Total-pairs budget gate + surrogate exclusion + compound/multi-pass
      fallback + selector in `build_blocking`; `n_rows_full` public on
      `auto_configure_df`.
- [ ] Unit tests (incl. zip-not-sole-key, id-never-blocking, benchmark-shape
      unchanged) green; auto-config regression suites green.
- [ ] #510 frozen config rebuilt (n_rows_full=200M) blocks scalably; band test
      in range.
- [ ] Ladder re-run shows ~linear wall + flat precision through 10M; cluster tier
      25M→(100M/200M) completes; `docs/quality-invariant-scale.md` updated with
      the full curve + the #876 fix.
