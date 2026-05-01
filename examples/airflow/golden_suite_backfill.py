"""Backfill — reprocess N days of history when match config changes.

When you tune thresholds, swap blocking strategies, or add a new scorer, every
day's existing golden records is potentially stale. This DAG re-runs the daily
dedupe over a date range with the *current* config, in parallel via dynamic
task mapping.

Trigger manually with:
    airflow dags trigger golden_suite_backfill --conf '{"start_date":"2026-04-01","end_date":"2026-04-30"}'

Outputs go to a `_backfill_<runid>` S3 prefix and a separate metrics table —
the daily DAG's outputs are NOT touched until you explicitly promote backfill
results (manual or as a separate "promote" DAG).

Tunable: parallelism, output isolation strategy.

Requires:
    pip install apache-airflow goldenpipe[full] \\
                apache-airflow-providers-amazon apache-airflow-providers-postgres polars
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pendulum
from airflow.decorators import dag, task

BACKFILL_PREFIX_TEMPLATE = "_backfill/{run_id}/customers/{ds}/golden.parquet"
BACKFILL_METRICS_TABLE = "analytics.golden_suite_backfill_runs"

# Cap so a wide range doesn't melt the cluster
MAX_PARALLEL = 8


@dag(
    dag_id="golden_suite_backfill",
    description="Reprocess a date range with current match config. Manual trigger.",
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    params={"start_date": "2026-04-01", "end_date": "2026-04-30"},
    default_args={"owner": "data-platform", "retries": 1, "retry_delay": timedelta(minutes=5)},
    tags=["golden-suite", "backfill", "manual"],
)
def golden_suite_backfill():
    """Backfill the daily dedupe over a configurable date range."""

    @task
    def expand_dates(**context) -> list[str]:
        """Turn the date range into a list of yyyy-mm-dd strings."""
        params = context["params"]
        start = datetime.fromisoformat(params["start_date"]).date()
        end = datetime.fromisoformat(params["end_date"]).date()
        if end < start:
            raise ValueError(f"end_date ({end}) is before start_date ({start})")
        days = (end - start).days + 1
        if days > 366:
            raise ValueError(
                f"backfill range is {days} days (limit 366). "
                "Split into multiple runs or raise the cap deliberately."
            )
        return [(start + timedelta(days=i)).isoformat() for i in range(days)]

    @task(max_active_tis_per_dag=MAX_PARALLEL)
    def reprocess_one_day(ds: str, **context) -> dict[str, Any]:
        """Run the full Check → Flow → Match pipeline for a single day."""
        from pathlib import Path

        from airflow.providers.amazon.aws.hooks.s3 import S3Hook
        import goldencheck
        import goldenflow
        import goldenmatch
        import polars as pl

        bucket = "{{ var.value.golden_suite_bucket }}"
        source_key = f"raw/customers/{ds}/customers.csv"
        local = Path(f"/tmp/golden_suite/backfill/{ds}/customers.csv")
        local.parent.mkdir(parents=True, exist_ok=True)

        s3 = S3Hook(aws_conn_id="aws_default")
        try:
            s3.get_key(source_key, bucket).download_file(Filename=str(local))
        except Exception as exc:  # noqa: BLE001
            return {"ds": ds, "skipped": True, "reason": str(exc)}

        # Same pipeline as the daily DAG — keep them in sync.
        scan_result = goldencheck.scan_file(str(local))
        df = pl.read_csv(local, encoding="utf8-lossy", ignore_errors=True)
        df = goldenflow.transform_df(df).df
        result = goldenmatch.dedupe_df(
            df, exact=["email"], fuzzy={"first_name": 0.85, "last_name": 0.85},
            blocking=["zip"], threshold=0.85,
        )

        out = local.with_name("golden.parquet")
        if result.golden is not None:
            result.golden.write_parquet(out)

        out_key = BACKFILL_PREFIX_TEMPLATE.format(run_id=context["run_id"], ds=ds)
        s3.load_file(filename=str(out), key=out_key, bucket_name=bucket, replace=True)

        return {
            "ds": ds, "skipped": False,
            "total_records": result.total_records,
            "total_clusters": result.total_clusters,
            "match_rate": float(result.match_rate),
            "scan_findings": len(scan_result.to_dict().get("findings", [])),
            "out_uri": f"s3://{bucket}/{out_key}",
        }

    @task
    def summarize(per_day: list[dict[str, Any]], **context) -> None:
        """One audit row per day. Lets you compare backfill output to the live daily."""
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        run_id = context["run_id"]
        pg = PostgresHook(postgres_conn_id="postgres_default")
        for record in per_day:
            pg.run(
                f"""
                INSERT INTO {BACKFILL_METRICS_TABLE}
                    (backfill_run_id, ds, total_records, total_clusters, match_rate,
                     scan_findings, out_uri, skipped, skip_reason, created_at)
                VALUES
                    (%(run_id)s, %(ds)s, %(tr)s, %(tc)s, %(mr)s, %(sf)s, %(uri)s,
                     %(skipped)s, %(reason)s, NOW())
                """,
                parameters={
                    "run_id": run_id,
                    "ds": record["ds"],
                    "tr": record.get("total_records"),
                    "tc": record.get("total_clusters"),
                    "mr": record.get("match_rate"),
                    "sf": record.get("scan_findings"),
                    "uri": record.get("out_uri"),
                    "skipped": record.get("skipped", False),
                    "reason": record.get("reason"),
                },
            )

    dates = expand_dates()
    per_day = reprocess_one_day.expand(ds=dates)
    summarize(per_day)


golden_suite_backfill()
