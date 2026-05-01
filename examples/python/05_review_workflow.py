"""05 — Review queue workflow.

When matching surfaces borderline pairs, GoldenMatch's review queue captures
them so a human can decide approve/reject. This script demonstrates the end-
to-end loop in-process:

  1. dedupe a small dataset and collect scored pairs
  2. partition them into auto-merge / review / auto-reject via gate_pairs()
  3. enqueue the review-bucket pairs into a ReviewQueue
  4. simulate a steward approving each pending item
  5. inspect the queue's stats

In production the queue lives in SQLite/Postgres (`ReviewQueue(backend="sqlite"|"postgres")`)
and a separate worker applies decisions back to the canonical store — see
`examples/airflow/golden_suite_review_worker.py`.

Run:
    pip install goldenmatch polars
    python 05_review_workflow.py
"""
from __future__ import annotations

import polars as pl

import goldenmatch
from goldenmatch.config.schemas import (
    GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
)
from goldenmatch.core.review_queue import ReviewQueue, gate_pairs


df = pl.DataFrame({
    "id":         [1, 2, 3, 4, 5, 6],
    "first_name": ["Jane", "Jane", "Robert", "Bob",   "Alice", "Alicia"],
    "last_name":  ["Smith", "Smyth", "Jones", "Jones", "Lee", "Lee"],
    "email":      ["jane@example.com", "jane@example.com",
                   "bob@example.com",  "robert.j@example.com",
                   "alice@example.com", "alice@example.com"],
})

JOB_NAME = "review-demo"


def main() -> None:
    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="identity", type="weighted", threshold=0.65,
            fields=[
                MatchkeyField(field="first_name", scorer="ensemble", weight=0.7,
                              transforms=["lowercase", "strip"]),
                MatchkeyField(field="last_name",  scorer="ensemble", weight=0.9,
                              transforms=["lowercase", "strip"]),
                MatchkeyField(field="email",      scorer="jaro_winkler", weight=1.0,
                              transforms=["lowercase", "strip"]),
            ],
        )],
    )

    result = goldenmatch.dedupe_df(df, config=config)

    # gate_pairs splits scored pairs into 3 buckets by score.
    auto_merged, review, auto_rejected = gate_pairs(
        result.scored_pairs,
        merge_threshold=0.95,
        review_threshold=0.75,
    )
    print(f"clusters: {result.total_clusters}")
    print(f"buckets — auto_merged={len(auto_merged)}  review={len(review)}  "
          f"auto_rejected={len(auto_rejected)}")

    # Enqueue review-bucket pairs for a human steward.
    queue = ReviewQueue(backend="memory")
    for id_a, id_b, score in review:
        a = df.filter(pl.col("id") == id_a).to_dicts()[0]
        b = df.filter(pl.col("id") == id_b).to_dicts()[0]
        explanation = (
            f"{a['first_name']} {a['last_name']} <{a['email']}> ↔ "
            f"{b['first_name']} {b['last_name']} <{b['email']}> @ {score:.3f}"
        )
        queue.add(JOB_NAME, id_a, id_b, score, explanation)

    pending = queue.list_pending(JOB_NAME)
    print(f"\nreview queue: {len(pending)} pending")
    for item in pending:
        print(f"  [{item.score:.3f}] {item.explanation}")

    # Steward decides — in real life via a UI / Slack approval / etc.
    print("\nsimulating steward decisions (approve all):")
    for item in pending:
        queue.approve(JOB_NAME, item.id_a, item.id_b, decided_by="alice")

    print(f"\nstats: {queue.stats(JOB_NAME)}")


if __name__ == "__main__":
    main()
