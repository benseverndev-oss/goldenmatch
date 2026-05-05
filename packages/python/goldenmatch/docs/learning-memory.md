---
layout: default
title: Learning Memory
nav_order: 19
---

# Learning Memory

GoldenMatch can remember past steward decisions and apply them automatically on every subsequent run. Reject a pair once -- it stays rejected. Approve a borderline pair once -- it stays approved. After enough corrections accumulate, the learner adjusts matchkey thresholds so the system stops needing the same correction twice.

This is the third layer that sits beside zero-config and explicit YAML: a feedback loop that survives input refresh and re-orders, with no rules to write and no models to train.

> **Status.** Shipped in v1.6.0. Off by default -- the zero-config posture is preserved. Enable via `config.memory.enabled = True` or a `memory:` block in YAML.

---

## What it does

Learning Memory is a persistent store of `(id_a, id_b, decision)` corrections plus a learner that turns enough corrections into threshold adjustments.

- **Pipeline applies corrections automatically.** `dedupe_df` and `match_df` apply stored corrections after scoring (hard `1.0` for approve, hard `0.0` for reject) and overlay learned threshold deltas before scoring.
- **Re-anchors via `record_hash`.** Corrections survive row reordering and refresh. If a correction's row IDs are no longer present, the system looks the entity up by content hash. Ambiguous rehydrations (duplicate rows) report as `stale_ambiguous` rather than silently misapplying.
- **Seven collection points.** Every place a steward, an LLM, or an agent makes a decision writes a correction: review queue, boost tab, `unmerge_record` / `unmerge_cluster`, LLM scorer, MCP `agent_approve_reject`, REST `POST /reviews/decide`, Python `add_correction()`.
- **Threshold learning.** Once a matchkey accumulates `threshold_min_corrections` (default 10) corrections, the learner runs a trust-weighted grid search and stores per-matchkey threshold deltas. The pipeline overlays them on the next run.
- **Postflight reports impact.** Every run with memory active emits `Memory: N applied, M stale, K stale-ambiguous, J unanchorable`.

---

## Quick walkthrough

Three commands. The data and the config don't change between runs -- the system improves because it remembers.

**`goldenmatch.yml`:**

```yaml
matchkeys:
  - name: identity
    type: weighted
    threshold: 0.85
    fields:
      - field: name
        scorer: jaro_winkler
        transforms: [lowercase, strip]
        weight: 1.0
      - field: email
        scorer: exact
        weight: 1.0

blocking:
  strategy: static
  keys:
    - fields: [zip]
      transforms: [lowercase]

memory:
  enabled: true
  backend: sqlite
  path: .goldenmatch/memory.db
  reanchor: true
  dataset: customers
  learning:
    threshold_min_corrections: 10
    weights_min_corrections: 50
```

**Run 1 -- produce the review queue.** Memory is empty, no corrections apply.

```bash
goldenmatch dedupe customers.csv --config goldenmatch.yml
```

**Run 2 -- the steward decides.** The interactive review TUI writes approve / reject decisions to `.goldenmatch/memory.db` with `source=steward, trust=1.0`.

```bash
goldenmatch review --config goldenmatch.yml
```

**Run 3 -- corrections apply automatically.** Same data, same config; the pipeline reads memory, hard-overrides scored pairs, and reports impact in postflight.

```bash
goldenmatch dedupe customers.csv --config goldenmatch.yml
# > Memory: 12 corrections applied, 0 stale, 0 stale-ambiguous, 0 unanchorable
```

After 10+ corrections accumulate against a matchkey, `goldenmatch memory learn` (or the auto-learn pass on the next pipeline call) tunes that matchkey's threshold so future runs need fewer corrections.

---

## Configuration

`MemoryConfig` lives at `config.memory`. Top-level YAML:

```yaml
memory:
  enabled: true                 # default: false. Off => no memory work, zero overhead.
  backend: sqlite               # sqlite | postgres
  path: .goldenmatch/memory.db  # sqlite path or postgres DSN
  reanchor: true                # default: true. Set false to require exact (id_a, id_b) match.
  dataset: customers            # tag corrections; isolates per-table memory in shared DBs
  learning:
    threshold_min_corrections: 10   # learner runs once per matchkey at this floor
    weights_min_corrections: 50     # field-weight learning floor (stub in v1.6, returns None)
```

| Field | Default | Notes |
|---|---|---|
| `enabled` | `false` | Zero-config preserved. Enabling does not change pipeline output until corrections exist. |
| `backend` | `"sqlite"` | `"postgres"` requires `pip install goldenmatch[postgres]`. |
| `path` | `".goldenmatch/memory.db"` | SQLite file or full DSN for postgres. |
| `reanchor` | `true` | Re-anchor by `record_hash` when row IDs miss. Disable for strictly positional behavior. |
| `dataset` | `None` | Use one DB across multiple tables; the pipeline filters corrections by dataset tag. |
| `learning.threshold_min_corrections` | `10` | Trust-weighted grid search runs once a matchkey crosses this floor. |
| `learning.weights_min_corrections` | `50` | Field-weight learning is stubbed in v1.6.0 and returns `None`. |

Postgres backend:

```yaml
memory:
  enabled: true
  backend: postgres
  path: postgresql://user:pass@host:5432/db
  dataset: customers_prod
```

---

## CLI

The `goldenmatch memory` subgroup exposes the store directly.

