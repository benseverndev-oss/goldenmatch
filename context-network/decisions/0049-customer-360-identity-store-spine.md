# 0049 — Customer 360 is served from the Identity Store spine; GoldenGraph federates, it does not become a second identity store

**Status:** Proposed. **Companion design:**
`context-network/architecture/customer-360-data-connection.md`. **Governed by:** decision
`0047` (one product, two engines, many surfaces) and its five decision-tests; scoped to the
*control-plane* engine (Tier 5), building on the Identity Control Plane manifesto
(`context-network/architecture/identity-control-plane-manifesto.md`).

## Context

The Customer 360 design (companion doc) turns the durable Identity Control Plane into a
product: a persistent, always-fresh, per-field-attributed unified view of each customer,
served by one call and kept current by connected sources. Almost every piece already exists
in `identity/` — stable UUIDv7 `entity_id` (`identity/store.py:269`), append-only
`IdentityEvent` log, per-cell `CellProvenance` (`identity/survivorship.py`), hash-chained audit
(`identity/audit.py`), merge/split (`identity/query.py`), channel stitching
(`identity/stitching.py`), and — since the manifesto's C2 landed — real incremental resolution
against a persisted block index (`identity/block_index.py`, `_resolve_via_index`).

One structural question gates the whole serving layer and has no answer today: **a Customer
360 view is "the entity *and its relationships*" (households, companies, accounts, referrals),
and there are two disjoint durable stores that could own the relationship graph.**

- `identity/store.py::IdentityStore` — SQLite/Postgres, owns identities, source records,
  evidence edges, events, audit. The customer spine.
- `goldengraph-native` — a separate Rust store (7 JSON-boundary symbols: `build_graph_json`,
  `neighborhood_json`, `communities_json`, `store_as_of_json`, …) with bi-temporal history.
  It *consumes* goldenmatch ER (`goldengraph/resolve.py:106` calls `gm.dedupe_df`) but keys its
  own entities by `record_fingerprint` and does **not** share `IdentityStore` persistence.

So the serving primitive `get_customer_360()` can assemble golden record + provenance + source
records + timeline from the spine today, but the *relationships* row has no home. Three ways to
resolve it, and the choice is architectural, not incidental — it decides which store is the
authoritative owner of customer identity.

## Options

- **(A) Unify — fold identity into GoldenGraph's store, or GoldenGraph's graph into
  `IdentityStore`.** One store, one query. *Rejected.* Merging either direction creates a second
  authoritative owner for a capability the other already owns (identity transactions, or KG
  bi-temporal graph), forces a columnar/graph store to also be the transaction-native identity
  state machine (or vice versa), and is a large rewrite of a subsystem the thesis-conformance
  audit already rates the weakest. Fails decision-test 1 and 3.
- **(B) IdentityStore is the spine; GoldenGraph federates as a relationship overlay.**
  `get_customer_360()` reads identity/provenance/timeline from `IdentityStore` (the authoritative
  owner of *who a customer is*), and requests the relationship neighborhood from GoldenGraph
  *keyed by the stable `entity_id`*, via a thin, versioned projection edge — the same read-back
  seam shape the manifesto's §4(ii) bidirectional handoff already established. Neither store
  duplicates the other's authoritative capability. **Recommended.**
- **(C) GoldenGraph becomes the Customer 360 store.** *Rejected.* It has no
  merge/split/steward/audit control-plane semantics, keys on fingerprint not stable ID, and is
  excluded from the uv workspace with its own pipeline — inverting spine and overlay. Fails
  decision-test 3 (forces identity state into a graph engine).

## Decision

Adopt **(B)**. The **Identity Store is the single authoritative spine** for Customer 360:
stable `entity_id`, golden record, per-field provenance, timeline, merge/split, audit. The
relationship graph is a **federated overlay**: GoldenGraph (or any KG backend) is queried by
`entity_id` through an explicit, versioned `entity_id ↔ graph-node` projection, and stays a
*replaceable relationship backend* — never a second source of truth for customer identity. The
projection edge is specified as a contract (like the manifesto's `ResolutionBatch`), not an
ad-hoc in-memory join, so a different graph backend conforms rather than re-implements.

## Scored against the five architecture decision-tests (0047)

1. **One authoritative semantic owner per capability.** ✅ The whole point. Identity
   (who-is-who, merge/split, provenance, audit) is owned by `IdentityStore`; relationship
   traversal is owned by the graph backend. (B) is the *only* option that keeps both single-owned;
   (A) and (C) each create a second owner for one of them.
2. **Arrow at bulk boundaries, not the universal calling convention.** ✅ Serving is a
   scalar/small-fan-out read (`get_customer_360(entity_id)` → one entity + its block-mates +
   its neighborhood); the projection carries a `list[entity_id]`/JSON node handle, not an Arrow
   batch. Bulk backfill of the projection (all entities → graph nodes) is the one legitimate
   Arrow-boundary path. No Arrow marshaling on the hot single-customer read.
3. **Compute vs. control stay distinct.** ✅ Customer 360 is *entirely* control-plane +
   serving. Matching stays in the stateless compute engine (already true via incremental
   resolve); the store, provenance, events, and the federation seam are control-plane. (B) adds
   no stateful logic to a columnar kernel; (A)/(C) risk exactly that.
4. **Kernelize on measurement.** ✅ (neutral / preserved) Nothing here proposes a new kernel.
   The serving read reuses the landed incremental path; if neighborhood assembly ever becomes a
   measured hotspot, that is a separate, motivated kernelization decision — not smuggled in here.
5. **Conformance defines correctness.** ✅ The `entity_id ↔ graph-node` projection and the
   `get_customer_360()` response are specified with conformance fixtures (the response shape is
   identical across MCP / REST / CLI / Python surfaces; the graph backend is validated against
   the projection contract, not by "it calls GoldenGraph"). A future non-GoldenGraph relationship
   backend proves itself against the contract.

**North Star alignment (the product five commitments):** advances *advanced-never-black-box*
(every 360 field carries source lineage + the timeline is the audit trail) and *shared
capabilities conform* (one 360 response shape across surfaces). Neutral on zero-config /
scale-invariance / approach-the-expert — it exposes existing resolution quality, it does not
change it.

## Consequence

- The serving layer can be built now against `IdentityStore` for everything except
  relationships, and the relationship overlay lands behind the projection contract without
  blocking the rest (staged in the companion doc's milestones).
- GoldenGraph stays a replaceable backend. A customer who wants Neo4j/Neptune for the
  relationship view conforms to the projection contract instead of forking the identity store.
- **Cost, stated honestly:** two stores means a projection to keep in sync — a real operational
  edge (staleness between an identity merge and its reflection in the graph). The companion doc
  homes this on the existing outbound `IdentityEvent` stream (a merge event drives the
  projection update), rather than a synchronous dual-write.
- **Known limit / OPEN:** this ADR fixes *ownership and the seam shape*, not the projection
  schema or the sync latency budget — those are specified in the companion design's milestone D3
  and need the same owner sign-off the manifesto's §4 fork required before code.

---
**Classification:** foundation/architecture • **Status:** proposed • **Last updated:** 2026-07-30
