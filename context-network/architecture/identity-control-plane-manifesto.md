# Identity Control Plane — architecture manifesto

Date: 2026-07-26
Status: **PROPOSED** — the Tier-5 doc `context-network/architecture/one-product-two-engines.md`
(decision 0047) deferred to. Recommendations here follow the two-engine frame; the
incremental-resolution fork (§4) is an OPEN decision that needs sign-off before code.
Companion to the governing frame; scoped to the *control plane* engine only.

## Why this doc

The governing frame splits GoldenMatch into two engines and says the **Identity Control
Plane** is transaction-native, out of the Arrow-core thesis, and needs its own architecture.
The thesis-conformance audit (decision 0047, `parity/thesis_conformance.yaml`) then found the
control plane is the *weakest* area in reality:

- **critical** — incremental resolution against a persisted index is absent (frame §9.1);
- **medium** — the compute→control handoff is ad-hoc in-memory args, not a versioned batch;
- **low** — the shared frame-residency budget across the seam is comment-documented only.

The acute payload-drop bug on that seam is already fixed (all three Postgres bulk paths now
carry provenance). This doc specifies the *design* the audit's remaining seam items need.

## 1. What exists today (grounded)

- **Handoff:** `identity/resolve.py::resolve_clusters(clusters|cluster_frames, df, scored_pairs,
  matchkey_name, store, run_name, *, dataset, controller_snapshot, emit_singletons,
  pair_score_view, actor, …)` — live polars/Arrow frames plus loose args. No schema, no
  version, no bundled provenance object. `controller_snapshot` is an opaque dict; `run_name`/
  `matchkey_name` are bare strings.
- **Write:** per-row (`upsert_identity`/`upsert_record`/`add_edge`/`emit_event`) and bulk
  (`bulk_*`) paths on SQLite + Postgres; provenance now carried on all of them.
- **Store schema:** `identity_nodes`, `source_records` (indexed on `entity_id`/`source`/
  `record_hash`), `evidence_edges` (UNIQUE on entity/pair/kind/run), append-only
  `identity_events` (+ `audit_seals` hash chain). **No block-key / fingerprint index.**
- **Incremental:** `resolve_record_incremental` / `match_record_to_entity` / `StreamProcessor`
  exist but **hold the entire prior corpus in RAM and re-block per record** (`streaming.py`
  grows `self._df` every record); `match_one` returns `[]` for exact matchkeys.
- **Residency:** `emit_singletons=True` materializes every referenced row; `_bulk_flush_rows`
  caps the write accumulator. The shared budget is inline comments + env knobs
  (`GOLDENMATCH_IDENTITY_BULK_FLUSH_ROWS`), not a contract.

## 2. Primary contract (unchanged from the frame, restated for scope)

> Given the same prior state, observations, evidence, configuration, and accepted steward
> decisions, identity transitions are **deterministic, durable, idempotent, and auditable.**

Correctness is defined by state transitions, transaction boundaries, stable identifiers,
mutation ordering, conflict handling, history preservation, and recovery — **not** by Arrow
layout. Storage backends (SQLite/Postgres/future) conform to the same externally observable
semantics but differ in locking/concurrency/throughput; storage behavior is part of the
design, not a passive box.

## 3. The versioned resolution-batch contract (closes the seam medium)

Replace the loose-args handoff with an explicit, **versioned** `ResolutionBatch` — specified
independently of any in-memory object model (Arrow may be *one* representation of its bulk
parts, but the semantic contract stands alone):

```
ResolutionBatch v1
├── contract_version: int              # bump on any field change
├── run_id: str                        # was run_name
├── dataset: str
├── model_config_version: str          # config + model + reference-data version
├── matchkey: {name, type}
├── controller_snapshot: json          # provenance, not opaque-by-accident
├── records[]: {record_id, source, source_pk, record_hash, payload}
├── clusters[]: {members[], confidence, bottleneck_pair, oversized}
├── pair_evidence[]: {a, b, score, matchkey, field_scores?, negative_evidence?}
└── actor, trust, recorded_at
```

Rules the contract makes explicit (each is a current implicit or a past bug):
- **Evidence payload carriage is part of the contract**, not backend-dependent — the
  payload-drop trap (fixed for Postgres bulk this wave) becomes structurally impossible:
  a batch either carries a payload field or it does not, identically on every backend/flush.
