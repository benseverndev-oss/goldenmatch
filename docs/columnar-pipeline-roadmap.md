# Columnar pipeline — remaining roadmap (Phases B–D) + housekeeping

Companion to `docs/columnar-pipeline-wiring.md` (the Phase 0 analysis + Phase A
gated fast-path, both merged in #653). This file specs the remaining phases.

**Where we are:** Phase A wired a default-OFF gate (`GOLDENMATCH_COLUMNAR_PIPELINE`)
that, for the *eligible* shape only, routes the fuzzy scoring + cluster steps through
`score_blocks_columnar → build_clusters_columnar`. Eligibility (in
`core/pipeline.py::_is_columnar_eligible`) requires: single `weighted` matchkey; no
exact/probabilistic; no auto-config postflight (`config._preflight_report is None`);
no rerank/LLM/boost; default backend; not `across_files_only`; columnar-safe scorers.
The measured prize (1M, `realistic_person`): columnar **359 s** vs list **575 s** (~38%),
and the win is the scorer's direct-DataFrame emit (#634/#639), not the cluster build
(`build_clusters_columnar` wraps the same `build_clusters`).

Each phase below widens eligibility or attacks a distinct lever, ships **default-OFF
behind a gate**, and must pass the pipeline parity harness
(`tests/test_columnar_pipeline_parity.py`).

---

## Phase B — df-native postflight (engage the fast-path under auto-config)

**Goal.** Let the columnar fast-path engage when auto-config ran. Today eligibility
excludes `config._preflight_report is not None` because `_apply_postflight`
(`core/pipeline.py`) consumes the pair **list**.

**What couples to the list.** `_apply_postflight(df, config, pair_scores: list)`:
1. is a **no-op** unless `config._preflight_report` is set;
2. otherwise calls `core/autoconfig_verify.py::postflight(df, config, pair_scores=...)`
   to compute **signals** (notably `signals["score_histogram"]`, `mass_above_threshold`),
   then applies a **threshold filter** `[p for p in pair_scores if p[2] >= adj.to_value]`.

**Spec.**
- Add a DataFrame-native postflight path. Signals derive from the score column:
  `score_histogram` ← `np.histogram(pairs_df["score"].to_numpy(), …)`;
  `mass_above_threshold` ← `(pairs_df["score"] >= t).mean()`. Keep the bin edges /
  formulas **byte-identical** to the list path in `autoconfig_verify.postflight`.
- Threshold filter becomes `pairs_df.filter(pl.col("score") >= adj.to_value)`.
- Shape: overload `_apply_postflight` (and the `postflight` signal computation) to accept
  `pl.DataFrame | list[tuple]`; dispatch on type. The list path stays for non-columnar.
- Drop the `_preflight_report is not None` clause from `_is_columnar_eligible`.
- Audit for any **other** list-iterating consumer of `all_pairs` before clustering when
  auto-config ran (grep `all_pairs` in `_run_dedupe_pipeline`); rerank/LLM/boost are
  already excluded by eligibility.

**Parity / tests.**
- Unit: `postflight` signals + chosen threshold adjustment are identical for a list and
  the equivalent `pairs_df` over a representative score distribution (incl. the
  "adjustment empties the pair set" advisory path).
- Extend `test_columnar_pipeline_parity.py` with an **auto-config** case
  (`dedupe_df(df)` zero-config, gate on vs off → identical clusters), now that the
  fast-path engages there.

**Risks.** `postflight` may emit non-threshold adjustments (matchkey weights etc.) — only
the threshold filter touches the pair collection; confirm nothing else does. Histogram
bin/edge drift would shift the committed threshold → assert exact equality.

---

## Phase C — multi-scorer columnar aggregation (columnar becomes the default output)

**Goal.** Emit **all** scorers' pairs as DataFrames and `pl.concat` them, retiring the
`all_pairs` Python list as the default scorer output. This is the phase that makes the
columnar path the default (after soak), not just an eligible-shape fast-path.

**Current aggregation** (`_run_dedupe_pipeline`, "Step 3: BLOCK + COMPARE"):
- exact (`find_exact_matches` → list; the fast path already builds id_a/id_b numpy arrays),
- fuzzy (`score_blocks_parallel` → list),
- probabilistic / Fellegi-Sunter (Phase 2b → list),
all `all_pairs.extend(...)`-ed into one `list[(int,int,float)]`.

**Spec.**
- Give each scorer a columnar emit:
  - exact: return a `(id_a, id_b, score)` DataFrame (its fast path already has the numpy
    arrays — wrap as a frame instead of materialising tuples);
  - fuzzy: `score_blocks_columnar` (exists);
  - probabilistic: emit a frame (port `score_probabilistic` / `_fast` output).
- Aggregate via `pl.concat([...])` of the per-scorer frames, then **canonicalize + dedup**
  with the existing `dedup_pairs_max_score` (#641) — pairs are project-wide canonical
  `(min(id_a,id_b), max(...))`, and a pair scored by multiple matchkeys keeps the max
  score (matches the list path's set/extend + cluster semantics; verify).
- `build_clusters_columnar` consumes the concatenated frame; `scored_pair_count`,
  postflight (Phase B), and downstream read the frame.
- rerank / LLM / boost: either port to df-native or keep routing those configs to the
  list path (widen eligibility incrementally — Phase C can require "none of
  rerank/LLM/boost active" and leave them list-only).
- This is where `GOLDENMATCH_COLUMNAR_PIPELINE` can flip to **default-ON** after a soak;
  keep the env opt-out (`=0`) for one release.

**Parity / tests.** Full-pipeline parity (columnar vs legacy list) across the scorer
combos: exact-only, fuzzy-only, probabilistic-only, and mixed (exact+fuzzy,
exact+fuzzy+probabilistic). Assert identical clusters (partition + pair_scores + size +
oversized + confidence) and `dupes`/`unique`/golden counts.

**Risks.** Cross-matchkey dedup must reproduce the list path's de-dup/score-precedence
exactly (the `(min,max)` + max-score rule). Probabilistic scores and NE/Path-Y exact
post-filters (`_apply_negative_evidence_to_exact_pairs` in `core/scorer.py`) must be
preserved in the columnar emit.

---

## Phase D — native Arrow-C `build_clusters` / `ClusterFrames` (the ~164 s cluster lever)

**Goal.** Attack the *other* half of the 1M wall — the ~164 s cluster build, which is
pure-Python Union-Find **plus a 131M-entry `pair_scores` dict fill** (and confidence).
Native `connected_components` already covers membership when
`native_enabled("clustering")`, but the dict fill + confidence remain Python and dominate.

**Building blocks (landed, unwired into the default cluster path):**
- Arrow-C `build_clusters` kernel (#645) — takes Arrow `(id_a,id_b,score)` arrays.
- `ClusterFrames` two-frame columnar cluster representation (#632) + numpy
  `cluster_dict_to_frames` (#635); golden (#636), identity (#638), and the
  hash-by-cluster_id partitioner (#642) already consume `ClusterFrames`.
- `mst_split_components` native MST kernel for oversized split (#649).

**Spec.**
- Add a `build_clusters_frames`-style path: when `native_enabled("clustering")`, feed the
  pairs frame's Arrow arrays to the Arrow-C kernel → `ClusterFrames` (membership frame +
  per-cluster aggregate frame), **bypassing the Python `pair_scores` dict** entirely.
- Materialize to the legacy `dict[int, dict]` only for callers that still need it (adapter
  exists); prefer routing golden through `ClusterFrames` directly (build_golden already
  accepts it, #636).
- Pure-Python `build_clusters` remains the **byte-for-byte reference**; gate on
  `native_enabled("clustering")`.
- **Weak-cluster downgrade caveat (learned in #650):** the Python path applies the
  weak-cluster confidence downgrade *after* `compute_cluster_confidence` (raw → downgraded,
  e.g. 0.494 → 0.3458). The native kernel computes **raw** confidence. Phase D MUST apply
  the downgrade (in Python on the ClusterFrames, or natively) so confidence + `cluster_quality`
  match — otherwise parity fails exactly as the prototype `build_clusters_native` did.
- Memory: at 131M pairs the dict fill is multiple GB; the columnar/Arrow path is the RSS
  win too — measure peak RSS alongside wall.

**Parity / tests.** Extend the native cluster parity suite to the full
`ClusterFrames` path: membership partition, per-pair `pair_scores`, `size`, `oversized`,
`bottleneck_pair`, and **downgraded** `confidence`/`cluster_quality` == pure-Python
`build_clusters`. Run with the ext built.

**Risks.** This is the one with real correctness surface (confidence downgrade, oversized
auto-split via the MST kernel, tie-breaks). Land it gated and parity-tested; do not flip
on by default until the native parity suite is in CI's `native` lane (see housekeeping).

---

## Housekeeping — add the 3 native-parity files to CI's `native` lane

**Problem.** CI's `native` job (`.github/workflows/ci.yml`, the
"Parity + in-house suite" step, ~lines 686–692) runs a fixed allowlist:
```
tests/test_native_parity.py
tests/test_record_fingerprint.py
tests/test_pairs.py
tests/test_embeddings.py
tests/test_inhouse_embedder.py
```
It does **not** run `test_native_cluster_orchestration_parity.py`,
`test_native_field_matrix_parity.py`, or `test_native_bulk_fingerprint_parity.py`. Those
bit-rotted unnoticed (native-vs-Python drift) and were only fixed reactively in #650.

**Spec.**
- Add the three files to that step's `pytest` invocation so native drift is caught:
  ```
  tests/test_native_cluster_orchestration_parity.py
  tests/test_native_field_matrix_parity.py
  tests/test_native_bulk_fingerprint_parity.py
  ```
- They pass post-#650 with the ext built (verified locally: 26 tests green). Confirm in CI.

**Do NOT add to the native lane:** the planner / partitioned-block-scoring tests
(`test_planner_integration.py`, `test_autoconfig_planner_protocol.py`,
`test_partitioned_block_scoring_pipeline.py`). With the ext built,
`core/autoconfig_planner_rules.py::_scoring_backend()` returns `"bucket"`
(`native_enabled("block_scoring")`), and those tests assume the non-native
`"polars-direct"` default → they **fail when the ext is present**. They belong only in the
no-ext `python (goldenmatch)` lane. (This is the same gotcha that produced 6 spurious
local failures while developing Phase A — see `HANDOFF.md`.)

**Risk.** Low — additive to a lane that already builds the ext; the three files are
parity-only and don't touch the planner.

---

### Suggested order
B (unblocks auto-config configs — most user-visible) → housekeeping (cheap, guards D) →
C (default columnar output) → D (native cluster kernel; biggest correctness surface, gate
on the now-CI'd native parity suite).
