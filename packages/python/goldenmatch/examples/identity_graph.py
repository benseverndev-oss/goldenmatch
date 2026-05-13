#!/usr/bin/env python
"""Identity Graph -- end-to-end demo.

Runs dedupe twice on overlapping inputs and inspects the durable
``entity_id`` that survives across runs, plus the event log.
"""
from __future__ import annotations

import goldenmatch as gm
import polars as pl
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    IdentityConfig,
    MatchkeyConfig,
    MatchkeyField,
    OutputConfig,
)
from goldenmatch.core.pipeline import run_dedupe_df


def _build_config(db_path: str, run_name: str) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        output=OutputConfig(run_name=run_name),
        matchkeys=[MatchkeyConfig(
            name="people_fuzzy", type="weighted", threshold=0.85,
            fields=[
                MatchkeyField(field="name",  scorer="jaro_winkler", weight=0.7),
                MatchkeyField(field="email", scorer="exact",        weight=0.3),
            ],
        )],
        blocking=BlockingConfig(strategy="static", keys=[
            BlockingKeyConfig(fields=["zip"]),
        ]),
        identity=IdentityConfig(
            enabled=True,
            path=db_path,
            source_pk_column="id",
            dataset="example",
        ),
    )


def main() -> None:
    db = ".goldenmatch/identity_demo.db"

    df_run1 = pl.DataFrame({
        "id":    ["1", "2", "3"],
        "name":  ["Alice Smith", "Alyce Smith", "Bob Jones"],
        "email": ["a@x.com", "a@x.com", "b@y.com"],
        "zip":   ["12345", "12345", "67890"],
    })

    print("=== run 1 ===")
    r1 = run_dedupe_df(df_run1, _build_config(db, "demo-r1"), source_name="people")
    print("identity summary:", r1["identity_summary"])

    print("\n=== run 2: same data + 1 new record ===")
    df_run2 = pl.concat([df_run1, pl.DataFrame({
        "id": ["4"], "name": ["Alise Smith"],
        "email": ["a@x.com"], "zip": ["12345"],
    })])
    r2 = run_dedupe_df(df_run2, _build_config(db, "demo-r2"), source_name="people")
    print("identity summary:", r2["identity_summary"])

    print("\n=== resolve a record across both runs ===")
    with gm.IdentityStore(path=db) as store:
        view = gm.find_by_record(store, "people:1")
        if view:
            print(f"people:1 -> {view.node.entity_id}  status={view.node.status}")
            print(f"  members: {[r.record_id for r in view.records]}")
            print(f"  events:  {[e.kind for e in view.events]}")
        # The new record (people:4) was absorbed into Alice's identity
        view4 = gm.find_by_record(store, "people:4")
        print(f"people:4 -> {view4.node.entity_id if view4 else '(none)'}")


if __name__ == "__main__":
    main()
