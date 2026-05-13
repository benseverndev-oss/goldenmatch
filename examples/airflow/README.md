# Airflow DAGs for the Golden Suite

Drop-in DAG examples that wire the Golden Suite into a real Airflow deployment. Copy the file you want into your Airflow `dags/` folder, adjust the knobs at the top, and ship.

These are **examples**, not a published DAG library. Read them, adapt them, and own them — every team's data shape, connection set, and SLAs are different.

## DAGs

### Core pipeline

| File | What it does | Cadence | Trigger |
|---|---|---|---|
| `golden_suite_daily_dedupe.py` | S3 CSV → Check → Flow → Match → S3 + metrics. The bread-and-butter daily ingestion DAG. | Daily 04:00 UTC | Scheduled |
| `golden_suite_incremental_match.py` | Poll `incoming/` S3 prefix every 15 min. For each new file, `match_one` against canonical. Auto-merge ≥0.95, queue 0.75–0.95, append <0.75. | Every 15 min | Scheduled |
| `golden_suite_warehouse_native.py` | Snowflake-native variant of daily_dedupe. No S3 hop — read source query, dedupe, write back. Same shape works for BigQuery / Databricks via the goldenmatch connectors. | Daily 04:30 UTC | Scheduled |
| `golden_suite_customer_360.py` | Multi-source unify: CRM + warehouse + support → one canonical customer table. InferMap aligns each source, multi-pass GoldenMatch dedupes the union, source provenance preserved. | Daily 05:00 UTC | Scheduled |
| `golden_suite_identity_graph.py` | **v1.15+ Identity Graph.** Check → Flow → Match → Identity Graph chain. Maintains a durable identity store on S3-synced shared storage; subsequent runs see stable `entity_id`s. Surfaces `conflicts_flagged` as XCom for the review-worker DAG. | Daily 05:00 UTC | Scheduled |

### Privacy

| File | What it does | Cadence | Trigger |
|---|---|---|---|
| `golden_suite_pprl_linkage.py` | Trusted-third-party PPRL across two parties. Encoded Bloom shards in, ID-pair matches out. Raw PII never crosses the boundary. | Weekly | Scheduled |

### Onboarding & monitoring

| File | What it does | Cadence | Trigger |
|---|---|---|---|
| `golden_suite_schema_align_and_load.py` | New-source onboarding: InferMap → cache mapping → GoldenFlow → upsert. Fails loudly below confidence threshold. | None | Manual (`conf` overrides) |
| `golden_suite_schema_drift_alarm.py` | Daily compare current source columns to cached InferMap mapping. Alert + log on new / removed / renamed columns. Never auto-updates the cached mapping. | Daily | Scheduled |
| `golden_suite_quality_gate.py` | GoldenCheck as **gatekeeper** (not preprocessor). Threshold-based: any check exceeding its budget fails the DAG, blocking dependent Datasets. | Daily 03:00 UTC | Scheduled |

### Feedback loop

| File | What it does | Cadence | Trigger |
|---|---|---|---|
| `golden_suite_review_worker.py` | Apply steward decisions on review queue → merge/append in canonical → record into learning memory. Closes the loop on `incremental_match`. | Every 5 min | Scheduled |
| `golden_suite_active_learning.py` | Retrain GoldenMatch boost classifier from labeled review-queue pairs. Promote to `current.yaml` only if F1 strictly beats the running model. | Weekly Sun 03:00 UTC | Scheduled |

### Operationalize

| File | What it does | Cadence | Trigger |
|---|---|---|---|
| `golden_suite_reverse_etl.py` | Push deduped golden records out to Salesforce + HubSpot, watermark-incremental. The "data lake → operational systems" half of the loop. | Every 2 hours | Scheduled |
| `golden_suite_backfill.py` | Reprocess N days of history with current match config when a tuning change makes existing golden records stale. Dynamic task mapping over date range; cap at MAX_PARALLEL=8. Outputs land in `_backfill/<run-id>/` so daily output isn't disturbed. | None | Manual (`conf` overrides date range) |

## Prerequisites

Each DAG declares its own pip deps in its docstring. Common stack:

```bash
pip install apache-airflow>=2.7 polars
pip install goldenpipe[full] goldenmatch[postgres,pprl] goldencheck goldenflow infermap
pip install apache-airflow-providers-amazon apache-airflow-providers-postgres
```

Compatible with Airflow 2.7+ via TaskFlow API. Tested at 2.10. Should run on 3.x — the only known caveat is HITL operators (not used here).

## Connections (Airflow UI → Admin → Connections)

| Conn ID | Type | Used by |
|---|---|---|
| `aws_default` | Amazon Web Services | Most DAGs (S3 read/write, active_learning config snapshots) |
| `postgres_default` | Postgres | daily_dedupe (metrics), incremental_match + review_worker (canonical + review queue), pprl_linkage (audit), schema_align + drift_alarm (mappings), customer_360 (unified), reverse_etl (watermarks), backfill (audit), schema_drift_alarm (drift events) |
| `snowflake_default` | Snowflake | warehouse_native only |
| `salesforce_default` | Salesforce | reverse_etl (CRM destination) |
| `hubspot_default` | HTTP / generic | reverse_etl (CRM destination — token in the password field) |

## Variables (Airflow UI → Admin → Variables)

| Key | Example | Used by |
|---|---|---|
| `golden_suite_bucket` | `acme-data-lake` | All four DAGs |

