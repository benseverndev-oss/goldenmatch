# Sail identity Layer-2: incremental resolution (absorb / merge)

Issue: #966 (split out of #859). Stage S5 shipped Layer 1 (create + `same_as`
edges) in 2.0.0; this is the deferred Layer 2.

## The gap

`goldenmatch.sail.build_identity_graph` is **fresh-store create only**. Every
cluster mints a new `ent:h1:` entity from the hash of its member record_ids. On
a second run over overlapping data it re-mints, so a record that already belongs
to an entity gets a *new* one — the store never converges.

One-box `identity.resolve.resolve_clusters` already has the semantics. This
re-expresses them as relational Spark ops, the same way S5 re-expressed Layer 1.

## Semantics to mirror (read out of `resolve.py`, not invented)

For each new cluster, look up which existing entities its records already belong
to (`existing`: record_id -> entity_id):

| overlapping entities | action | entity_id |
|---|---|---|
| 0 | **create** | `entity_id_for_members(sorted record_ids)` |
| 1 | **absorb** | that entity |
| >= 2 | **merge** | the winner (below); losers retired |

**Winner selection is the subtle part.** `resolve.py:1091` does
`counts = Counter(existing.values())` then ranks by `(-count, _node_age(...))`.
`existing` is scoped to *this cluster's* records, so the count is **how many of
this cluster's records point at that entity** — NOT the entity's total size. A
large existing entity contributing one record loses to a small one contributing
three. Getting this backwards would silently change which entity survives across
a whole store, so it is pinned by a dedicated test.

Tie-break is oldest `created_at`. We add `entity_id` ascending as a FINAL
tie-break: one-box's tie among equal `(count, created_at)` resolves by dict
insertion order, which is not a contract, and Spark has no stable sort at all.
Making it explicit is a determinism *improvement*, and it is the only place this
implementation is deliberately more defined than one-box.

Losers are retired with `status="merged_into"`, `merged_into=<winner>`, and
their records are reassigned to the winner (`resolve.py:1123`).

## Shape: additive, contract preserved

`IdentityGraphFrames` is a frozen public contract (`test_sail_identity_contract.py`
pins the field list, `build_identity_graph`'s signature, and the four column
tuples). So Layer 2 arrives as a **new entry point**, not a reshaped dataclass:

```python
build_identity_graph_incremental(
    pairs, assignments, source_df, golden_df,
    *, existing_records, existing_nodes, run_meta, ...
) -> IdentityGraphFrames
```

Same four output frames, same columns. `build_identity_graph` is untouched, so
every existing consumer is byte-unaffected. Passing `existing_records=None`
degrades to exactly the create path (asserted).

## Output semantics

- **records** — every record of every new cluster mapped to its resolved entity,
  PLUS the reassigned records of merge losers. `first_seen_at` is preserved for
  a record already in the store, `recorded_at` for a new one; `last_seen_at` is
  always this run.
- **nodes** — creates (new, `created_at=now`); absorb/merge winners (`created_at`
  preserved from the existing node, `updated_at=now`, status active); losers
  (`status="merged_into"`, `merged_into=winner`, `created_at` preserved).
- **edges** — unchanged in kind, but keyed to the *resolved* entity_id.
- **events** — `CREATED` per created entity, `ABSORBED_RECORD` per newly-added
  record on an absorb, `MERGED_WITH` for the winner and each loser.

## Known divergence, recorded not fixed here

The shipped S5 `build_identity_events` emits `kind="CREATED"` **uppercase**,
while the one-box `EventKind.CREATED` value is lowercase `"created"`. Layer 2
follows the existing Sail casing so the frame stays internally consistent; the
mismatch is pre-existing shipped behaviour on a frozen contract and is out of
scope for this issue. Flagged so it is a decision, not an accident.

## What is NOT covered

- `split` (the inverse of merge) — one-box has `manual_split`; nothing in the
  pipeline path emits it, so there is nothing to mirror.
- Conflict / `possible_same_as` edges — S5 Layer 1 does not emit them either.
- Golden-record recomputation on absorb uses this run's cluster payloads, as
  one-box does; it does not re-read the absorbed entity's prior members.

## Testing

Mirrors the established two-tier pattern in `test_sail_identity_parity.py`:
pure-helper unit tests that run in the normal python lane, plus server tests
behind the `pysail`/`pyspark` `importorskip` that run in the `sail` CI lane.
The three actions each get a dedicated case, plus the winner-selection rule,
the create-path degradation, and idempotency (re-running the same input against
the store it produced is a no-op beyond timestamps).
