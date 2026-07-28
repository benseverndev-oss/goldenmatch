"""14M identity-store write bench for the merged initial-load fast path.

Runs the SHIPPED ``IdentityStore.initial_load_writes`` path (direct COPY into the
real tables, secondary indexes dropped up front + rebuilt after, optional
UNLOGGED) against a co-located Postgres. Frames are generated + loaded in chunks
so client memory stays flat. Reports the wall-clock write time -- the number the
fast path exists to move down from the ~48 min baseline.

Usage:
  python scripts/bench_identity_initial_load.py <pg_url> [n_entities] [chunk] [mode]
    mode -> fast | fast_unlogged   (default fast_unlogged)
"""
from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime

import polars as pl
import psycopg

from goldenmatch.identity.store import IdentityStore

URL = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 14_000_000
CHUNK = int(sys.argv[3]) if len(sys.argv) > 3 else 1_000_000
MODE = sys.argv[4] if len(sys.argv) > 4 else "fast_unlogged"
UNLOGGED = MODE == "fast_unlogged"
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def build(start: int, count: int):
    """`count` 2-member clusters from entity index `start`. Global ids so chunks
    never collide. Payloads are realistic-width JSON strings (utf8, as the
    resolve path hands them to the store)."""
    ids = [f"e{i}" for i in range(start, start + count)]
    nodes = pl.DataFrame({
        "entity_id": ids,
        "status": ["active"] * count,
        "merged_into": [None] * count,
        "golden_record": [json.dumps({"name": f"person {i}", "npi": f"{i:010d}",
                                      "email": f"user{i}@example.org"}) for i in range(start, start + count)],
        "confidence": [0.95] * count,
        "dataset": ["prof"] * count,
        "created_at": [NOW] * count,
        "updated_at": [NOW] * count,
    })
    r0 = 2 * start
    rids = [f"r{i}" for i in range(r0, r0 + 2 * count)]
    records = pl.DataFrame({
        "record_id": rids,
        "source": ["bench"] * (2 * count),
        "source_pk": rids,
        "record_hash": [f"h{i}" for i in range(r0, r0 + 2 * count)],
        "entity_id": [f"e{start + (j // 2)}" for j in range(2 * count)],
        "payload": [json.dumps({"npi": f"{i:010d}", "email": f"user{i}@example.org",
                                "phone": "5551234567", "zip5": "08536"}) for i in range(r0, r0 + 2 * count)],
        "dataset": ["prof"] * (2 * count),
        "first_seen_at": [NOW] * (2 * count),
        "last_seen_at": [NOW] * (2 * count),
    })
    edges = pl.DataFrame({
        "entity_id": ids,
        "record_a_id": [f"r{r0 + 2 * j}" for j in range(count)],
        "record_b_id": [f"r{r0 + 2 * j + 1}" for j in range(count)],
        "kind": ["same_as"] * count,
        "score": [0.95] * count,
        "matchkey_name": ["weighted"] * count,
        "controller_snapshot": [None] * count,
        "run_name": ["prof"] * count,
        "dataset": ["prof"] * count,
        "actor": ["resolver"] * count,
        "trust": [1.0] * count,
        "recorded_at": [NOW] * count,
    })
    events = pl.DataFrame({
        "entity_id": ids,
        "kind": ["created"] * count,
        "payload": [json.dumps({"size": 2}) for _ in range(count)],
        "run_name": ["prof"] * count,
        "dataset": ["prof"] * count,
        "actor": ["resolver"] * count,
        "trust": [1.0] * count,
        "recorded_at": [NOW] * count,
    })
    return nodes, records, edges, events


def main() -> None:
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS public CASCADE")
        c.execute("CREATE SCHEMA public")
    store = IdentityStore(backend="postgres", connection=URL)  # creates schema + indexes

    frame_gen = 0.0
    db_load = 0.0
    print(f"14M initial-load bench: N={N:,} chunk={CHUNK:,} mode={MODE}", flush=True)
    wall = time.perf_counter()
    with store.initial_load_writes(enabled=True, unlogged=UNLOGGED):
        done = 0
        while done < N:
            count = min(CHUNK, N - done)
            t0 = time.perf_counter()
            nodes, records, edges, events = build(done, count)
            frame_gen += time.perf_counter() - t0
            t0 = time.perf_counter()
            store.bulk_upsert_identities(nodes)
            store.bulk_upsert_records(records)
            store.bulk_add_edges(edges)
            store.bulk_emit_events(events)
            db_load += time.perf_counter() - t0
            del nodes, records, edges, events
            done += count
            rate = done / (time.perf_counter() - wall)
            print(f"  ..{done:>11,}/{N:,}  ({rate:,.0f} ent/s)", flush=True)
    total = time.perf_counter() - wall  # drop + all COPYs + index rebuild + frame gen
    rebuild = total - frame_gen - db_load  # ~= drop_indexes + rebuild_indexes + analyze

    n_nodes = store.count_identities(dataset="prof")
    n_records = store.count_records() if hasattr(store, "count_records") else -1
    store.close()

    print(f"\n=== 14M FAST PATH  mode={MODE}  (committed {n_nodes:,} nodes, {n_records:,} records) ===")
    print(f"  frame_gen (client build) {frame_gen:8.1f}s")
    print(f"  db_load   (direct COPY)  {db_load:8.1f}s")
    print(f"  idx_drop+rebuild+analyze {rebuild:8.1f}s")
    print(f"  TOTAL WRITE              {total:8.1f}s   = {total/60:.1f} min   -> {N/total:,.0f} ent/s")
    print(f"  baseline (old path) was ~48 min; speedup ~= {48*60/total:.1f}x")


if __name__ == "__main__":
    main()
