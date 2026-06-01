# Arrow-native finish-line roadmap

**Date:** 2026-06-01
**Status:** design (approved, pre-plan)
**Supersedes execution of:** `2026-05-31-arrow-native-roadmap.md` (the original
7-phase design). This document does NOT redefine the phases. The kill criteria
are carried over unchanged from the original spec, with one clarification: the
Phase 3 `dedup_pairs >= 5x` gate is taken from issue #625 (where `dedup_pairs`
became a real shipped kernel, PR #643), not from the original roadmap doc, which
listed `dedup_pairs` only as a future deliverable with no number. It re-casts
the work as a completion plan grounded in the actual state after the first-day
implementation burst (PRs #630-#653).

## Why this document exists

The original roadmap defined 7 phases, each with a binding kill criterion
measured in CI. Over 2026-05-31 -> 06-01, PRs #630-#653 landed a large amount
of the capability, but an audit on 2026-06-01 found a consistent pattern:

- Capability + parity tests were built as **additive siblings behind opt-in
  gates** (`GOLDENMATCH_COLUMNAR_PIPELINE`, the `native_enabled(...)` flags).
- The **cutover was not done**: the legacy `list[tuple]` pair stream and
  `dict[int, dict]` cluster representation are still the defaults, ~20 callers
  still consume the legacy shapes, and `materialize_cluster_dict` is still on
  the distributed path.
- The **binding kill-criterion benches were never run**. Issues #623-#628 are
  all open and partial; #629 (Phase 7) is correctly unstarted.

So "cross the finish line" is not new design. It is: measure the built-but-gated
capability, then cut it over to default in dependency order, retiring the legacy
paths and closing the issues.

## Decisions that shape this plan

1. **Finish line = validate, then full cutover.** Columnar/native becomes the
   DEFAULT path; the legacy `list[tuple]` / `dict[int, dict]` paths are retired;
   the ~20 callers are migrated; issues #623-#629 are resolved.
2. **Gate policy = hold and optimize to the gate.** A phase does not cut over
   until its kill-criterion bench clears the original target. If a bench comes
   in below target, profile and tune until it passes. (Stall backstop below.)
3. **Phase 7 (DataFusion B2 / #629) stays gated.** No DataFusion code until a
   100M-on-one-box bucket bench (run after Phases 1-3 cut over) says bucket
   strains at >100M. Otherwise shelve and close #629.

## Approach: measurement-first sweep, then dependency-ordered cutover

```
Stage 0  Bench sweep ── measures Phases 1,2,3,4,6 against current gated capability
   |                    (Phase 5 not built enough to sweep; benched after it lands)
   v
Phase 1  Pair stream ──► Phase 2  Cluster columnar ──► [ Phase 3  Native kernels
   (substrate)                                          [ Phase 4  Golden columnar
                                                              |
                                                              v
Phase 6  Standardization (parallel, independent surface)  Phase 5  Identity per-partition
                                                              |
                                                              v
                                                        Phase 7  DataFusion B2 (GATED)
```

Rationale: the "hold and optimize to the gate" policy makes "which gates pass?"
the pivot of the entire plan, and that is currently unknown. A cheap upfront
sweep (the capability already exists behind flags) turns that unknown into a map
before investing in the wide-surface cutover and caller-migration work. Cutover
then proceeds in strict dependency order so Phase 1's columnar pair stream (the
substrate the rest consume) lands before anything builds on it.

## Stage 0: Bench sweep

A single job that runs every existing kill-criterion bench against the
capability already behind the columnar/native flags, producing one decision
artifact before any cutover starts. Estimated 2-3 days.

- **Fixture:** `realistic_person` (the profiler already uses it). The original
  spec's degenerate Day-3 fixture is retired. Confirm the fixture scales to
  5M / 25M; generate larger inputs via the Railway `goldenmatch-bench-gen`
  service if the in-repo fixture is too slow to build at scale.
- **Scales / box:** 5M and 25M on `large-new-64GB`, matching each phase's kill
  criterion (Phase 1: 5M; Phases 2/4: 25M; Phase 3: 5M; Phase 6: 10M).
- **Per phase, record:** columnar/native wall + peak RSS vs the legacy path,
  and parity (byte-identical pairs/fingerprints, Rand index 1.0 on cluster
  assignments).
- **Output:** a results table appended to this spec, classifying each phase:
  - **PASS** - gate already met; cutover is pure plumbing.
  - **CLOSE** - beats legacy but under target; needs optimization.
  - **BLOCKED** - parity fails, or not measurable yet (Phase 5).
  This table drives all downstream effort allocation. Because
  `docs/superpowers/specs/` is gitignored (commit with `git add -f`), the
  Stage-0 results table is committed back into this spec so the gate decisions
  are version-controlled and do not get lost.

## The per-phase cutover unit

Every phase after Stage 0 runs the same four steps:

1. **Bench** - take the Stage-0 number (or run it fresh, for Phase 5).
2. **Optimize to gate** - if below target, profile + tune until it clears.
   **Stall backstop:** if two optimization passes do not reach the gate, stop
   and escalate with the measured ceiling and a recommendation
   (cut-over-anyway / re-scope / shelve). This is the only place "hold and
   optimize" is allowed to terminate without clearing the bar.
3. **Cut over** - flip the default to columnar/native, migrate the callers,
   retire the legacy path (see retirement strategy below).
4. **Close** - record the passing bench in this spec; close the issue.

### What cutover means per phase (from the audit gap list)

| Phase | Gate (must clear first) | Cutover work |
|---|---|---|
| **1** pair stream (#623) | handoff wall <= 50%, RSS <= 25% @5M, byte-identical | `find_fuzzy_matches` returns `pl.DataFrame` by default; migrate ~20 `scored_pairs: list[tuple]` callers (web/MCP/REST/lineage); add CI lint banning the annotation |
| **2** cluster columnar (#624) | RSS -30% @25M, `materialize_cluster_dict` retired | `build_clusters` returns the two-frame shape; move `pair_scores` + confidence to the lazy view; adapt `unmerge_record`/`unmerge_cluster` |
| **3** native kernels (#625) | dedup >= 5x, build_clusters >= 2x, fingerprints >= 3x @5M (each vs its pure-Python loop baseline); parity | wire native default; close the `build_clusters_arrow` scope gap (auto-split, weak-cluster downgrade, quality) so it is a true drop-in for `build_clusters` |
| **4** golden columnar (#626) | golden <= 60s, RSS <= 60% @25M | distributed `build_golden_records` consumes `ClusterFrames`; drop `materialize_cluster_dict` from the distributed path |
| **5** identity per-partition (#627) | wall <= 50%, driver RSS <= 10% @25M/4-worker | real `ds.map_batches(per_partition_resolver)` with a per-worker pool budget (only the `partition_cluster_frames` helper exists today) |
| **6** standardization (#628) | `apply_standardization` <= 20s @10M, zero full-df `map_elements` | replace the justified-but-present `map_elements` sites in `standardize.py`/`matchkey.py` with native Polars/Arrow; the `check_map_elements.py` CI lint already guards regressions |

## Sequencing and dependencies

- Strict dependency order: **1 -> 2 -> {3, 4} -> 5**. Phase 1's columnar pair
  stream is the substrate Phases 2-5 consume, so it lands first despite being
  the widest surface; Stage 0 gives early signal to offset that front-loaded
  risk.
- **Phase 6 runs in parallel from day one** (prep stage, independent surface).
- **Phase 5** is gated on its bench being run after it is actually built (it is
  the least-complete phase today: only the partitioner helper exists).
- **Phase 7** decision happens after Phases 1-3 cut over.

## Cross-cutting concerns

### Legacy retirement: one-release deprecation window per phase

Full cutover means deleting the legacy `list[tuple]` / `dict[int, dict]` paths,
but deleting them in the same PR that flips the default is where silent breakage
lives (the audit counted ~20 live `scored_pairs` consumers across web/MCP/REST/
lineage). So per phase:

- **Release N:** columnar/native becomes the default; the legacy path stays
  reachable behind the existing flag as an escape hatch; callers migrated; a CI
  lint bans new legacy-shape annotations.
- **Release N+1:** delete the legacy path + the flag once the default has soaked
  with no regressions.

This keeps a one-release rollback lever and turns "did we miss a caller?" from a
silent behavior change into a flag-toggle diagnosis.

### Parity discipline

Cutover is blocked until parity is proven at bench scale, not just unit scale:
byte-identical pairs/fingerprints, Rand index 1.0 on cluster assignments,
identical golden output. The parity tests from PRs #631-#650 already exist per
phase; Stage 0 and each cutover re-run them at 5M / 25M, not only the fixture
sizes they currently cover.

### Phase 7 decision gate (DataFusion B2 / #629)

Stays closed-but-open. After Phases 1-3 cut over, run a **100M-on-one-box
bucket+native bench** on `large-new-64GB` (the strategic "do we need a query
engine at all?" question). Then a one-page decision artifact:

- If bucket holds 100M in <= 30 min: **shelve Phase 7, close #629** ("DataFusion
  not needed at this scale"), and remove the `[datafusion]` spike extra.
- If bucket strains: green-light the Rust-ScalarUDF / PyCapsule build per the
  #629 deliverables.

No DataFusion code is written until that bench decides.

## Definition of done (the finish line)

- Stage 0 sweep recorded; every phase bench at its kill scale recorded in this
  spec.
- Phases 1-6 cut over: columnar/native is the DEFAULT, legacy paths deleted
  (after the N+1 window), issues #623-#628 closed.
- Phase 7 decided (shipped or shelved); #629 resolved either way.
- The 100M-on-one-box number is published, which also answers the standing
  "do we need Ray?" question from 2026-05-30.

## Effort estimate

- Stage 0 sweep: 2-3 days.
- Cutover per phase: the original spec's estimates still hold for the
  optimize+migrate work (Phase 1: 1-2 wk, Phase 2: 1-2 wk, Phase 3: ~1 wk,
  Phase 4: 3-5 d, Phase 5: ~1 wk, Phase 6: 1-2 wk parallel). Phases that come
  back PASS in Stage 0 collapse to plumbing + caller migration only (days, not
  weeks).
- Total: ~5-7 weeks solo, less if Stage 0 shows several gates already PASS.

## What this plan explicitly does NOT do

- Re-open the phase definitions or kill criteria (unchanged from the original).
- Adopt DataFusion/Sail/Polars Cloud as a default backend (Phase 7 is gated and
  exploratory; bucket+native stays the production winner).
- Touch the Identity Graph schema or the auto-config controller.

## References

- Original design: `2026-05-31-arrow-native-roadmap.md`.
- Implementation burst: PRs #630-#653 (capability + parity behind gates).
- Audit: 2026-06-01 open-issue audit (per-phase shipped/partial/open verdicts).
- Profiler evidence: run 26759043853 (columnar 354.94s vs list 564.33s @1M;
  remaining cluster-build hotspot is `compute_cluster_confidence` + the
  131M-pair min/max/set.add fill -- exactly the Phase 2/3 cutover lever).
- Bucket+native baseline: 25M-on-one-node 6.5min / 57.7 GB (run 26095134836).
