"""Spike: can a *streaming* DuckDB ingest bound peak RSS below the stdlib
staging path for the Identity Store bulk write?

Decides docs/design/2026-07-25-duckdb-identity-backend.md.

The ADBC spike (2026-07-24) already settled that an *in-memory* Arrow-native
write does not move peak RSS: the materialised source frame is the floor, so
adbc sat within 6% of staging at 5M. DuckDB, handed the same in-memory Arrow
table, has no reason to do better -- and `duckdb_inmem` below is the arm that
confirms that floor. The one lever left is to never materialise the frame:
stream the source to Parquet in bounded batches, then let DuckDB scan + spill.
`duckdb_stream` is that arm, and it is the real test.

Arms (each in its own subprocess, so peak RSS is that arm's alone):

  staging        stdlib SQLite staging table + executemany + upsert. The
                 baseline the streaming arm must beat by >=30% RSS at 5M.
  duckdb_inmem   register the SAME in-memory pyarrow.Table as a DuckDB view,
                 INSERT ... SELECT ... ON CONFLICT DO UPDATE into a DuckDB file.
                 Expected ~= staging RSS (confirms the frame is the floor).
  duckdb_stream  generate the source to Parquet in bounded batches WITHOUT ever
                 holding the whole table, then INSERT ... SELECT ... FROM
                 read_parquet(...) ON CONFLICT into a memory-limited DuckDB file
                 so it scans + spills. The decision arm.

Correctness gate: every arm's resulting store is content-hashed over
identity_nodes + source_records (order-independent), so a faster/leaner arm that
writes different bytes fails loudly. The DuckDB schema mirrors the SQLite one
(VARCHAR / DOUBLE, ISO-string timestamps) so the read-back values -- hence the
hash -- are identical across the SQLite and DuckDB arms.

Run on large-new-64GB via the spike-duckdb-identity-ingest workflow, NOT locally.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

ARMS = ("staging", "duckdb_inmem", "duckdb_stream")

_STAGE_NODES = "_stage_identity_nodes"
_STAGE_RECORDS = "_stage_source_records"

_NODE_COLS = [
    "entity_id", "status", "merged_into", "golden_record", "confidence",
    "dataset", "created_at", "updated_at",
]
_RECORD_COLS = [
    "record_id", "source", "source_pk", "record_hash", "entity_id", "payload",
    "dataset", "first_seen_at", "last_seen_at",
]

_NOW = datetime(2026, 7, 25, 12, 0, 0).isoformat()
# ~2.14 records per identity, matching the reported cluster shape.
_RECORD_FACTOR = 2.14
_STREAM_BATCH = 250_000

# stdlib SQLite upsert (mirrors IdentityStore.upsert_identity/record exactly).
# The `WHERE true` is load-bearing on SQLite: an UPSERT whose INSERT draws from a
# SELECT confuses the parser (`near "DO"`) without it. DuckDB needs no such thing.
_UPSERT_NODES_SQLITE = f"""
INSERT INTO identity_nodes
    (entity_id, status, merged_into, golden_record, confidence, dataset,
     created_at, updated_at)
SELECT {', '.join(_NODE_COLS)} FROM {_STAGE_NODES} WHERE true
ON CONFLICT(entity_id) DO UPDATE SET
    status=excluded.status, merged_into=excluded.merged_into,
    golden_record=excluded.golden_record, confidence=excluded.confidence,
    dataset=excluded.dataset, updated_at=excluded.updated_at
"""
_UPSERT_RECORDS_SQLITE = f"""
INSERT INTO source_records
    (record_id, source, source_pk, record_hash, entity_id, payload,
     dataset, first_seen_at, last_seen_at)
SELECT {', '.join(_RECORD_COLS)} FROM {_STAGE_RECORDS} WHERE true
ON CONFLICT(record_id) DO UPDATE SET
    record_hash=excluded.record_hash, entity_id=excluded.entity_id,
    payload=excluded.payload, last_seen_at=excluded.last_seen_at
