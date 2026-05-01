"""Reverse ETL: push golden records out to operational systems (Salesforce, HubSpot).

Closes the loop. The daily / customer-360 DAGs build deduped golden records in
the warehouse; this DAG sends those records back to the systems where the
business actually operates. Sales reps, support agents, and CRM dashboards
benefit from a deduped reality.

Watermark-based incremental: only push records updated since the last successful
run. The watermark lives in `audit.reverse_etl_watermarks` keyed by destination.

Tunable: destination matrix (which CRMs / which fields), batch size, parallel
fan-out across destinations.

Requires:
    pip install apache-airflow apache-airflow-providers-salesforce \\
                apache-airflow-providers-postgres polars
    # Plus your CRM's Python client. This example uses simple-salesforce.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pendulum
from airflow.decorators import dag, task

UNIFIED_TABLE = "warehouse.customers_unified"
WATERMARK_TABLE = "audit.reverse_etl_watermarks"

# (destination, conn_id, target_object_or_endpoint, field_map)
DESTINATIONS = [
    {
        "name": "salesforce",
        "conn_id": "salesforce_default",
        "target": "Contact",
        "field_map": {
            "first_name": "FirstName",
            "last_name": "LastName",
            "email": "Email",
            "phone": "Phone",
        },
    },
    {
        "name": "hubspot",
        "conn_id": "hubspot_default",
        "target": "contacts",
        "field_map": {
            "first_name": "firstname",
            "last_name": "lastname",
            "email": "email",
            "phone": "phone",
        },
    },
]

BATCH_SIZE = 200


@dag(
    dag_id="golden_suite_reverse_etl",
    description="Push deduped golden records to operational CRMs (incremental).",
    schedule="0 */2 * * *",  # every 2 hours
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "data-platform", "retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["golden-suite", "reverse-etl", "operational"],
)
def golden_suite_reverse_etl():
    """Push golden records to operational systems."""

    @task
    def get_watermark(dest_name: str) -> str:
        """Pull last-successful watermark for this destination. Default to epoch."""
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        row = PostgresHook(postgres_conn_id="postgres_default").get_first(
            f"SELECT updated_at FROM {WATERMARK_TABLE} WHERE destination = %(d)s",
            parameters={"d": dest_name},
        )
        return row[0].isoformat() if row else "1970-01-01T00:00:00+00:00"

    @task
    def fetch_changed(watermark: str) -> list[dict]:
        """Fetch unified rows updated since watermark."""
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        df = PostgresHook(postgres_conn_id="postgres_default").get_pandas_df(
            f"SELECT * FROM {UNIFIED_TABLE} WHERE last_seen > %(w)s ORDER BY last_seen ASC",
            parameters={"w": watermark},
        )
        return df.to_dict(orient="records")

    @task
    def push(records: list[dict], destination: dict[str, Any]) -> dict[str, Any]:
        """Send records to the destination CRM in batches. Returns counts + new watermark."""
        if not records:
            return {"sent": 0, "max_updated_at": None, "destination": destination["name"]}

        # Map columns to destination's naming
        mapped = []
        for r in records:
            row = {dst: r.get(src) for src, dst in destination["field_map"].items()}
            row["__source_id__"] = r.get("id")
            mapped.append(row)

        if destination["name"] == "salesforce":
            from airflow.providers.salesforce.hooks.salesforce import SalesforceHook
            sf = SalesforceHook(salesforce_conn_id=destination["conn_id"]).get_conn()
            for i in range(0, len(mapped), BATCH_SIZE):
                batch = mapped[i:i + BATCH_SIZE]
                # Upsert by Email — use Salesforce's external-ID upsert if Email is unique.
                getattr(sf.bulk, destination["target"]).upsert(batch, "Email")
        elif destination["name"] == "hubspot":
            # HubSpot example uses a hypothetical hubspot connection; replace with your client.
            from airflow.hooks.base import BaseHook
            conn = BaseHook.get_connection(destination["conn_id"])
            import requests
            for i in range(0, len(mapped), BATCH_SIZE):
                batch = mapped[i:i + BATCH_SIZE]
                resp = requests.post(
                    f"https://api.hubapi.com/crm/v3/objects/{destination['target']}/batch/upsert",
                    headers={"Authorization": f"Bearer {conn.password}"},
                    json={"inputs": [{"properties": p} for p in batch]},
                    timeout=60,
                )
                resp.raise_for_status()
        else:
            raise ValueError(f"unknown destination: {destination['name']}")

        max_updated = max(r["last_seen"] for r in records if r.get("last_seen"))
        return {
            "sent": len(mapped),
            "max_updated_at": max_updated.isoformat() if max_updated else None,
            "destination": destination["name"],
        }

    @task
    def advance_watermark(result: dict[str, Any]) -> None:
        """Persist the new watermark only if push succeeded with rows."""
        if not result.get("max_updated_at"):
            return
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        PostgresHook(postgres_conn_id="postgres_default").run(
            f"INSERT INTO {WATERMARK_TABLE} (destination, updated_at) "
            "VALUES (%(d)s, %(t)s::timestamptz) "
            "ON CONFLICT (destination) DO UPDATE SET updated_at = EXCLUDED.updated_at",
            parameters={"d": result["destination"], "t": result["max_updated_at"]},
        )

    # Per-destination fan-out — each destination has its own watermark + retry budget.
    for dest in DESTINATIONS:
        wm = get_watermark.override(task_id=f"watermark_{dest['name']}")(dest["name"])
        recs = fetch_changed.override(task_id=f"fetch_{dest['name']}")(wm)
        result = push.override(task_id=f"push_{dest['name']}")(recs, dest)
        advance_watermark.override(task_id=f"advance_{dest['name']}")(result)


golden_suite_reverse_etl()
