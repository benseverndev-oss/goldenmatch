# Drop eager per-cluster pair_scores from the columnar build (Phase 2 SP4) — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The gated columnar `build_clusters` returns `dict[int,dict]` with `pair_scores={}` (no eager per-cluster dicts — the SP1 bench loss); scores exposed via a `ClusterPairScores` view built at the pipeline level from SP3's `scored_pairs`. Byte-identical on everything else. Measure-first → flip the gate default-ON if it wins.

**Architecture:** (1) Extend the native `build_clusters_arrow` kernel to emit `min_edge`/`avg_edge` on `frames.metadata` (already computed, currently discarded) so the native weak-quality test stays byte-identical without per-cluster scores. (2) `_build_clusters_via_frames` drops the eager `pair_scores` fill; native reads `confidence`/`bottleneck`/`min_edge`/`avg_edge` from metadata, off-native uses a transient pairs-order fill (then clears). (3) `_finalize_clusters` materializes a per-cluster `pair_scores` ONLY for oversized clusters (from the raw pairs, pairs-input order) before the edge-budget meter + split, and the weak test reads min/avg passed in. (4) New `ClusterPairScores.from_scored_pairs`; the pipeline builds the view from `scored_pairs` (reordered above the view build) and feeds identity. (5) Bench + gate decision.

**Tech Stack:** Rust (the existing `packages/rust/extensions/native` maturin/abi3 kernel) + Python 3.11 + Polars.

**Spec:** `docs/superpowers/specs/2026-06-02-columnar-drop-pairscores-design.md` — READ it. **Branch:** `feat/columnar-drop-pairscores`.

**ENVIRONMENT (critical):** The local box currently HANGS on `import goldenmatch` (and even `import polars`) AND cannot build the native kernel. So: verify Python edits with `ruff` + `py_compile` ONLY; verify Rust with `cargo fmt --check` + `cargo check` IF the toolchain is available locally (the native crate at `packages/rust/extensions/native` — try `cargo check`; if the pinned toolchain/abi3 fails, rely on CI). ALL behavior (Python pytest AND the native kernel) is validated in CI. The CI `native` lane builds the kernel fresh and runs `test_native_parity.py` + the columnar parity tests. Expect a CI-gated loop. ruff: `D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check <files>`.

## Background (exact current code)

- **`core/cluster.py::_build_clusters_via_frames`** (the columnar core): step 1 UF (native `build_clusters_arrow_native` → `frames`, OR off-native `connected_components`/`UnionFind`) `:664-700`; step 2 sort-by-min + result dict with `pair_scores={}` `:702-722`; **step 3 eager fill** via `pl.col("id_a").replace_strict(member_to_cid)` + in-row-order iterate `:729-743`; **step 4** `compute_cluster_confidence` per cluster `:745-750`; step 5 `return _finalize_clusters(result, max_cluster_size, weak_cluster_threshold, auto_split)` `:752`.
- **`_finalize_clusters`** `:530-610`: auto-split loop `:559-589` — `edge_work += len(cinfo["pair_scores"])` `:562`, `split_oversized_cluster(cinfo["members"], cinfo["pair_scores"])` `:568`; weak-quality `:600-610` reads `cinfo["pair_scores"].values()` → `min_edge=min(scores)`, `avg_edge=sum(scores)/len(scores)`, `if avg_edge-min_edge > weak_cluster_threshold: weak + confidence*=0.7`.
- **`build_clusters_arrow_native`** wrapper `:1414-1501`: calls `arrow_fn(...)` → unpacks an 8-tuple `(a_cid, a_mid, m_cid, m_size, m_conf, m_over, m_bot_a, m_bot_b)` `:1481-1484`; builds the `metadata` DataFrame `:1490-1500`.
- **Rust `build_clusters_arrow`** `packages/rust/extensions/native/src/cluster.rs:392-547`: metadata loop `:508-527` does `let (_min_e, _avg_e, _conn, bn, conf) = cluster_confidence(edges.clone(), size);` `:512` (DISCARDS min/avg); returns an 8-tuple `:538-547`.
- **`ClusterPairScores`** `core/cluster_pairscores.py`: has `from_cluster_dict`, `for_cluster`, `iter_clusters`, `score_for`.
- **Pipeline** `core/pipeline.py`: `pair_score_view = ClusterPairScores.from_cluster_dict(clusters)` under the gate `:1720-1725`; `_resolve_identities(..., pair_score_view=...)` `:1726-1730`; `scored_pairs` computed `:1737-1742` (AFTER the view build — MUST move above); results dict `:1744+`.

---

## File Structure

