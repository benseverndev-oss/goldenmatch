# Columnar Pair-Score View (opt #3b) Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Steps use
> checkbox (`- [ ]`) syntax. CI-validated posture: the dev box HANGS on
> `import goldenmatch`/`import polars`. Subagents validate Python via `ruff
> check` + `python -m py_compile` ONLY — NEVER `import`, `pytest`, `pyright`,
> or `uv`. The real parity tests run in CI's `python (goldenmatch)` lane
> (native=1 parity included). Run pyright yourself (the controller), bounded.

**Goal:** Make `ClusterPairScores.from_frames` cheap + low-RSS at 100M pairs via
a vectorized Polars join (Stage 1) then a frame-backed view with no resident
100M-entry dict-of-dicts (Stage 2), byte-identical to the dict path.

**Architecture:** Stage 1 replaces the `_bucket_pairs` Python loop in
`from_frames` with a join+group_by that produces a `view_df`, then materializes
the same `_by_cid` dict (byte-identical, parity-gated, NO 100M bench). Stage 2
backs the view with `_partitions = view_df.partition_by("cid", as_dict=True)`
and makes `for_cluster`/`score_for`/`iter_clusters` serve per-cid slices on
demand — killing the global dict-of-dicts. Identity consumption unchanged.

**Tech Stack:** Polars, pytest (CI only), Rust native kernel (unaffected).

**Spec:** `docs/superpowers/specs/2026-06-02-columnar-view-frame-backed-design.md`

**Branch/auth:** off `main`; benzsevern (`GH_TOKEN=$(gh auth token --user
benzsevern)`); NEVER benzsevern-mjh. `docs/superpowers/` is gitignored — do NOT
`git add` the spec/plan.

---

## Byte-identical invariants (every task must hold these — from the spec)

1. Keep pair iff both endpoints map to same cid; either endpoint absent → drop.
2. Key `(a,b)` AS GIVEN (not canonicalized); `(7,3)` ≠ `(3,7)`.
3. Key insertion order within a cid = FIRST-occurrence order.
4. Value = LAST-occurrence score. `last_score = pl.col("s").sort_by("__i__").last()`
   — **NEVER `pl.col("s").max()`**.
5. `score_for` canonicalizes ONLY the query `(min,max)`; stored keys stay
   as-given → the reversed-orientation MISS (→ `None`) is PRESERVED.
6. `assignments` is unique on `member_id` (join fan-out guard).

---

### Task 1: Stage 1 parity fixture + test (RED first)

**Files:**
- Test: `packages/python/goldenmatch/tests/test_cluster_frames_out_parity.py`

- [ ] **Step 1: Write the failing parity test.** Build a small fixture
  `pairs` list + final `clusters` dict + matching `assignments` frame that
  INCLUDES, by construction:
  - a cascading auto-split clique (cluster that splits, then a child re-splits);
  - a duplicate `(a,b)` where the LATER occurrence's score is LOWER than the
    earlier (so MAX≠LAST);
  - cross-cut edges (a pair with one endpoint in a different final cid);
  - a reversed pair `(7,3)` and `(3,7)` both present;
  - a self-pair `(a,a)` with `a` in a cid (must be KEPT — join+filter passes it);
  - at least one singleton (member with no kept pair).
  Assert `assignments["member_id"].is_unique().all()`. Then assert, for every
  cid: `ClusterPairScores.from_frames(assignments, pairs).for_cluster(cid)` ==
  `ClusterPairScores.from_pairs(pairs, clusters).for_cluster(cid)` — EXACT dict
  equality (keys, key order via `list(d.items())`, values). Also assert
  `list(view_frames.iter_clusters())` == `list(view_pairs.iter_clusters())`.

- [ ] **Step 2: Validate the test file** — `ruff check <file>` +
  `python -m py_compile <file>`. (No pytest locally.) The test will run RED in
  CI until Task 2 lands; that's expected — note it in the commit.

- [ ] **Step 3: Commit.** `feat(test): Stage-1 from_frames join parity fixture
  (cascading split, MAX≠LAST dup, cross-cut, reversed pair)`

### Task 2: Stage 1 — vectorized `from_frames` (GREEN)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/cluster_pairscores.py`

