# Customer 360 data connection — design

Date: 2026-07-30
Status: **PROPOSED** — companion to the Identity Control Plane manifesto
(`identity-control-plane-manifesto.md`) and the governing frame `one-product-two-engines.md`
(decision `0047`). The one gating architectural fork — which store owns the Customer 360 view —
is decided in `context-network/decisions/0049-customer-360-identity-store-spine.md`
(IdentityStore is the spine; GoldenGraph federates). Scoped to the **control-plane** engine
(Tier 5) plus its **serving** and **data-connection** surfaces.

## Why this doc

The control-plane manifesto specifies the *engine*: durable identities, the versioned
`ResolutionBatch` seam, and — now landed (C2) — real incremental resolution against a persisted
block index. This doc specifies the **product built on that engine**: a *Customer 360* — one
persistent, always-fresh, per-field-attributed unified view of each customer, served by one
call and kept current by connected source systems.

The distinction matters because the engine is ~80% of a Customer 360 already, and naming the
missing 20% precisely keeps us from rebuilding what exists. What is missing is not resolution —
it is **connection, serving, and orchestration**: a durable notion of a *source*, a single
*360 read*, and a loop that keeps N connected systems flowing into the spine. This design
centers the three areas the product owner prioritized: **persistent 360 store**, **serving /
query layer**, **incremental / real-time**.

## What exists today (grounded)

- **Spine (strong).** `identity/store.py::IdentityStore` (SQLite default / Postgres) with stable
  UUIDv7 `entity_id` (`store.py:269`), `source_records`, `evidence_edges`, append-only
  `identity_events`, `audit_seals` hash chain (`identity/audit.py`). Bulk COPY paths + pooled
  Postgres (`identity/pool.py`).
- **Golden + provenance (strong).** `identity/survivorship.py::build_golden_with_provenance`
  emits per-cell `CellProvenance` (source / row / timestamp / strategy / confidence);
  `learn_field_survivorship` learns field-level winners. `core/golden.py` is the survivorship
  engine.
- **Incremental (landed).** `identity/block_index.py` +
  `resolve.py::resolve_record_incremental::_resolve_via_index` — compute a new record's block
  keys statelessly → query the persisted index → gather only block-mates → resolve → commit.
  Bounded work in corpus size (proof: `test_incremental_scale.py`). Exact-matchkey gap closed
  (`_exact_match_rows`).
- **Serving pieces (scattered).** `identity/profile.py::entity_profile`,
  `identity_summary_stats`, `steward_worklist`; REST `/api/v1/identities/...`; ~10 identity MCP
  tools; web Identities tab. These exist but are *not composed into one 360 read*.
- **Connectors (dormant).** `connectors/base.py` has 14 built-ins (Snowflake, BigQuery,
  Databricks, Postgres, Salesforce, HubSpot, object storage, …) — but `load_connector().read()`
  is **never consumed by the pipeline** (`core/pipeline.py:966` notes the branch was removed).
  The only wired live-source paths are the thin `core/api_connector.py` and the single-table
  `db/sync.py` / `db/watch.py` loop.

**Reading:** the write/resolve half is product-grade; the *connection* half is a broad but
disconnected surface, and the *serving* half is un-composed. This design wires those two.

## The three pillars

### Pillar 1 — Persistent 360 store: promote "source" to a first-class object

Today a source is an ad-hoc `(path, name)` tuple; there is no `Source` in `config/schemas.py`.
The load-bearing new primitive is a **durable source registry** — the "data connection" made a
persisted entity, and the natural join key for provenance, freshness, and trust.

```
Source (new, control-plane owned)
├── source_id            # stable
├── connector            # one of connectors/base.py's 14 (finally wired to read())
├── schema_map           # source columns → canonical entity schema (infermap can propose)
├── trust_tier           # drives field-level survivorship (below)
├── watermark            # last-synced cursor, per source (drives incremental sync)
└── freshness_sla        # staleness budget; surfaced on the 360 view + steward worklist
```

Two capabilities hang off it:

- **Field-level source-of-truth policy.** Wire `trust_tier` into `source_priority` /
  `learn_field_survivorship` so "billing wins for address, CRM wins for name" is declarative
  policy per field, not a global strategy. This is the MDM feature buyers ask for, and the
  provenance substrate (`CellProvenance`) already records which source won each cell.
- **Point-in-time / bi-temporal profiles.** The append-only `identity_events` log + audit chain
  already record every transition; GoldenGraph is already bi-temporal. Expose "as-of": *what did
  we believe about customer X on date D, and which source changed it* — compliance/GDPR-grade,
  built on existing state, no new write path.

This pillar also finally **wires the dormant connectors**: `Source.connector` →
`load_connector().read()` → the resolve/identity path. That is the literal "data connection."

### Pillar 2 — Serving / query layer: one 360 read, and identity-as-a-lookup

