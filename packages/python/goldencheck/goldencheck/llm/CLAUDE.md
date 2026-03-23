# LLM Module

## Two-Stage Flow

```
scan_file(..., return_sample=True)   # Stage 1: full profiler pipeline + suppression + boost
    → build_sample_blocks(sample, findings)
    → call_llm(provider, user_prompt) # Stage 2: LLM sees sample data + profiler findings
    → parse_llm_response(raw)
    → merge_llm_findings(findings, response)
```

Call `scan_file_with_llm(path, provider)` — never call stage 2 steps manually.

## Providers

| Provider | Default model | Env var for key | Override model |
|---|---|---|---|
| `anthropic` | `claude-haiku-4-5-20251001` | `ANTHROPIC_API_KEY` | `GOLDENCHECK_LLM_MODEL` |
| `openai` | `gpt-4o-mini` | `OPENAI_API_KEY` | `GOLDENCHECK_LLM_MODEL` |

Install deps first: `pip install goldencheck[llm]`. `check_llm_available()` raises `SystemExit` (not an exception) if key or package is missing.

## Standardized Check Names

The system prompt enforces these exact check name strings — LLM findings must use them or the merger lookup fails:

`uniqueness`, `nullability`, `format_detection`, `type_inference`, `range_distribution`, `cardinality`, `pattern_consistency`, `temporal_order`, `encoding_detection`, `sequence_detection`, `drift_detection`, `cross_column`, `invalid_values`, `checksum_failure`

## Budget Tracking

```python
from goldencheck.llm.budget import estimate_cost, check_budget, CostReport
estimated = estimate_cost(input_tokens=2000, output_tokens=500, model="claude-haiku-...")
if check_budget(estimated):   # reads GOLDENCHECK_LLM_BUDGET env var
    ...
report = CostReport()
report.record(input_tok, output_tok, model)
logger.info("Cost: $%.4f", report.cost_usd)
```

Set `GOLDENCHECK_LLM_BUDGET=0.10` to cap spending per run.

## Merger Behaviour

`merge_llm_findings(findings, response)` — always returns a **new list**, never mutates:
- **New issues**: appended as `Finding(source="llm")`
- **Upgrades/Downgrades**: matched by `(column, check)` key; uses `dataclasses.replace()`. Strips `(suppressed: ...)` suffix from message before appending `[LLM: reason]`
- **Relations**: appended as `Finding(column="col_a,col_b", check=relation.type, source="llm")`
- If `(column, check)` not found for an upgrade, creates a new Finding rather than silently dropping

## Parser

`parse_llm_response(raw_text)` in `llm/parser.py` strips markdown fences (` ```json ... ``` `) before JSON parsing. Returns `None` on parse failure — scanner logs a warning and returns profiler-only results.

## Testing LLM Locally

```bash
source .testing/.env   # loads OPENAI_API_KEY
goldencheck tests/fixtures/messy.csv --llm-boost --llm-provider openai --no-tui
```

Mock-based tests: see `tests/llm/test_integration.py` — patches `call_llm` and `check_llm_available`.

## Gotchas

- All columns are sent to the LLM (confidence routing was removed — LLM adds value even on high-confidence columns for semantic issues)
- LLM availability is checked **before** running profilers in `scan_file_with_llm` — fail fast, don't waste scan time
- `apply_confidence_downgrade` is **not** called when LLM boost is active (the LLM handles low-confidence cases)
- Relations use a comma-joined sorted column name as the `column` field — queries like `finding.column.split(",")` to recover column list
