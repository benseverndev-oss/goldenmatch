# FS work handoff — accuracy investigation + historical_50k speed

Session context for resuming in a fresh session (GH MCP was erroring on
`create_pull_request`/`get_me` — retry there). Bash was intermittently blocked by
a platform classifier outage at handoff time; some verification steps are staged
but unrun.

## TL;DR of what to do next (priority order)

1. **Preserve the native disagree-mode work** (currently UNCOMMITTED in the
   working tree, see §3). Move it to its own branch `feat/fs-native-disagree-mode`
   off `main`, commit, push. It's cleanly separable from the batch fix (touches
   different files).
2. **Run the staged verification** (`scratchpad/verify_disagree.py`) + the FS test
   suite to confirm the native-disagree partition churn is inherent rapidfuzz
   tolerance, not a bug (see §3).
3. **Create the two draft PRs** that GH MCP wouldn't create this session (§2).
4. **(Optional) push historical toward the ~20s target** — native disagree got it
   to 34.6s; the residual is 7-pass re-scoring (§4).

## 1. The FS-accuracy verdict (out-of-panel validation) — DONE, decisive

The three open FS-accuracy PRs (#2078 Otsu threshold calibration, #2095
evidence-cut, #2091 post-blocking-u) each post a huge `dblp_acm` control win
(+0.42–0.49 F1) that **does NOT generalize**. Built an out-of-panel harness
(`scripts/bench_er_headtohead/validate_fs_holdout.py` + 3 held-out dataset loaders
`febrl4`/`dblp_scholar`/`amazon_google` in `datasets.py`) and measured OFF-vs-ON
on control (in-panel) + holdout:

| lever | dblp_acm (ctrl) | febrl3 (ctrl) | febrl4 | dblp_scholar | amazon_google |
|---|---|---|---|---|---|
| #2078 Otsu | +0.486 | +0.002 | +0.000 | +0.002 | +0.000 |
| #2095 evidence-cut c=9 | +0.424 | +0.001 | +0.000 | **+0.061** | **-0.060** |
| #2091 post-blocking-u | +0.494 | +0.002 | +0.001 | +0.009 | +0.000 |

**Verdict: no lever robustly moves FS accuracy.** `dblp_acm`'s OFF baseline is a
pathological under-merger (recall 0.27 at the fixed 0.50 cut, blocking_recall 1.0)
— every lever just lowers its effective bar. Nothing transfers to held-out data.
#2095 is the only one with real out-of-panel signal (+0.06 dblp_scholar) but a
fixed `c` kills product (-0.06). Recommendation given to user: don't flip any
default; the harness is the durable deliverable (every future lever gets
out-of-panel-validated).

The harness + 3 loaders are **committed** on `feat/fs-threshold-calibration`
(commit 8ba1101f) and pushed.

### What WILL move accuracy (measured loss decomposition)
`blocking_recall` (recall ceiling) vs `threshold_loss` per dataset showed the
campaign tuned the ONE stage (threshold) that only helps the ONE dataset whose
problem is threshold (dblp_acm). Real levers:
- **Blocking recall** — held-out sets cap recall at 0.38 (dblp_scholar) / 0.43
  (amazon_google) *before scoring*. Semantic/embedding blocking is the lever.
- **Comparison-model precision** where blocking is complete (dblp_acm P~0.80).
- **historical_50k specifically**: P0.91/R0.76/F1 0.826, blocking_recall 0.779,
  frontier-limited. Threshold slides the frontier the WRONG way (Otsu made it
  0.826→0.761). Levers that LIFT it: blocking recall (missing 22%) + namesake
  discrimination (negative evidence / TF weighting / transitive-consistency
  postflight #2084). NOT threshold.

## 2. Branches / PRs state

- **`feat/fs-threshold-calibration`** (PR #2078, draft, OPEN): rebased onto
  current main this session (was `dirty`→now `blocked`=CI gate). HEAD `bb693731`.
  Contains Otsu calibration (opt-in, default OFF) + the out-of-panel harness.
  CI run 30136747806 was green on all drift/gate lanes; heavy python matrix was
  still running with zero failures. **This session is SUBSCRIBED to PR #2078
  activity.**
- **`perf/fs-bucket-batch-disagree`** (PUSHED, commit `b53aeaf8`; draft PR NOT
  created — GH MCP failed): the batch fix (§3a). Independent standalone perf win.
  **Create its draft PR** (base main). Body is in the session transcript / below.
- **`feat/fs-native-disagree-mode`**: DOES NOT EXIST YET — create it and move the
  uncommitted native disagree work here (§3b).
- Designated branch `claude/benchmark-failure-gh-7h5ryr` (out-of-core work) — its
  PR #1944 was CLOSED by the user (superseded by main's #1973 etc.). Done.

## 3. historical_50k speed — root cause + two fixes

**Root cause:** `historical_50k` is null-heavy → auto-config picks
`missing="disagree"` (a precision feature: null field = evidence against). The
native FS kernel only did "unobserved" nulls, so `_fs_native_eligible` hard-
declined disagree → numpy `prob_scorer` ran **once per block**; the 7-pass
blocking scheme has passes with ~6000 tiny blocks → per-call fan-out = **~95s**
for a 50k dedupe (nearly all in `score_buckets`; confirmed by cProfile +
GOLDENMATCH_BUCKET_DEBUG).

### 3a. Batch fix — COMMITTED + PUSHED (`perf/fs-bucket-batch-disagree`)
Wired the existing #869 batched matrix scorer into `score_buckets.py::
_score_one_bucket`'s non-native branch: coalesce small blocks into row-capped
(`GOLDENMATCH_FS_BATCH_ROWS`, default 256) `score_probabilistic_vectorized_batch`
calls instead of per-block. New flag `GOLDENMATCH_FS_BUCKET_BATCH` (default ON,
`=0` escape hatch). **116s→47s (2.47x), BYTE-IDENTICAL** (clusters 9300 +
partitions identical batch-on vs off). 204 FS tests green.

### 3b. Native disagree-mode — IMPLEMENTED, REBUILT, UNCOMMITTED, verification pending
Made the native FS kernel handle `missing="disagree"` so historical runs native.
**Uncommitted working-tree changes (5 files):**
- `packages/rust/extensions/fs-core/src/lib.rs`:
  - Added `pub missing_disagree: bool` field to `FsPairParams` (after
    `require_positive_evidence`).
  - In `score_fs_pair`, the field loop's null branch: `} else if p.missing_disagree {
    has_regular_evidence = true; total_weight += p.match_weights[f][0]; }` (mirrors
    numpy `lvl=where(observed,lvl,0); observed=all True`).
  - Added `missing_disagree: false,` to the ~6 test constructors + a new unit test
    `score_fs_pair_missing_disagree_adds_level0_weight` (asserts 8/18 vs 12/18).
    **fs-core: 20 tests green.**
- `packages/rust/extensions/native/src/score.rs`: both `score_block_pairs_fs` and
  `score_block_pairs_fs_arrow` got `missing_disagree=false` in the pyo3 signature,
  `missing_disagree: bool` fn param, and `missing_disagree,` in the FsPairParams
  constructor.
- `packages/rust/extensions/native/src/lib.rs`: added
  `m.add("FS_SUPPORTS_MISSING_DISAGREE", true)?;` capability flag.
- `packages/rust/extensions/fs-wasm/src/lib.rs`: `missing_disagree: false,` in its
  FsPairParams constructor (wasm behavior unchanged).
- `packages/python/goldenmatch/goldenmatch/core/probabilistic.py`:
  - `_fs_native_eligible`: replaced the hard `if fs_missing_mode(mk)=="disagree":
    return False` with `needs_disagree = fs_missing_mode(mk)=="disagree"` + a gate
    inside the capability block: `if needs_disagree and not getattr(mod,
    "FS_SUPPORTS_MISSING_DISAGREE", False): return False`.
  - In the opt_kwargs builder (near the `require_positive_evidence` kwarg): `if
    fs_missing_mode(mk)=="disagree" and getattr(mod,"FS_SUPPORTS_MISSING_DISAGREE",
    False): opt_kwargs["missing_disagree"] = True`.
- Native rebuilt: `.venv/bin/python scripts/build_native.py` (~70s). The
  `_native.abi3.so` in the tree already has the disagree kernel.

**Measured:** historical native disagree **34.6s** vs numpy-disagree 57s (1.65x);
~3x over the original ~95-116s. `native_eligible=True`, `missing_mode=disagree`
confirmed. Cluster COUNT identical (9300), but **109/95 multi-member partitions
differ** (~1-2% churn).

**The parity question to close (staged, unrun — Bash was down):** the churn is
almost certainly the inherent native-vs-numpy rapidfuzz tolerance (rust rapidfuzz
vs py rapidfuzz on OBSERVED fields), NOT a disagree bug — the fs-core unit test
proves the disagree weight math is exact, the min-max range is computed
identically both sides, and a systematic weight bug would move the cluster COUNT
(it didn't). Confirm with:
- `python scratchpad/verify_disagree.py` (staged in scratchpad; may not survive a
  fresh container — re-create from this doc if gone): runs historical native-vs-
  numpy in BOTH `disagree` and `unobserved` modes (via GOLDENMATCH_FS_MISSING). If
  the partition churn is ~equal in unobserved mode, it's inherent tolerance, not
  disagree-specific → acceptable under "Rust is the reference".
- `pytest tests/test_probabilistic.py tests/test_native_parity.py
  tests/test_fs_autoconfig_v2.py` (env: GOLDENMATCH_NATIVE=1 GOLDENMATCH_FS_NATIVE=1
  GOLDENMATCH_AUTOCONFIG_MEMORY=0). Expect green (native parity is tolerance-gated).

If verification is clean: commit native disagree-mode on `feat/fs-native-disagree
-mode`, push, draft PR. This is a bigger/riskier change than the batch fix (Rust
kernel + parity posture: native≠numpy byte-identical, within-tolerance, Rust is
reference) — flag that in the PR body. Consider whether the batch fix (3a) is
still worth landing given native disagree supersedes it for the default path (it
still helps the GOLDENMATCH_FS_NATIVE=0 / unsupported-scorer fallback).

## 4. Path to the ~20s target (not done)
Native disagree = 34.6s. Residual: ~15s in the 7 blocking passes re-scoring
(~2.66M pairs scored across passes but only ~1.1M unique → **cross-pass candidate
dedup** is the lever, the known-unmet #869 follow-on) + ~19s non-scoring
(GoldenCheck scan ~4s, EM, auto-config, cluster, golden). Cross-pass dedup is a
structural change to the bucket route with real parity risk — measure first.

## 5. Env / gotchas for resuming
- Native rebuild: `.venv/bin/python scripts/build_native.py` (~70s). Rebuild after
  ANY Rust change OR after switching branches whose Rust source differs (stale
  `.so` silently gives wrong results — bit us this session).
- `GOLDENMATCH_AUTOCONFIG_MEMORY=0` on every bench/parity run (cross-run leak).
- Datasets (all gitignored under `packages/python/goldenmatch/tests/benchmarks/
  datasets/`): `historical_50k` via `splink_datasets` (bench extra). Held-out sets
  need `uv pip install recordlinkage` (febrl3/4) + vendored Leipzig CSVs — download
  with `curl -fsSL https://dbs.uni-leipzig.de/file/{DBLP-ACM,DBLP-Scholar,Amazon-
  GoogleProducts}.zip` and unzip into the matching `datasets/<Name>/` dirs (use
  ABSOLUTE paths — shell cwd drifts to scripts/bench_er_headtohead across bg runs,
  which sent a download to the wrong place this session).
- Push auth: `benzsevern`. Commit trailers required (Co-Authored-By: Claude Opus
  4.8 + Claude-Session). NEVER put the model id in commits/PRs.
- GH MCP: `create_pull_request` was failing with "invalid session" — retry in the
  fixed session; `get_me` first may re-init.

## 6. Draft PR bodies to create (GH MCP)
**perf/fs-bucket-batch-disagree** (base main, draft): title "perf(fs): batch small
blocks on the non-native bucket path (2.5x on historical_50k)". Body: the batch
fix explanation from §3a — 116s→47s byte-identical, GOLDENMATCH_FS_BUCKET_BATCH
flag, 204 tests green, residual = cross-pass dedup + native disagree.

**feat/fs-native-disagree-mode** (base main, draft, AFTER commit+verification):
title "perf(fs): native missing=disagree kernel support (historical_50k off the
numpy fallback)". Body: §3b — root cause, FS_SUPPORTS_MISSING_DISAGREE, 34.6s
(~3x), parity is within-tolerance (Rust is reference), fs-core unit test + native
parity suite.
