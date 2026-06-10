# Handoff: Layer 2 columnar/frames orchestration -- Phase 4 decision + docs PR

**Date:** 2026-06-09
**For:** a fresh Claude (cloud session) picking this up with zero conversation history
**Repo:** `benseverndev-oss/goldenmatch` (monorepo), work off `origin/main`
**Status:** Phases 0-3 DONE and merged. ONE decision is pending (Ben's call), then a single docs PR (plus a 3-line code flip if the decision is FLIP).

---

## TL;DR of what you're inheriting

`GOLDENMATCH_CLUSTER_FRAMES_OUT` is a default-OFF env gate that swaps the cluster->golden->identity
pipeline from the per-cluster `dict[int, dict]` representation to a dict-free two-frame columnar
representation (the "frames-out" path). We ran the verdict bench. The result is **ambiguous-middle**,
not a clear flip and not a clear delete:

- **~2.1x wall win** at both 25M and 100M pairs (golden stage alone 14-15x).
- **RSS regression that converges to zero at scale**: +14% at 25M, +0.5% at 100M. Neither path OOM'd at 100M.
- The roadmap's "finishes where dict OOMs" thesis did **not** materialize (dict survived 100M on the 64GB box).

So: **delete is off the table** (it's a real 2.1x win). The choice is **FLIP vs HOLD**.
My recommendation is **FLIP** to default-ON. The only thing arguing for HOLD is Ben's standing
RSS constraint ("hold wall+accuracy, DROP peak RSS"); flip is RSS-neutral at scale but +14% at 25M,
so a strict reading of that rule favors HOLD-and-revisit.

**This decision is Ben's.** Do not flip without his explicit "flip" / "hold" / "delete". Present the
verdict table below and let him choose. Then execute the matching branch in "What to do next".

---

## Where the work already landed (all on `origin/main`)

- **#836** (`33455242`) -- Layer 2 Phase 1: removed the dominated `COLUMNAR_CLUSTER_BUILD` (SP1) gate
  and its dispatch (`_columnar_cluster_build_enabled`, the `build_clusters` SP1 branch,
  `_build_clusters_via_frames`). KEPT the frames-out machinery.
- **#837** (`92bd74cf`) -- a SEPARATE scorer-path perf fix (`track_matched` guard on
  `score_blocks_columnar`). This is the `GOLDENMATCH_COLUMNAR_PIPELINE` axis (Phase 5 territory),
  NOT the frames-out decision. Already merged; mentioned so you don't conflate the two axes.
- Three planning docs already on main under `docs/superpowers/plans/`:
  - `2026-06-09-columnar-frames-layer2-verdict-roadmap.md` -- the verdict-first decision roadmap (read this first).
  - `2026-06-09-columnar-frames-layer2-phase0-phase1.md` -- the Phase 0 ledger + Phase 1 deletion plan.
  - `2026-06-09-columnar-frames-layer2-ledger.md` -- the deletion manifest from Phase 0.

The verdict numbers below are NOT yet in the repo. Recording them is the point of the docs PR.

---

## The verdict table (Phase 3 -- complete-path bench)

Harness: `packages/python/goldenmatch/scripts/bench_pipeline_complete_path.py`
Workflow: `.github/workflows/bench-pipeline-complete-path.yml` (`workflow_dispatch`, `runs-on: large-new-64GB`, native built fresh).
`--np` = target PAIR count (self-generated, no dataset dependency). Each variant runs in its own subprocess.
Peak RSS via `resource.getrusage` (Linux-only). The terminal `results["clusters"]` rebuild and IdentityStore
I/O are EXCLUDED from the measured window so they don't mask the delta. Built-in `_membership_sanity`
parity check passed on every run.

| Scale | Path     | Wall      | Peak RSS    | vs legacy        |
|-------|----------|-----------|-------------|------------------|
| 25M   | legacy (dict)        | 94.7s | 17,035 MB | --                       |
| 25M   | columnar (frames-out)| 42.3s | 19,424 MB | **2.24x wall**, **+14% RSS** |
| 100M  | legacy (dict)        | 380.5s| 61,062 MB | --                       |
| 100M  | columnar (frames-out)| 184.4s| 61,390 MB | **2.06x wall**, **+0.5% RSS** |

- 25M run: GitHub Actions run `27232298368`.
- 100M run: GitHub Actions run `27234343909`. Neither path OOM'd.
- Golden stage in isolation: 14-15x faster on the frames-out path.

Reading: the +14% RSS at 25M is the small-N Arrow-overhead false-negative the roadmap predicted.
It converges to noise (+0.5%) at 100M. There is no OOM divergence -- dict did not fall over at 100M
on the 64GB box, so "frames-out finishes where dict can't" is unproven at this scale. The win is wall, not headroom.

---

## Phase 2 (verification) -- already satisfied, NO code needed

The roadmap required a frames-vs-dict parity test before any flip. It already exists on main:

- `packages/python/goldenmatch/tests/test_cluster_frames_out_parity.py` -- cluster+golden frames-vs-dict parity;
  carries the cascading-split adversarial fixture (`_adversarial_pairs`, Group A ids 40-48, Group B ids 50-57).
  In #836 its reference build was repointed from `COLUMNAR_CLUSTER_BUILD=1` to plain `build_clusters`.
- `packages/python/goldenmatch/tests/test_identity_from_frames_parity.py` -- SP-C identity parity:
  `test_frames_path_partition_matches_dict_path` (native + off-native, entity-partition equality, anti-vacuous
  `edges_added>=1`) and `test_frames_path_literal_identical_under_deterministic_mint`.
- `packages/python/goldenmatch/tests/test_pipeline_frames_out_parity.py` -- end-to-end pipeline frames-vs-dict parity.

These run in the `python (goldenmatch)` CI lane. The bench's own `_membership_sanity` is a third, runtime check.
SP-C (identity-from-frames) is confirmed landed. So Phase 2 is "verify it's green", not "write it".

---

## The flip site (only relevant if Ben says FLIP)

File: `packages/python/goldenmatch/goldenmatch/core/cluster.py`, function `_cluster_frames_out_enabled()`.
On current main it is:

```python
def _cluster_frames_out_enabled() -> bool:  # pyright: ignore[reportUnusedFunction]
    ...docstring...
    return os.environ.get("GOLDENMATCH_CLUSTER_FRAMES_OUT", "0").strip() != "0"
```

The flip is the default sentinel: `"0"` -> `"1"` so the gate is ON when the env var is absent and can still
be force-disabled with `GOLDENMATCH_CLUSTER_FRAMES_OUT=0`:

```python
    return os.environ.get("GOLDENMATCH_CLUSTER_FRAMES_OUT", "1").strip() != "0"
```

Also remove the `# pyright: ignore[reportUnusedFunction]` and the "Default OFF / not consumed in SP-A"
docstring lines -- they are stale once the gate is the default. Update the docstring to say "Default ON;
set GOLDENMATCH_CLUSTER_FRAMES_OUT=0 to force the legacy dict path."

**Gate consumer** (for context, do NOT change the wiring -- only the default flips):
`packages/python/goldenmatch/goldenmatch/core/pipeline.py`
- `:1662` `elif _cluster_frames_out_enabled():` -> `build_cluster_frames(...)`
- `:1795-1820` golden via `build_golden_records_from_frames(...)`
- `:2082-2098` identity pair-score-view via `ClusterPairScores.from_frames(...)`

When the gate is OFF, the legacy path passes `pair_score_view=None` + the real-pairs dict (byte-identical to today).

---

## INVARIANT that survives any decision (do not break)

The DataFusion spine imports `build_cluster_frames`, `build_golden_records_from_frames`,
`cluster_frames_to_dict`, and `ClusterPairScores.from_frames`. Even under HOLD (gate stays default-off) these
symbols and the frames-out code path MUST remain. Do not "clean up" the frames-out machinery as dead code --
it is the spine's dependency. Grep before deleting anything:

```
grep -rn "build_cluster_frames\|build_golden_records_from_frames\|cluster_frames_to_dict\|ClusterPairScores.from_frames" packages/python/goldenmatch/goldenmatch/spine* packages/python/goldenmatch/goldenmatch/datafusion*
```

---

## What to do next (pick the branch Ben chose)

Branch off `origin/main`. Follow the suite branch/merge SOP: feature branch, squash-merge via PR, clean history.
`main` requires `ci-required` + strict up-to-date head, so arm auto-merge and babysit the update-branch loop on a cascade.

### If FLIP (recommended)
1. Make the 3-line default flip in `cluster.py` (above) + docstring scrub.
2. Confirm the parity tests still assert frames-vs-dict equality with the gate now default-on -- the tests force
   the env var explicitly on the columnar arm and force `=0` on the legacy arm, so they should stay valid either
   way. Re-read each test's setup to be sure it does NOT rely on the default being OFF; if any test reads the
   default for its "legacy" arm, pin it to `GOLDENMATCH_CLUSTER_FRAMES_OUT=0` explicitly.
3. One docs PR (can be the same PR or a follow-up) that records: Phase 2 verification (tests green in CI),
   the Phase 3 verdict table above, and the Phase 4 decision = FLIP with the RSS-tradeoff rationale. Update
   `2026-06-09-columnar-frames-layer2-verdict-roadmap.md` to mark the decision resolved.
4. Let CI settle (especially the `python (goldenmatch)` and `native` lanes -- the native cluster path is only
   validatable in CI). Do NOT auto-merge; hand back to Ben for the merge.

### If HOLD
1. No code change. Gate stays default-off.
2. One docs PR recording: Phase 2 verification, the Phase 3 verdict table, and Phase 4 decision = HOLD because
   the +14% RSS at 25M violates the strict "drop peak RSS" constraint and there's no scale-unlock (no OOM
   divergence at 100M) to justify the regression. Note the revisit trigger: if a workload appears where dict
   actually OOMs and frames-out survives, re-open. Mark the roadmap decision resolved=HOLD.

