# Python usage examples

Cross-suite, runnable scripts. Each is standalone — pick the one closest to your scenario, adapt to your data shape, ship.

| File | What | Imports |
|---|---|---|
| `01_quickstart_dedupe.py` | 30-second zero-config dedupe of a CSV | `goldenmatch` |
| `02_full_suite_pipeline.py` | Manually compose Check → Flow → Match. Useful when you need to inspect or branch on intermediate results. | `goldencheck`, `goldenflow`, `goldenmatch` |
| `03_multi_source_unify.py` | Customer 360: align heterogeneous source schemas with InferMap, standardize, multi-pass dedupe the union. | `infermap`, `goldenflow`, `goldenmatch` |
| `04_pprl_two_party.py` | Privacy-preserving record linkage between two parties. Bloom-filter encoding, no raw PII shared. | `goldenmatch[pprl]` |
| `05_review_workflow.py` | Borderline-pair review queue + Learning Memory feedback loop in-process. | `goldenmatch[memory]` |
| `06_mcp_client.py` | Connect to a `goldensuite-mcp` container from a Python MCP client. | `mcp` |
| `07_web_ui_walkthrough.py` | Drive every web workbench endpoint from Python: rules, preview, compare runs (CCMS), sensitivity sweep, match, memory store, label round-trip, learn pass. Useful for scripting bulk ops or smoke-testing a deployment. | `goldenmatch[web]`, `requests` |
| `08_identity_graph.py` | Six-act tour of the v1.15 Identity Graph: stable `entity_id` across runs, absorb on rerun, cross-source matching, conflict edge for review, manual merge, manual split, full event-log audit. | `goldenmatch` |

## Sample data

Examples 03–05 ship with toy DataFrames inline so they're hermetic. For 01 and 02 you'll need a real CSV — any small customer file with name + email fields works.

If you don't have one handy, generate a tiny fixture:

```python
import polars as pl
pl.DataFrame({
    "first_name": ["Jane", "Jane", "Robert", "Bob",   "Alice"],
    "last_name":  ["Smith", "Smyth", "Jones", "Jones", "Lee"],
    "email":      ["jane@example.com", "jane@example.com",
                   "bob@example.com",  "robert.j@example.com",
                   "alice@example.com"],
    "zip":        ["10001", "10001", "94110", "94110", "60601"],
}).write_csv("customers.csv")
```

## When to use what

- **One-off dedupe of a file** → 01.
- **Pipeline you'll re-run** → 02 if you need stage-level inspection, or `goldenpipe` if you don't.
- **Many sources, one canonical entity per real person** → 03.
- **Linking across organizations without sharing raw data** → 04.
- **Closing a feedback loop with humans in the loop** → 05.
- **Calling Suite tools from outside Python (Claude Desktop, an agent, a notebook)** → 06 + the deployed `goldensuite-mcp` container.
- **Editing rules, comparing runs, labeling pairs in a browser** → run `goldenmatch serve-ui <project>` and use the workbench directly. To script the same surface, see 07.

## Going to production

Each example is intentionally minimal. For production:

- **Move I/O to your data layer** — read from S3 / Snowflake / Postgres rather than local CSV.
- **Run as Airflow DAGs** — `examples/airflow/` has 12 production-shaped DAGs that wrap these patterns with retries, idempotency, and observability.
- **Pin match config** — keep your tuned `GoldenMatchConfig` in YAML and check it in. `goldenpipe` reads YAML directly.
- **Add review queue + Learning Memory** — see 05 + the `golden_suite_review_worker.py` Airflow DAG for the loop.
