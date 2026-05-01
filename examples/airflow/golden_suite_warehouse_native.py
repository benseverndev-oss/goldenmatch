"""Warehouse-native dedupe — read from Snowflake, dedupe, write back. No S3 hop.

Variant of `golden_suite_daily_dedupe` for teams whose source-of-truth is
already in a warehouse. GoldenMatch ships first-party connectors for
Snowflake, BigQuery, and Databricks; this example shows the Snowflake path.
The same shape works for the others — swap the connector + dialect.

Tunable: warehouse choice (replace SnowflakeConnector with BigQueryConnector
or DatabricksConnector), source query, target table, match config.

Requires:
    pip install apache-airflow goldenmatch[snowflake] \\
                apache-airflow-providers-snowflake polars
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pendulum
from airflow.decorators import dag, task

SNOWFLAKE_CONN_ID = "snowflake_default"

SOURCE_QUERY = "SELECT * FROM raw.customers WHERE _ingested_at::date = '{{ ds }}'"
TARGET_TABLE = "warehouse.customers_golden"
RUNS_TABLE = "analytics.golden_suite_warehouse_runs"


@dag(
    dag_id="golden_suite_warehouse_native",
    description="Snowflake-native daily dedupe. No S3 hop.",
    schedule="30 4 * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "data-platform", "retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["golden-suite", "snowflake", "warehouse-native"],
)
def golden_suite_warehouse_native():
    """Warehouse-native dedupe via the Snowflake connector."""

    @task
    def fetch() -> str:
        """Pull the day's source rows directly from Snowflake into a local parquet."""
        from pathlib import Path

        from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook
        import polars as pl

        df = pl.from_pandas(
            SnowflakeHook(snowflake_conn_id=SNOWFLAKE_CONN_ID).get_pandas_df(SOURCE_QUERY)
        )
        if df.is_empty():
            raise ValueError(f"Source query returned 0 rows: {SOURCE_QUERY}")

        out = Path("/tmp/golden_suite/warehouse/source.parquet")
        out.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(out)
        return str(out)

    @task
    def transform(source_path: str) -> str:
        """GoldenFlow standardize."""
        from pathlib import Path

        import goldenflow
        import polars as pl

        df = pl.read_parquet(source_path)
        result = goldenflow.transform_df(df)
        out = Path(source_path).with_name("transformed.parquet")
        result.df.write_parquet(out)
        return str(out)

    @task
    def dedupe(transformed_path: str) -> dict[str, Any]:
        """GoldenMatch — multi-pass blocking + ensemble scoring."""
        from pathlib import Path

        import goldenmatch
        import polars as pl
        from goldenmatch.config.schemas import (
            GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
            BlockingConfig, BlockingKeyConfig,
        )

        df = pl.read_parquet(transformed_path)
        config = GoldenMatchConfig(
            blocking=BlockingConfig(
                strategy="multi_pass",
                keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase", "strip"])],
                passes=[
                    BlockingKeyConfig(fields=["email"], transforms=["lowercase", "strip"]),
                    BlockingKeyConfig(fields=["last_name"], transforms=["soundex"]),
                ],
            ),
            matchkeys=[MatchkeyConfig(
                name="identity", type="weighted", threshold=0.85,
                fields=[
                    MatchkeyField(field="first_name", scorer="ensemble", weight=0.7,
                                  transforms=["lowercase", "strip"]),
                    MatchkeyField(field="last_name", scorer="ensemble", weight=0.9,
                                  transforms=["lowercase", "strip"]),
                    MatchkeyField(field="email", scorer="jaro_winkler", weight=1.0,
                                  transforms=["lowercase", "strip"]),
                ],
            )],
        )
        result = goldenmatch.dedupe_df(df, config=config)
        out = Path(transformed_path).with_name("golden.parquet")
        if result.golden is not None:
            result.golden.write_parquet(out)
        return {
            "path": str(out),
            "total_records": result.total_records,
            "total_clusters": result.total_clusters,
            "match_rate": float(result.match_rate),
            "duplicates": result.dupes.height if result.dupes is not None else 0,
        }

    @task
    def write_to_snowflake(meta: dict[str, Any]) -> int:
        """Truncate-and-load is the simplest atomic pattern. For prod, prefer staging+swap."""
        from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook
        import polars as pl

        df = pl.read_parquet(meta["path"])
        if df.is_empty():
            return 0

        hook = SnowflakeHook(snowflake_conn_id=SNOWFLAKE_CONN_ID)
        # Stage data via the hook's pandas path. For larger volumes use COPY INTO from S3
        # (you'd write parquet to a Snowflake-stage S3 bucket and execute COPY).
        hook.run(f"TRUNCATE TABLE {TARGET_TABLE}")
        hook.insert_rows(table=TARGET_TABLE,
                         rows=[tuple(row.values()) for row in df.iter_rows(named=True)],
                         target_fields=df.columns)
        return df.height

    @task
    def emit_metrics(meta: dict[str, Any], rows_written: int, **context) -> None:
        from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook

        SnowflakeHook(snowflake_conn_id=SNOWFLAKE_CONN_ID).run(
            f"""
            INSERT INTO {RUNS_TABLE}
                (run_id, ds, total_records, total_clusters, duplicates,
                 match_rate, rows_written, created_at)
            VALUES (%(rid)s, %(ds)s, %(tr)s, %(tc)s, %(d)s, %(mr)s, %(rw)s, CURRENT_TIMESTAMP())
            """,
            parameters={
                "rid": context["run_id"],
                "ds": context["ds"],
                "tr": meta["total_records"],
                "tc": meta["total_clusters"],
                "d": meta["duplicates"],
                "mr": meta["match_rate"],
                "rw": rows_written,
            },
        )

    src = fetch()
    cleaned = transform(src)
    meta = dedupe(cleaned)
    written = write_to_snowflake(meta)
    emit_metrics(meta, written)


golden_suite_warehouse_native()
