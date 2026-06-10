# Sail tier — Stage S5: identity-on-Sail (distributed create + edge-emit)

**Date:** 2026-06-10
**Status:** design (approved by Ben; pre-spec-review)
**Parent:** `2026-06-03-sail-tier-design.md` (the Sail tier). S1–S4 shipped the buildable
Sail pipeline (load → block → score → dedup → WCC → golden). The S3 scope decision split
**identity** off to its own later stage ("stateful entity store + `resolve_clusters`
overlap, resolved driver-side even in the Ray path — not a relational op"). This is that
stage.

**Goal:** Re-express the **create + edge-emit** layer of identity resolution against Sail
(Spark Connect), distributed, so the identity graph for a **fresh-store run** is produced
as Spark DataFrames written distributed — removing the driver-side identity island for the
common case. The stateful **incremental** layer (absorb/merge against an existing store)
is explicitly deferred as an honest-null, exactly as the one-box DataFusion spine recorded
for one-box spill survival (Stage E) and as S3 did for order-dependent golden strategies.

---

## The finding that shapes the scope

`goldenmatch.identity.resolve.resolve_clusters` splits into two layers:

**Layer 1 — relational (distributable).**
- Emitting `same_as` evidence edges between scored within-cluster pairs. Entity-independent:
  depends only on cluster membership + pair scores.
- The **brand-new-cluster create** path. In `resolve.py` this is already a special-cased
  batch path (`use_bulk_fast_path`, postgres-only, no existing entities, no weak conflict →
  bulk COPY of nodes/records/edges/events). On a **fresh store every cluster takes it.**

**Layer 2 — stateful (NOT relational).**
- The create/absorb/**merge** decision reads the existing identity store, is order-dependent
  (merge winner = most members, tie-break oldest `created_at`; loser records reassigned
  *before* the next iteration sees them), and writes to a transactional DB.

**Decisive evidence:** the Ray path never distributed Layer 2 either.
`distributed/identity.py::resolve_identities_distributed` does `materialize_cluster_dict`
(driver-side collect) then runs the *same* `resolve_clusters` against a pooled Postgres
connection. The "true per-partition resolve" was a documented Phase-5 follow-up that never
landed — only the `identity_partition.py::partition_cluster_frames` building block exists,
used in tests, never wired into a production per-worker resolve. So Layer 2 staying
driver-side is **intrinsic to the algorithm**, not a Sail limitation.

**Scope (Ben's call, 2026-06-10):** S5 covers **Layer 1 only** — distributed create +
edge-emit on a fresh store. Layer 2 (incremental) is deferred, honest-null. This mirrors
the S3 golden-only scoping exactly.

## Scope guard

**IN:** a new `sail/identity.py` — given the upstream Sail frames (`pairs`, `assignments`,
`source_df`, `golden_df`) plus run metadata, produce the durable identity graph as
**distributed Spark DataFrames** (`nodes`, `source_records`, `same_as` edges, + an optional
`events` bookkeeping frame), written distributed (parquet), never collected to the driver.
Pure-relational joins + scalar pandas-UDFs, reusing S1/S3's proven `pandas_udf` mechanism.

**OUT (deferred, explicit honest-null):**
- Incremental resolve — absorb/merge against an existing store. Inherently stateful +
  order-dependent; stays driver-side exactly like the Ray path. Documented, not silent.
- Weak-cluster `conflicts_with` edges and merge carry-forward (both touch Layer 2).
- Loading the produced frames into the durable Postgres store (a thin downstream COPY step;
  the one-box `bulk_upsert_*`/`bulk_add_edges` COPY methods already exist). S5 *produces*
  the graph distributed; *landing* it is out of scope for the distributed stage.

---

## Determinism (a load-bearing property, not a nicety)

The one-box create path mints **UUIDv7** entity_ids (`new_entity_id()` — time + random,
non-reproducible even one-box). A distributed create cannot and should not reproduce that
sequence. S5 instead mints a **deterministic content-derived entity_id**:

> `entity_id = "ent:h1:" + sha256(canonical(sorted(member_record_ids)))[:N]`

Sorted member record_ids → order-independent; SHA-256 (the existing record-id `h1`
h-scheme) → reproducible; no worker coordination, no driver-side counter. Combined with
**passed-in** run timestamps (`run_meta.recorded_at`, not per-row `datetime.now()`), the
entire S5 output is **byte-identical on re-run** for the same input — matching the spine's
determinism-gate posture and making the parity test exact rather than fuzzy.

This introduces a second entity-id scheme (`ent:h1:`), **Sail-create-only**, gated by a
kill-switch (`GOLDENMATCH_SAIL_IDENTITY_ID_SCHEME`, default `h1`). On a fresh store there is
no prior identity to be stable against, so the content hash *is* the natural stable seed;
cross-run stability via overlap detection is a property of the deferred Layer 2, not of S5.

---

## Module layout (`sail/identity.py`)

All functions take/return Spark DataFrames; joins are on **shared column names** (the S2
`AMBIGUOUS_REFERENCE` lesson: never `df["col"]` cross-handle refs across self-similar joins).

- `derive_record_ids(source_df, *, source_col="__source__", source_pk_col=None)`
  → `source_df` + a `record_id` column. A scalar `pandas_udf` mirroring one-box
  `_record_id_candidates` **primary** path: PK present → `{source}:{pk}`; no PK →
  `{source}:h1:{record_fingerprint(_canonical_payload(payload))[:12]}` (the canonical
  cross-surface fingerprint, reused inside the UDF → parity by construction). The legacy
  `{source}:hash:` *lookup candidate* is a Layer-2 concern (overlap resolution) and is not
  emitted by S5.

- `mint_entity_ids(assignments_with_recid)` → `(cluster_id, entity_id)`.
  `groupBy(cluster_id).agg(collect_list(record_id))` → a scalar `pandas_udf` that sorts the
  member record_ids and hashes them to `ent:h1:{hash[:N]}`. Order-independent, reproducible.

- `build_same_as_edges(pairs, assignments, recid_map, entity_ids, *, run_meta)` → the
  `same_as` edge frame `(entity_id, record_a_id, record_b_id, kind, score, matchkey_name,
  run_name, dataset, recorded_at)`. Join `pairs(a,b,score)` → `cluster_id` (via assignments)
  → `entity_id`; map `a,b → record_id`. Every above-threshold dedup'd pair is within-cluster
  by WCC construction, so the edge set equals one-box's per-cluster `pair_scores` items.

- `build_identity_nodes(entity_ids, golden_df, *, run_meta)` → node frame
  `(entity_id, status, merged_into, golden_record, confidence, dataset, created_at,
  updated_at)`, **one node per entity including singletons**. `status=ACTIVE`,
  `merged_into=NULL` (create-only); timestamps from `run_meta`. `golden_record`:
  **LEFT-join** S3's `build_golden` output (which emits multi-member clusters only,
  `count>1`) onto the full `entity_ids` set — the LEFT join is what keeps singleton
  entities (their `golden_record` is **NULL**). The parity gate checks node *count* (one
  per cluster), not `golden_record` *content*, so NULL singleton golden is gate-neutral;
  populating it from the single source row (as one-box does) is a deferred polish, not
  needed for the create-path graph. The load-bearing point: naive reuse of `build_golden`
  (inner-join) would silently DROP singleton entities and fail count parity — the LEFT join
  is the fix.

- `build_source_records(assignments, recid_map, entity_ids, *, run_meta)` → record frame
  `(record_id, source, source_pk, record_hash, entity_id, dataset, first_seen_at,
  last_seen_at)` = the record→entity assignment (the join `assignments → record_id →
  entity_id`).

- `build_identity_graph(pairs, assignments, source_df, golden_df, *, run_meta, ...)` →
  orchestrates the above; returns `IdentityGraphFrames(nodes, records, edges, events?)`.
  The optional `events` frame is one `CREATED` row per entity (store-parity bookkeeping).

## Data flow

```
source_df ─┬─► derive_record_ids ─────────► source_df + record_id
           │
assignments(cluster_id,member_id) ─► join record_id ─┬─► mint_entity_ids ─► (cluster_id, entity_id)
                                                      │
pairs(a,b,score) ──────────────────────────────────► build_same_as_edges ──► EDGES frame
golden_df(cluster_id,*cols) ──LEFT-join─► build_identity_nodes ◄─ entity_ids ──► NODES frame (incl. singletons; singleton golden=NULL)
                                          build_source_records  ◄─ entity_ids ──► RECORDS frame
                                          (CREATED per entity)   ◄─ entity_ids ──► EVENTS frame (optional)
```

## Pipeline wiring

`run_sail_pipeline` gains an opt-in `emit_identity: bool = False` (+ `source_col`,
`source_pk_col`, `run_meta`). When set, after `build_golden` it also calls
`build_identity_graph` and returns/writes the identity-graph frames alongside the golden
frame (default path returns golden only — byte-identical to today). The `run_meta`
(run_name, dataset, recorded_at) is passed in, not synthesized, for determinism.

---

## Parity gate (the make-or-break, CI)

Against the **one-box** `resolve_clusters` on a **fresh SQLite store**, on a small chain +
junction-multimerge + singleton fixture (the S2/S4 fixture family), via the in-process Sail
server.

**Two fixture-construction constraints the plan MUST honor:**
- The parity reference is the **per-row "Brand-new identity" create branch** (`resolve.py`
  line ~527), the path taken on a fresh SQLite store. The postgres-only `use_bulk_fast_path`
  (foregrounded in "the finding" above) is semantically identical for create output but is
  never exercised on SQLite — do NOT wire the fixture against postgres to reach it.
- One-box `resolve_clusters` must be fed the **same post-dedup `pairs`** (MAX-aggregated) as
  its `scored_pairs` argument, the identical set S5's edge frame is built from. Feeding raw
  pre-dedup pairs would inflate the one-box edge count and break edge-count parity (gate
  part 3) for a non-real reason.

The comparison:

1. **`same_as` edge-set parity** — the canonical `(record_a_id, record_b_id)` set is
   identical; `score` matches within the established native-kernel/rapidfuzz tolerance.
   Entity-independent → exact set comparison.
2. **record→entity partition equivalence** — `{record_id → entity_id}` from S5 induces the
   *same partition* of records as one-box (exact partition match / Rand index 1.0), even
   though literal entity_id strings differ (one-box mints UUIDv7, S5 mints `ent:h1:`).
3. **count parity** — one node per cluster; every member record assigned to exactly one
   entity; edge count == one-box edge count.

This is the 7th `sail` CI gate, path-filtered into the existing `sail` lane (install the
`[sail]` extra, `.venv/bin/python -m pytest`, in-process Sail server), same posture as S1–S4.

## Testing

- Unit: `derive_record_ids` PK and no-PK paths vs one-box `_record_id_candidates`;
  `mint_entity_ids` order-independence (shuffle members → same id) and determinism (re-run →
  same id); `build_same_as_edges` within-cluster invariant.
- Parity: the three-part gate above on the chain/multimerge/singleton fixture.
- Determinism: run S5 twice on the same input → byte-identical `nodes`/`records`/`edges`.
- Negative: `emit_identity=False` returns golden only, unchanged.

---

## What S5 is and is NOT (the honest framing)

**Is:** the genuine removal of the driver-side identity island for the
**first-resolution-of-a-fresh-dataset** case — the common case and the one that actually
scales — produced distributed and parity-proven against the one-box create path.

**Is NOT:** "identity fully solved on Sail." Incremental resolve (absorb/merge against an
existing store) remains genuinely driver-side, because the algorithm is stateful and
order-dependent (the Ray path confirms it never distributed). S5 makes the Sail tier's
identity story honest and parity-proven for the create case; closing incremental at scale
is a separate, harder problem deferred here by design — the same call S3 made for golden.

## Sign-off checklist (to be finalized in the plan)

- [ ] `sail/identity.py` with the six functions above; joins on shared names only.
- [ ] Deterministic `ent:h1:` entity_id + kill-switch; passed-in run timestamps.
- [ ] 7th `sail` CI gate: edge-set + partition + count parity vs one-box fresh-store resolve.
- [ ] `run_sail_pipeline(emit_identity=...)` opt-in; default path unchanged.
- [ ] Honest-null note for deferred incremental recorded in the sail design doc + memory.
- [ ] Ray NOT retired (unchanged — that still gates on the real 100M bench).
