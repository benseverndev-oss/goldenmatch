# 0064 — The identity control plane stays on Postgres/SQLite; the cost is not the store

**Status:** accepted (2026-09-07, Ben) • **Measured:** `scripts/bench_identity_control_plane.py`, [run 34154603961](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34154603961) • **Shipped from it:** #2893 (fix in #2895) • **Frame:** [../architecture/one-product-two-engines.md](../architecture/one-product-two-engines.md), decision [0047](0047-one-product-two-engines-architecture.md)

## Context
Standing question, raised repeatedly: is Postgres the right long-horizon store
for the identity control plane? The framing was speed and memory — "Postgres is
heavy and can't do zero-copy" — with a proposed alternative of building a
custom relational database inside the Arrow-native Rust framework, tailored to
ER access patterns (merge/split, provenance, append-only event log).

It had never been measured. Every scale win in the repo's recent history came
from somewhere else (jemalloc page-decay −33% peak RSS, the bucket scorer, the
FS stage profile, blocking selection) — none from storage. So the question was
turned into a measurement before it was turned into architecture.

## Decision
**Keep Postgres/SQLite. Do not build a storage engine.** The measurement does
not support it, on either axis the proposal was aimed at.

At 5M rows, cold load, co-located `services: postgres:16` (network latency
removed on purpose — the honest way to isolate engine cost):

| | postgres | sqlite | gap |
|---|---|---|---|
| wall | 528.5 s | 452.1 s | +17% |
| store wall | 324.7 s | 230.5 s | +41% |
| **non-store wall** | **203.9 s** | **221.6 s** | **~0%** |
| **peak RSS** | **12,568 MB** | **12,556 MB** | **0.1%** |

1. **Roughly 210 s of wall is backend-independent** — Python-side batch prep in
   `apply_batch` (row dicts, record-id derivation, payload hashing) that runs
   before the store is touched. SQLite's figure is marginally *higher* than
   Postgres's, which is the clearest available evidence it has nothing to do
   with the storage engine.
2. **Peak RSS differs by 0.1% across two completely different engines.** Memory
   is entirely Python-side row materialization. A new store would not move it
   at all.
3. Therefore an *infinitely fast* store buys ~40% of wall and **zero** memory.
   A custom RDBMS is a multi-year project aimed at the smaller half of one axis
   and none of the other, and it would make the store the product — which
   contradicts 0047 (backends are replaceable; none is synonymous with
   GoldenMatch) and the North Star (renting storage is what lets us say *point
   it at the Postgres you already run*).
4. **Postgres's real price is 17%** against an embedded in-process database
   with the network already removed. That is the honest number, and it is not
   what makes this slow.

## Consequence
- The Arrow/zero-copy instinct was right about the *technique* and wrong about
  the *target*. The zero-copy opportunity is `apply_batch`'s materialization of
  5M rows into Python dicts — Arrow-shaped work **inside** the control plane,
  not underneath it. That is the sanctioned follow-on, in this order:
  1. `bulk_upsert_records` — 200 s (PG) / 110 s (SQLite), the largest single
     item on both, though already COPY/staged and possibly near its floor.
  2. The ~210 s prep path and the 12.6 GB, both untouched and with obvious
     headroom. Riskier than it looks: `apply_batch` is load-bearing correctness
     code with many branches. **Partly done — see the addendum below.**
  3. Residual `lookup_entity_ids` gap: 10.4 s (PG) vs 1.9 s (SQLite) post-#2893.
