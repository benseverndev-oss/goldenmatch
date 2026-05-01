"""Review queue worker — apply human decisions, persist learning memory.

Closes the loop on `golden_suite_incremental_match`'s review queue. Stewards
mark queued pairs as approve/reject in your review UI; this DAG picks up
the decided rows, applies them to the canonical store, and feeds the
labeled pair into goldenmatch's learning memory so future scoring improves.

Schedule defaults to every 5 minutes — short feedback loop is the point.

Requires:
    pip install apache-airflow goldenmatch[memory] apache-airflow-providers-postgres
"""
from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.decorators import dag, task

REVIEW_QUEUE_TABLE = "warehouse.customers_review_queue"
CANONICAL_TABLE = "warehouse.customers_canonical"

# Pull at most this many decisions per run to keep iterations bounded.
BATCH_LIMIT = 500


@dag(
    dag_id="golden_suite_review_worker",
    description="Apply steward decisions from the review queue and update learning memory.",
    schedule="*/5 * * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "data-platform", "retries": 1, "retry_delay": timedelta(minutes=1)},
    tags=["golden-suite", "review", "feedback-loop"],
)
def golden_suite_review_worker():
    """Process steward decisions on the review queue."""

    @task
    def claim_decided() -> list[dict]:
        """Atomically claim up to BATCH_LIMIT decided rows. Returns the claimed rows."""
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        pg = PostgresHook(postgres_conn_id="postgres_default")
        # SKIP LOCKED prevents two workers fighting over the same rows.
        sql = f"""
            UPDATE {REVIEW_QUEUE_TABLE}
            SET claimed_at = NOW()
            WHERE id IN (
                SELECT id FROM {REVIEW_QUEUE_TABLE}
                WHERE decided_at IS NOT NULL AND claimed_at IS NULL
                ORDER BY decided_at ASC
                LIMIT {BATCH_LIMIT}
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, payload, candidate_id, score, decision
        """
        rows = pg.get_records(sql)
        return [
            {
                "id": r[0], "payload": r[1], "candidate_id": r[2],
                "score": r[3], "decision": r[4],
            }
            for r in rows
        ]

    @task
    def apply_decisions(rows: list[dict]) -> dict[str, int]:
        """For each decision: approve → merge into canonical; reject → insert as new."""
        import json

        from airflow.providers.postgres.hooks.postgres import PostgresHook

        pg = PostgresHook(postgres_conn_id="postgres_default")
        approved = rejected = 0

        for r in rows:
            payload = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])

            if r["decision"] == "approve":
                # Merge: update canonical's last_seen + merge_count, drop the queued payload.
                pg.run(
                    f"UPDATE {CANONICAL_TABLE} "
                    "SET last_seen = NOW(), merge_count = merge_count + 1 "
                    "WHERE id = %(id)s",
                    parameters={"id": r["candidate_id"]},
                )
                approved += 1
            elif r["decision"] == "reject":
                # Reject: queued payload is a real new entity, insert it.
                cols = [c for c in payload.keys() if not c.startswith("__")]
                placeholders = ", ".join(f"%({c})s" for c in cols)
                pg.run(
                    f"INSERT INTO {CANONICAL_TABLE} ({', '.join(cols)}) VALUES ({placeholders})",
                    parameters={c: payload[c] for c in cols},
                )
                rejected += 1

        return {"approved": approved, "rejected": rejected}

    @task
    def update_memory(rows: list[dict]) -> int:
        """Push labeled pairs into goldenmatch's learning memory."""
        from goldenmatch.core.memory.store import MemoryStore
        from goldenmatch.config.schemas import MemoryConfig

        if not rows:
            return 0

        store = MemoryStore.from_config(MemoryConfig(
            enabled=True,
            backend="postgres",
            connection_id="postgres_default",  # via env / secrets in real config
        ))

        recorded = 0
        for r in rows:
            store.record_correction(
                pair_score=r["score"],
                decision=r["decision"],          # "approve" or "reject"
                features=r.get("payload", {}),    # whatever fields the matcher saw
            )
            recorded += 1
        return recorded

    @task
    def finalize(rows: list[dict], applied: dict[str, int], memory_count: int) -> None:
        """Mark claimed rows fully resolved. Emit a one-line stat for monitoring."""
        import logging

        from airflow.providers.postgres.hooks.postgres import PostgresHook

        if not rows:
            logging.info("review_worker: no decided rows this run.")
            return
        ids = [r["id"] for r in rows]
        PostgresHook(postgres_conn_id="postgres_default").run(
            f"UPDATE {REVIEW_QUEUE_TABLE} SET applied_at = NOW() WHERE id = ANY(%(ids)s)",
            parameters={"ids": ids},
        )
        logging.info(
            "review_worker: claimed=%d approved=%d rejected=%d memory_recorded=%d",
            len(rows), applied["approved"], applied["rejected"], memory_count,
        )

    rows = claim_decided()
    applied = apply_decisions(rows)
    mem_count = update_memory(rows)
    finalize(rows, applied, mem_count)


golden_suite_review_worker()
