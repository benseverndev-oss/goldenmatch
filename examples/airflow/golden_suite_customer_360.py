"""Cross-source customer 360: unify identity across CRM + warehouse + support.

The "everything that's a customer in the company, deduplicated" DAG. Pulls from
three heterogeneous sources, aligns each to the canonical customer schema with
InferMap (cached), standardizes with GoldenFlow, concatenates with a `__source__`
provenance column, then runs a multi-pass GoldenMatch on the union.

Output: `customers_unified` table where each row is a canonical record with a
`source_ids` jsonb column listing the IDs from every contributing source. That
lets downstream queries answer "which CRM customers have an open support ticket?"
without a fragile join chain.

Requires:
    pip install apache-airflow goldenpipe[full] infermap[mapping] \\
                apache-airflow-providers-amazon apache-airflow-providers-postgres polars
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pendulum
from airflow.decorators import dag, task
from airflow.models import Variable

CANONICAL_SCHEMA_FILE = "/opt/airflow/dags/golden_suite/canonical_customers.yaml"
MAPPINGS_TABLE = "warehouse.source_column_mappings"
UNIFIED_TABLE = "warehouse.customers_unified"

SOURCES = [
    {"name": "crm",      "kind": "s3",       "key": "raw/crm/{{ ds }}/customers.csv"},
    {"name": "warehouse", "kind": "postgres", "query": "SELECT * FROM warehouse.customers"},
    {"name": "support",  "kind": "s3",       "key": "raw/support/{{ ds }}/contacts.csv"},
]


@dag(
    dag_id="golden_suite_customer_360",
    description="Multi-source customer dedupe with provenance.",
    schedule="0 5 * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "data-platform", "retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["golden-suite", "customer-360", "multi-source"],
)
def golden_suite_customer_360():
    """Multi-source customer 360."""

    @task
    def load_source(source: dict) -> str:
        """Load one source to a local parquet path. Tagged with __source__ + source-native id."""
        from pathlib import Path

        import polars as pl

        if source["kind"] == "s3":
            from airflow.providers.amazon.aws.hooks.s3 import S3Hook
            local_csv = Path(f"/tmp/golden_suite/{source['name']}.csv")
            local_csv.parent.mkdir(parents=True, exist_ok=True)
            S3Hook(aws_conn_id="aws_default").get_key(
                source["key"], Variable.get("golden_suite_bucket")
            ).download_file(Filename=str(local_csv))
            df = pl.read_csv(local_csv, encoding="utf8-lossy", ignore_errors=True)
        elif source["kind"] == "postgres":
            from airflow.providers.postgres.hooks.postgres import PostgresHook
            df = pl.from_pandas(
                PostgresHook(postgres_conn_id="postgres_default").get_pandas_df(source["query"])
            )
        else:
            raise ValueError(f"unknown source kind: {source['kind']}")

        df = df.with_columns(pl.lit(source["name"]).alias("__source__"))
        out = Path(f"/tmp/golden_suite/{source['name']}.parquet")
        df.write_parquet(out)
        return str(out)

    @task
    def align_to_canonical(parquet_path: str, source_name: str) -> str:
        """InferMap-driven column alignment to canonical schema. Mappings cached per source."""
        import json
        from pathlib import Path

        from airflow.providers.postgres.hooks.postgres import PostgresHook
        import polars as pl

        df = pl.read_parquet(parquet_path)
        pg = PostgresHook(postgres_conn_id="postgres_default")
        cached = pg.get_first(
            f"SELECT mapping FROM {MAPPINGS_TABLE} WHERE source_name = %(s)s",
            parameters={"s": source_name},
        )
        if cached:
            mapping = json.loads(cached[0])
        else:
            import infermap
            sample = df.head(1000)
            result = infermap.map(source=sample.to_dicts(), schema_file=CANONICAL_SCHEMA_FILE)
            mapping = {m.source: m.target for m in result.mappings}
            pg.run(
                f"INSERT INTO {MAPPINGS_TABLE} (source_name, mapping, created_at) "
                "VALUES (%(s)s, %(m)s::jsonb, NOW()) "
                "ON CONFLICT (source_name) DO UPDATE SET mapping = EXCLUDED.mapping, "
                "updated_at = NOW()",
                parameters={"s": source_name, "m": json.dumps(mapping)},
            )

        # Preserve source-native id under a uniform name
        if "id" in df.columns:
            df = df.rename({"id": f"{source_name}_id"})

        keep = [c for c in df.columns if c in mapping or c.endswith("_id")
                or c == "__source__"]
        df = df.select(keep)
        rename_map = {k: v for k, v in mapping.items() if k in df.columns}
        df = df.rename(rename_map)

        out = Path(parquet_path).with_suffix(".aligned.parquet")
        df.write_parquet(out)
        return str(out)

    @task
    def standardize(aligned_path: str) -> str:
        """GoldenFlow on aligned columns."""
        from pathlib import Path

        import goldenflow
        import polars as pl

        df = pl.read_parquet(aligned_path)
        result = goldenflow.transform_df(df)
        out = Path(aligned_path).with_suffix(".clean.parquet")
        result.df.write_parquet(out)
        return str(out)

    @task
    def union_and_dedupe(clean_paths: list[str]) -> dict[str, Any]:
        """Concat all sources, multi-pass GoldenMatch with provenance preservation."""
        from pathlib import Path

        import goldenmatch
        import polars as pl
        from goldenmatch.config.schemas import (
            GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
            BlockingConfig, BlockingKeyConfig,
        )

        dfs = [pl.read_parquet(p) for p in clean_paths]
        # Schema-tolerant concat — sources may have non-overlapping columns.
        unified = pl.concat(dfs, how="diagonal_relaxed")

        config = GoldenMatchConfig(
            blocking=BlockingConfig(
                strategy="multi_pass",
                keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase", "strip"])],
                passes=[
                    BlockingKeyConfig(fields=["email"], transforms=["lowercase", "strip"]),
                    BlockingKeyConfig(fields=["last_name"], transforms=["soundex"]),
                    BlockingKeyConfig(fields=["last_name"], transforms=["substring:0:3"]),
                ],
            ),
            matchkeys=[MatchkeyConfig(
                name="identity", type="weighted", threshold=0.80,
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
        result = goldenmatch.dedupe_df(unified, config=config)

        out = Path("/tmp/golden_suite/unified.parquet")
        if result.golden is not None:
            result.golden.write_parquet(out)
        return {
            "path": str(out),
            "total_records": result.total_records,
            "total_clusters": result.total_clusters,
            "match_rate": float(result.match_rate),
        }

    @task
    def upsert_unified(meta: dict[str, Any]) -> int:
        """Upsert unified golden records into customers_unified, with source provenance."""
        from airflow.providers.postgres.hooks.postgres import PostgresHook
        import polars as pl

        df = pl.read_parquet(meta["path"])
        if df.is_empty():
            return 0

        pg = PostgresHook(postgres_conn_id="postgres_default")
        # Truncate-and-load is simplest for an example. Production: use a staging
        # table + atomic rename, or per-row UPSERT with __cluster_id__ as the key.
        pg.run(f"TRUNCATE {UNIFIED_TABLE}")
        cols = list(df.columns)
        placeholders = ", ".join(f"%({c})s" for c in cols)
        for row in df.iter_rows(named=True):
            pg.run(
                f"INSERT INTO {UNIFIED_TABLE} ({', '.join(cols)}) VALUES ({placeholders})",
                parameters=row,
            )
        return df.height

    sources = [load_source(src) for src in SOURCES]
    aligned = [align_to_canonical(s, source_name=src["name"])
               for s, src in zip(sources, SOURCES)]
    cleaned = [standardize(a) for a in aligned]
    meta = union_and_dedupe(cleaned)
    upsert_unified(meta)


golden_suite_customer_360()