- [ ] **Step 1: Rewrite `from_frames`** to build the view via Polars from the
  RAW `all_pairs` list (AS-GIVEN orientation — NEVER source from
  `_columnar_pairs_df`, which is canonicalized `id_a<id_b` and would break the
  as-given invariant + the F3 miss). Keep the `_by_cid` dict output
  (byte-identical). Algorithm — note the EXPLICIT cid column naming:
  ```
  # all_pairs is the raw list[(a,b,s)] (as-given); build the frame here.
  pairs_df = pl.DataFrame({"a":[...], "b":[...], "s":[...]}).with_row_index("__i__")
  amap_a = assignments.select("member_id", pl.col("cluster_id").alias("cid_a"))
  amap_b = assignments.select("member_id", pl.col("cluster_id").alias("cid_b"))
  j = (pairs_df
        .join(amap_a, left_on="a", right_on="member_id", how="left")   # member_id key dropped
        .join(amap_b, left_on="b", right_on="member_id", how="left")
        .filter(pl.col("cid_a").is_not_null()
                & pl.col("cid_b").is_not_null()
                & (pl.col("cid_a")==pl.col("cid_b")))
        .with_columns(pl.col("cid_a").alias("cid")))
  g = (j.group_by("cid","a","b")
        .agg(pl.col("__i__").min().alias("first_i"),
             pl.col("s").sort_by("__i__").last().alias("last_score"))  # NEVER .max()
        .sort("cid","first_i"))   # REQUIRED: group_by output order is undefined
  by_cid: dict = {}
  for cid,a,b,s in g.select("cid","a","b","last_score").iter_rows():
      by_cid.setdefault(cid, {})[(a,b)] = s
  return cls(by_cid=by_cid)
  ```
  Pre-aliasing `cluster_id`→`cid_a`/`cid_b` in the two `amap` selects avoids any
  suffix/collision ambiguity (the `right_on="member_id"` key column is dropped
  by the join, so it never collides). Keep the existing `from_frames` docstring
  caveat (RAW pairs, not max-score). `from_pairs`/`from_cluster_dict`/
  `_bucket_pairs` UNCHANGED (legacy paths; `_bucket_pairs` still used by
  `from_pairs`). The `(assignments, all_pairs)` signature is UNCHANGED — no
  pipeline edit (see dropped Task 4).

- [ ] **Step 2: Validate** — `ruff check` + `py_compile` on the module.
  Controller runs bounded `pyright` on the file.

- [ ] **Step 3: Commit.** `perf(cluster): Stage-1 vectorize from_frames via
  polars join (byte-identical _by_cid; no 100M bench)`