```bash
# Inspect
goldenmatch memory stats --config goldenmatch.yml
goldenmatch memory show --config goldenmatch.yml --limit 50

# Train (run the learner over the current store)
goldenmatch memory learn --config goldenmatch.yml

# Move memory between environments
goldenmatch memory export --config goldenmatch.yml --output corrections.jsonl
goldenmatch memory import --config goldenmatch.yml --input corrections.jsonl
```

| Command | Purpose |
|---|---|
| `memory stats` | Counts by source / decision, learned threshold deltas, last-learned timestamp. |
| `memory show` | List recent corrections with reason and trust. |
| `memory learn` | Force a learning pass; otherwise auto-runs at next pipeline call. |
| `memory export` | JSONL dump of all corrections (one record per line). |
| `memory import` | Bulk-load corrections from JSONL. Trust-based upsert (higher trust wins). |

---

## Python API

```python
import goldenmatch

# Programmatically register a correction (same effect as the review TUI)
goldenmatch.add_correction(
    id_a=42,
    id_b=87,
    decision="reject",
    source="steward",
    reason="Different EIN despite name match",
    dataset="customers",
)

# Force a learning pass (otherwise auto-runs at next pipeline call)
adjustments = goldenmatch.learn()
print(f"Adjusted {len(adjustments)} matchkey thresholds")

# Inspect what's stored
print(goldenmatch.memory_stats())

# Direct store access
store = goldenmatch.get_memory()
for c in store.get_corrections(dataset="customers"):
    print(c.id_a, c.id_b, c.decision, c.trust, c.reason)
```

| Function | Returns |
|---|---|
| `goldenmatch.get_memory()` | The active `MemoryStore` (constructed from `config.memory`). |
| `goldenmatch.add_correction(id_a, id_b, decision, ...)` | Upserts a correction, trust-weighted. |
| `goldenmatch.learn()` | Runs `MemoryLearner`, returns the dict of threshold adjustments. |
| `goldenmatch.memory_stats()` | Same dict the CLI prints. |

After a pipeline run, every result also carries a `memory_stats` field:

```python
result = goldenmatch.dedupe_df(df, config=config)
print(result.memory_stats)
# {'applied': 12, 'stale': 0, 'stale_ambiguous': 0, 'unanchorable': 0}
```

---

## MCP

Five new MCP tools (`memory_*`) bring Learning Memory into Claude Desktop / Code. Total tool count is now 35.

| Tool | Behavior |
|---|---|
| `list_corrections` | Page through stored corrections, optionally filtered by dataset and source. |
| `add_correction` | Same arguments as the Python API; writes a correction with caller-supplied trust. |
| `learn_thresholds` | Runs `MemoryLearner.learn()`; returns the adjustment dict. |
| `memory_stats` | Counts and last-learned timestamps. |
| `memory_export` | Returns all corrections as a JSON array (use server-side for review portability). |

A natural-language workflow against an MCP-connected goldenmatch run:

> "Show me uncertain pairs from the last goldenmatch run on customers.csv, then mark rows 17 and 23 as not-a-match because they have different EINs."

The host LLM calls `list_corrections` -> `add_correction` -> `learn_thresholds`.

---

## How it works

```
            scored_pairs                          stored corrections
                |                                          |
                v                                          v
           apply_corrections() -- match by (id_a,id_b) ----+
                |                                          |
                | row IDs missing?                         |
                v                                          |
            re-anchor via record_hash  <-------------------+
                |
                v
           overridden pairs ---> cluster ---> golden ---> postflight
                                                              |
                                                              v
                                              Memory: N applied, M stale, ...
```

- **Trust-weighted upsert.** Every correction has a `trust` score (`steward`/`unmerge` 1.0, `agent`/`llm` 0.5). New corrections only override existing ones when their trust is at least as high.
- **Dual-hash staleness.** Each correction stores both a `field_hash` (only the matchkey fields) and a `record_hash` (all columns). On apply, if either hash diverges from the live data, the correction is reported `stale` rather than applied -- it would no longer be safe.
- **Re-anchoring.** When a correction's stored `(id_a, id_b)` are not present in the current frame, the system looks both rows up by `record_hash`. Single hits re-anchor cleanly; multiple hits report `stale_ambiguous`; no hits report `unanchorable`. Ambiguous and unanchorable corrections are not applied.
- **Stale persistence.** Stale corrections are enqueued to a sibling SQLite review queue (`.goldenmatch/review_queue.db`) so the next `goldenmatch review` invocation surfaces them for human re-decision.
- **Threshold learner.** A trust-weighted grid search picks the threshold that maximizes agreement with the stored decisions for that matchkey. Learned deltas overlay before the next scoring pass.

The full design lives in `docs/superpowers/specs/2026-05-04-learning-memory-completion.md` for readers who want algorithm-level detail.

---

## When to enable

- **Always**, if you have stewards reviewing borderline pairs. Their decisions otherwise evaporate.
- **Always**, if you re-run the same dataset on a schedule. The same false positives shouldn't keep coming back.
- **Probably not**, for one-shot dedupes on data you'll never see again.
- **Probably not**, if you need byte-for-byte reproducible output (e.g. DQBench parity runs). Use `auto_configure_df(df, strict=True)` and leave memory off.

---

## See also

| Topic | Link |
|---|---|
| YAML reference | [Configuration](configuration) |
| `goldenmatch memory ...` | [CLI Reference](cli) |
| `goldenmatch.add_correction` etc. | [Python API](python-api) |
| MCP `list_corrections` etc. | [MCP Server](mcp) |
| Review queue (the steward UI) | [REST API](rest-api) |
