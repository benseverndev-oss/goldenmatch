# Columnar Pair-Score View (opt #3b) Design

**Status:** Approved scope (both stages, staged). Parity-gated.

**Goal:** Make `ClusterPairScores.from_frames` (the columnar identity pair-score
view) cheap and low-RSS at 100M pairs, so the Phase-2 frames-out cutover wins
wall AND RSS at scale instead of losing 2x on `id_prep`.

**Author context:** continues the SP-A/B/C frames-out cutover. SP-A/B/C shipped
(PRs #684/#685/#686) + opt #1 (#691, native-frames-direct build). The binding
100M complete-path bench (run `26858805412`) returned the verdict that motivates
this work.

---

## Background: the 100M verdict

Complete-path bench (`scripts/bench_pipeline_complete_path.py`), 100M pairs,
`large-new-64GB`:

| variant  | build s | golden s | id_prep s | peak RSS MB |
|----------|---------|----------|-----------|-------------|
| legacy   | 303.7   | 60.9     | 49.1      | 61,079.6    |
| columnar | 316.1   | **3.9**  | **515.7** | 60,540.0    |

- **No OOM** on either; RSS delta is **-0.9%** (540 MB) — negligible.
- Columnar is **~2x slower wall** (836s vs 414s), entirely in `id_prep`:
  golden is a real 15.6x win, but `id_prep` is **10.5x slower** and swamps it.

`id_prep` on each variant (bench `_one_run`):
- legacy: `ClusterPairScores.from_pairs(pairs_list, clusters)` → 49.1s
- columnar: `ClusterPairScores.from_frames(frames.assignments, pairs_list)` → 515.7s

Both call the SAME `_bucket_pairs(pairs, member_to_cid)` Python loop over the
SAME 100M `pairs_list`. The 5M cProfile shows `_bucket_pairs` is ~91% of
`from_frames` (6.35s of 6.99s); the `member_to_cid` rebuild (`.to_list()` x2 +
`int()` casts) is small at 5M. So the 10x blowup at 100M is **not** linear CPU —
it is the **100M-entry `dict[int, dict[tuple[int,int], float]]` being built at
the 60 GB ceiling**, where GC + page pressure make it superlinear. Legacy's
49.1s is roughly the same `_bucket_pairs` work but it does not pay the columnar
allocation profile at the same memory phase.

**Conclusion:** the dict-of-dicts view IS the scale cost. SP-A/B/C was necessary
plumbing but net-negative alone at 100M. The fix is to stop materializing a
100M-entry Python dict-of-dicts on the columnar path.

---

## The byte-identical invariant (the durability constraint)

`_bucket_pairs` semantics that MUST be preserved exactly (the CI parity gate +
`test_cluster_frames_out_parity` enforce this; two runtime bugs already survived
static review on SP-A, so the gate — not review — is the verifier):

```python
by_cid = {}
for a, b, s in pairs:              # INPUT order
    ca = member_to_cid.get(a)
    if ca is not None and ca == member_to_cid.get(b):   # both endpoints, same cid
        by_cid.setdefault(ca, {})[(a, b)] = s            # key (a,b) AS GIVEN; LAST-WINS
return by_cid
```

Four properties any replacement must reproduce per cid, byte-for-byte:
1. **Membership:** a pair is kept iff both endpoints map to the same cid
   (cross-cut edges of a split cluster are dropped — one endpoint outside).
   Either endpoint absent from `assignments` (`.get` → `None`) drops the pair.
2. **Key:** `(a, b)` exactly as given in the input pair — NOT canonicalized.
   `(7,3)` and `(3,7)` are DISTINCT keys in the same cid's dict.
3. **Insertion order** of keys within a cid: order of **first** occurrence
   (Python dict preserves first-insert position; later updates keep position).
4. **Value:** the score at the **last** occurrence of that `(a, b)` (LAST-WINS).

**Two latent behaviors that MUST be preserved bug-for-bug (the parity gate
asserts current behavior, not "correct" behavior):**
- **`score_for` query-canonicalize vs stored-as-given MISS.** `score_for`
  (cluster_pairscores.py:106) looks up `(min(a,b), max(a,b))` while keys are
  stored as-given. So a pair stored `(7,3)` is MISSED by `score_for(cid,3,7)`
  → returns `None` → resolve.py:712 emits `score=None`. The frame-backed version
  MUST canonicalize ONLY the query argument and compare against as-given stored
  keys — do NOT canonicalize the stored keys (that would "fix" the miss and
  change identity output). Identity parity fixture MUST include a weak cluster
  whose `bottleneck_pair` orientation is the REVERSE of the stored input pair.
- **Self-pairs** `(a,a)` with `a` in a cid pass the membership test and are
  kept; the join+filter (`cid_a==cid_b`) keeps them too. Parity holds; no action
  beyond not special-casing them.

**Structural invariant the join depends on:** `assignments` is UNIQUE on
`member_id` (one row per member, singletons included). If a member appeared in
two rows the endpoint join would fan out and corrupt `first_i`/`last_score`.
The parity test MUST assert `assignments["member_id"].is_unique().all()`.

Consumers (`for_cluster`, `score_for`, `iter_clusters`) and their callers in
`identity/resolve.py` (489/656 `for_cluster`, 702 `score_for`) read per-pair
scores to emit one evidence edge per pair, so the view is load-bearing — it
cannot be skipped.

---

## Stage 1 — #3b-min: vectorized build, dict-of-dicts retained

Replace the Python `_bucket_pairs` loop in `from_frames` with a Polars join,
then materialize the SAME dict-of-dicts. Output structure unchanged → identity
and accessors untouched → smallest parity surface.

**Build:**
1. `pairs_df = pl.DataFrame({"a","b","s"})` with a row index `__i__` (0..N-1,
   input order). On the pipeline path, prefer the existing `_columnar_pairs_df`
   (SP3, pipeline.py:1935) so the 100M-tuple list is never re-materialized; the
   bench can build it from `pairs_list` once.
2. Map endpoints to cids via join against `assignments` (member_id → cluster_id):
   `cid_a` on `a`, `cid_b` on `b`.
3. Filter `cid_a.is_not_null() & cid_b.is_not_null() & (cid_a == cid_b)`; set
   `cid = cid_a`.
4. LAST-WINS + first-insert order, vectorized. The `last_score` expression is
   PINNED — use exactly `pl.col("s").sort_by("__i__").last()` (or
   `pl.col("s").gather(pl.col("__i__").arg_max())`). **NEVER `pl.col("s").max()`**
   — max is MAX-score, not LAST-score, the exact bug the docstring at
   cluster_pairscores.py:59-62 warns against, and it also mishandles NaN.
   Per `(cid, a, b)` group: `first_i = pl.col("__i__").min()`,
   `last_score = pl.col("s").sort_by("__i__").last()`. Then `sort` by
   `(cid, first_i)` (a total order — `first_i` is unique within a cid, so the
   sort is deterministic; do NOT sort by `(a,b)`).
5. Materialize `by_cid` from the sorted frame.

**Honest risk (do NOT bench Stage 1 at 100M):** step 5 still builds a 100M-entry
dict-of-dicts AND adds a join + group_by + sort, so by this spec's own root-cause
analysis (the blowup is allocation-bound at the ceiling, NOT `_bucket_pairs`
CPU) Stage 1 is LIKELY a no-op-to-regression on the 100M verdict axis. Its
value is ONLY as the byte-identical join FOUNDATION that Stage 2 reuses (it
produces the `view_df` Stage 2 keeps frame-backed). Therefore Stage 1's gate is
the **cheap CI parity lane only** — "prove it green" = byte-identical parity,
NOT a 100M bench run. The single 100M complete-path bench is spent ONCE, after
Stage 2, where the frame-backing actually removes the dict-of-dicts.

## Stage 2 — #3b-full: frame-backed view, no dict-of-dicts

Back `ClusterPairScores` with a Polars frame instead of `dict[int, dict]`.

**Backing:** add a `_partitions` slot alongside the existing `_by_cid`.
`from_frames` produces `view_df(cid, a, b, s)` already deduped (LAST-WINS) and
ordered (`cid`, first-insert) from the Stage-1 join WITHOUT materializing the
global dict-of-dicts, then `_partitions = view_df.partition_by("cid",
as_dict=True)` ONCE → `dict[cid → small frame]`. This is a dict of ~num_clusters
Arrow frames (dense, columnar), NOT a 100M-entry Python dict-of-tuples — that is
the peak-RSS win. `_by_cid` stays the backing for the legacy dict paths
(`from_pairs`/`from_cluster_dict`); the accessors check which backing is set.

**Accessors (byte-identical semantics; the resolver calls `for_cluster(cid)`
once per cid, so per-call cost must be O(cid), never O(total)):**
- `for_cluster(cid)` → when `_partitions` is set, build the per-cid `dict`
  on demand from `_partitions[cid]` (a small frame), in first-occurrence order
  → keys/order/values byte-identical to Stage 1. The dict is transient (the
  resolver GCs it after emitting that cluster's edges), so peak RSS holds ONE
  cid's dict at a time, not all 100M. O(cid pairs) per call; total across the
  resolver loop is O(total pairs) — same as legacy `_bucket_pairs`, but never
  resident all at once. No `partition_by` per call (done once at build).
- `score_for(cid, a, b)` → canonicalize ONLY the `(min,max)` QUERY, look up
  against the as-given stored keys of `_partitions[cid]` — preserving the
  existing MISS-on-reversed-orientation behavior (see latent-behaviors above).
- `iter_clusters()` → yields `(cid, [(a,b,s), ...])` from each partition in
  first-occurrence order (ordering is parity-tested even though no resolve.py
  call site uses it today).

**Identity consumption (`identity/resolve.py`):** UNCHANGED. The resolver keeps
calling `for pair_key, score in view.for_cluster(cid).items()` per cid (489/656)
and `view.score_for(...)` (702). Because `for_cluster` is now O(cid) and
transient, no resolver restructure is needed and the dict and frame paths share
one consumer. (We do NOT claim an `iter_clusters` fast path — the resolver loop
is cid-driven; `iter_clusters` exists for completeness/future use only.) The
dict-backed legacy paths stay exactly as they are.

**Win target:** columnar `id_prep` at/below legacy's 49s AND peak RSS below
legacy's 61 GB (no resident 100M-entry dict-of-dicts) → columnar wins wall AND
RSS. Verified by the single post-Stage-2 100M bench.

---

## Parity & testing (the real verifier)

- `tests/test_cluster_frames_out_parity.py` extended: assert the view (Stage 1
  `_by_cid`, and Stage 2 via `for_cluster`/`score_for`/`iter_clusters`) is
  byte-identical to `from_pairs(...)` for every cid, on a fixture that INCLUDES:
  - a **cascading auto-split** clique (the shape that caught the two SP-A
    runtime bugs);
  - a **duplicate `(a,b)` whose LATER occurrence has a LOWER score than the
    earlier** — so MAX-score and LAST-score DIVERGE (pins F1: catches any
    `max()` slip);
  - cross-cut edges (both-endpoints-same-cid drop);
  - a `(7,3)` / `(3,7)` reversed-orientation pair (distinct-keys property);
  - an assertion that `assignments["member_id"].is_unique().all()` (the join's
    fan-out invariant);
  - `iter_clusters()` tuple-order equality per cid.
- An identity-level parity test (extend `test_identity_from_frames_parity`):
  resolving with the frame-backed view yields the same evidence-edge set
  (entity partition + per-edge scores) as the dict path — INCLUDING a weak
  cluster whose `bottleneck_pair` is stored in the REVERSE orientation of the
  `score_for` query, asserting the conflict edge still emits `score=None`
  (preserves the F3 miss bug).
- CI-validated posture: subagents validate Python via `ruff` + `py_compile`
  only (the box hangs on `import goldenmatch`/`polars`); the parity gate runs in
  CI's `python (goldenmatch)` lane (native=1 included). No local pytest.
- After Stage 2 ONLY (Stage 1 gates on the cheap parity lane — see Stage 1's
  honest-risk note): re-dispatch the 100M complete-path bench
  (`bench-pipeline-complete-path.yml`, `np=100000000`) to measure the real
  `id_prep` + RSS delta. The bench is DATA, not a kill gate.

## Files

- Modify: `goldenmatch/core/cluster_pairscores.py` (`from_frames`, the
  `ClusterPairScores` backing + accessors).
- Modify: `goldenmatch/core/pipeline.py` (thread `_columnar_pairs_df` into the
  `from_frames` call so the view build is frame-native end-to-end).
- Modify: `goldenmatch/identity/resolve.py` (frame-friendly consumption fast
  path; dict interface preserved).
- Test: `tests/test_cluster_frames_out_parity.py`,
  `tests/test_identity_from_frames_parity.py`.
- Bench: `scripts/bench_pipeline_complete_path.py` (pass the columnar pairs
  frame to `from_frames` on the columnar leg so the bench measures the new path).

## Non-goals

- No change to legacy (`from_pairs`/`from_cluster_dict`) behavior or the
  non-frames-out pipeline path.
- No change to `build_cluster_frames` / SP-A/B/C.
- No new public API surface beyond the frame-backed accessors.

## Auth / process

- Branch off `main`; benzsevern auth for push
  (`GH_TOKEN=$(gh auth token --user benzsevern)`); open PR; CI parity gate
  verifies. NEVER benzsevern-mjh.
