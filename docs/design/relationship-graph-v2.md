# Relationship graph v2: configurable edge fields + authoritative reconciliation

## Why

The semantic relationship layer (#2210/#2211) turns the identity graph into an
entity-to-entity graph by emitting an edge when two DISTINCT entities share a
non-identity attribute (two prescribers on one clinic phone = `shares_phone`).
Validated at 14M on real MJH data (623,765 edges). Two gaps block it from being a
production, "updated every run" differentiator:

1. **Edge fields are hand-listed and raw only.** You pass `RelationshipRule(field=
   "phone_number", ...)`. There is no way to (a) edge on a DERIVED value (the
   *domain* of an email, not the full email; a normalized company name; a degree),
   or (b) have the engine *suggest* which fields are good to edge on.

2. **The write is insert-only, so it drifts.** `build_relationships` calls
   `add_relationships`, an `ON CONFLICT DO NOTHING` upsert. It never deletes. On a
   warm re-run: stale edges (a phone that is no longer shared, a value that changed)
   live forever; a merge/split that changes entity ids leaves dangling edges; there
   is no inserted/deleted accounting and no defined partial-failure behavior. The
   graph is only trustworthy on the FIRST run.

## Goals

- Edge on **any field** in the data, including **derived** values (transforms), with
  the engine able to **auto-suggest** good edge fields; users can accept/override.
- Each run leaves `identity_relationships` **equal to the current desired state**
  for the (dataset, kind) it manages: new edges inserted, stale edges deleted,
  merges/splits reflected, with real **inserted/deleted counts** and **all-or-nothing**
  per-(dataset,kind) semantics.
- Zero behavior change for callers that do not opt in; existing tests stay green.

## Part A — Configurable / derived / auto-detected edge fields

### A1. Derived fields on `RelationshipRule`

Add an optional transform so the edge key is a computed value, not the raw payload:

```python
class RelationshipRule(BaseModel):
    field: str                       # payload field to read
    kind: str                        # edge label
    transform: str | None = None     # None = raw; else a named derivation applied to the value
    min_entities: int = 2
    max_fanout: int = 50
    # v2: rarity-aware hub handling (see A3)
    max_fanout_mode: Literal["skip", "tf"] = "skip"
```

`transform` values (start with the ones MJH data needs, reuse GoldenFlow where a
kernel exists so it is byte-parity with the matcher):

| transform | from -> key | example |
|---|---|---|
| `email_domain` | `a@acme.com` -> `acme.com` | two people at one company domain |
| `normalize_company` | `Acme, Inc.` / `ACME INC` -> `acme` | employer edges robust to formatting |
| `lower_trim` | generic casefold+trim | specialty, degree |
| `phone_e164` | reuse GoldenFlow | normalized phone |
| `zip3` | `08536` -> `085` | coarse geo |

Implementation: `relationship_groups(field, transform, ...)` applies the transform
in SQL where cheap (`split_part(email,'@',2)`, `lower(btrim(...))`) or falls back to
a GoldenFlow kernel. The covering expression index (`_ensure_relationship_index`)
is built on the SAME transform expression so the group-by still uses an index.

### A2. Multiple rules already compose

`config.identity.relationships` is a list, so N fields = N rules today. v2 keeps
that; a rule can now be raw or derived. e.g.
```python
relationships=[
  RelationshipRule(field="phone_number", kind="shares_phone"),
  RelationshipRule(field="email",   transform="email_domain",     kind="same_org"),
  RelationshipRule(field="employer",transform="normalize_company", kind="same_employer"),
  RelationshipRule(field="specialty", transform="lower_trim",       kind="same_specialty"),
]
```

### A3. Auto-detection: `suggest_relationship_rules`

A profiler that scans the resolved store (or the input frame) and proposes rules
for fields that behave *relationally* — reuse the GoldenCheck / autoconfig profiler
that already classifies columns. A field is a good edge candidate when it is:

- **populated enough** (fill rate above a floor, e.g. >= 20%) — else the edge set is
  tiny and noisy;
- **shared but not degenerate** — a healthy share of values held by 2..K entities
  (relational), NOT dominated by a single mega-value (a switchboard) NOR unique per
  entity (that is an identity key, handled by matching, not relationships);
- **not an identity claim** — the autoconfig controller already flags npi/email/phone
  as shared GROUP attributes vs identity claims; edge on the group attributes.

Output: `list[RelationshipRule]` with a suggested `kind`, `transform` (e.g. it will
propose `email_domain` for an email column, `normalize_company` for employer), and a
`max_fanout` scaled to the value distribution. Surfaced via CLI/MCP (`goldenmatch
suggest-relationships DATA`) so a user reviews and edits before committing.

### A4. Rarity-aware hubs (`max_fanout_mode="tf"`)

`max_fanout` (skip a value held by > N entities) is blunt: it drops a large-practice
phone entirely and keeps 2..N clinic-line noise. Optional TF mode weights an edge by
value rarity (the same idea that fixed the matcher's shared-clinic-phone precision):
rare value shared by 2 entities = strong edge; common value = weak/dropped. Stored on
the edge as a `weight` column so downstream (GoldenGraph) can threshold.

## Part B — Authoritative reconciliation

### B1. Desired vs existing

`build_relationships` becomes, per (dataset, kind):

```
desired  = set of (a, b, kind, field, shared_value) the current data + rule imply
existing = SELECT ... FROM identity_relationships WHERE dataset=? AND kind=?
to_insert = desired - existing
to_delete = existing - desired
```

Edge identity is the full tuple `(a, b, kind, shared_value)` (the current PK), so a
link whose shared value CHANGED (phone X removed, phone Y added) is a delete + an
insert, not a silent stale row.

### B2. Merge / split fall out of "recompute from current ids"

`desired` is computed from the CURRENT `identity_nodes` (post merge/split), so:

- **merge** (E1,E2 -> E1): edges to/from E2 are not in `desired` (E2 is gone) ->
  deleted; edges the merged entity should have are recomputed under E1 -> inserted.
- **split** (E1 -> E1,E3): the new entity E3's edges appear in `desired` -> inserted.
- **dangling**: a hard guard also deletes any existing edge whose endpoint is not an
  active `identity_nodes` row (belt-and-suspenders for ids retired outside a rule
  recompute).

### B3. The write: `reconcile_relationships`

New store method, replacing the insert-only path for opt-in rules:

```python
def reconcile_relationships(
    self, dataset: str, kind: str, desired: list[tuple],
) -> RelationshipStats:   # (inserted, deleted, unchanged)
```

- Runs the diff in ONE transaction (Postgres) / one batched transaction (SQLite);
  either the whole (dataset, kind) reconcile commits or it rolls back -> **explicit
  partial-failure behavior** (no half-updated graph).
- Deletes are scoped to `dataset=? AND kind=?` so a rule never touches another rule's
  or another dataset's edges.
- Returns `RelationshipStats(inserted, deleted, unchanged)`; `build_relationships`
  aggregates across rules.

### B4. Summary + observability

`ResolveSummary` gains `relationships_deleted` (keeps `relationships_added`), so a run
reports e.g. `+4,102 / -318` instead of just "attempted". Emit a per-kind identity
event (`RELATIONSHIPS_RECONCILED`, payload = counts) for the audit spine.

### B5. Idempotency (the #2197 lesson)

Same data twice -> `desired == existing` -> 0 inserted, 0 deleted, all unchanged. No
churn, no doubling. (This is the relationship analogue of the evidence_edges
double-write fix; here it is structural, not just `ON CONFLICT DO NOTHING`.)

## Schema

Additive: `identity_relationships` already has `(entity_a_id, entity_b_id, kind,
field, shared_value, dataset)` + PK `(a,b,kind,shared_value)`. Add nullable `weight
REAL` (A4). Migration `v7 -> v8`, `ADD COLUMN IF NOT EXISTS`, sqlite + pg.

## Config surface

`IdentityConfig.relationships: list[RelationshipRule]` unchanged in shape; rules gain
`transform`, `max_fanout_mode`. New `RelationshipStats` dataclass. New public fns
`suggest_relationship_rules`, store `reconcile_relationships`. Doc-regen: agent_codemap
(module fns), config-matrix (RelationshipRule fields), TS parity + emptySummary for
the new `relationships_deleted` summary field, MDX-safe descriptions (no `<->`).

## Build order

1. **Reconcile core** (B1-B4): `reconcile_relationships` + `build_relationships`
   desired-vs-existing + `relationships_deleted`. Correctness-critical, biggest win.
2. **Derived fields** (A1): `transform` on the rule + `relationship_groups` transform
   + matching expression index. The user-facing "edge on anything" ask.
3. **Auto-detect** (A3) + **TF hubs** (A4): quality layer.
4. Tests at each step (reconcile delete/merge/split/idempotency; transform parity;
   suggest on a fixture); the existing `test_relationships.py` stays green.