### Task 3: Stage 2 — frame-backed partitions + accessors

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/cluster_pairscores.py`
- Test: `packages/python/goldenmatch/tests/test_cluster_frames_out_parity.py`

- [ ] **Step 1: Extend the parity test** to also exercise the frame-backed
  accessors directly: after Task-3 Step-2, `from_frames` returns a
  `_partitions`-backed view; assert `for_cluster`, `score_for` (including the
  reversed-orientation MISS → `None`), and `iter_clusters` ordering are all
  byte-identical to `from_pairs`. (Independent of Task 4 — Task 4 is dropped.)
  Validate (ruff + py_compile).

- [ ] **Step 2: Add `_partitions` backing.** Add `"_partitions"` to
  `__slots__`; `__init__(self, by_cid=None, partitions=None)` MUST set BOTH
  `self._by_cid = by_cid` (default None) AND `self._partitions = partitions`
  (slots raises on unset attribute access). `from_frames` builds `view_df` (the
  `g` frame from Task 2, selected to columns `cid,a,b` + `last_score` aliased to
  `s`) and sets `_partitions = view_df.partition_by("cid", as_dict=True)` (ONCE),
  passing `partitions=` to `__init__` and leaving `by_cid=None`. The legacy
  `from_pairs`/`from_cluster_dict` set `by_cid=` and leave `partitions=None`.

- [ ] **Step 2b: Make accessors backing-aware** (O(cid) per call, no per-call
  `partition_by`):
  ```
  for_cluster(cid):
      if _partitions is not None:
          f = _partitions.get((cid,)) ; if None: return {}
          d = {}
          for a,b,s in f.select("a","b","s").iter_rows(): d[(a,b)] = s  # frame already last-wins+ordered
          return d
      return _by_cid.get(cid, {})
  score_for(cid,a,b):
      key=(min(a,b),max(a,b))
      if _partitions is not None:
          f=_partitions.get((cid,)); if None: return None
          # compare key against AS-GIVEN stored (a,b): preserve reversed MISS
          return for_cluster(cid).get(key)     # lookup uses canonical query vs as-given keys -> same miss as today
      return _by_cid.get(cid,{}).get(key)
  iter_clusters():
      if _partitions is not None:
          for (cid,),f in _partitions.items():
              yield cid, [(a,b,s) for a,b,s in f.select("a","b","s").iter_rows()]
      else:
          for cid,ps in _by_cid.items(): yield cid,[(a,b,s) for (a,b),s in ps.items()]
  ```
  NOTE: confirm `partition_by(..., as_dict=True)` key shape on the installed
  Polars — current Polars keys by a 1-tuple `(cid,)`; older (≤0.19) keyed by
  scalar `cid`. **CI failure mode: if `for_cluster` returns `{}` for EVERY cid,
  the key shape is wrong — flip `_partitions.get((cid,))` ↔ `_partitions.get(cid)`.**
  The parity gate catches this loudly. `partition_by` preserves the input frame's
  row order within each partition (current Polars), so the prior
  `.sort("cid","first_i")` yields first-occurrence order per slice; drop the
  `first_i` helper column before/at partition so slices carry only `a,b,s`.

- [ ] **Step 3: Validate** (ruff + py_compile); controller runs bounded pyright.

- [ ] **Step 4: Commit.** `perf(cluster): Stage-2 frame-backed ClusterPairScores
  (partition_by view; kills resident 100M dict-of-dicts; byte-identical)`

### Task 4: DROPPED

Original "thread `_columnar_pairs_df` into `from_frames`" is removed: plan review
proved `_columnar_pairs_df` is `None` at the `from_frames` call site
(pipeline.py:1906 fires only on the frames-out branch, mutually exclusive with
the columnar pair-stream branch) AND it is canonicalized `id_a<id_b` (would
break the as-given invariant + F3 miss). `from_frames(assignments, all_pairs)`
builds its own `pairs_df` from the raw list (Task 2). No pipeline.py change. The
call site stays exactly as-is.

### Task 5: Identity-level parity test

**Files:**
- Test: `packages/python/goldenmatch/tests/test_identity_from_frames_parity.py`

- [ ] **Step 1:** Extend the identity parity test: resolve once with the
  dict-backed view (`from_pairs`) and once with the frame-backed view
  (`from_frames`), assert identical evidence-edge set (entity partition + per-
  edge scores) AND that a weak cluster whose `bottleneck_pair` is stored in the
  REVERSE orientation of the `score_for` query still emits a conflict edge with
  `score=None` (preserves the F3 miss). Validate (ruff + py_compile).

- [ ] **Step 2: Commit.** `test(identity): frame-backed view evidence-edge
  parity + reversed-bottleneck score=None`

### Task 6: Bench — confirm it exercises the new path

**Files:**
- `packages/python/goldenmatch/scripts/bench_pipeline_complete_path.py` (~212)

- [ ] **Step 1:** The columnar `id_prep` leg already calls
  `ClusterPairScores.from_frames(frames.assignments, pairs_list)` — which now
  builds the frame + partitions internally (Tasks 2-3), so it ALREADY measures
  the Stage-2 path. Verify no code change is needed; if the `_keepalive` tuple
  (line 218) references `view._by_cid`-shaped internals, leave it (it just holds
  the view object for peak-RSS). Likely NO edit — confirm + note in the commit,
  or make the minimal touch if the leg constructs the view differently.

- [ ] **Step 2: Commit (if changed).** `bench: confirm columnar id_prep
  measures the Stage-2 frame-backed from_frames`

---

## Execution order & gates

1. Tasks 1→2 (Stage 1): land, push, CI `python (goldenmatch)` parity GREEN.
   NO 100M bench on Stage 1.
2. Tasks 3→5 (Stage 2 + identity parity): land, push, CI parity GREEN.
3. Task 6 + dispatch the 100M complete-path bench (`np=100000000`) ONCE.
   Report `id_prep` + peak RSS delta vs legacy. The bench is DATA.

## Final review

After all tasks: dispatch a final code-reviewer over the whole diff (byte-
identical focus + the F1-F6 landmines), then finish the branch (PR off main,
benzsevern auth, CI parity gate is the verifier).