- **One real defect came out of the profile.** #2893: `lookup_entity_ids`
  chunked its IN-list at 900 — `SQLITE_MAX_VARIABLE_NUMBER` (#670) — for *every*
  SQL backend, turning one bulk pre-flight into ~5,556 Postgres round trips.
  50.82 s → 10.40 s after switching Postgres to a single `= ANY(array)`
  parameter. It also exposed that `python_goldenmatch_postgres` had no
  `identity/**` path filter, so the identity store's Postgres backend had never
  had PR-time Postgres coverage.
- **The incremental path is not the disaster it looked like.** It fires a
  consistent 1.2 store calls per member row at every scale (`upsert_record` per
  member, `upsert_identity` per cluster), but costs 125 µs/row against cold's
  140 µs/row. Worth batching eventually; not the reason to change stores.

## The measurement was harder to trust than the architecture
Three separate harness bugs, each of which produced a *plausible, explainable,
wrong* number rather than an obvious failure:

1. The QIS generator emits no `__row_id__`, and `apply_batch` skips every row
   lacking one (`resolve.py:654`) — the first run resolved nothing and reported
   0 wall / 0 writes, which reads exactly like "the control plane is free".
2. Postgres reused one shared database across rungs while SQLite got a fresh
   temp file. Because the QIS generator is prefix-stable, the 5M rung's first
   1,000,000 rows were already resident from the 1M rung — exactly 200,000
   clusters at `ROWS_PER_CLUSTER=5`, which correctly fell off the bulk fast
   path into exactly 1,000,000 per-row writes. Filed as a Postgres engine bug
   (#2894) and withdrawn once the arithmetic matched too well to be a
   coincidence. It also inflated the headline gap from 17% to 25%.
3. A sibling bench (#2633's) exited 0 with 2 of 4 rungs unmeasured.

**Every quantitative claim in this decision post-dates all three fixes.** The
generalisable lesson, and the reason it is recorded here rather than in a PR
description: a profiling harness that measures nothing must *fail*, not report
zeros — and a result that confirms your prior is the one to re-derive from the
other direction before filing it. Each guard now asserts the thing it assumed:
the zero-work check, and the cold-phase check that `created` equals the clusters
processed (verified to fire, not assumed to).

## Addendum, 2026-09-08: follow-on item 2, first results

The 12.6 GB figure above is superseded. Item 2 was attacked in four slices;
the peak RSS at 5M cold is now **7,791.1 MB (sqlite) / 7,800.3 MB (postgres)**
— **−38%** — measured against the same rungs, backends and
`pair_scores=spanning` as the original
([run 34256747975](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34256747975)
for #2903,
[run 34266817099](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34266817099)
[run 34270540909](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34270540909)
and [run 34274766982](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34274766982)
for #2904). Wall is unchanged throughout: every slice is inside the ~19%
run-to-run variance the store wall already carries, which is the point — this
is a memory result, not a speed one.

| slice | what | peak RSS at 5M |
|---|---|---|
| — | baseline | 12,554.8 MB |
| #2900 | payload + hash computed once, not twice | (wall only, −42% on `identity_prep_record_ids`) |
| #2902 | `_rowid_candidates` stored sparsely | −0.08% — **a negative result** |
| #2903 | streamed prep, row dicts bounded at O(chunk) | **8,996–9,010 MB (−28%)** |
| #2904 | post-prep maps derived, not stored | **8,390–8,405 MB (−33% cumulative)** |
| #2904 | one rowid index + dense lists, not four dicts | **8,197–8,200 MB (−35% cumulative)** |
| #2904 | sha256 digest retained, not its 64-char hex | **7,791–7,800 MB (−38% cumulative)** |

Three things worth carrying forward.

**The instruments disagreed with the outcome twice, in opposite directions.**
#2902 was sized from tracemalloc at ~0.9 GB and delivered 0.08%: tracemalloc
counts requested Python allocation, not resident pages, and the difference is
allocator behaviour, not arithmetic. #2903 was shipped with *no* local memory
evidence at all — the only A/B available compared new-1-chunk against
new-2-chunks, both of which release row dicts, so it measured the wrong
contrast — and it delivered the full predicted 3.5 GB. The usable rule is that
peak RSS is only knowable from `ru_maxrss` on the real rung; a local proxy is
worth running for a signal but never for a number.

**Stage boundaries decide what you can see.** The `+935 MB` that appeared to
belong to `identity_write_loop` was not in the write loop: it was an unstaged
pass between two markers that built `rowid_to_recid` and `rowid_to_pk`, two
whole-frame dicts duplicating values already in hand. It was invisible while
the prep peak (12,557 MB) sat above it and only became attributable once #2903
lowered the peak beneath it. A stage marker that does not bracket a loop hides
that loop inside its neighbour.

**A stage gap is an upper bound on what lives in it.** #2904 was sized at
−935 MB — the entire unstaged gap between the prep peak and the write-loop
peak — and delivered **−592 MB (postgres) / −619 MB (sqlite)**, about 65%. The
prediction assumed the whole gap was the two maps; measured, ~620 MB was, and
the remaining ~340 MB is the write loop's own accumulators, which were simply
never visible on their own. An unstaged region attributes to whatever you
guess is in it until you remove one candidate and re-measure. It also is not
free: deriving `source_pk` on read costs a slice per member access, and the
write loop's non-store wall rose a few seconds on both backends, consistently
and as expected. ~600 MB for that is the right trade at this scale; it is
still a trade.

**The container theory is mostly wrong, and that is the useful result.** The
ordinal collapse was sized at ~600 MB on the reasoning that four dicts over an
identical 5M-key set pay for the hash table four times. It returned **−204 MB
(postgres) / −193 MB (sqlite)**, about a third. Netting off what it ADDED — 5M
ordinal `int` objects (~140 MB) and three list slots per row (~120 MB) — the
gross saving is ~470 MB across three removed dicts, i.e. **~31 B/row/dict**,
not the ~100 B assumed. A dict whose keys are already shared with another dict
is far cheaper than an isolated one.

So the ~5.3 GB of derived structures is overwhelmingly **content, not
container**, and the full columnar reshape can only pay if it changes how the
VALUES are represented: the payload dict per row (~324 B/row, ~1.6 GB), the
64-char hash string (~157 B/row, ~785 MB → ~320 MB as a fixed-width buffer),
the primary id (~118 B/row). The honest ceiling on that work is perhaps 2–2.5
GB of the 5.3, and most of it requires replacing the payload dict — the one
change that touches `_golden_record_from_payloads` and both write paths.

This is what the slice was for. It cost one small, low-risk change and it
converted the reshape from a guess into a bounded estimate; had it returned
600 MB the container argument would have carried the larger rewrite on its own.
It also came free on wall — prep fell ~3–5 s, one dict write per row instead
of four.

**Representation change is the one that pays, and it is predictable.** The
digest slice was sized at ~445 MB — a 64-char hex `str` is ~113 B of Python
object against the 32 B of digest it encodes — and returned **−400 MB
(postgres) / −406 MB (sqlite)**, ~91% of the estimate. That is the first
prediction in this sequence to land, and the reason is that it was about
REPRESENTATION of a known-size value rather than about allocator or container
behaviour, which is where the other three estimates went wrong in both
directions.

It is not free either: the hex is regenerated on read, and non-store wall rose
~8 s on both backends. The same trade as the post-prep views, at the same kind
of rate.

**Residual, and where the next slice has to go.** Of the 7.8 GB: ~2.1 GB before
prep, ~0.45 GB the filtered frame, **~4.9 GB the derived structures**, ~0.35 GB
the write loop. What remains is dominated by `rowid_to_payload`, and by more
than its own dicts: each payload holds the row's field VALUES as Python
objects, materialized out of Arrow by `select_dicts`. So the ~1.6 GB of dict
containers is only part of it — the retained strings behind them are the rest,
and no representation trick reaches them. Not storing dicts means not
retaining the values at all, i.e. re-reading from Arrow at access time. The
write loop touches members in CLUSTER order, not row order, so that is a
random single-row Arrow access per member and a plausible wall regression;
whether it is affordable is a measurement nobody has taken. That measurement,
not the rewrite, is the correct next step. The 5.5 GB is five per-row Python maps over an identical
5M-key set — payload 324 B/row, hash 157, primary 118, source 52 — where the
container overhead is paid five times and the values are stored as individual
Python objects. Streaming cannot touch it, because those values outlive the
chunk. The columnar reshape (one rowid→ordinal index plus dense arrays, and a
payload representation that is not a dict per row) is the remaining item, and
it is a materially larger change than any of the four above.
