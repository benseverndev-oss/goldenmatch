# goldengraph SP2 — native portable store + bi-temporal model — design

**Status:** Design draft 2026-06-20 (rev 2, post spec-review). Keystone phase of the program roadmap (`2026-06-20-goldengraph-program-roadmap.md`). Awaiting approval → plan.

**Builds on:** SP1 (`goldengraph-core` in-memory engine, shipped #1131). **Surface:** Rust core, pyo3-free (WASM/C inherit it in SP5). **Unblocks:** SP3 persisted communities, SP4 durable pipeline, and the Identity Graph v3 substrate.

---

## Motivation

SP1 builds a resolution-merged graph in memory and throws it away. To be a KG *engine* — and the Identity Graph v3 substrate — the graph must persist, survive incremental updates, and answer **true bi-temporal** time-travel: not just "what is true in the world as of valid-time V" but "what did the store *believe* as of transaction-time T" (reproduce a report issued before a later correction). Only Graphiti among the popular frameworks does bi-temporal at all, and it does so via LLM-driven edges; a deterministic, portable, native bi-temporal store is both the competitive feature and the suite's durable-identity foundation.

## Scope

A persistence + temporal layer baked into `goldengraph-core`:

1. **Durable identity keyed on a host-supplied stable record key** — each entity is anchored by the `record_key`s of its member records (the `:h1:` fingerprint computed upstream — see "Identity key" below). Entities keep a stable id across appends, with full merge/split history.
2. **True bi-temporal facts** — every edge fact is an append-only version carrying *valid time* `[valid_from, valid_to)` and *transaction time* `ingested_at`. `as_of(valid_t, tx_t)` filters on **both** axes.
3. **Portable snapshot** — load/save in canonical JSON every binding can read and golden-vector parity can diff byte-for-byte.
4. **Incremental, identity-reconciling append** — a new batch is reconciled against stored identity by record-key overlap (NOT re-resolved); membership changes emit `Merge`/`Split` history.

## Resolved decisions

- **Identity key: host-supplied opaque `record_key: String`** (the existing goldenmatch `:h1:` record fingerprint, from `goldenmatch-fingerprint-core`). `goldengraph-core` treats it as **opaque** — it does NOT depend on `fingerprint-core`; SP4's Python pipeline computes the key and passes it in. This reuses the suite's established cross-surface stable id and keeps the core dependency-light.
- **True bi-temporal**, two-axis `as_of(valid_t, tx_t)`. Corrections are append-only new versions with a later `ingested_at` (no in-place mutation), so tx-time queries are exact.
- **Store format: serde model + `serde_json` snapshot.** SP2's value is the *model*, not the wire format. JSON is inspectable, parity-diffable, WASM/C-friendly. Compact binary (bincode/Arrow IPC) is a later optimization behind the same `GraphStore` trait — out of SP2. Promotes `serde` + `serde_json` to real (pyo3-free, WASM-safe) deps.
- **Time is caller-supplied `i64`, never read from a clock in the core** (preserves SP1's determinism rule).

## Identity key (the keystone)

A `record_key` is an opaque, durable string the host assigns to each source record (goldenmatch's `:h1:` fingerprint). It is stable across append batches and surfaces — unlike SP1's `MentionId`, which is a within-build index and is NOT a valid cross-append key. Each stored entity owns the **set** of `record_key`s of the records that resolved into it; reconciliation across appends is set overlap on these keys.

## Data model (new `store.rs`; reuses SP1 `model.rs`)

```
type StableId = u64;                 // durable; assigned once, monotonic, never reused

// ---- append input (SP4 builds this from extraction + SP1 resolution) ----
struct BatchEntity { local_id: u32, canonical_name: String, typ: String,
                     surface_names: Vec<String>, record_keys: Vec<String> }   // record_keys: sorted, deduped
struct BatchEdge   { subj_local: u32, predicate: String, obj_local: u32,
                     valid_from: i64, valid_to: Option<i64>, source_refs: Vec<String> }
struct StoreBatch  { entities: Vec<BatchEntity>, edges: Vec<BatchEdge>, ingested_at: i64 }

// ---- stored state ----
struct StoredEntity { id: StableId, canonical_name: String, typ: String,
                      surface_names: Vec<String>, record_keys: Vec<String>,   // sorted
                      created_at: i64,                       // tx-time first seen
                      superseded_by: Option<StableId>, superseded_at: Option<i64> } // tx-time of supersession
struct StoredEdge   { subj: StableId, predicate: String, obj: StableId,
                      valid_from: i64, valid_to: Option<i64>, ingested_at: i64,
                      source_refs: Vec<String> }              // sorted, deduped
enum HistoryEvent   { Merge { kept: StableId, absorbed: Vec<StableId>, at: i64 },
                      Split { from: StableId, into: Vec<StableId>, at: i64 } }   // at = tx-time

struct GraphStore { entities: BTreeMap<StableId, StoredEntity>,
                    edges: Vec<StoredEdge>, history: Vec<HistoryEvent>, next_id: StableId }
```

SP1's `EntityId` (`u32`) stays the within-build id; `StableId` (`u64`) is the durable id the store owns.

## API

```
trait GraphStore {
    fn open(snapshot: Option<&str>) -> Result<Self, StoreError>;  // parse JSON or empty
    fn snapshot(&self) -> String;                                 // canonical serde_json
    fn append(&mut self, batch: StoreBatch);                      // reconcile + version + history
    fn as_of(&self, valid_t: i64, tx_t: i64) -> Graph;            // bi-temporal slice -> SP1 Graph
    fn history(&self, id: StableId) -> Vec<HistoryEvent>;         // events literally naming id (kept/absorbed/from/into); no chain-follow
}
```

`as_of` returns an SP1 `Graph`, so SP1's `neighborhood` / `seeds_by_name` run unchanged over a temporal slice.

## Stable-id reconciliation (total, deterministic, collision-free)

Identity flows from **one** rule — *each stored entity is inherited by its plurality-heir* — so id assignment and merge/split are a single consistent computation, never two rules that can disagree.

Candidates = stored entities **current as-of `batch.ingested_at`** sharing ≥1 `record_key` with some batch entity. (Invariant: batch entities within one `StoreBatch` have **distinct** `record_key` sets — they are the output of a single resolution pass, so a `record_key` belongs to exactly one batch entity. This makes the step-1 tie-break total.)

1. **Heir of each stored id.** For each candidate stored id `s`, its *plurality-heir* is the batch entity with the largest `record_key` overlap with `s` (tie → batch entity with the lexicographically-smallest sorted `record_keys`). Each stored id maps to **exactly one** heir, so no stored id is ever claimed by two batch entities — **collision-free by construction**.
2. **Assign each batch entity `N`.** Let `inherited(N)` = the stored ids whose heir is `N`.
   - empty → `N` is new: mint `next_id`.
   - `{k}` → `N` keeps `k`.
   - `{k1,…,kn}`, n>1 → **Merge:** `N` keeps `k = min(k1…kn)`; the rest are absorbed (`superseded_by=k`, `superseded_at=batch.ingested_at`); emit `Merge{kept:k, absorbed:[…], at}`. Their `record_key`s union into `k`.
3. **Split.** A stored id `s` is split when its `record_key`s land across >1 batch entity (some of `s`'s keys go to a batch entity other than `s`'s heir). `s` stays with its heir; emit `Split{from:s, into:[heir-id, …other absorbing ids], at}`.
4. **Edge remap.** Each `BatchEdge`'s `subj_local`/`obj_local` remap to the assigned `StableId`s before storage; an endpoint whose stored id was absorbed in this same batch lands on the surviving `kept` id.

Totality: every batch entity inherits (≥1 stored id chose it) or mints — always assigned. Collision-free: each stored id has exactly one heir (step 1). No reuse: `next_id` only increments. Determinism: plurality (max overlap; tie → smallest sorted `record_keys`) and `min()` are permutation-invariant, so the result is independent of within-batch order. The merge, split, and merge+split cases are each pinned by a test. (The earlier counterexample — `s={a,b,c,d,e}`, batch `N1={a,b}`, `N2={c,d,e}` — now resolves unambiguously: `s`'s heir is `N2` (overlap 3>2), so `N2` keeps `s`'s id and `N1` mints; one `Split`.)

## Bi-temporal semantics (`as_of(valid_t, tx_t)`)

- **Edges:** group `StoredEdge`s by the triple `(subj, predicate, obj)`. Within a group, take the version with the greatest `ingested_at ≤ tx_t` (the store's belief as-of `tx_t`); include it iff `valid_from ≤ valid_t < valid_to` (open `valid_to` = `+∞`). Corrections (a later `ingested_at` with a changed `valid_to`) are therefore reflected only for `tx_t` at/after the correction — exact tx-time travel. A full **retraction** is expressed as a new version with an empty valid window (`valid_to ≤ valid_from`), which the inclusion test excludes for every `valid_t`.
- **Entities:** resolve the supersession chain as-of `tx_t` — an entity is *current as-of `tx_t`* iff `created_at ≤ tx_t` and (`superseded_at` is `None` or `superseded_at > tx_t`); if superseded as-of `tx_t`, follow `superseded_by` (only while that hop's `superseded_at ≤ tx_t`) to the surviving id.
- **Graph assembly:** the returned `Graph` contains the entities that are endpoints of the included edges (mapped to their current-as-of-`tx_t` ids), each rendered as an SP1 `EntityNode`. Isolated entities (no live edge) are omitted, matching SP1's edge-driven retrieval semantics.

## Determinism + parity

Canonical snapshot:
- `entities`: `BTreeMap` by `StableId`. Each entity's `record_keys` + `surface_names` sorted.
- `edges`: sorted by the **full tuple** `(subj, predicate, obj, valid_from, valid_to[None last], ingested_at, source_refs joined)` — total order, no ambiguity. Each edge's `source_refs` sorted + deduped.
- `history`: append order (deterministic under the single-writer non-goal).
- No `HashMap` anywhere in the serialized state.

Parity tests: (a) **round-trip** — `open(snapshot())` re-serializes byte-identical; (b) **within-batch order independence** — permuting entity/edge order *within* a `StoreBatch` (same `ingested_at`) yields a byte-identical snapshot (catches any residual ordering bug, e.g. `source_refs`). Golden vectors extend the SP1 contract: `(batches with timestamps) -> (snapshot, as_of(v,t) queries)` byte-checked; SP5's WASM/C reuse it.

## Testing (TDD)

- **Round-trip** + **within-batch order independence** (above).
- **Stable id:** same `record_key` across two appends → same `StableId`, no dup.
- **Merge (headline):** two entities that later share a record set → one `Merge`, `superseded_by/at` set, both surface forms retained; `as_of(_, before)` separate, `as_of(_, after)` merged. Time-travel across a resolution change.
- **Split:** one stored entity's keys land in two batch entities → `Split`, plurality-heir keeps id, other minted.
- **Merge+split double-claim:** a batch where two batch entities both best-overlap the same stored id → deterministic arbitration, **no id collision** (the reviewer's collision case).
- **Inherit + non-heir absorb (composition):** a batch entity `N` that inherits `s1` while also holding some of `s2`'s keys (whose heir is a different entity `M`) → `N` keeps `s1`'s id, `s2` stays with `M`, and `s2`'s `Split.into` lists `N` as a non-heir absorbing entity. Pins that inherit and split compose correctly.
- **Bi-temporal valid axis:** edge with `valid_to` set absent from `as_of(after valid_to, _)`, present within.
- **Bi-temporal tx axis:** append an edge, then append a correction (later `ingested_at`, changed `valid_to`); `as_of(v, before_correction)` shows the original, `as_of(v, after)` shows the correction — proves tx-time travel.
- **Golden-vector byte-equality.**

## CI

Extend `.github/workflows/goldengraph.yml` `core` job — new tests run under the existing `cargo test --manifest-path goldengraph-core/Cargo.toml` + clippy. No new lane.

## Non-goals (SP2)

Compact binary format (later, same trait). Pyo3 binding of the store (SP4). Community structure (SP3). LLM/extraction + `record_key` computation (SP4 / fingerprint-core). Distributed/sharded storage. Concurrent writers (single-writer snapshot model). In-place edge mutation (corrections are append-only versions).

## Risks / open questions (resolve in the plan)

- **Reconciliation cost:** record-key overlap is O(batch × candidates); fine for SP2 correctness. An inverted index `record_key -> StableId` is the obvious optimization (note for the plan, not required).
- **Snapshot size** with JSON at scale — acceptable for SP2 (correctness first); compact binary is the escape hatch behind the trait.
- **Supersession chains** (an entity merged, then merged again): `as_of` chain-follow must stop at hops with `superseded_at > tx_t`; the merge-history test should include a two-hop chain to pin it.