- **Modify** `packages/rust/extensions/native/src/cluster.rs`: emit `min_edge`/`avg_edge` on metadata (10-tuple return) + a Rust unit test.
- **Modify** `goldenmatch/core/cluster.py`: `build_clusters_arrow_native` (unpack 10, add min/avg columns), `ClusterFrames.metadata` schema (`cluster_dict_to_frames` empty-schema + `cluster_dict_to_frames` emit), `_build_clusters_via_frames` (drop eager fill; native metadata / off-native transient), `_finalize_clusters` (per-oversized materialization + weak from passed min/avg).
- **Modify** `goldenmatch/core/cluster_pairscores.py`: add `from_scored_pairs`.
- **Modify** `goldenmatch/core/pipeline.py`: reorder `scored_pairs` above the view build; switch view source to `from_scored_pairs` when gate-on.
- **Test** `tests/test_columnar_drop_pairscores_parity.py` (create); extend `tests/test_columnar_cluster_build_parity.py`; `tests/test_cluster_pairscores.py` (from_scored_pairs).
- **Create** `scripts/bench_columnar_drop_pairscores.py` (or reuse `bench_columnar_cluster_build.py`) + workflow.

---

## Task 1: Rust — emit min_edge/avg_edge on build_clusters_arrow metadata

**Files:**
- Modify: `packages/rust/extensions/native/src/cluster.rs`
- Modify: `goldenmatch/core/cluster.py` (`build_clusters_arrow_native` wrapper + `ClusterFrames.metadata` schema)

- [ ] **Step 1: Rust unit test (in cluster.rs `#[cfg(test)]`)**

Add a test that `build_clusters_arrow` emits `min_edge`/`avg_edge` equal to the per-cluster `cluster_confidence` min/avg on a small fixture. (Model on existing cluster.rs tests if present; else assert the returned min/avg arrays match a hand-computed min + sequential-sum/len.)

- [ ] **Step 2: Emit min/avg in the kernel**

In `cluster.rs:508-527`, un-discard: `let (min_e, avg_e, _conn, bn, conf) = cluster_confidence(edges.clone(), size);`. Add `let mut m_min: Vec<f64> = Vec::with_capacity(n_clusters);` + `m_avg` near `:504`, `m_min.push(min_e); m_avg.push(avg_e);` in the loop. Build `Float64Array::from(m_min)` / `m_avg` and append them to the return tuple (now 10 elements). Update the function's return type signature accordingly. `cargo fmt`.

- [ ] **Step 3: Update the Python wrapper to unpack 10 + add metadata columns**

In `build_clusters_arrow_native` (`cluster.py:1481-1500`): unpack `(a_cid, a_mid, m_cid, m_size, m_conf, m_over, m_bot_a, m_bot_b, m_min, m_avg)`. Add to the `metadata` DataFrame: `"min_edge": _pl.from_arrow(m_min), "avg_edge": _pl.from_arrow(m_avg)`.

- [ ] **Step 4: Extend ClusterFrames.metadata schema**

In `cluster_dict_to_frames`'s empty-frame schema (`cluster.py:~1252-1261`) add `"min_edge": _pl.Float64(), "avg_edge": _pl.Float64()`. In `cluster_dict_to_frames`'s populated path, emit `min_edge`/`avg_edge` (compute from each cluster's `pair_scores` — `min(scores)` and `sum/len`, or 0.0 for empty) so the dict→frames adapter stays consistent. (Readers that don't use min/avg are unaffected — additive columns.)

- [ ] **Step 5: Static check + commit**

`cargo fmt --check` (Rust) + try `cargo check -p ...` (may fail locally on toolchain → CI). ruff + py_compile `cluster.py`. Commit: `feat(native): emit min_edge/avg_edge on build_clusters_arrow metadata (un-discard already-computed values)`.
**NOTE:** this task is validated by CI's `native` lane (fresh kernel build); the Rust kernel cannot run locally.

---

## Task 2: columnar build drops eager pair_scores; metadata/transient confidence + min/avg

**Files:**
- Modify: `goldenmatch/core/cluster.py` (`_build_clusters_via_frames`, `_finalize_clusters`)
- Test: `tests/test_columnar_drop_pairscores_parity.py` (create)

- [ ] **Step 1: Write the byte-identical parity test (gate ON vs OFF, except pair_scores)**

Create `tests/test_columnar_drop_pairscores_parity.py` modeled on `tests/test_columnar_cluster_build_parity.py` (reuse `_adversarial_pairs`). Run `build_clusters` gate-OFF (dict path, has pair_scores) vs gate-ON (columnar, pair_scores={}) on the fixture, parametrized `GOLDENMATCH_NATIVE` `["1","0"]` (skip native=1 when the kernel is absent, copy the guard). Assert per cluster: `members` as a SET; `size`, `oversized`, `confidence` (EXACT float), `bottleneck_pair`, `cluster_quality` strict-equal; AND `on[cid]["pair_scores"] == {}` for every gate-ON cluster. (Do NOT compare pair_scores between paths — gate-ON is intentionally empty.) Include the weak-chain + oversized-split clusters so the weak-quality + split paths are exercised.

