---
layout: default
title: Identity Graph
nav_order: 20
---

# Identity Graph

GoldenPipe v1.2 adds first-class orchestration of the GoldenMatch v1.15 Identity Graph — the durable, queryable graph of entities + evidence + events that survives across pipeline runs.

The Pipeline picks up a new stage, `goldenmatch.identity_resolve`, that takes the cluster output of dedupe and persists it into an identity store. Subsequent runs against the same store get stable `entity_id`s and an audit trail of how identities formed.

---

## Quickstart

Two equivalent paths — CLI and Python — both opt-in:

```bash
goldenpipe run customers.csv \
  --identity-path .goldenmatch/identity.db \
  --identity-source-pk customer_id \
  --identity-dataset customers
```

```python
import goldenpipe as gp

result = gp.run(
    "customers.csv",
    identity_opts={
        "path": ".goldenmatch/identity.db",
        "source_pk_column": "customer_id",
        "dataset": "customers",
    },
)
print(result.artifacts["identity_summary"])
# {'created': 12, 'absorbed_records': 0, 'merged': 0, 'split': 0,
#  'edges_added': 27, 'events_emitted': 12, 'records_upserted': 100,
#  'conflicts_flagged': 0}
```

In both cases, the auto-config path appends `goldenmatch.identity_resolve` after dedupe with your config as its `stage_config`. Without `--identity-path` (or `identity_opts=None`), the stage is omitted — backwards-compatible.

---

## When to use GoldenPipe vs direct GoldenMatch

**Use GoldenPipe's identity stage when:**

- You're running the full `Check -> Flow -> Match -> Identity` chain and want one CLI / Airflow DAG to own it end-to-end.
- You want a unified `PipeResult.artifacts` dict that includes `golden` records and `identity_summary` side by side.
- You're shipping an Airflow DAG — the existing `examples/airflow/golden_suite_identity_graph.py` shape works for any GoldenPipe-orchestrated graph.

**Use direct GoldenMatch `dedupe_df(config=...identity:...)` when:**

- You're embedding identity resolution inside a library or service.
- You need `match_one()` real-time matching against an existing graph (GoldenPipe is batch-only).
- You're already inside a non-GoldenPipe workflow (dbt, a custom Polars pipeline).

Both paths talk to the same `IdentityStore` and produce the same JSON shape across every surface.

---

## CLI flags

| Flag | Maps to `IdentityConfig` | Default |
|---|---|---|
| `--identity-path` | `path` | required to enable identity |
| `--identity-dataset` | `dataset` | None |
| `--identity-source-pk` | `source_pk_column` | None (falls back to row-hash record_id) |
| `--identity-weak-threshold` | `weak_confidence_threshold` | 0.6 |

When `--identity-path` is omitted, no identity stage is added. When YAML config is supplied via `--config`, the YAML wins and CLI identity flags are ignored.

---

## YAML config equivalent

```yaml
pipeline: identity-customers
stages:
  - use: goldencheck.scan
  - use: goldenflow.transform
  - use: goldenmatch.dedupe
    config:
      matchkeys: [...]
      blocking: { strategy: static, keys: [{ fields: [zip] }] }
  - use: goldenmatch.identity_resolve
    config:
      path: .goldenmatch/identity.db
      dataset: customers
      source_pk_column: customer_id
      weak_confidence_threshold: 0.6
```

---

## What lands in `PipeResult.artifacts`

| Key | Type | Source |
|---|---|---|
| `clusters` | `dict[int, dict]` | DedupeStage |
| `golden` / `unique` / `dupes` | `pl.DataFrame` | DedupeStage |
| `match_stats` | `dict` | DedupeStage |
| `scored_pairs` | `list[(id_a, id_b, score)]` | DedupeStage (v1.2+) |
| `matchkey_used` | `str` | DedupeStage (v1.2+) |
| `identity_summary` | `dict` | IdentityResolveStage |
| `identity_store_path` | `str` | IdentityResolveStage |
| `conflicts` | `int` | IdentityResolveStage (`conflicts_flagged` count) |

`identity_summary` has counters from `ResolveSummary.as_dict()`: `created`, `absorbed_records`, `merged`, `split`, `edges_added`, `events_emitted`, `records_upserted`, `conflicts_flagged`.

---

## Airflow

`examples/airflow/golden_suite_identity_graph.py` is a production-shaped daily DAG. It:

1. Pulls the day's source CSV from S3.
2. Pulls the canonical identity store from S3 (starts fresh on first run).
3. Runs the full chain.
4. Pushes the updated store + golden records back to S3.
5. Surfaces `conflicts_flagged` as XCom for the `golden_suite_review_worker.py` DAG.

For high-volume / multi-writer setups, switch the backend to Postgres via `IdentityConfig.backend = "postgres"` + `connection`.

---

## Determinism

Two runs against the same input + same identity store produce identical `entity_id` sets. The v1.15 controller's `has_run_event(run_name, kind)` guard makes the resolve step idempotent — replaying the same `metadata['run_id']` is a no-op.

Test contracts: `tests/test_identity_stage.py::test_two_runs_produce_stable_entity_ids` and `tests/test_identity_cli.py::test_identity_path_persists_across_two_runs`.

---

## See also

- `examples/airflow/golden_suite_identity_graph.py`
- [GoldenMatch Identity Graph docs](https://benzsevern.github.io/goldenmatch/identity-graph)
- `docs/superpowers/specs/2026-05-13-goldenpipe-v1.2-identity-orchestration-design.md` — the spec this implements