### If DELETE (not recommended -- here for completeness)
Do not delete. It's a 2.1x wall win and the spine depends on the symbols (see INVARIANT). If Ben insists,
the delete is much larger than #836 (it pulls the consumer wiring in pipeline.py and would orphan the spine)
-- push back and re-confirm before touching anything.

---

## Operational constraints (carry these forward)

Some are specific to Ben's Windows laptop. A cloud Linux session can ignore the laptop-only ones, but the
auth, merge, and ASCII rules are universal.

- **No local pytest / import / uv on Ben's Windows box** (LAPTOP-ONLY): importing polars/torch spawns zombie
  python processes that starve the box into fork-starvation. On the laptop, validate with `ruff` + `py_compile`
  only and lean on CI. A cloud Linux runner CAN run pytest normally -- but the native cluster path is still
  CI-validated regardless, because the native wheel build is CI-shaped.
- **GitHub auth dance** (UNIVERSAL for this org): `benseverndev-oss/*` and `benzsevern/*` use the personal
  account `benzsevern`, not work `benzsevern-mjh`. `gh auth switch --user benzsevern` before any push /
  workflow dispatch / PR create, then ALWAYS switch back to `benzsevern-mjh` after. `gh pr create` and
  `gh workflow run` may need `export GH_TOKEN=$(gh auth token --user benzsevern)` even after the switch.
