# Learning Memory

GoldenMatch can remember past steward decisions and apply them automatically on every subsequent run. Reject a pair once -- it stays rejected. Approve a borderline pair once -- it stays approved. After enough corrections accumulate, the learner adjusts matchkey thresholds so the system stops needing the same correction twice.

Off by default. Enable via `config.memory.enabled = True` or a `memory:` section in YAML. Shipped in v1.6.0.

## What it does

- **Pipeline applies corrections automatically** -- `dedupe_df` and `match_df` apply stored corrections after scoring (hard 1.0 for approve, hard 0.0 for reject) and overlay learned threshold deltas before scoring.
- **Re-anchors via `record_hash`** -- corrections survive row reordering and refresh; ambiguous re-anchors report as `stale_ambiguous` rather than silently misapplying.
- **Seven collection points** -- review queue, boost tab, `unmerge_record` / `unmerge_cluster`, LLM scorer, MCP `agent_approve_reject`, REST `POST /reviews/decide`, Python `add_correction()`.
- **Threshold learning** -- once a matchkey accumulates 10+ corrections, a trust-weighted grid search adjusts that matchkey's threshold for the next run.
- **Postflight reports impact** -- every run with memory active emits `Memory: N applied, M stale, K stale-ambiguous, J unanchorable`.

## Walkthrough

`goldenmatch.yml`:

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

```bash
# 1. First run -- produces the review queue
goldenmatch dedupe customers.csv --config goldenmatch.yml

# 2. Steward decides borderline pairs (writes to .goldenmatch/memory.db)
goldenmatch review --config goldenmatch.yml

# 3. Re-run -- corrections apply automatically; postflight reports impact
goldenmatch dedupe customers.csv --config goldenmatch.yml
# > Memory: 12 corrections applied, 0 stale, 0 stale-ambiguous, 0 unanchorable
```

## Configuration

| Field | Default | Notes |
|---|---|---|
| `enabled` | `false` | Zero-config preserved. |
| `backend` | `sqlite` | `postgres` requires `pip install goldenmatch[postgres]`. |
| `path` | `.goldenmatch/memory.db` | SQLite path or full DSN for postgres. |
| `reanchor` | `true` | Re-anchor by `record_hash` when row IDs miss. |
| `dataset` | `null` | Tag corrections; isolates per-table memory in shared DBs. |
| `learning.threshold_min_corrections` | `10` | Floor for the threshold learner. |
| `learning.weights_min_corrections` | `50` | Field-weight learning floor (stub in v1.6, returns `null`). |

## CLI

```bash
goldenmatch memory stats   --config goldenmatch.yml
goldenmatch memory show    --config goldenmatch.yml --limit 50
goldenmatch memory learn   --config goldenmatch.yml
goldenmatch memory export  --config goldenmatch.yml --output corrections.jsonl
goldenmatch memory import  --config goldenmatch.yml --input  corrections.jsonl
```

## Python API

```python
import goldenmatch

goldenmatch.add_correction(
    id_a=42, id_b=87, decision="reject", source="steward",
    reason="Different EIN despite name match", dataset="customers",
)

adjustments = goldenmatch.learn()
print(f"Adjusted {len(adjustments)} matchkey thresholds")

print(goldenmatch.memory_stats())

result = goldenmatch.dedupe_df(df, config=config)
print(result.memory_stats)
# {'applied': 12, 'stale': 0, 'stale_ambiguous': 0, 'unanchorable': 0}
```

## MCP

Five new MCP tools (total now 35):

| Tool | Behavior |
|---|---|
| `list_corrections` | Page through stored corrections (optionally filtered by dataset/source). |
| `add_correction` | Writes a correction with caller-supplied trust. |
| `learn_thresholds` | Runs the learner and returns the adjustment dict. |
| `memory_stats` | Counts plus last-learned timestamps. |
| `memory_export` | Returns all corrections as a JSON array. |

Natural-language workflow:

> "Show me uncertain pairs from the last goldenmatch run on customers.csv, then mark rows 17 and 23 as not-a-match because they have different EINs."

The host LLM calls `list_corrections` -> `add_correction` -> `learn_thresholds`.

## How it works

- **Trust-weighted upsert.** `steward`/`unmerge` write trust 1.0; `agent`/`llm` write trust 0.5. New corrections only override existing ones at equal-or-higher trust.
- **Dual-hash staleness.** Each correction stores `field_hash` (matchkey fields only) and `record_hash` (all columns). If either diverges, the correction reports `stale` instead of applying.
- **Re-anchoring.** When stored row IDs are missing, the system looks rows up by `record_hash`. Single hits re-anchor; multiple hits report `stale_ambiguous`; no hits report `unanchorable`. Stale corrections enqueue to a sibling review queue for human re-decision.
- **Threshold learner.** A trust-weighted grid search picks the threshold that maximizes agreement with stored decisions for that matchkey.

## See also

- [[Quick-Start]]
- [[Python-API]]
- [[Pipeline-Overview]]
