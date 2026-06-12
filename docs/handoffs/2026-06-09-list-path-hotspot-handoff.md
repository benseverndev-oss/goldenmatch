# Handoff: list-path scoring/clustering hot spots (post-#837)

> **For the agent picking this up:** this is a measure-first optimization handoff, not an
> approved plan. Do NOT write a kernel or a "fix" until you have reproduced the cost on the
> real shape and confirmed it is real on the *production* path. The last item on this list
> turned out to be a free Python guard, not a kernel — see #837 below. Same discipline applies
> here. The two governing rules (from the repo owner's standing memory):
> - **Measure wall-clock before designing.** A sample-sized test cannot catch an at-scale
>   blow-up (block/pair counts scale with N).
> - **Verify the WALL moved on the failing/real env, not just that code shipped.** cProfile
>   cumtime != wall, especially under threading. Compare 5-run median wall on the real 1M shape.

---

## What just shipped (the context you're inheriting)

**PR #837** (`perf(scorer): eliminate dead matched_pairs bookkeeping on the columnar + list paths`)
merged to `main` as squash commit `92bd74cf`. Remote branch deleted.

What it did: `matched_pairs` is a `set[tuple[int,int]]` threaded through the matchkey loop as a
*cross-pass* exclude set. It is a **side-effect**, not output — it only affects a *later* matchkey
pass's exclusion. The profiler showed it as the #1 hot spot (~104s / ~30% of wall at 1M / 131M
candidate pairs), almost entirely `builtins.min`/`max`/`set.add` per pair. The structural finding:
on the columnar path (single-weighted-matchkey by eligibility) and on the last/only weighted pass
of the list path, that set is **structurally dead** — nothing consumes it. So the fix was a free
`track_matched=False` guard, **no kernel**.

Measured on `large-new-64GB` (cProfile, 1M / 131M pairs):
- columnar path **351s → 168s**
- list path **579.6s → 377.8s**
- `builtins.min` on the columnar path: 131,177,554 calls / 37.3s → 11,173 calls / 1.5s

The answer to "should we add more Rust kernels for this?" was **no** — the hot spot was dead
Python bookkeeping. Keep that prior in mind: profile output flags *where time goes*, not *whether
the work is necessary*. Half the win here was deleting work, not speeding it up.