- **RSS optimization constraint** (UNIVERSAL): hold wall + accuracy, DROP peak RSS. Emit per-phase RSS markers
  before proposing any "fix". This is the rule the FLIP tension is about.
- **Bench/eval workflows default to `runs-on: large-new-64GB`** (16c/64GB), not `ubuntu-latest`.
- **ASCII-only in PR / release bodies** -- no em-dashes. `gh release --notes` rejects em-dashes (422 from the API).
  `gh repo edit --description` rejects strings >350 chars (422).
- **Never auto-merge to main without Ben's explicit approval.** Open the PR, get CI green, hand back.
- **Commit only when asked.**
- **`continue-on-error: true` lies in JSON**: a step's `conclusion: success` in `gh run view --json` does NOT
  mean pytest passed. Grep the raw log for the pytest summary line (`gh run view <id> --log | grep -E "passed|failed,"`).
- **CI poll loop**: `while gh pr checks <N> | grep -qE "pending|in_progress"; do sleep 30; done` (the naive
  `grep -qv pending` is wrong -- it returns true on the first non-pending line).

---

## Quick-start commands for the picker-upper

```bash
# 1. Sync and read the existing roadmap + ledger.
git fetch origin main && git checkout origin/main
cat docs/superpowers/plans/2026-06-09-columnar-frames-layer2-verdict-roadmap.md
cat docs/superpowers/plans/2026-06-09-columnar-frames-layer2-ledger.md

# 2. Confirm Phase 2 parity tests exist and the flip site is intact.
ls packages/python/goldenmatch/tests/test_*frames*parity.py
grep -n "GOLDENMATCH_CLUSTER_FRAMES_OUT" packages/python/goldenmatch/goldenmatch/core/cluster.py

# 3. Confirm the spine invariant before touching frames-out machinery.
grep -rn "build_cluster_frames\|build_golden_records_from_frames\|cluster_frames_to_dict\|ClusterPairScores.from_frames" \
  packages/python/goldenmatch/goldenmatch/

# 4. (Re-run the verdict bench only if you doubt the numbers -- otherwise trust the table.)
#    Needs the benzsevern auth + dispatch against benseverndev-oss/goldenmatch.
gh workflow run bench-pipeline-complete-path.yml -f np=25000000 -f runs=1
```

---

## Open items beyond this decision

- **Phase 5**: the `GOLDENMATCH_COLUMNAR_PIPELINE` scorer-gate axis (separate from frames-out). #837 already
  shaved the dead `matched_pairs` bookkeeping on that path. The default-on decision for the columnar scorer is
  its own verdict-first exercise -- untouched, queued.