- **Shared residency budget is a declared contract term** — the batch is consumed in bounded
  chunks; the compute prep floor + control write accumulator share one documented budget
  (fold the `emit_singletons` / flush-rows knobs into the contract, not comments).
- **Idempotency key** = `(run_id, entity_id, kind)` (matches today's `has_run_event` +
  `evidence_edges` UNIQUE) is a contract field, so replay is defined, not incidental.

Migration: `resolve_clusters` keeps its signature as a thin adapter that builds a
`ResolutionBatch` and calls a new `apply_batch(store, batch)`; no behavior change on day 1.

## 4. OPEN — incremental resolution against a persisted index (the critical)

Today "incremental" is faked by re-blocking the whole in-RAM corpus. Real incremental ER
scores a NEW record against a **persisted index of prior identities** without re-blocking the
corpus. This is *compute that reads durable state* — the frame's §9.1 concern that neither
engine owns as drawn. Three ways to home it:

- **(i) Stateful compute engine** — the compute engine persists + reads its own blocking
  index. *Rejected:* puts durable state in the engine the thesis keeps stateless-per-call.
- **(ii) Control-plane-owned index, compute queries it** — the control plane persists a
  block-key / fingerprint index alongside identities (it already owns durable state); the
  seam becomes **bidirectional**: control→compute hands candidate rows, compute scores them
  statelessly, control commits. *Recommended.* Keeps compute stateless, durable state with
  durable identities, and only adds a read-back edge to the existing seam.
- **(iii) Explicit third "incremental resolution" service** — *Deferred:* premature; (ii)
  already expresses the alternation without a third engine.

**Recommendation: (ii).** Concretely:
1. Add a persisted **blocking index** to the store (`record_block_keys(entity_id, record_id,
   block_key, pass_sig)` + a fingerprint column), populated on every write (bulk + per-row).
2. `resolve_record_incremental` computes the new record's block keys/fingerprint (compute,
   stateless) → queries the store index for candidate `record_id`s (control read) → scores
   new-vs-candidates (compute) → resolves/commits (control). No full-corpus materialization.
3. Fix the exact-matchkey gap (`match_one` returns `[]` today) so exact incremental works.
4. `LanceCandidateStore` already proves the out-of-core gather pattern for the ANN path —
   reuse that shape for the persisted block gather.

**Decision needed from the owner:** confirm (ii), or pick (i)/(iii). This gates all
incremental code below.

## 5. Transaction-native semantics to specify (Tier-5 scope)

Each gets a spec entry + conformance fixtures (the frame's spec+conformance contract), not
prose: stable-ID assignment + cross-run stabilization; record ownership/absorption; **merge**
(winner = most members, tie oldest `created_at`) and **split** rules; idempotency + replay;
concurrency (the Postgres pool + `write_pipeline` batching already exist); append-only event
ordering + audit-seal chain; stewardship worklists + mediation verdicts; rollback/compensation;
temporal identity version. Most already have code (`stabilize.py`, `mediation.py`,
`survivorship.py`, `audit.py`) — this wave writes the *conformance* layer, not new behavior.

Also fold in the medium `three-golden-record-implementations`: `resolve.py`'s rollup,
`core.golden.merge_field`, and `survivorship.py::build_golden_with_provenance` are three
sources of truth — the survivorship spec entry picks one authoritative owner.

## 6. Milestones

- **C1 (contract):** `ResolutionBatch v1` + `apply_batch`; `resolve_clusters` becomes its
  adapter (no behavior change); residency budget as a contract term. Closes the medium + low.
- **C2 (persisted index):** store block-key/fingerprint index + population on write; the
  §4(ii) bidirectional seam; exact-matchkey incremental. Closes the critical. *Needs the §4
  decision first.*
- **C3 (conformance layer):** spec entries + fixtures for the §5 semantics; single-source the
  golden-record rollup. Closes `three-golden-record-implementations`.
- **C4 (scale proof):** incremental resolve of N new records against an M-identity store with
  bounded RSS (no full-corpus materialization) — the measured kill criterion.

## What this is not

Not a rewrite: C1 is an adapter, C3/C5 are conformance over existing behavior; only C2 adds a
real subsystem (the persisted index), and only after the §4 decision. Storage stays
SQLite-default / Postgres-optional; no Arrow in the transaction semantics.

---
**Classification:** foundation/architecture • **Status:** proposed • **Last updated:** 2026-07-26