- **`get_customer_360(entity_id)`** — one call returning the whole picture, composed from
  existing readers:

  | Component | Owner today |
  |---|---|
  | Golden record | `core/golden.py` rollup / `survivorship.py` |
  | Per-field provenance ("why this value") | `survivorship.py::CellProvenance` |
  | All linked source records | `IdentityStore` source records |
  | **Relationships** (household / company / account) | **federated overlay — see decision 0049** |
  | Event timeline | `identity_events` |
  | Confidence / completeness | `identity_summary_stats` |

  The response shape is specified once and identical across MCP / REST / CLI / Python (decision
  0049 test 5). The relationships row is the only piece not sourced from the spine — it is read
  from the federated graph backend keyed by `entity_id` (decision 0049).

- **Identity resolution *as* a lookup API (the sleeper wedge).** `match_record_to_entity`
  (`resolve.py`) already answers "given an inbound email+name, which existing entity is this?"
  Expose it as a low-latency serving endpoint and Customer 360 gains **real-time identity
  resolution at the point of interaction** (login, checkout, support ticket) — the one surface
  that reaches beyond data teams to app developers. Reuses the landed incremental block-index
  path, so it is bounded-work by construction.

- **Reverse-ETL / activation (closes the loop into a CDP).** `BaseConnector.write()` is a stub
  (`connectors/base.py:63`); only `db/*` reconcile writes back. Push the resolved golden field
  back to source systems ("sync the winning email into Salesforce"). Deferred past the read path,
  but the source registry (Pillar 1) is its prerequisite.

### Pillar 3 — Incremental / real-time: orchestrate many sources, emit change out

The engine resolves a single new record incrementally today; the product needs the *plural*
and the *outbound* half.

- **Multi-source sync orchestrator.** `db/watch.py` watches exactly one table. The missing
  piece is a scheduler that pulls each registered `Source` on its own cadence (using its
  `watermark`), streams deltas into `resolve_record_incremental`, and advances the watermark
  transactionally. This is the concrete "keep the 360 fresh" engine — it is orchestration over
  the landed incremental path, not new resolution logic.
- **The event log *is* outbound CDC.** Every absorb / merge / split already emits an
  `IdentityEvent`. Expose that append-only log as a subscribable **webhook / stream**: *"entities
  123 and 456 are now the same person"* becomes an event downstream consumers act on (cache
  invalidation, reverse-ETL, the decision-0049 graph-projection update). High value, low build —
  it surfaces an existing substrate rather than adding one.
- **Incremental survivorship recompute.** On a source change, recompute only the golden fields
  whose contributing source changed, not the whole cluster — `CellProvenance` already records the
  per-cell contributor, so the dependency set is known.

## Milestones

- **D1 — Serving primitive (highest leverage, no new subsystem).** `get_customer_360()` composing
  the existing spine readers (everything except relationships), one response shape across
  surfaces. Demoable "unified customer with full provenance" on day one.
- **D2 — Source registry.** The `Source` object + `sources` table; wire the dormant
  `connectors/` `read()` into the resolve/identity path; trust-tier → field survivorship. Turns
  the ad-hoc `(path, name)` tuple into a durable connection.
- **D3 — Relationship overlay (decision 0049).** The versioned `entity_id ↔ graph-node`
  projection + neighborhood read; fills the one missing row of the D1 response. Needs owner
  sign-off on the projection schema + sync-latency budget.
- **D4 — Outbound event stream.** Expose `identity_events` as a webhook/stream; use it to drive
  the D3 projection update (event-driven, not synchronous dual-write).
- **D5 — Multi-source orchestrator.** Per-source watermark scheduler over
  `resolve_record_incremental`; freshness-SLA staleness surfaced on the 360 view + steward
  worklist.
- **D6 — Activation (deferred).** Reverse-ETL `write()` back to source systems.

## Decision-test alignment

Full architecture-tenet scoring is in decision 0049 (it is the architectural crux). Product
North-Star reading: this design most advances **advanced-never-black-box** (every 360 field
carries source lineage; the timeline is its audit trail) and **shared-capabilities-conform**
(one 360 response shape across MCP / REST / CLI / Python). It is deliberately neutral on
zero-config, scale-invariance, and approach-the-expert — it *exposes* the engine's existing
resolution quality, it does not alter it. The compute↔control split is preserved by
construction: this is all control-plane and serving; matching stays stateless in the compute
engine.

## What this is not

Not a new resolution engine and not a rewrite. D1/D3/D4 compose or expose existing state; only
D2 (source registry + connector wiring) and D5 (orchestrator) add real subsystems, and both are
orchestration over the landed incremental path rather than new matching logic. Storage stays
SQLite-default / Postgres-optional; no Arrow enters the transaction or serving semantics. The
relationship graph stays a replaceable federated backend, never a second identity store
(decision 0049).

---
**Classification:** foundation/architecture • **Status:** proposed • **Last updated:** 2026-07-30
