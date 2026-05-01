"""Daily Golden Suite pipeline: ingest → check → flow → match → load.

Drop this into your Airflow `dags/` folder. Pulls a CSV from S3, runs the full
Golden Suite, and writes the canonical golden records back to S3 plus a
summary row to a Postgres metrics table.

Tunable knobs are at the top. The pipeline-step functions below are written
so each one fails loudly with a useful error rather than silently producing
empty output.

Requires:
    pip install apache-airflow goldenpipe[full] apache-airflow-providers-amazon \\
                apache-airflow-providers-postgres polars

Connections (Airflow UI → Admin → Connections):
    aws_default       — S3 read/write
    postgres_default  — metrics + canonical store

Tested against Airflow 2.10. Compatible with 3.x via the same TaskFlow API.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pendulum
from airflow.decorators import dag, task
from airflow.models import Variable

# -----------------------------------------------------------------------------
# Knobs — adjust per environment
# -----------------------------------------------------------------------------
S3_BUCKET = Variable.get("golden_suite_bucket")  # Airflow Variable
SOURCE_KEY_TEMPLATE = "raw/customers/{{ ds }}/customers.csv"
GOLDEN_KEY_TEMPLATE = "golden/customers/{{ ds }}/golden.parquet"
METRICS_TABLE = "analytics.golden_suite_runs"

# Match config — tune for your data shape
MATCH_EXACT_FIELDS = ["email"]
MATCH_FUZZY_FIELDS = {"first_name": 0.85, "last_name": 0.85, "city": 0.9}
MATCH_BLOCKING = ["zip"]
MATCH_THRESHOLD = 0.85


@dag(
    dag_id="golden_suite_daily_dedupe",
    description="Daily Check → Flow → Match → load. Customer dedupe.",
    schedule="0 4 * * *",  # 04:00 UTC daily
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "data-platform",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    },
    tags=["golden-suite", "dedupe", "customers"],
)
def golden_suite_daily_dedupe():
    """Daily customer dedupe via the Golden Suite."""

    @task
    def extract(source_key: str) -> str:
        """Pull source CSV from S3 to a local path. Returns the local path."""
        from pathlib import Path
        from airflow.providers.amazon.aws.hooks.s3 import S3Hook

        local = Path(f"/tmp/golden_suite/{source_key}")
        local.parent.mkdir(parents=True, exist_ok=True)
        S3Hook(aws_conn_id="aws_default").get_key(source_key, S3_BUCKET).download_file(
            Filename=str(local)
        )
        if local.stat().st_size == 0:
            raise ValueError(f"Downloaded empty file from s3://{S3_BUCKET}/{source_key}")
        return str(local)

    @task
    def scan(local_path: str) -> dict[str, Any]:
        """GoldenCheck — surface data quality issues. Returns a findings dict."""
        import goldencheck

        scan_result = goldencheck.scan_file(local_path)
        # Don't fail on findings — just hand them to the transform step.
        # If you want a hard gate, raise here when scan_result.severity == "critical".
        return scan_result.to_dict()

    @task
    def transform(local_path: str, findings: dict[str, Any]) -> str:
        """GoldenFlow — standardize messy fields based on findings."""
        from pathlib import Path

        import goldenflow
        import polars as pl

        df = pl.read_csv(local_path, encoding="utf8-lossy", ignore_errors=True)
        # Use findings to drive transform selection if available; otherwise zero-config.
        if findings.get("findings"):
            from goldenflow.engine.selector import select_from_findings
            ops = select_from_findings(findings["findings"])
            from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
            config = GoldenFlowConfig(transforms=[
                TransformSpec(column=op["column"], ops=[op["transform"]])
                for op in ops
            ])
            result = goldenflow.transform_df(df, config=config)
        else:
            result = goldenflow.transform_df(df)

        out = Path(local_path).with_name("transformed.parquet")
        result.df.write_parquet(out)
        return str(out)

    @task
    def dedupe(transformed_path: str) -> dict[str, Any]:
        """GoldenMatch — cluster duplicates, build golden records."""
        import goldenmatch
        import polars as pl

        df = pl.read_parquet(transformed_path)
        result = goldenmatch.dedupe_df(
            df,
            exact=MATCH_EXACT_FIELDS,
            fuzzy=MATCH_FUZZY_FIELDS,
            blocking=MATCH_BLOCKING,
            threshold=MATCH_THRESHOLD,
        )

        # Persist outputs to a temp parquet so the load task can stream-read.
        from pathlib import Path
        golden_path = Path(transformed_path).with_name("golden.parquet")
        if result.golden is not None and result.golden.height:
            result.golden.write_parquet(golden_path)

        return {
            "golden_path": str(golden_path),
            "total_records": result.total_records,
            "total_clusters": result.total_clusters,
            "match_rate": float(result.match_rate),
            "duplicates": result.dupes.height if result.dupes is not None else 0,
        }

    @task
    def load_to_s3(dedupe_meta: dict[str, Any], golden_key: str) -> str:
        """Upload golden records to S3 in parquet."""
        from airflow.providers.amazon.aws.hooks.s3 import S3Hook

        S3Hook(aws_conn_id="aws_default").load_file(
            filename=dedupe_meta["golden_path"],
            key=golden_key,
            bucket_name=S3_BUCKET,
            replace=True,
        )
        return f"s3://{S3_BUCKET}/{golden_key}"

    @task
    def emit_metrics(dedupe_meta: dict[str, Any], golden_uri: str, **context) -> None:
        """Write a row to the metrics table for dashboards / alerting."""
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        run_id = context["run_id"]
        ds = context["ds"]
        sql = f"""
            INSERT INTO {METRICS_TABLE}
                (run_id, ds, total_records, total_clusters, duplicates, match_rate, golden_uri)
            VALUES (%(run_id)s, %(ds)s, %(total_records)s, %(total_clusters)s,
                    %(duplicates)s, %(match_rate)s, %(golden_uri)s)
        """
        PostgresHook(postgres_conn_id="postgres_default").run(
            sql,
            parameters={
                "run_id": run_id,
                "ds": ds,
                "total_records": dedupe_meta["total_records"],
                "total_clusters": dedupe_meta["total_clusters"],
                "duplicates": dedupe_meta["duplicates"],
                "match_rate": dedupe_meta["match_rate"],
                "golden_uri": golden_uri,
            },
        )

    # Wire the DAG
    local_path = extract(SOURCE_KEY_TEMPLATE)
    findings = scan(local_path)
    transformed = transform(local_path, findings)
    meta = dedupe(transformed)
    uri = load_to_s3(meta, GOLDEN_KEY_TEMPLATE)
    emit_metrics(meta, uri)


golden_suite_daily_dedupe()