Artifacts that exist because of #837 and are useful to you:
- `packages/python/goldenmatch/tests/test_scorer_matched_pairs_parity.py` — the parity-harness
  pattern (exclusion-sensitive snapshot: `total_records`, cluster count, frozenset-of-frozenset
  members, sorted scored-pairs; plus a load-bearing "bites" test that proves skipping on a
  *consumed* pass changes the next pass's output). Copy this shape for any scoring/cluster change.
- `packages/python/goldenmatch/scripts/profile_hotspots.py` — the profiler. `_profile_list_path`
  and `_profile_columnar_path` both already pass `track_matched=False` to mirror the production
  single-matchkey path. Run it via the `profile-hotspots` GitHub workflow on `large-new-64GB`
  (pyinstrument ~5% overhead + cProfile ~30-40%; read both, trust pyinstrument for wall).

---

## The candidates (what the #837 bench surfaced next)

With `matched_pairs` removed, the list-path profile re-ranked. Three new top items, all measured at
1M / `large-new-64GB`, all **list path** (`backend` polars-direct / `score_blocks_parallel`):

| # | Symbol | Approx wall @1M | File:line |
|---|--------|-----------------|-----------|
| 1 | `_emit_scoring_profile` / histogram + dip stats | ~149s | `packages/python/goldenmatch/goldenmatch/core/scorer.py:41` (def); called at `scorer.py:1156` and `scorer.py:1240` |
| 2 | `_build_clusters_dict_path` | ~127s | `packages/python/goldenmatch/goldenmatch/core/cluster.py:840` |
| 3 | `compute_cluster_confidence` | ~58s | `packages/python/goldenmatch/goldenmatch/core/cluster.py:1290` |

### Candidate #1 — `_emit_scoring_profile` ✅ RESOLVED in PR #840

> **UPDATE (post-#840): the verify-first step was done, and the kill-hypothesis below is
> *refuted* — in the good direction.** The full production pass *does* run with a null emitter
> (every `profile_capture()` lives in the auto-config controller's sample iterations;
> `_run_dedupe_pipeline` / `run_dedupe_df` have none). **But "null emitter" does NOT mean
> "nothing to optimize"** — the original reasoning below missed that the expensive work
> (`histogram_20` + `hartigan_dip` over every scored pair + the `mass_*` passes) is computed
> *unconditionally, before* `current_emitter()` is ever consulted; only `set_scoring` is the no-op.
> So on the full pass that ~149s was **computed and immediately discarded** — the exact #837
> `matched_pairs` dead-work pattern, not a measurement artifact. Fix (PR #840): a
> `has_active_emitter()` predicate + an early `return` at the top of `_emit_scoring_profile`,
> before the profile is built. Byte-identical when a capture *is* active (the sample-iteration
> path the controller reads); scored-pair output unchanged on every path.
>
> **MEASURED (profile-hotspots, `large-new-64GB`, list path, run 27243161141 main vs 27243162275
> branch):** 1M wall **390.4s → 242.5s = −147.9s / −37.9%** (pyinstrument; cProfile 387.8s →
> 243.6s, −37.2%), output **byte-identical** (`n_pairs=131,166,381`, `n_clusters=5,407` on both;
> 100K also identical at `1,303,941` / `5,410`). cProfile attribution: `_emit_scoring_profile`
> (143.81s cumtime, was the #6 hotspot) **drops off the top-10 entirely**, and
> `score_blocks_parallel` cumtime falls 247.8s → 103.3s — the ~144s vanished exactly where the
> emit was, nothing else moved. The predicted ~149s materialized within a second. **Lesson for the
> next reader: "the consumer is a no-op when X" only kills a hot spot if the *production of the
> discarded value* is also gated on X — here it wasn't.**
>
> **Next target is now empirical:** with candidate #1 gone, `_build_clusters_dict_path`
> (`cluster.py:813`, ~139.5s cumtime / 39.5s tottime, unchanged across the two runs) is the #1
> hot spot on the post-fix list path — i.e. candidate #2 below, confirmed, and on the genuine
> production clustering path (no emitter gate).

Original analysis (kept for context; the blockquote's "artifact ⇒ nothing to optimize" branch is
the part that was wrong). This was the headline candidate **and the biggest trap.** Read
`scorer.py:41-68`:

```python
def _emit_scoring_profile(pairs, threshold, *, candidates_compared=0, per_field_variance=None):
    """Emit ScoringProfile to current emitter. No-op when emitter is null."""
    scores = [s for _, _, s in pairs]
    profile = ScoringProfile(
        n_pairs_scored=len(scores),
        candidates_compared=candidates_compared,
        score_histogram=histogram_20(scores),     # full-list pass
        dip_statistic=hartigan_dip(scores),        # O(n log n) sort over every scored pair
        mass_above_threshold=mass_above(scores, threshold),
        mass_in_borderline=mass_borderline(scores, threshold),
        per_field_score_variance=per_field_variance or {},
    )
    current_emitter().set_scoring(profile)
```

The docstring says **"No-op when emitter is null."** The architecture note (package CLAUDE.md):
*"Stage instrumentation in core/scorer.py emits sub-profiles via a thread-local ProfileEmitter
stack (zero cost when no capture is active)."* The auto-config **controller** captures the emitter
(it reads the `ComplexityProfile` from it to drive refit), so during sample iterations the emitter
is active. The question that decides whether candidate #1 is real or a profiling artifact:

> **On the final full-1M `run_dedupe` pass (not the sample iterations), is the ProfileEmitter
> active?** ~~If it is null there, the ~149s only exists because the profiler harness captures —
> i.e. it's a measurement artifact and there is nothing to optimize in production.~~ **(WRONG — see
> the RESOLVED note above: it IS null on the full pass, but the histogram/dip/`mass_*` are computed
> before the emitter is checked, so the cost is real dead work either way, deletable by an early
> return.)** If it IS active on the full pass, then `hartigan_dip(scores)` sorting all ~131M scored
> pairs is a genuine production cost and worth attacking.

**Do this before anything else.** Concretely:
1. Trace where capture is pushed/popped around the full dedupe (`grep -rn "bench_capture\|ProfileEmitter\|push.*emitter\|current_emitter" packages/python/goldenmatch/goldenmatch/core/`),
   and confirm whether the final `find_fuzzy_matches`/`score_blocks_parallel` call on the full
   frame runs under an active emitter or a null one.
2. Add a one-line `GOLDENMATCH_BUCKET_DEBUG`-style counter (or a temporary log) that fires only
   when `_emit_scoring_profile` does real work (emitter non-null) at full scale, and run the 1M
   bench *without* the profiler attached. If it never fires on the full pass → artifact, document
   it, and move to candidate #2. If it fires → measure its true wall and proceed.
3. Only if it's real: the cheap win is computing the histogram/dip on a **bounded sample** of the
   score array (the controller's signals are distribution shape, not exact order stats over 131M
   pairs) — or gating the dip statistic behind the planning-effort tier. A native kernel for
   `hartigan_dip` is the *last* resort and must clear the same "wall moved on 1M" bar.

This is the same trap #837 dodged: it looks like a fat hot spot, but it may be doing zero work on
the path users actually hit.

### Candidates #2 and #3 — clustering (`_build_clusters_dict_path`, `compute_cluster_confidence`)

These are on the genuine production path (clustering always runs). They're plausible real targets,
but **do not design from the profile alone** — get a stage-level wall split first. `compute_cluster_confidence`
(`cluster.py:1290`) computes `0.4*min_edge + 0.3*avg_edge + 0.3*connectivity` per cluster; at ~836K
clusters on 1M that's a lot of per-cluster Python. `_build_clusters_dict_path` (`cluster.py:840`)
is the dict-materialization path. Before touching either:
- Read both functions end to end and identify whether the cost is per-pair, per-cluster, or
  per-edge — that determines whether vectorization (Polars groupby/agg) even applies.
- Check whether the columnar cluster path (`build_clusters_columnar`, frame-backed) already avoids
  this — Layer 2 Phase 1 (PR #836, `33455242`) just removed the dominated `COLUMNAR_CLUSTER_BUILD`
  variant, so the columnar cluster story is mid-cutover. Coordinate with that work rather than
  optimizing a path that's being replaced. (See the columnar-frames plans under
  `docs/superpowers/plans/2026-06-09-columnar-frames-*` — those are local-only working notes, ask
  the repo owner for them.)

Recommended order: **#1 verify-or-kill → #2/#3 only after a clean stage-split bench confirms they
dominate the *post-#837, post-#836* wall on the path you intend to ship.**

---

## How to work this repo (constraints that will bite you)

- **GitHub auth dance.** `benseverndev-oss/*` (this repo) uses the **personal** account
  `benzsevern`, not the work account. `gh auth switch --user benzsevern` before any push/PR, and
  switch back to `benzsevern-mjh` immediately after. `gh pr create`/push may need an explicit
  `GH_TOKEN=$(gh auth token --user benzsevern)`.
- **Never run the full pytest suite locally** — xdist OOMs / DLL-contends the owner's Windows box.
  Targeted single-file runs are fine: `.venv/Scripts/python.exe -m pytest tests/test_X.py -x`.
  Run the *full* suite in CI (the PR's `python (goldenmatch)` lane). Set
  `POLARS_SKIP_CPU_CHECK=1` (and `PYTHONIOENCODING=utf-8`) or local polars import hangs on a WMI
  query at startup.
- **`docs/superpowers/` is local-only by convention** — specs/plans there are working notes; don't
  rely on them being in a fresh checkout and don't commit new ones. (This handoff lives under
  `docs/handoffs/` precisely so it *is* tracked and a remote checkout can read it.)
- **Branch/merge SOP.** Feature branch → squash merge via PR. `main` has strict-up-to-date branch
  protection (`ci-required` + head must be current), so a green PR can sit in `BEHIND` state and
  needs a branch-update before it merges; arm auto-merge + babysit the update loop.
- **Worktree/editable-install shadow.** The root `.venv` editable `.pth` may point at a *different*
  worktree than the one you're editing. In-editor pyright will throw false "No parameter named X"
  against a new kwarg until you `PYTHONPATH`-shadow your worktree source or run a fresh
  `python -m pyright`. CI builds from the branch, so it's clean regardless — don't chase the
  in-editor ghost.
- **pyright gotcha that already bit #837:** when you add a bool/object kwarg that flows through a
  kwargs dict built from `dict(some_str_dict)`, pyright infers `dict[str, str]` and rejects the
  bool. Declare `_kwargs: dict[str, object] = {}` then `.update(...)`. And keep test-file imports
  sorted (`ruff check --fix --select I001`) — the CI ruff lane fails on `I001`.
- **Profiling infra.** `profile-hotspots` workflow runs `scripts/profile_hotspots.py` on
  `large-new-64GB`. `GOLDENMATCH_BUCKET_DEBUG=1` prints a per-bucket prep/kernel/post-filter split
  for `backend=bucket` (zero cost, output-invariant) — handy for localizing "Polars wrapping vs
  the kernel." Bench/eval workflows default to `runs-on: large-new-64GB` (16c/64GB), not
  `ubuntu-latest`.
- **Native-kernel prior (if you end up there anyway).** `goldenmatch-native` is a separate
  maturin/abi3 wheel. Two hard-won lessons: (1) a perf fix must be verified by the *wall moving on
  the failing env*, not just the symbol shipping — a stale published wheel silently slow-falls-back
  (#688); (2) the rayon path in that kernel parked on a `LockLatch` futex on 8-core EPYC Linux —
  env-specific perf bugs are real, so any kernel claim must name the runner. ort/onnxruntime crates
  don't link locally on Windows; use `cargo check` + CI.

---

## Definition of done

A change here is done only when:
1. Parity is proven with an exclusion/output-sensitive harness in the
   `test_scorer_matched_pairs_parity.py` mold (byte-identical clusters + scored pairs on a
   multi-pass config, plus a load-bearing "bites" test if you add any conditional skip).
2. The **wall moved on the real 1M shape** on `large-new-64GB` (5-run median, profiler-off where
   the change concerns emitter/instrumentation cost — see candidate #1), not just cProfile cumtime.
3. CI is fully green (`python (goldenmatch)` full suite + `pyright` + `ci-required`).
4. If the honest finding is "this hot spot is a profiling artifact / already a no-op in
   production," that's a *complete and good* outcome — document it and stop. #837's sibling
   conclusion ("Stage 3 native kernel: dropped, measured-unnecessary") is the model.

---

## One-line orientation commands

```bash
# the three candidate symbols
grep -n "def _emit_scoring_profile\|def _build_clusters_dict_path\|def compute_cluster_confidence" \
  packages/python/goldenmatch/goldenmatch/core/scorer.py \
  packages/python/goldenmatch/goldenmatch/core/cluster.py

# where the scoring profile is emitted (is the emitter active on the full pass?)
grep -rn "current_emitter\|ProfileEmitter\|bench_capture" \
  packages/python/goldenmatch/goldenmatch/core/

# the profiler + harness from #837
sed -n '1,60p' packages/python/goldenmatch/scripts/profile_hotspots.py
```
