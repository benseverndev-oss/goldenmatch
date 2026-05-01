"""Incremental match: new records → match against canonical store → upsert.

Streaming-style DAG. Polls a "new records" S3 prefix, and for each batch:
  1. loads the canonical golden record set,
  2. uses goldenmatch.match_one to score each new record,
  3. classifies into auto-merge / review-queue / append,
  4. upserts the canonical store.

Pairs naturally with `golden_suite_daily_dedupe` — the daily run rebuilds
the canonical set from scratch, this DAG keeps it fresh between runs.

Tunable: confidence gates (auto/review thresholds), batch size, schedule.

Requires:
    pip install apache-airflow goldenmatch[postgres] \\
                apache-airflow-providers-amazon apache-airflow-providers-postgres polars
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pendulum
from airflow.decorators import dag, task
from airflow.models import Variable

NEW_RECORDS_PREFIX = "incoming/customers/"
CANONICAL_TABLE = "warehouse.customers_canonical"
REVIEW_QUEUE_TABLE = "warehouse.customers_review_queue"

AUTO_MERGE_THRESHOLD = 0.95     # >= this → auto-merge into existing cluster
REVIEW_THRESHOLD = 0.75         # >= this and < AUTO → goes to human review
# below REVIEW_THRESHOLD → treated as a new unique record


@dag(
    dag_id="golden_suite_incremental_match",
    description="Match new records against canonical store every 15 min.",
    schedule="*/15 * * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "data-platform",
        "retries": 1,
        "retry_delay": timedelta(minutes=2),
    },
    tags=["golden-suite", "incremental", "customers"],
)
def golden_suite_incremental_match():
    """Match new records against the canonical store."""

    @task
    def list_new_keys() -> list[str]:
        """Find unprocessed keys in the incoming prefix."""
        from airflow.providers.amazon.aws.hooks.s3 import S3Hook

        hook = S3Hook(aws_conn_id="aws_default")
        keys = hook.list_keys(bucket_name=Variable.get("golden_suite_bucket"),
                              prefix=NEW_RECORDS_PREFIX) or []
        # Skip the prefix itself and any already-processed marker.
        return [k for k in keys if not k.endswith("/") and not k.endswith(".processed")]

    @task
    def load_canonical() -> str:
        """Snapshot the canonical store to a local parquet for matching."""
        from pathlib import Path

        from airflow.providers.postgres.hooks.postgres import PostgresHook
        import polars as pl

        rows = PostgresHook(postgres_conn_id="postgres_default").get_pandas_df(
            f"SELECT * FROM {CANONICAL_TABLE}"
        )
        if rows.empty:
            raise ValueError(f"{CANONICAL_TABLE} is empty — nothing to match against. "
                             "Run golden_suite_daily_dedupe to seed it.")
        local = Path("/tmp/golden_suite/canonical.parquet")
        local.parent.mkdir(parents=True, exist_ok=True)
        pl.from_pandas(rows).write_parquet(local)
        return str(local)

    @task
    def match_batch(new_key: str, canonical_path: str) -> dict[str, Any]:
        """Match every record in a single new file against the canonical set."""
        from pathlib import Path

        from airflow.providers.amazon.aws.hooks.s3 import S3Hook
        import polars as pl

        from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
        from goldenmatch.core.match_one import match_one

        local_new = Path(f"/tmp/golden_suite/{new_key}")
        local_new.parent.mkdir(parents=True, exist_ok=True)
        S3Hook(aws_conn_id="aws_default").get_key(
            new_key, Variable.get("golden_suite_bucket")
        ).download_file(Filename=str(local_new))

        new_df = pl.read_csv(local_new, encoding="utf8-lossy", ignore_errors=True)
        canonical_df = pl.read_parquet(canonical_path)

        mk = MatchkeyConfig(
            name="identity",
            type="weighted",
            threshold=REVIEW_THRESHOLD,
            fields=[
                MatchkeyField(field="email", scorer="exact", weight=1.0,
                              transforms=["lowercase", "strip"]),
                MatchkeyField(field="first_name", scorer="ensemble", weight=0.7,
                              transforms=["lowercase", "strip"]),
                MatchkeyField(field="last_name", scorer="ensemble", weight=0.9,
                              transforms=["lowercase", "strip"]),
            ],
        )

        auto: list[dict] = []
        review: list[dict] = []
        unique: list[dict] = []

        for record in new_df.iter_rows(named=True):
            best = match_one(record, canonical_df, mk, top_k=1)
            if not best:
                unique.append(record)
                continue
            top_id, top_score = best[0]
            if top_score >= AUTO_MERGE_THRESHOLD:
                auto.append({**record, "__matched_id__": top_id, "__score__": top_score})
            elif top_score >= REVIEW_THRESHOLD:
                review.append({**record, "__matched_id__": top_id, "__score__": top_score})
            else:
                unique.append(record)

        return {"key": new_key, "auto": auto, "review": review, "unique": unique}

    @task
    def write_results(batches: list[dict[str, Any]]) -> None:
        """Apply auto-merges, queue reviews, append uniques. Mark keys processed."""
        import json

        from airflow.providers.amazon.aws.hooks.s3 import S3Hook
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        pg = PostgresHook(postgres_conn_id="postgres_default")
        s3 = S3Hook(aws_conn_id="aws_default")

        for batch in batches:
            # Auto-merges: update canonical.last_seen + provenance.
            for rec in batch["auto"]:
                pg.run(
                    f"UPDATE {CANONICAL_TABLE} SET last_seen = NOW(), "
                    "merge_count = merge_count + 1 "
                    "WHERE id = %(id)s",
                    parameters={"id": rec["__matched_id__"]},
                )

            # Review-queue: insert pair for human steward.
            for rec in batch["review"]:
                pg.run(
                    f"INSERT INTO {REVIEW_QUEUE_TABLE} "
                    "(payload, candidate_id, score, queued_at) "
                    "VALUES (%(payload)s::jsonb, %(cid)s, %(score)s, NOW())",
                    parameters={
                        "payload": json.dumps(rec, default=str),
                        "cid": rec["__matched_id__"],
                        "score": rec["__score__"],
                    },
                )

            # Uniques: append as new canonical rows.
            for rec in batch["unique"]:
                cols = [c for c in rec.keys() if not c.startswith("__")]
                placeholders = ", ".join(f"%({c})s" for c in cols)
                pg.run(
                    f"INSERT INTO {CANONICAL_TABLE} ({', '.join(cols)}) "
                    f"VALUES ({placeholders})",
                    parameters={c: rec[c] for c in cols},
                )

            # Mark the source file as processed (idempotency for sensor).
            s3.copy_object(
                source_bucket_key=batch["key"],
                dest_bucket_key=batch["key"] + ".processed",
                source_bucket_name=Variable.get("golden_suite_bucket"),
                dest_bucket_name=Variable.get("golden_suite_bucket"),
            )

    new_keys = list_new_keys()
    canonical = load_canonical()
    batches = match_batch.expand(new_key=new_keys, canonical_path=[canonical])
    write_results(batches)


golden_suite_incremental_match()
