"""Spike: is Arrow-native (ADBC) bulk ingest into SQLite worth a dependency?

Decides the question in docs/design/2026-07-24-adbc-sqlite-bulk-writes.md.

``IdentityStore``'s bulk write fast path is Postgres-only. SQLite could get one
via Apache Arrow's ADBC SQLite driver -- but SQLite has no COPY, so ADBC's
ingest is prepared INSERTs in a transaction underneath, which is materially what
the post-#2111 row path already does. The transaction-batching win is banked;
the only thing left for ADBC to buy is **eliminating the per-row Python object**.

So the control arm is deliberately unkind to ADBC:

  rowpath     today's batched row path (IdentityStore public API, inside
              bulk_writes()). The baseline.
  staging     staging table + stdlib executemany + INSERT ... SELECT ...
              ON CONFLICT DO UPDATE. **No new dependency.** If this captures
              most of ADBC's win, ship this and drop ADBC.
  adbc        adbc_ingest into the same staging table + the same upsert.
              Skipped with a clear message when adbc_driver_sqlite is absent.

All three start from the SAME pyarrow.Table, because in the real pipeline the
data originates from a frame. ``rowpath`` and ``staging`` pay the
Arrow -> Python-rows conversion inside their timed region -- that conversion IS
the cost under examination, not an artifact of the harness.

Each arm runs in its own subprocess so peak RSS is that arm's alone, and every
arm's resulting database is content-hashed so a faster arm that writes different
bytes fails loudly instead of looking like a win.

Run on large-new-64GB via the bench-identity-resolve-scaling workflow, NOT
locally.
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

ARMS = ("rowpath", "staging", "adbc")

_STAGE_NODES = "_stage_identity_nodes"
_STAGE_RECORDS = "_stage_source_records"

# Mirrors IdentityStore.upsert_identity / upsert_record exactly, including which
# columns the conflict clause refreshes. Any drift here invalidates the spike.
#
# The `WHERE true` is load-bearing, not decoration: when an UPSERT's INSERT takes
# its values from a SELECT, SQLite's parser cannot tell whether `ON` introduces
# the upsert clause or a join constraint, and errors with `near "DO": syntax
# error`. The documented workaround is a WHERE clause on the SELECT. Postgres
# does not need this, so the "near-verbatim port" of the Postgres bulk SQL is
# verbatim EXCEPT here.
_UPSERT_NODES = f"""
INSERT INTO identity_nodes
    (entity_id, status, merged_into, golden_record, confidence, dataset,
     created_at, updated_at)
SELECT entity_id, status, merged_into, golden_record, confidence, dataset,
       created_at, updated_at
FROM {_STAGE_NODES} WHERE true
ON CONFLICT(entity_id) DO UPDATE SET
    status=excluded.status,
    merged_into=excluded.merged_into,
    golden_record=excluded.golden_record,
    confidence=excluded.confidence,
    dataset=excluded.dataset,
    updated_at=excluded.updated_at
"""

_UPSERT_RECORDS = f"""
INSERT INTO source_records
    (record_id, source, source_pk, record_hash, entity_id, payload,
     dataset, first_seen_at, last_seen_at)
SELECT record_id, source, source_pk, record_hash, entity_id, payload,
       dataset, first_seen_at, last_seen_at
FROM {_STAGE_RECORDS} WHERE true
ON CONFLICT(record_id) DO UPDATE SET
    record_hash=excluded.record_hash,
    entity_id=excluded.entity_id,
    payload=excluded.payload,
    last_seen_at=excluded.last_seen_at
