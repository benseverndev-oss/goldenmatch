---
title: LLM Boost
layout: default
nav_order: 15
---

LLM Boost is an optional enhancement pass that runs after the standard profilers. It sends a compact representation of your data to an LLM and merges the LLM's assessments back into the findings list.

---

## How It Works

LLM Boost operates in **two stages**: type classification and finding review.

### Stage 1 — Semantic type classification

Before the finding review call, a lightweight LLM call classifies each column's semantic type (e.g., `email`, `name`, `currency`, `id`, `category`). This classification is used to:

- Improve the severity of findings that depend on column meaning (e.g., nulls in an email column are more likely errors)
- Provide context to the Stage 2 finding review prompt

This call uses the cheapest available model and typically costs under $0.001.

### Stage 2 — Finding review

#### Step 1 — Profiler scan

The standard profiler pipeline runs first and produces a `list[Finding]` along with the sampled DataFrame.

#### Step 2 — Sample block construction

`build_sample_blocks()` compiles a JSON summary for each column (up to 50 columns; columns with the most existing findings are prioritized if the dataset exceeds that limit):

```json
{
  "email": {
    "column": "email",
    "dtype": "String",
    "semantic_type": "email",
    "row_count": 10000,
    "null_count": 45,
    "null_pct": 0.005,
    "unique_count": 9821,
    "top_values": [{"value": "user@example.com", "count": 3}],
    "rare_values": [{"value": "bad@", "count": 1}],
    "random_sample": ["alice@corp.com", "bob@example.org"],
    "flagged_values": ["bad@", "notanemail"],
    "existing_findings": [
      {"severity": "warning", "check": "format_detection",
       "message": "6 value(s) do not match expected email format"}
    ]
  }
}
```

#### Step 3 — Single LLM call

The sample blocks are serialized to JSON and sent in a single API call with this system prompt:

> You are a data quality analyst. Identify issues the profilers missed, upgrade severity of findings that are worse than assessed, downgrade false positives, and identify cross-column relationships.

The LLM returns structured JSON with per-column assessments and relation findings.

#### Step 4 — Merge

`merge_llm_findings()` integrates the LLM response:

- **New issues** from the LLM are appended as `Finding` objects with `source="llm"`
- **Upgrades** change the `severity` on an existing finding matched by `check` name
- **Downgrades** reduce the `severity` on matched findings
- **Relations** become new cross-column findings

The final list is sorted by severity (ERROR first) and returned.

### Scores: profiler-only vs LLM boost

| Mode | DQBench Score | Cost |
|------|---------------|------|
| Profiler-only (v0.2.0) | 72.00 | $0 |
| With LLM Boost | ~74–76 (varies by model) | ~$0.003–0.01/scan |

The profiler-only score of 72.00 already outperforms all competitors' hand-written rules. LLM Boost provides incremental gains on adversarial tier issues requiring semantic understanding.

---

## What LLM Boost Catches That Profilers Miss

| Category | Example |
|----------|---------|
| Semantic type violations | `"12345"` in a `first_name` column |
| Business rule knowledge | Email columns should almost never be null |
| Contextual severity | A `status` column with `"UNKNOWN"` is an error, not info |
| Implicit relations | `signup_date` should precede `last_login_date` |
| False positive reduction | Mixed phone formats in a global dataset are expected |

---

## Provider Setup

### Anthropic (default)

```bash
pip install goldencheck[llm]
export ANTHROPIC_API_KEY=sk-ant-...
goldencheck data.csv --llm-boost --no-tui
```

Default model: `claude-haiku-4-5-20251001`

### OpenAI

```bash
pip install goldencheck[llm]
export OPENAI_API_KEY=sk-...
goldencheck data.csv --llm-boost --llm-provider openai --no-tui
```

Default model: `gpt-4o-mini`

---

## Cost Tracking

GoldenCheck tracks actual token usage after each LLM call and logs the cost:

```
INFO  LLM boost cost: $0.0082 (input: 8420, output: 312, model: claude-haiku-4-5-20251001)
```

Cost is calculated per-model using known pricing rates:

| Model | Input (per 1K tokens) | Output (per 1K tokens) |
|-------|-----------------------|------------------------|
| claude-haiku-4-5-20251001 | $0.0008 | $0.004 |
| claude-sonnet-4-20250514 | $0.003 | $0.015 |
| gpt-4o-mini | $0.00015 | $0.0006 |
| gpt-4o | $0.0025 | $0.01 |

For unknown models, a conservative fallback rate is used.

---

## Budget Limits

Set a maximum spend per scan with `GOLDENCHECK_LLM_BUDGET`:

```bash
export GOLDENCHECK_LLM_BUDGET=0.10  # max $0.10 per scan
goldencheck data.csv --llm-boost --no-tui
```

If the estimated cost exceeds the budget before the API call is made, the LLM pass is skipped and profiler-only results are returned. A warning is logged:

```
WARNING  Estimated LLM cost ($0.1240) exceeds budget ($0.10). Skipping LLM boost.
```

Budget is pre-checked using a conservative estimate (~2,000 input + ~500 output tokens). The actual call is only made if the estimate is within budget.

---

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Required when using the `anthropic` provider | `sk-ant-...` |
| `OPENAI_API_KEY` | Required when using the `openai` provider | `sk-...` |
| `GOLDENCHECK_LLM_BUDGET` | Maximum USD spend per scan | `0.50` |
| `GOLDENCHECK_LLM_MODEL` | Override the default model for the selected provider | `claude-sonnet-4-20250514` |

---

## Failure Handling

If the LLM call fails (network error, invalid response, API error), GoldenCheck logs a warning and returns profiler-only results. It never crashes or exits because of an LLM failure:

```
WARNING  LLM boost failed: Connection timeout. Showing profiler-only results.
```

If the response cannot be parsed as valid JSON matching the expected schema:

```
WARNING  LLM response could not be parsed. Showing profiler-only results.
```

---

## Column Limit

For datasets with more than 50 columns, LLM Boost prioritizes the columns with the most existing profiler findings. A warning is logged:

```
WARNING  LLM boost limited to 50 columns (dataset has 120).
         Columns with most findings prioritized.
```
