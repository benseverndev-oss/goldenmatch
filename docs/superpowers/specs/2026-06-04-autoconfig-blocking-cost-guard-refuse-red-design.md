# Auto-config blocking cost guard + refuse-on-RED (#715 reopened) -- design

Date: 2026-06-04
Issue: benseverndev-oss/goldenmatch#715 (reopened after PR #720)
Status: design (investigation reproduced; pending spec review)

## Context

PR #720 fixed the matchkey half of #715 (high-cardinality identifiers now back
exact matchkeys). The reporter verified that half works, then reopened: the
config still commits `RED` and runs anyway, and a new failure mode replaced the
old one -- auto-config picks an unbounded `soundex(name)` blocking pass that
produces ~30K-row blocks at 1M rows -> 39.6M candidate edges -> 18-min run ->
cancelled at the GH job timeout. Net: equivalent-or-worse than pre-fix.

## Reproduced root cause (not reasoned -- measured)

Ran `profile_columns` + `build_blocking` (unchanged by #720) on the synthetic
healthcare shape at 50K (dense zip5) and 200K (sparse zip5, 45% null, matching
the reporter's "30-70% non-null identifier" description):

- **Dense zip5 (50K):** blocking picks the bounded compound `zip5+last_name`
  (max_block 77). No problem. Confirms the bug is shape-specific.
- **Sparse zip5 (200K):** `zip5` reclassifies `zip -> identifier` (near-unique on
  the non-null sample) and is dropped from the compound candidate pool. Blocking
  falls back to a `multi_pass` whose passes include a single-column
  `soundex(last_name)` -- `last_name` alone has max_block 4,055 vs a
  `max_safe_block` target of 1,000; soundex collapses surnames further. At 1M
  (5x) this is the reporter's ~30K block / 39.6M edges.

Three separable problems:

- **B1 (primary):** `build_blocking` emits single-column `soundex(name)` /
  substring passes (and can emit an oversized primary key) **without checking
  each emitted key/pass's full-N max block size against `max_safe_block`.** v0 is
  built on the FULL df (`autoconfig_controller.py:580-586`), so `_max_block_size`
  is exact at build time -- the code simply never gates the recall passes on it.
- **B2 (support):** a sparse numeric column (`zip5` at 45% null) reclassifies to
  `identifier` and is excluded from `_build_compound_blocking`'s candidate pool
  (`autoconfig.py:892` excludes `identifier`), starving the compound search of the
  one column that would bound block size (`zip5+last_name` = max_block 1,864,
  usable).
- **A (backstop):** the controller commits the RED v0 and the downstream pipeline
  runs anyway. The raise gate exists but is gated on
  `confidence_required=True AND n_rows >= REFUSE_AT_N`; the reporter opts out via
  `confidence_required=False` and gets the 18-min run.

**On the iteration-budget angle (the user's hypothesis):** confirmed that
`ControllerBudget.for_dataset` scales `max_seconds` (15->120s) and `sample_size`
(2K->20K) with row count but leaves `max_iterations = 3` fixed for all sizes. It
is a real limitation, but NOT the primary cause here: the controller iterates on
a 2K-20K sample where `soundex(name)` blocks are small; the at-scale explosion is
invisible at that altitude, so more sample-iterations would not catch it. We
scale it anyway (cheap, correct-direction) but it is not load-bearing for #715.

## Design

### B1 -- bound every emitted blocking key/pass by full-N block size (core fix)

`build_blocking` must not emit any blocking key or multi_pass pass whose max
block size (on the full df; for the distributed/sample path, the #410 Chao1
projection) exceeds `max_safe_block`.

- Add a helper that, given candidate `fields`, returns its (projected) max block
  size, reusing the existing `_max_block_size` + `n_rows_full` projection.
- In the name-fallback multi_pass construction (`autoconfig.py:~1387-1499`),
  build the pass list, then **filter out any pass whose projected max block size
  > `max_safe_block`.** The primary `keys=` must itself be a bounded key.
- If, after filtering, no bounded key/pass survives (and compound also failed),
  `build_blocking` returns a config flagged degenerate (empty/oversized) -- the
  blocking sub-profile then rolls up RED, which (with A) refuses.
- Do NOT silently emit an oversized pass for "recall" -- an oversized soundex pass
  does not improve recall, it produces a candidate-pair bomb that the cluster
  budget then truncates anyway (the reporter's "422 oversized clusters" log).

### B2 -- let the compound search use sparse/identifier-typed numeric columns

`_build_compound_blocking` should judge candidates by whether they *bound block
size*, not by col_type. Concretely:

- Stop excluding `col_type == "identifier"` outright from the compound candidate
  pool. Instead include a column when its single-column block size is NOT
  near-singleton (i.e. it actually groups records) AND it is not perfectly unique
  (`cardinality_ratio < 1.0`, excludes surrogate keys like `matching_id`).
- High-null handling: a high-null column can still be a useful *multi_pass* key
  (covers its non-null subset; other passes cover the rest), so it should be
  admissible as a compound *component* even above the 20% null ceiling that gates
  single-key blocking -- BUT only inside a multi_pass set where other passes cover
  the null rows. Keep the existing single-key null ceiling; relax only for the
  compound-component role.
- Net: on the sparse-zip shape, `zip5+last_name` (max_block 1,864) becomes
  reachable, bounding the fuzzy matchkey's candidate set.

### Iteration-budget scaling (add-on, per user)

In `ControllerBudget.for_dataset`, scale `max_iterations` with the size tier
(e.g. 3 -> 4 -> 5 as n_rows crosses 100K / 1M). Low-risk, correct-direction; not
load-bearing for #715 (documented above). Keep the default `max_iterations=3` for
the base dataclass and small data.

### A -- refuse-on-RED via `allow_red_config` (reporter's ask)

- Add `allow_red_config: bool = False` to `auto_configure_df` / `dedupe_df` /
  `match_df` and thread to `AutoConfigController.run`.
- When the committed entry's health is RED with `stop_reason` set (genuine
  give-up) AND `n_rows >= REFUSE_AT_N`, raise `ControllerNotConfidentError` by
  DEFAULT. **CORRECTION (during implementation):** keep the `REFUSE_AT_N`
  threshold -- small datasets (<100K) are cheap to run even when RED, and the
  existing design deliberately scopes the raise to >=100K (memory
  `feedback_controller_confidence_required`). The reporter's case is ~1M
  (>> REFUSE_AT_N), so this fully covers it. (Original spec said "independent of
  REFUSE_AT_N / small-N also raises" -- that over-reached and broke 19 small-N
  warn-and-run tests; reverted to keep REFUSE_AT_N.)
- The escape is `allow_red_config=True` (restores warn-and-run, the `:1071`
  path). **`confidence_required=False` no longer bypasses the RED-refuse** -- that
  was the reporter's actual bug (they used it to get a result and got garbage).
  The RED gate is now `committed_RED AND n_rows >= REFUSE_AT_N AND not
  allow_red_config`; `confidence_required` is dropped from the RED gate (still
  gates the #417 NON-RED degenerate guard). Message names the failing sub-profile
  and points at an explicit config or `allow_red_config=True`.

### Reconciliation with the existing #408/#417 degenerate-blocking guard

There is already a raise path at `autoconfig_controller.py:879-937`
(`ControllerNotConfidentError`, `StopReason.BLOCKING_DEGENERATE`) that estimates
avg block size scaled to full population (`estimate_avg_block_size` +
`degenerate_guard_*` in `blocking_candidates.py`) and catches the no-keys /
too-coarse-overall case -- but it is gated on the SAME
`confidence_required AND n_rows >= REFUSE_AT_N AND profile RED` triple the
reporter opts out of. The plan MUST reconcile the new work with it:

- **A (allow_red_config)** is a NEW, orthogonal default-raise. Precedence: the new
  `allow_red_config=False` RED-refuse fires regardless of `confidence_required`
  and regardless of `REFUSE_AT_N`. The existing #417 guard stays as-is for the
  `confidence_required` path. When both could fire, raise ONCE with the most
  specific message (blocking-degenerate if that's the failing sub-profile, else
  the generic RED-config message). Do not emit two different errors for one
  condition.
- **B1's per-pass guard** needs *max* block size per emitted key/pass; the
  existing infra estimates *avg*. B1's block-size projection is **new code**
  (Chao1-style on group sizes), NOT the existing #410 cardinality projection
  (which corrects cardinality ratio, not block size) and NOT
  `estimate_avg_block_size` (avg, not max). Add it alongside the existing helpers
  in `blocking_candidates.py` for consistency; do not assume a projection helper
  already exists.

## Testing and validation

- **Unit (`build_blocking`), B1:** sparse-zip healthcare profiles at a size where
  a single `soundex(name)` pass exceeds `max_safe_block` -> assert NO emitted
  key/pass has projected max block size > `max_safe_block`; assert a bounded
  compound is chosen when available; assert degenerate-flag when nothing bounds.
- **Unit, B2:** sparse `zip5` (identifier-typed, 45% null) -> assert it is
  reachable as a compound component and `zip5+last_name` is selected.
- **Unit, A:** monkeypatch a RED committed entry -> `auto_configure_df` raises by
  default; `allow_red_config=True` returns the config; `confidence_required` left
  at default does not change the new behavior. **Include a small-N (<100K) RED
  case** asserting it now raises by default -- that is the broadest behavior
  change (default-raise is independent of REFUSE_AT_N) and the easiest to miss.
- **Unit, iteration budget:** `ControllerBudget.for_dataset(2_000_000).max_iterations`
  > `for_dataset(10_000).max_iterations`.
- **At-scale reproduction (the gap that let #715 reopen):** a CI
  `workflow_dispatch` job that runs the FULL `auto_configure_df` (or
  `build_blocking` on the full df) on the sparse-zip healthcare shape at >= 500K
  rows and asserts the committed blocking's projected max block size <=
  `max_safe_block` AND candidate-edge estimate under budget. A sample-sized unit
  test CANNOT catch this (the whole reason #715 reopened) -- the at-scale check is
  mandatory, not optional.
- **Quality gate / DQbench:** B1/B2 change blocking-key selection for
  sparse-identifier shapes; run the #528 quality gate + DQbench T1/T2/T3 before
  merge to confirm no recall regression from dropping oversized recall passes.

## Risks

- **Dropping recall passes could lower recall** on shapes where the oversized pass
  was actually finding true pairs. Mitigation: B2 makes a bounded compound
  available so recall is preserved via a bounded key; the at-scale + DQbench gates
  measure it. If a bounded key genuinely cannot be formed, refusing (A) is the
  correct outcome (the user must supply an explicit config), not shipping a bomb.
- **B2 admitting identifier-typed columns to blocking** risks singleton-block
  keys. Mitigation: judge by block size (not type), exclude `card == 1.0`
  surrogate keys, and only as a multi_pass component.
- **`allow_red_config` default-raise is a behavior change** for callers currently
  relying on `confidence_required=False` warn-and-run. Mitigation: document in
  CHANGELOG; the escape hatch is one kwarg.

## Out of scope

- The matchkey admission fix (#720, done).
- Distributed/Ray blocking (the reporter is single-node); the #410 projection
  already covers the sample path's cardinality gate, and B1 reuses it.
- `build_probabilistic_matchkey` identifier admission (#721 follow-up).
