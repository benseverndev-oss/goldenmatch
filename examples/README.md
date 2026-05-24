# Examples

Runnable demos for the Golden Suite, organized by host.

| Directory | Audience | Highlights |
|---|---|---|
| [`python/`](python/README.md) | Python users | 6 scripts: zero-config quickstart, full Suite composed, customer 360, PPRL, review workflow, MCP client. |
| [`typescript/`](typescript/README.md) | TypeScript / edge users | 4 scripts: quickstart, Vercel-Edge route, MCP client, goldenpipe orchestration. |
| [`sql/`](sql/README.md) | SQL / warehouse users | DuckDB + Postgres core-API + GoldenFlow examples (profile, evaluate, suggest threshold, normalize). |
| [`airflow/`](airflow/README.md) | Data-platform users | 12 drop-in DAGs: daily/incremental/warehouse-native dedupe, customer 360, PPRL, schema align + drift alarm, quality gate, review worker, active learning, reverse ETL, backfill. |

## Where to start

- **Just want to dedupe a CSV?** → [`python/01_quickstart_dedupe.py`](python/01_quickstart_dedupe.py) (3 lines of code) or `npx goldenmatch dedupe customers.csv`.
- **Building a pipeline?** → [`airflow/golden_suite_daily_dedupe.py`](airflow/golden_suite_daily_dedupe.py) for the production-shaped pattern, or [`python/02_full_suite_pipeline.py`](python/02_full_suite_pipeline.py) for a notebook-friendly composed version.
- **Unifying customers across systems?** → [`python/03_multi_source_unify.py`](python/03_multi_source_unify.py) (in-process), [`airflow/golden_suite_customer_360.py`](airflow/golden_suite_customer_360.py) (production).
- **Linking across organizations?** → [`python/04_pprl_two_party.py`](python/04_pprl_two_party.py), [`airflow/golden_suite_pprl_linkage.py`](airflow/golden_suite_pprl_linkage.py).
- **Calling the suite from Claude / agents?** → run the [`goldensuite-mcp`](../packages/python/goldensuite-mcp/README.md) container, then point an MCP client at it: [`python/06_mcp_client.py`](python/06_mcp_client.py) or [`typescript/03-mcp-client.ts`](typescript/03-mcp-client.ts).
- **TypeScript / edge runtime?** → [`typescript/01-quickstart.ts`](typescript/01-quickstart.ts), [`typescript/02-edge-runtime.ts`](typescript/02-edge-runtime.ts). Orchestrating the whole suite? → [`typescript/04-goldenpipe-orchestration.ts`](typescript/04-goldenpipe-orchestration.ts).
- **Working in SQL / a warehouse?** → [`sql/duckdb_core_apis.sql`](sql/duckdb_core_apis.sql) or [`sql/postgres_core_apis.sql`](sql/postgres_core_apis.sql) for the SQL-native core API + GoldenFlow transforms.

## Convention

Every example is **standalone and small** — pick one, copy it, adapt it. The Airflow DAGs are production-shaped (idempotent, observable, fail-loud); the Python and TypeScript scripts are notebook-shaped (toy data inline, no I/O assumptions). Don't deploy the scripts as-is — promote them into a real pipeline shape (see Airflow examples for the reference target).