"""

# DuckDB schema mirrors the SQLite identity schema, but with a PRIMARY KEY (DuckDB
# ON CONFLICT needs a PK/UNIQUE target) and VARCHAR timestamps so the stored bytes
# match the SQLite arms' ISO strings.
_DUCKDB_DDL = """
CREATE TABLE identity_nodes (
    entity_id     VARCHAR PRIMARY KEY,
    status        VARCHAR,
    merged_into   VARCHAR,
    golden_record VARCHAR,
    confidence    DOUBLE,
    dataset       VARCHAR,
    created_at    VARCHAR,
    updated_at    VARCHAR
);
CREATE TABLE source_records (
    record_id     VARCHAR PRIMARY KEY,
    source        VARCHAR,
    source_pk     VARCHAR,
    record_hash   VARCHAR,
    entity_id     VARCHAR,
    payload       VARCHAR,
    dataset       VARCHAR,
    first_seen_at VARCHAR,
    last_seen_at  VARCHAR
);
"""
_UPSERT_NODES_DUCKDB = """
INSERT INTO identity_nodes BY NAME SELECT * FROM {src}
ON CONFLICT (entity_id) DO UPDATE SET
    status=excluded.status, merged_into=excluded.merged_into,
    golden_record=excluded.golden_record, confidence=excluded.confidence,
    dataset=excluded.dataset, updated_at=excluded.updated_at
"""
_UPSERT_RECORDS_DUCKDB = """
INSERT INTO source_records BY NAME SELECT * FROM {src}
ON CONFLICT (record_id) DO UPDATE SET
    record_hash=excluded.record_hash, entity_id=excluded.entity_id,
    payload=excluded.payload, last_seen_at=excluded.last_seen_at
