# unmerge_record: optional scored_pairs source (decouple from cluster pair_scores) — design

**Date:** 2026-06-02
**Status:** design (approved by user, pre-spec-review)
**Decision context:** Phase-2 SP1 (#673), SP2 (#679), SP3 (#680) shipped. The blocker
trace for the future build-drops-`pair_scores` SP identified 5 consumers; SP3
removed #2-#4 (the `scored_pairs`-reconstruction family). This sub-project removes
**#1 — `unmerge_record`**, the hardest/post-hoc one: it re-clusters a cluster's
remaining members from `clusters[cid]["pair_scores"]` via a DIRECT subscript
(`cluster.py:994`), so it would `KeyError` if the build dropped `pair_scores`.
#5 (distributed build) remains deferred. After this, the only `pair_scores`
dependencies left on the build's returned dict are graceful-degraders.

## Problem

`unmerge_record(record_id, clusters, threshold, ...)` (`core/cluster.py:941`)
removes a record from its cluster and re-clusters the remaining members using the
stored per-cluster `pair_scores` (read at `:980` and `:994`, the latter a direct
`cinfo["pair_scores"]` subscript). This hard-couples it to the cluster dict
carrying `pair_scores`. `unmerge_cluster` (`:1027`) only shatters to singletons
(uses `.get`, no scores) — NOT a blocker, out of scope.

## Goal

`unmerge_record` accepts an optional explicit `scored_pairs` list and re-clusters
from it (falling back to `cluster["pair_scores"]` when not given), so it survives
a future build that drops `pair_scores` from the returned dict. BYTE-IDENTICAL —
no behavior change today.

## Why byte-identical (single-cluster filter argument)

`unmerge_record` operates on exactly ONE cluster (the one containing `record_id`).
Filtering a flat `scored_pairs` list to that cluster's members yields exactly the
within-member edges — which equals that cluster's `pair_scores`. The auto-split
cross-cut-edge difference that made SP3's *full-flatten* a superset does NOT arise
here: a cross-cut edge has one endpoint OUTSIDE this cluster's members, so the
member-filter excludes it. So `{pairs in scored_pairs with both endpoints in
cluster.members} == cluster["pair_scores"]` for any single final cluster. The
re-clustering input is identical → byte-identical output.

## Architecture

1. **`core/cluster.py::unmerge_record`** — add keyword-only
   `scored_pairs: list[tuple[int, int, float]] | None = None`. After identifying
   the affected cluster + its members (existing logic, ~`:963-979`), build the
   per-cluster score map:
   - `scored_pairs is not None`: `pair_scores = {(min(a,b),max(a,b)): s for (a,b,s)
     in scored_pairs if a in member_set and b in member_set}` where `member_set =
     set(cinfo["members"])`. (Canonical keys to match the cluster-dict convention;
     dedup last-wins is irrelevant since `scored_pairs` is already deduped, but use
     a dict so duplicates collapse safely.)
   - `scored_pairs is None`: `pair_scores = cinfo.get("pair_scores") or {}`. (NOTE:
     the re-cluster extraction at `:994` is the DIRECT `cinfo["pair_scores"]`
     subscript — the actual KeyError blocker. The memory-correction loop at `:980`
     is ALREADY tolerant `cinfo.get("pair_scores", {})` and only runs under
     `if memory_store is not None`. The None-path `.get(...) or {}` makes `:994`
     tolerant too — a safe strict-improvement.)
   **Both** consumers — the memory-correction loop (`:980`, which scans for
   `(record_id, other)` edges) AND the re-cluster extraction (`:994`) — must read
   from the ONE locally-built `pair_scores` map, so the memory corrections recorded
   are byte-identical on the `scored_pairs` path (the member-filtered map carries
   the same `(record_id, other)` edge set as `cinfo["pair_scores"]`). The remaining
   re-clustering + record-removal + result-dict logic is UNCHANGED; it just
   consumes the locally-built `pair_scores`.
2. **`tui/engine.py::unmerge_record`** (`:408`) — pass
   `scored_pairs=self._last_result.scored_pairs` to the core call.
   `EngineResult.scored_pairs` already exists (`:40`, populated at `:320`). This
   makes the TUI path — and the **MCP** (`_engine.unmerge_record`, `mcp/server.py:916`)
   and **REST** (`/reviews/decide`, `api/server.py:233`) paths that route through
   the engine — source scores independently of the cluster dict.

## Edge cases / invariants

- **scored_pairs orientation:** the flat list may be canonical `(min,max)` (SP3) or
  raw; canonicalize keys when building the per-cluster map so lookups match the
  cluster-dict `(min,max)` convention. Use a dict comprehension keyed on
  `(min,max)` — collapses any dup/orientation.
- **Removed record:** unchanged — the existing logic removes `record_id` and
  re-clusters the rest from the per-cluster `pair_scores`; feeding the same map
  (whether from `scored_pairs` filter or `cinfo["pair_scores"]`) gives the same
  result.
- **Member not in scored_pairs / singleton:** filter yields `{}` → re-cluster of a
  no-edge member set → singletons, same as an empty `pair_scores` today.
- **None path is now tolerant:** changing the direct subscript to `.get(...) or {}`
  means a pair_scores-less dict on the None path degrades to "no edges → shatter"
  instead of KeyError. Acceptable strict-improvement (today's callers always have
  pair_scores, so no behavior change in practice).

## Testing

- **Byte-identical parity (HARD):** on a fixture clusters dict (multi-member, a
  weak-chain cluster that re-clusters into 2 on record removal, a split cluster,
  singletons), assert `unmerge_record(rid, deepcopy(clusters), scored_pairs=flat)`
  `==` `unmerge_record(rid, deepcopy(clusters))` for several `record_id`s — where
  `flat` is the cluster dict's pair_scores flattened to `[(a,b,s)]` (so the two
  sources carry the same data). Covers the core equivalence.
- **scored_pairs-less dict:** `unmerge_record(rid, clusters_without_pair_scores,
  scored_pairs=flat)` succeeds (re-clusters from `scored_pairs`), proving the
  decouple. And the None path on a pair_scores-less dict degrades gracefully
  (no KeyError).
- **Engine wiring:** `engine.unmerge_record` passes its `scored_pairs`; a small
  test that the engine path produces the same updated clusters as before (the
  engine result already carries scored_pairs equal to the build's pairs).
- **CI-validated:** the local runtime hangs on `import goldenmatch` (Polars-level,
  unresolved); verify via ruff + py_compile locally and the full pytest in CI.

## Scope boundary (YAGNI)

- ONLY `core/cluster.py::unmerge_record` + the `tui/engine.py` caller + the test.
- NOT `unmerge_cluster` (shatters, no scores). NOT the web router (reconstructs its
  own pair_scores from disk; separate concern). NOT dropping `pair_scores` from the
  build (the future SP). NOT #5 (distributed). No new param on `unmerge_cluster`.

## References

- SP3 `docs/superpowers/specs/2026-06-02-scored-pairs-decouple-design.md` (#680).
- `core/cluster.py`: `unmerge_record` (`:941`, pair_scores reads `:980`/`:994`),
  `unmerge_cluster` (`:1027`). `tui/engine.py`: `unmerge_record` (`:401`),
  `EngineResult.scored_pairs` (`:40`/`:320`). `mcp/server.py:916`,
  `api/server.py:233`.
- Related: [[project_663_arrow_kernels]], [[project_identity_graph_v2]].
