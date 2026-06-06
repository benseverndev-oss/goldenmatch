"""Emit a cross-language parity fixture for identity resolveClusters.

Runs the Python `resolve_clusters` through a deterministic 3-run scenario
(create -> absorb -> merge) against a temp SQLite IdentityStore, capturing the
real per-run ResolveSummary and the final structural snapshot (record_ids
grouped by active entity). Entity ids are UUIDs, so parity is structural:
the TS port must produce the same summary counts + the same record groupings.

Output: packages/typescript/goldenmatch/tests/parity/fixtures/resolve-clusters.json
Run:    .venv/Scripts/python.exe packages/python/goldenmatch/scripts/emit_resolve_fixture.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import polars as pl

from goldenmatch.identity.resolve import resolve_clusters
from goldenmatch.identity.store import IdentityStore

SOURCE = "s"


def _df(rows: list[dict]) -> pl.DataFrame:
    df = pl.DataFrame(rows)
    df = df.with_row_index("__row_id__").with_columns(
        pl.col("__row_id__").cast(pl.Int64),
        pl.lit(SOURCE).alias("__source__"),
    )
    return df


# Each run: rows (pk + name), clusters (dict[int, dict]), scored_pairs.
RUNS = [
    {
        "run_name": "r1",
        "rows": [
            {"pk": "a1", "name": "Alice"},
            {"pk": "a2", "name": "Alice A"},
            {"pk": "b1", "name": "Bob"},
            {"pk": "c1", "name": "Carol"},
        ],
        "clusters": {
            0: {"members": [0, 1], "size": 2, "oversized": False,
                "pair_scores": {(0, 1): 0.95}, "confidence": 0.95,
                "bottleneck_pair": None},
            1: {"members": [2], "size": 1, "oversized": False,
                "pair_scores": {}, "confidence": 1.0, "bottleneck_pair": None},
            2: {"members": [3], "size": 1, "oversized": False,
                "pair_scores": {}, "confidence": 1.0, "bottleneck_pair": None},
        },
        "scored_pairs": [(0, 1, 0.95)],
    },
    {
        "run_name": "r2",
        "rows": [
            {"pk": "a1", "name": "Alice"},
            {"pk": "a3", "name": "Alice Anne"},
        ],
        "clusters": {
            0: {"members": [0, 1], "size": 2, "oversized": False,
                "pair_scores": {(0, 1): 0.9}, "confidence": 0.9,
                "bottleneck_pair": None},
        },
        "scored_pairs": [(0, 1, 0.9)],
    },
    {
        "run_name": "r3",
        "rows": [
            {"pk": "a1", "name": "Alice"},
            {"pk": "b1", "name": "Bob"},
        ],
        "clusters": {
            0: {"members": [0, 1], "size": 2, "oversized": False,
                "pair_scores": {(0, 1): 0.9}, "confidence": 0.9,
                "bottleneck_pair": None},
        },
        "scored_pairs": [(0, 1, 0.9)],
    },
]


def main() -> None:
    tmp = Path(tempfile.mkdtemp()) / "identity.db"
    store = IdentityStore(backend="sqlite", path=str(tmp))

    runs_out = []
    for run in RUNS:
        df = _df(run["rows"])
        summary = resolve_clusters(
            clusters=run["clusters"],
            df=df,
            scored_pairs=run["scored_pairs"],
            matchkey_name="identity",
            store=store,
            run_name=run["run_name"],
            source_pk_col="pk",
            emit_singletons=True,
        )
        runs_out.append({
            "run_name": run["run_name"],
            "rows": run["rows"],
            "clusters": {
                str(cid): {
                    "members": info["members"],
                    "confidence": info["confidence"],
                    "pair_scores": [[a, b, s] for (a, b), s in info["pair_scores"].items()],
                }
                for cid, info in run["clusters"].items()
            },
            "scored_pairs": [[a, b, s] for a, b, s in run["scored_pairs"]],
            "expected_summary": summary.as_dict(),
        })

    # Final structural snapshot: record_id groups per ACTIVE entity.
    active = store.list_identities(status="active", limit=1000)
    groups = []
    total_edges = 0
    total_events = 0
    for node in active:
        recs = store.get_records_for_entity(node.entity_id)
        groups.append(sorted(r.record_id for r in recs))
        total_edges += len(store.edges_for_entity(node.entity_id))
        total_events += len(store.history(node.entity_id, limit=1000))
    groups.sort(key=lambda g: g[0] if g else "")

    fixture = {
        "source_pk_col": "pk",
        "runs": runs_out,
        "expected_final": {
            "groups": groups,
            "active_identities": len(active),
            "edges": total_edges,
            "events": total_events,
        },
    }

    out = (
        Path(__file__).resolve().parents[3]
        / "typescript" / "goldenmatch" / "tests" / "parity" / "fixtures"
        / "resolve-clusters.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, indent=2, default=str))
    print(f"Wrote {out}")
    print(json.dumps(fixture["expected_final"], indent=2, default=str))


if __name__ == "__main__":
    main()