"""


# --- row-value formulas (shared by the full-table builder and the batched
# Parquet writer, so the content hash matches across arms) --------------------
def _golden(i: int) -> str:
    return json.dumps({f"col{j}": f"value-{i}-{j}" for j in range(14)})


def _node_batch(lo: int, hi: int):
    import pyarrow as pa
    idx = range(lo, hi)
    return pa.table({
        "entity_id": [f"e{i:012d}" for i in idx],
        "status": ["active"] * (hi - lo),
        "merged_into": pa.nulls(hi - lo, pa.string()),
        "golden_record": [_golden(i) for i in idx],
        "confidence": [0.85] * (hi - lo),
        "dataset": ["spike"] * (hi - lo),
        "created_at": [_NOW] * (hi - lo),
        "updated_at": [_NOW] * (hi - lo),
    })


def _record_batch(lo: int, hi: int, n: int):
    import pyarrow as pa
    idx = range(lo, hi)
    return pa.table({
        "record_id": [f"providers:{i:012d}" for i in idx],
        "source": ["providers"] * (hi - lo),
        "source_pk": [str(i) for i in idx],
        "record_hash": [hashlib.sha256(str(i).encode()).hexdigest() for i in idx],
        "entity_id": [f"e{i // 2:012d}" if i // 2 < n else f"e{n - 1:012d}" for i in idx],
        "payload": [_golden(i) for i in idx],
        "dataset": ["spike"] * (hi - lo),
        "first_seen_at": [_NOW] * (hi - lo),
        "last_seen_at": [_NOW] * (hi - lo),
    })


def _n_records(n: int) -> int:
    return int(n * _RECORD_FACTOR)


def _build_tables(n: int):
    """Full in-memory Arrow tables (staging + duckdb_inmem consume these)."""
    return _node_batch(0, n), _record_batch(0, _n_records(n), n)


def _write_parquet_streaming(n: int, nodes_path: str, records_path: str) -> None:
    """Generate the source to Parquet in bounded batches, never holding the whole
    table -- the whole point of the streaming arm."""
    import pyarrow.parquet as pq
    nrec = _n_records(n)
    w = None
    for lo in range(0, n, _STREAM_BATCH):
        b = _node_batch(lo, min(lo + _STREAM_BATCH, n))
        w = w or pq.ParquetWriter(nodes_path, b.schema)
        w.write_table(b)
    if w:
        w.close()
    w = None
    for lo in range(0, nrec, _STREAM_BATCH):
        b = _record_batch(lo, min(lo + _STREAM_BATCH, nrec), n)
        w = w or pq.ParquetWriter(records_path, b.schema)
        w.write_table(b)
    if w:
        w.close()


# --- schema bootstrap + content hashing --------------------------------------
def _init_sqlite_schema(path: str) -> None:
    from goldenmatch.identity.store import IdentityStore
    IdentityStore(backend="sqlite", path=path).close()


def _peak_rss_gb() -> float:
    try:
        import resource
    except ImportError:
        return 0.0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 1e6 if sys.platform != "darwin" else rss / 1e9


def _hash_rows(rows_iter) -> str:
    h = hashlib.sha256()
    for row in rows_iter:
        h.update(repr(tuple(row)).encode())
    return h


def _content_hash_sqlite(path: str) -> str:
    conn = sqlite3.connect(path)
    h = hashlib.sha256()
    for table, cols in (("identity_nodes", _NODE_COLS), ("source_records", _RECORD_COLS)):
        sub = _hash_rows(conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} ORDER BY {cols[0]}"))
        h.update(sub.digest())
    conn.close()
    return h.hexdigest()[:16]


def _content_hash_duckdb(path: str) -> str:
    import duckdb
    conn = duckdb.connect(path, read_only=True)
    h = hashlib.sha256()
    for table, cols in (("identity_nodes", _NODE_COLS), ("source_records", _RECORD_COLS)):
        sub = _hash_rows(conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} ORDER BY {cols[0]}").fetchall())
        h.update(sub.digest())
    conn.close()
    return h.hexdigest()[:16]


# --- arms --------------------------------------------------------------------
def _run_staging(path: str, nodes, records) -> str:
    _init_sqlite_schema(path)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN")
        for stage, real, cols, upsert in (
            (_STAGE_NODES, "identity_nodes", _NODE_COLS, _UPSERT_NODES_SQLITE),
            (_STAGE_RECORDS, "source_records", _RECORD_COLS, _UPSERT_RECORDS_SQLITE),
        ):
            tbl = nodes if stage == _STAGE_NODES else records
            conn.execute(f"CREATE TEMP TABLE {stage} AS SELECT {', '.join(cols)} "
                         f"FROM {real} WHERE 0")
            ph = ", ".join("?" * len(cols))
            conn.executemany(
                f"INSERT INTO {stage} ({', '.join(cols)}) VALUES ({ph})",
                (tuple(r[c] for c in cols) for r in tbl.to_pylist()),
            )
            conn.execute(upsert)
            conn.execute(f"DROP TABLE {stage}")
        conn.execute("COMMIT")
    finally:
        conn.close()
    return _content_hash_sqlite(path)


def _run_duckdb_inmem(path: str, nodes, records) -> str:
    import duckdb
    conn = duckdb.connect(path)
    try:
        conn.execute(_DUCKDB_DDL)
        conn.register("nodes_view", nodes)
        conn.register("records_view", records)
        conn.execute(_UPSERT_NODES_DUCKDB.format(src="nodes_view"))
        conn.execute(_UPSERT_RECORDS_DUCKDB.format(src="records_view"))
    finally:
        conn.close()
    return _content_hash_duckdb(path)


def _run_duckdb_stream(path: str, n: int) -> str:
    import duckdb
    d = os.path.dirname(path)
    nodes_pq = os.path.join(d, "nodes.parquet")
    records_pq = os.path.join(d, "records.parquet")
    spill = os.path.join(d, "spill")
    os.makedirs(spill, exist_ok=True)
    _write_parquet_streaming(n, nodes_pq, records_pq)  # bounded-memory source
    conn = duckdb.connect(path)
    try:
        # Force DuckDB to stream + spill rather than buffer the scan in RAM.
        conn.execute("SET memory_limit='4GB'")
        conn.execute(f"SET temp_directory='{spill}'")
        conn.execute(_DUCKDB_DDL)
        conn.execute(_UPSERT_NODES_DUCKDB.format(
            src=f"read_parquet('{nodes_pq}')"))
        conn.execute(_UPSERT_RECORDS_DUCKDB.format(
            src=f"read_parquet('{records_pq}')"))
    finally:
        conn.close()
    return _content_hash_duckdb(path)


def _run_arm(arm: str, n: int) -> dict:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = os.path.join(tmp, "identity.duckdb" if arm.startswith("duckdb") else "identity.db")
        # Materialised arms get the frame built OUTSIDE the timed region (it
        # already exists in the real pipeline); the streaming arm's cost IS the
        # bounded generation, so it is timed. Peak RSS is whole-process either way.
        if arm == "duckdb_stream":
            t0 = time.perf_counter()
            content_hash = _run_duckdb_stream(path, n)
            wall = time.perf_counter() - t0
        else:
            nodes, records = _build_tables(n)
            fn = _run_staging if arm == "staging" else _run_duckdb_inmem
            t0 = time.perf_counter()
            content_hash = fn(path, nodes, records)
            wall = time.perf_counter() - t0
        return {
            "arm": arm, "identities": n, "records": _n_records(n),
            "wall_s": round(wall, 3),
            "peak_rss_gb": round(_peak_rss_gb(), 3),
            "db_mb": round(os.path.getsize(path) / 1e6, 2),
            "content_hash": content_hash,
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", default="100000,1000000,5000000")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--output", default=None)
    ap.add_argument("--_arm", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--_n", type=int, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args._arm:
        print(json.dumps(_run_arm(args._arm, args._n)))
        return 0

    ns = [int(x) for x in args.ns.split(",") if x.strip()]
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    results: list[dict] = []

    for n in ns:
        print(f"\n=== {n:,} identities ===")
        print(f"{'arm':>14} {'wall_s':>9} {'peak_rss_gb':>12} {'db_mb':>10} "
              f"{'content_hash':>14}")
        for arm in arms:
            proc = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "--_arm", arm, "--_n", str(n)],
                capture_output=True, text=True, check=False,
            )
            if proc.returncode != 0:
                tail = (proc.stderr.strip().splitlines() or ["?"])[-1]
                miss = "ModuleNotFoundError" in proc.stderr and "duckdb" in proc.stderr
                label = "SKIPPED (no duckdb)" if miss else "FAILED"
                print(f"{arm:>14}   {label}  {'' if miss else tail[:56]}")
                results.append({"arm": arm, "identities": n,
                                "skipped" if miss else "failed": True,
                                "error": tail[:300]})
                continue
            row = json.loads(proc.stdout.strip().splitlines()[-1])
            results.append(row)
            print(f"{row['arm']:>14} {row['wall_s']:>9.2f} {row['peak_rss_gb']:>12.3f} "
                  f"{row['db_mb']:>10.2f} {row['content_hash']:>14}")

        ok = [r for r in results if r.get("identities") == n
              and not r.get("failed") and not r.get("skipped")]
        hashes = {r["content_hash"] for r in ok}
        if len(hashes) > 1:
            print(f"  !! CONTENT MISMATCH across arms: {hashes} -- a leaner arm "
                  f"that writes different bytes is not a win")
        elif ok:
            print(f"  content identical across {len(ok)} arm(s)")

        by = {r["arm"]: r for r in ok}
        if "duckdb_stream" in by and "staging" in by:
            s_rss = by["staging"]["peak_rss_gb"]
            d_rss = by["duckdb_stream"]["peak_rss_gb"]
            rss_cut = (s_rss - d_rss) / s_rss if s_rss else 0.0
            wall = by["staging"]["wall_s"] / max(by["duckdb_stream"]["wall_s"], 1e-9)
            # The pre-committed test lives at 5M; report it at every N.
            verdict = "GO" if rss_cut >= 0.30 else "NO-GO"
            marker = "  <-- decision scale" if n >= 5_000_000 else ""
            print(f"  duckdb_stream vs staging: {wall:.2f}x wall, "
                  f"{rss_cut * 100:.0f}% less peak RSS -> {verdict} "
                  f"(kill: <30% RSS at 5M => NO-GO){marker}")

    if args.output:
        with open(args.output, "w") as fh:
            json.dump({"results": results}, fh, indent=2)
        print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