## Tables you'll need

```sql
-- Daily run summary (golden_suite_daily_dedupe)
CREATE TABLE analytics.golden_suite_runs (
    run_id text, ds date, total_records int, total_clusters int,
    duplicates int, match_rate float, golden_uri text,
    created_at timestamptz DEFAULT now()
);

-- Canonical store (incremental_match, schema_align)
CREATE TABLE warehouse.customers_canonical (
    id bigserial primary key,
    -- … your canonical columns here …
    last_seen timestamptz DEFAULT now(),
    merge_count int DEFAULT 0
);

-- Human review queue (incremental_match)
CREATE TABLE warehouse.customers_review_queue (
    id bigserial primary key,
    payload jsonb,
    candidate_id bigint references warehouse.customers_canonical(id),
    score float,
    queued_at timestamptz,
    decided_at timestamptz,
    decision text  -- approve | reject
);

-- PPRL audit (pprl_linkage)
CREATE TABLE audit.pprl_runs (
    run_id text, ds date,
    party_a_rows int, party_b_rows int, match_count int, match_rate_a float,
    threshold float, security_level text, result_uri text,
    created_at timestamptz DEFAULT now()
);

-- InferMap mapping cache (schema_align, customer_360, schema_drift_alarm)
CREATE TABLE warehouse.source_column_mappings (
    source_name text primary key,
    mapping jsonb,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz
);

-- Multi-source canonical (customer_360)
CREATE TABLE warehouse.customers_unified (
    -- canonical columns + per-source IDs the customer_360 DAG preserves
    -- (e.g., crm_id, warehouse_id, support_id) + last_seen, merge_count, source_ids jsonb
);

-- Review queue extras (review_worker depends on these columns)
ALTER TABLE warehouse.customers_review_queue
    ADD COLUMN claimed_at timestamptz,
    ADD COLUMN applied_at timestamptz;

-- Reverse ETL watermarks (reverse_etl)
CREATE TABLE audit.reverse_etl_watermarks (
    destination text primary key,
    updated_at timestamptz NOT NULL
);

-- Backfill audit (backfill)
CREATE TABLE analytics.golden_suite_backfill_runs (
    backfill_run_id text, ds date,
    total_records int, total_clusters int, match_rate float,
    scan_findings int, out_uri text, skipped boolean, skip_reason text,
    created_at timestamptz DEFAULT now()
);

-- Schema drift events (schema_drift_alarm)
CREATE TABLE audit.schema_drift_events (
    source_name text, new_columns text[], removed_columns text[],
    renamed jsonb, detected_at timestamptz DEFAULT now()
);

-- Warehouse-native runs (warehouse_native — Snowflake DDL)
CREATE TABLE analytics.golden_suite_warehouse_runs (
    run_id varchar, ds date,
    total_records int, total_clusters int, duplicates int,
    match_rate float, rows_written int,
    created_at timestamp_tz DEFAULT current_timestamp()
);
```

## Knobs you'll tune

Each DAG file has a constants block at the top. Most common edits:

- **`MATCH_*` thresholds** (daily / incremental) — lower for high recall, higher for high precision. Run on a labeled sample first.
- **`AUTO_MERGE_THRESHOLD` / `REVIEW_THRESHOLD`** (incremental) — depends on your tolerance for false-positive merges. Most teams start at 0.95 / 0.75.
- **`SECURITY_LEVEL`** (PPRL) — `standard` for fastest, `paranoid` for highest privacy guarantees. `high` is a good default.
- **`MIN_MAPPING_CONFIDENCE`** (schema align) — raise this to force humans to validate any uncertain mapping.

## Pattern notes

- Every task is **idempotent or marker-protected** (the incremental DAG renames processed S3 keys to `*.processed`). Safe to retry.
- DAGs **fail loudly on empty input** — silent zero-row processing has bitten enough data teams that I'd rather a red square than a missed dedupe.
- `goldenflow.transform_df` is called on **already-loaded Polars DataFrames**, not file paths. Lets you compose with whatever in-memory transforms you want before/after.
- **No cross-DAG XComs of large payloads** — outputs go through S3 / Postgres so each task can be retried independently and the scheduler isn't ferrying parquet blobs.
- **Connection / Variable names are conventional defaults** (`aws_default`, `postgres_default`, `golden_suite_bucket`). Rename to match your platform's standards.

## Suite-wide alternative: GoldenPipe

If your pipeline is "Check → Flow → Match" with no intermediate custom logic, GoldenPipe collapses the whole thing into one declarative config:

```python
import goldenpipe as gp
result = gp.Pipeline.from_yaml("pipeline.yaml").run("data.csv")
```

You'd wrap that in a single `@task` and skip the per-stage breakdown the example DAGs demonstrate. Use the explicit-stage pattern when you need stage-level retries, mid-pipeline branching, or to inject your own logic between Suite tools.

See [`packages/python/goldenpipe`](../../packages/python/goldenpipe/README.md) for GoldenPipe details.

## Status reporting

If you want each DAG run to push status to the team:
- **Slack on success/failure** — wire `on_success_callback` / `on_failure_callback` at the DAG level.
- **Cluster-quality alerting** — `goldenmatch` ships per-cluster confidence; surface "weak" clusters from the daily run as a separate task that posts to a review channel.

These hooks are intentionally *not* in the examples — they vary too much per org. Add them where you need them.