"""

_NODE_COLS = [
    "entity_id", "status", "merged_into", "golden_record", "confidence",
    "dataset", "created_at", "updated_at",
]
_RECORD_COLS = [
    "record_id", "source", "source_pk", "record_hash", "entity_id", "payload",
    "dataset", "first_seen_at", "last_seen_at",
]


def _peak_rss_gb() -> float:
    try:
        import resource
    except ImportError:
        return 0.0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 1e6 if sys.platform != "darwin" else rss / 1e9


def _build_tables(n: int):
    """Source data as Arrow, shaped like a real identity write.

    Deterministic: every arm ingests byte-identical values, so the content-hash
    check below is meaningful. Timestamps are ISO strings and JSON columns are
    TEXT, matching what IdentityStore actually stores.
    """
    import pyarrow as pa

    now = datetime(2026, 7, 24, 12, 0, 0).isoformat()
    # ~2.1 records per identity, matching the reported cluster shape.
    n_records = int(n * 2.14)
    golden = [
        json.dumps({f"col{j}": f"value-{i}-{j}" for j in range(14)})
        for i in range(n)
    ]
    nodes = pa.table({
        "entity_id": [f"e{i:012d}" for i in range(n)],
        "status": ["active"] * n,
        "merged_into": pa.nulls(n, pa.string()),
        "golden_record": golden,
        "confidence": [0.85] * n,
        "dataset": ["spike"] * n,
        "created_at": [now] * n,
        "updated_at": [now] * n,
    })
    records = pa.table({
        "record_id": [f"providers:{i:012d}" for i in range(n_records)],
        "source": ["providers"] * n_records,
        "source_pk": [str(i) for i in range(n_records)],
        "record_hash": [hashlib.sha256(str(i).encode()).hexdigest()
                        for i in range(n_records)],
        "entity_id": [f"e{i // 2:012d}" if i // 2 < n else f"e{n - 1:012d}"
                      for i in range(n_records)],
        "payload": [json.dumps({f"col{j}": f"value-{i}-{j}" for j in range(14)})
                    for i in range(n_records)],
        "dataset": ["spike"] * n_records,
        "first_seen_at": [now] * n_records,
        "last_seen_at": [now] * n_records,
    })
    return nodes, records


def _init_schema(path: str) -> None:
    """Create the real identity schema, then get out of the way."""
    from goldenmatch.identity.store import IdentityStore
    IdentityStore(backend="sqlite", path=path).close()


def _content_hash(path: str) -> str:
    """Order-independent hash of what actually landed in the two tables."""
    conn = sqlite3.connect(path)
    h = hashlib.sha256()
    for table, cols in (("identity_nodes", _NODE_COLS),
                        ("source_records", _RECORD_COLS)):
        collist = ", ".join(cols)
        for row in conn.execute(
            f"SELECT {collist} FROM {table} ORDER BY {cols[0]}"
        ):
            h.update(repr(row).encode())
    conn.close()
    return h.hexdigest()[:16]


def _run_rowpath(path: str, nodes, records) -> None:
    from goldenmatch.identity.model import IdentityNode, SourceRecord
    from goldenmatch.identity.store import IdentityStore

    store = IdentityStore(backend="sqlite", path=path)
    with store.bulk_writes():
        for r in nodes.to_pylist():
            store.upsert_identity(IdentityNode(
                entity_id=r["entity_id"], status=r["status"],
                merged_into=r["merged_into"],
                golden_record=json.loads(r["golden_record"]),
                confidence=r["confidence"], dataset=r["dataset"],
                created_at=datetime.fromisoformat(r["created_at"]),
                updated_at=datetime.fromisoformat(r["updated_at"]),
            ))
        for r in records.to_pylist():
            store.upsert_record(SourceRecord(
                record_id=r["record_id"], source=r["source"],
                source_pk=r["source_pk"], record_hash=r["record_hash"],
                entity_id=r["entity_id"], payload=json.loads(r["payload"]),
                dataset=r["dataset"],
                first_seen_at=datetime.fromisoformat(r["first_seen_at"]),
                last_seen_at=datetime.fromisoformat(r["last_seen_at"]),
            ))
    store.close()


def _run_staging(path: str, nodes, records) -> None:
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN")
        for stage, real, cols, upsert in (
            (_STAGE_NODES, "identity_nodes", _NODE_COLS, _UPSERT_NODES),
            (_STAGE_RECORDS, "source_records", _RECORD_COLS, _UPSERT_RECORDS),
        ):
            tbl = nodes if stage == _STAGE_NODES else records
            conn.execute(f"CREATE TEMP TABLE {stage} AS SELECT "
                         f"{', '.join(cols)} FROM {real} WHERE 0")
            placeholders = ", ".join("?" * len(cols))
            conn.executemany(
                f"INSERT INTO {stage} ({', '.join(cols)}) "
                f"VALUES ({placeholders})",
                (tuple(r[c] for c in cols) for r in tbl.to_pylist()),
            )
            conn.execute(upsert)
            conn.execute(f"DROP TABLE {stage}")
        conn.execute("COMMIT")
    finally:
        conn.close()


def _adbc_connect(path: str):
    """The docs only show in-memory connect(); probe the file-path form."""
    import adbc_driver_sqlite.dbapi as dbapi
    for attempt in (
        lambda: dbapi.connect(uri=path),
        lambda: dbapi.connect(path),
        lambda: dbapi.connect(driver="adbc_driver_sqlite",
                              db_kwargs={"uri": path}),
    ):
        try:
            return attempt()
        except TypeError:
            continue
    raise RuntimeError("could not open an ADBC SQLite connection to a file")


def _run_adbc(path: str, nodes, records) -> None:
    # Everything runs on the ADBC connection: SQLite allows one writer, so
    # mixing a stdlib write txn with an ADBC one here would deadlock (this is
    # the single-writer problem the design doc calls out).
    conn = _adbc_connect(path)
    try:
        cur = conn.cursor()
        try:
            for stage, cols, upsert in (
                (_STAGE_NODES, _NODE_COLS, _UPSERT_NODES),
                (_STAGE_RECORDS, _RECORD_COLS, _UPSERT_RECORDS),
            ):
                tbl = (nodes if stage == _STAGE_NODES else records).select(cols)
                cur.adbc_ingest(stage, tbl, mode="create")
                cur.execute(upsert)
                cur.execute(f"DROP TABLE {stage}")
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()


def _batched_row_path_available() -> bool:
    """Whether #2111's SQLite transaction batching is present.

    Without it ``bulk_writes()`` is a no-op on SQLite and the ``rowpath``
    baseline measures the OLD per-statement-autocommit path -- which would make
    every other arm look far better than it is. Reported so a result read in
    isolation cannot be misinterpreted.
    """
    try:
        from goldenmatch.identity import store as _s
        return hasattr(_s, "_sqlite_batch_writes_enabled")
    except Exception:
        return False


def _run_arm(arm: str, n: int) -> dict:
    nodes, records = _build_tables(n)
    # ignore_cleanup_errors: on Windows SQLite can still hold the file at
    # teardown, which would mask the arm's real result with a PermissionError.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = os.path.join(tmp, "identity.db")
        _init_schema(path)
        fn = {"rowpath": _run_rowpath, "staging": _run_staging,
              "adbc": _run_adbc}[arm]
        t0 = time.perf_counter()
        fn(path, nodes, records)
        wall = time.perf_counter() - t0
        return {
            "arm": arm, "identities": n, "records": records.num_rows,
            "wall_s": round(wall, 3),
            "peak_rss_gb": round(_peak_rss_gb(), 3),
            "db_mb": round(os.path.getsize(path) / 1e6, 2),
            "content_hash": _content_hash(path),
            "batched_row_path": _batched_row_path_available(),
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", default="100000,1000000",
                    help="Identity counts to sweep (comma-separated)")
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
        print(f"{'arm':>10} {'wall_s':>9} {'peak_rss_gb':>12} "
              f"{'db_mb':>9} {'content_hash':>14}")
        for arm in arms:
            proc = subprocess.run(
                [sys.executable, os.path.abspath(__file__),
                 "--_arm", arm, "--_n", str(n)],
                capture_output=True, text=True, check=False,
            )
            if proc.returncode != 0:
                tail = (proc.stderr.strip().splitlines() or ["?"])[-1]
                # A missing driver is "not installed", not "ADBC lost" -- never
                # let it read as a NO-GO result.
                skipped = "ModuleNotFoundError" in proc.stderr and \
                          "adbc" in proc.stderr
                label = "SKIPPED (not installed)" if skipped else "FAILED"
                print(f"{arm:>10}   {label}  {'' if skipped else tail[:60]}")
                results.append({"arm": arm, "identities": n,
                                "skipped" if skipped else "failed": True,
                                "error": tail[:300]})
                continue
            row = json.loads(proc.stdout.strip().splitlines()[-1])
            results.append(row)
            print(f"{row['arm']:>10} {row['wall_s']:>9.2f} "
                  f"{row['peak_rss_gb']:>12.3f} {row['db_mb']:>9.2f} "
                  f"{row['content_hash']:>14}")

        ok = [r for r in results if r.get("identities") == n
              and not r.get("failed") and not r.get("skipped")]
        if ok and not ok[0].get("batched_row_path", True):
            print("  NOTE: #2111's SQLite write batching is ABSENT -- the "
                  "rowpath baseline is the old autocommit path and every other "
                  "arm will look better than it really is.")
        hashes = {r["content_hash"] for r in ok}
        if len(hashes) > 1:
            print(f"  !! CONTENT MISMATCH across arms: {hashes} -- a faster arm "
                  f"that writes different bytes is not a win")
        elif ok:
            print(f"  content identical across {len(ok)} arm(s)")

        # The decision the design doc pre-committed to.
        by_arm = {r["arm"]: r for r in ok}
        if "adbc" in by_arm and "staging" in by_arm:
            w = by_arm["staging"]["wall_s"] / max(by_arm["adbc"]["wall_s"], 1e-9)
            sr, ar = (by_arm["staging"]["peak_rss_gb"],
                      by_arm["adbc"]["peak_rss_gb"])
            rss = (sr - ar) / sr if sr else 0.0
            verdict = ("GO" if (w >= 1.5 or rss >= 0.30) else "NO-GO")
            print(f"  adbc vs staging(control): {w:.2f}x wall, "
                  f"{rss * 100:.0f}% less peak RSS -> {verdict} "
                  f"(kill criteria: <1.5x AND <30% => NO-GO)")

    if args.output:
        with open(args.output, "w") as fh:
            json.dump({"results": results}, fh, indent=2)
        print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