- [ ] **Step 2: Run — verify FAIL (gate-ON still fills pair_scores today)**

`cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_columnar_drop_pairscores_parity.py -v` (NOTE: local import hangs → this won't run locally; establish RED logically — today gate-ON fills pair_scores so `on[cid]["pair_scores"] != {}`; CI confirms). py_compile the test.

- [ ] **Step 3: `_build_clusters_via_frames` — retain metadata, drop the eager fill**

- In the native branch (`:669-683`), after building `member_sets`, ALSO build `metadata_by_cid: dict[int, dict]` from `frames.metadata` keyed by `cluster_id`: `{int(cid): {"confidence": conf, "bottleneck": (None if (ba,bb)==(0,0) else (ba,bb)), "min_edge": mn, "avg_edge": av}}` (read columns `confidence`, `bottleneck_pair_a/b`, `min_edge`, `avg_edge`). Set `metadata_by_cid = None` in the off-native branch.
- **Step 3 fill (`:729-743`):** when `metadata_by_cid is not None` (native): SKIP the fill entirely (pair_scores stays {}). When off-native: do a TRANSIENT fill into a local `transient: dict[int, dict]` (same replace_strict + in-row-order iterate, but into `transient[cid]` not `result[cid]["pair_scores"]`), keep it for step 4, do NOT put it on `result`.
- **Step 4 confidence (`:745-750`):** native → `cinfo["confidence"] = metadata_by_cid[cid]["confidence"]; cinfo["bottleneck_pair"] = metadata_by_cid[cid]["bottleneck"]`. off-native → `compute_cluster_confidence(transient.get(cid, {}), size)` as today. (Both compute on pairs-input order → byte-identical.)
- Compute a per-cluster `min_edge`/`avg_edge` map for the weak test: native → from `metadata_by_cid`; off-native → from `transient` (`min(vals)`, `sum/len`, or for empty: match the dict path's `cinfo.get("pair_scores")` falsy → skip weak, so only build min/avg for non-empty). Thread this into `_finalize_clusters` (new param `weak_stats: dict[int, tuple[float,float]] | None`).
- Pass the RAW `pairs_list` to `_finalize_clusters` (new param `raw_pairs`) for the per-oversized materialization.

- [ ] **Step 4: `_finalize_clusters` — per-oversized materialization + weak from passed stats**

Add keyword-only `raw_pairs=None` and `weak_stats=None`. In the auto-split loop: when `raw_pairs is not None` AND `cinfo["pair_scores"]` is empty AND the cluster is oversized, materialize it FIRST (before `edge_work += len(...)`): `member_set = set(cinfo["members"]); cinfo_ps = {}; for a,b,s in raw_pairs: if a in member_set and b in member_set: cinfo_ps[(a,b)] = s` (pairs-input order, last-wins — byte-identical to the dict path's fill), assign `cinfo["pair_scores"] = cinfo_ps`. Then the existing `edge_work`/`split_oversized_cluster` works unchanged. AFTER the split loop (columnar path), RESET every cluster's `pair_scores` to `{}` (so split sub-clusters don't retain scores) — gate this on `raw_pairs is not None` so the dict path is untouched. In the weak-quality step (`:604-610`): when `weak_stats is not None`, use `min_edge, avg_edge = weak_stats[cid]` (skip clusters absent from `weak_stats` = no edges); else today's `cinfo["pair_scores"]` path. **CRITICAL:** the dict path (`weak_stats=None`, `raw_pairs=None`) must be UNCHANGED.

- [ ] **Step 5: Run parity (CI) — verify PASS native + off-native**

Push to CI (local can't run). The CI `python (goldenmatch)` + `native` lanes run the parity test. Iterate on CI until green. ruff + py_compile locally each push.

- [ ] **Step 6: Commit**

Commit: `feat(cluster): columnar build returns pair_scores={} (metadata/transient confidence + min/avg; per-oversized split materialization)`.

---

## Task 3: ClusterPairScores.from_scored_pairs + pipeline view switch

**Files:**
- Modify: `goldenmatch/core/cluster_pairscores.py`, `goldenmatch/core/pipeline.py`
- Test: `tests/test_cluster_pairscores.py` (add), `tests/test_columnar_drop_pairscores_parity.py` (view assertion)

- [ ] **Step 1: Add `from_scored_pairs` + test**

`ClusterPairScores.from_scored_pairs(scored_pairs, clusters) -> ClusterPairScores`: build `member_to_cid` from `clusters` membership; `by_cid: dict[int, dict] = {}`; for `(a,b,s)` in scored_pairs, if `member_to_cid.get(a) == member_to_cid.get(b) is not None`, set `by_cid.setdefault(cid, {})[(min(a,b),max(a,b))] = s`. Return `cls(by_cid)`. Test in `tests/test_cluster_pairscores.py`: `from_scored_pairs` on a clusters dict + its flattened scored_pairs reproduces each cluster's `for_cluster(cid)`.

- [ ] **Step 2: Pipeline — reorder scored_pairs above the view build + switch source**

In `core/pipeline.py`: MOVE the `scored_pairs` computation (currently `:1737-1742`) to ABOVE the `pair_score_view` build (`:1720`). Then change the gate-on view build to: `view = ClusterPairScores.from_scored_pairs(_scored_pairs, clusters)` instead of `from_cluster_dict(clusters)`. (Gate-off path unchanged — no view.) Confirm `_scored_pairs` is the same value stored on the results dict (build once, reuse).

- [ ] **Step 3: Add the view-carries-the-scores parity assertion**

In `tests/test_columnar_drop_pairscores_parity.py`, add: build `view = ClusterPairScores.from_scored_pairs(<flat scored_pairs from the fixture>, on_clusters)`; assert `view.for_cluster(cid) == off_clusters[cid]["pair_scores"]` for every cluster (the view reproduces exactly what the columnar dict dropped). NOTE: canonicalize the OFF pair_scores keys to `(min,max)` for the comparison if needed (they're `(min,max)` in practice).

- [ ] **Step 4: Run (CI) + commit**

ruff + py_compile locally; CI validates. Confirm `tests/identity/`, `test_golden.py`, `test_scored_pairs_decouple.py`, `tests/test_unmerge_scored_pairs.py` green with `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD=1` (they consume the view / scored_pairs). Commit: `feat(cluster,pipeline): ClusterPairScores.from_scored_pairs; pipeline builds the view from scored_pairs (gated)`.

---

## Task 4: measure-first bench + gate decision

**Files:**
- Create: `scripts/bench_columnar_drop_pairscores.py` (or extend `scripts/bench_columnar_cluster_build.py`)
- Create: `.github/workflows/bench-columnar-drop-pairscores.yml`

- [ ] **Step 1: Bench script** — model on `scripts/bench_columnar_cluster_build.py` (per-variant subprocess, ASCII table, parity-first). Bench `build_clusters` gate-OFF vs gate-ON at `--np 1000000,5000000`, wall + peak RSS. This is the measure that decides the gate default.
- [ ] **Step 2: Workflow** — `bench-columnar-drop-pairscores.yml`, workflow_dispatch, `large-new-64GB`, build native, run the bench. Model on `bench-columnar-cluster-build.yml`.
- [ ] **Step 3: Commit** — `bench(cluster): columnar drop-pairscores vs dict measure-first harness`.
- [ ] **Step 4 (orchestrator): dispatch + decide the gate default** — merge, dispatch at 1M/5M, read wall + RSS. **If columnar wins net, flip `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` default-ON** (the roadmap payoff) in a follow-up commit (`_columnar_cluster_build_enabled` default `"1"`, kill-switch `=0`) + update the spec/memory with the numbers. If it does NOT win, keep gated and record why.

---

## Final validation (orchestrator)

1. CI `python (goldenmatch)` + `native` lanes green: the new parity test (native + off-native), the metadata min/avg unit test, and all consumer suites with the gate ON.
2. The Rust `native` lane builds the extended kernel + `test_native_parity.py` passes.
3. Dispatch the bench; decide + (if win) flip the gate default; fold numbers into spec + memory.

## Notes for the implementer

- **Byte-identical EXCEPT pair_scores** (now `{}` on the columnar path). The view + scored_pairs carry the real scores; consumers are already migrated (SP2/SP3/#681).
- **The dict path (gate-OFF) must be UNCHANGED** — `_finalize_clusters` with `raw_pairs=None`/`weak_stats=None` behaves exactly as today.
- **Confidence + min/avg are pairs-input order in BOTH states** (native metadata = kernel input-order; off-native = transient input-order fill). Do NOT source them from the `(a,b)`-sorted `scored_pairs` (float drift).
- **CI-gated loop** — local can't build the kernel or import goldenmatch; verify with ruff + py_compile + cargo fmt, push, read CI. Budget several cycles (Rust + native lane).
- **Auth:** this repo uses `benzsevern` ALWAYS (do NOT switch to benzsevern-mjh). [[feedback_github_auth_switch]]
- **Skill:** @superpowers:test-driven-development per task (RED via CI given the local hang).
