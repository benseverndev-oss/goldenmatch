"""05 — Review queue workflow.

When auto-config can't decide, GoldenMatch surfaces borderline pairs to a
review queue. A steward decides approve / reject, the decision flows back
into Learning Memory, and future scoring on similar records improves.

This script simulates the full loop in-process so you can see the moving
parts. In a real deployment the review queue lives in Postgres / a UI, and
the apply step runs as a separate worker (see
`examples/airflow/golden_suite_review_worker.py`).

Run:
    pip install goldenmatch[memory] polars
    python 05_review_workflow.py
"""
from __future__ import annotations

import polars as pl

import goldenmatch
from goldenmatch.config.schemas import (
    GoldenMatchConfig, MatchkeyConfig, MatchkeyField, MemoryConfig,
)
from goldenmatch.core.review_queue import ReviewQueue


df = pl.DataFrame({
    "id":         [1, 2, 3, 4, 5, 6],
    "first_name": ["Jane", "Jane", "Robert", "Bob",   "Alice", "Alicia"],
    "last_name":  ["Smith", "Smyth", "Jones", "Jones", "Lee", "Lee"],
    "email":      ["jane@example.com", "jane@example.com",
                   "bob@example.com",  "robert.j@example.com",
                   "alice@example.com", "alice@example.com"],
})


def main() -> None:
    config = GoldenMatchConfig(
        memory=MemoryConfig(enabled=True, backend="memory"),  # in-process for demo
        matchkeys=[MatchkeyConfig(
            name="identity", type="weighted", threshold=0.65,
            fields=[
                MatchkeyField(field="first_name", scorer="ensemble",     weight=0.7,
                              transforms=["lowercase", "strip"]),
                MatchkeyField(field="last_name",  scorer="ensemble",     weight=0.9,
                              transforms=["lowercase", "strip"]),
                MatchkeyField(field="email",      scorer="jaro_winkler", weight=1.0,
                              transforms=["lowercase", "strip"]),
            ],
        )],
    )

    result = goldenmatch.dedupe_df(df, config=config)

    # Gate scored pairs into a review queue: high-confidence auto-merge,
    # mid-confidence to review, low to reject.
    queue = ReviewQueue(backend="memory")
    queue.gate_pairs(result.scored_pairs, auto=0.95, review_lo=0.75, reject=0.65)

    print(f"clusters: {result.total_clusters}")
    print(f"review queue: {len(queue.list_pending())} borderline pairs awaiting decision")
    for item in queue.list_pending():
        a_row = df.filter(pl.col("id") == item.id_a).to_dicts()[0]
        b_row = df.filter(pl.col("id") == item.id_b).to_dicts()[0]
        print(f"  {item.id_a} ↔ {item.id_b}  score={item.score:.3f}")
        print(f"    A: {a_row['first_name']} {a_row['last_name']} <{a_row['email']}>")
        print(f"    B: {b_row['first_name']} {b_row['last_name']} <{b_row['email']}>")

    # Steward decides — in real life via a UI / Slack approval / etc.
    print("\nsimulating steward decisions (approve all):")
    for item in queue.list_pending():
        queue.decide(item.pair_id, decision="approve", reviewer="alice")

    # Apply: the matcher's learning memory now records these as positive labels;
    # next dedupe run will boost similar pairs above the auto threshold.
    print(f"\ndecisions applied: {len(queue.list_decided())}")
    print("Future runs benefit from this feedback via Learning Memory.")


if __name__ == "__main__":
    main()
