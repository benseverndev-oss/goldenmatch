# LLM Boost Design Spec

**Date:** 2026-03-23
**Author:** Ben Severn
**Status:** Draft
**Parent spec:** `docs/superpowers/specs/2026-03-22-goldencheck-design.md`

## Overview

LLM Boost is an optional enhancement for GoldenCheck that sends representative sample blocks to an LLM to improve profiler accuracy. It runs as a post-profiler pass — existing profilers execute first, then the LLM reviews their findings alongside column samples and returns enhanced assessments.

This mirrors GoldenMatch's LLM Boost pattern: the zero-config profilers are the baseline, the LLM is the optional accuracy layer on top.

## CLI Interface

```bash
# Default scan + LLM boost (uses Anthropic)
goldencheck data.csv --llm-boost

# Specify provider
goldencheck data.csv --llm-boost --llm-provider openai

# Works with all existing flags
goldencheck data.csv --llm-boost --no-tui
goldencheck data.csv --llm-boost --json
goldencheck review data.csv --llm-boost
```

### New CLI Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--llm-boost` | bool | False | Enable LLM enhancement pass |
| `--llm-provider` | str | "anthropic" | LLM provider: "anthropic" or "openai" |

These flags must be added to: `scan` command, `review` command, and the `main()` callback's hand-rolled arg parser (which currently handles `--no-tui` and `--json`). The `_do_scan` function signature must accept `llm_boost` and `llm_provider` parameters.

**Hand-rolled parser update:** The `main()` callback's `while args` loop must handle `--llm-boost` as a boolean flag and `--llm-provider` as a value-consuming flag:
```python
elif arg == "--llm-boost":
    llm_boost = True
elif arg == "--llm-provider":
    llm_provider = args.pop(0)  # consume next token as value
```

**Review command integration:** The `review` command must call `_do_scan` (not `scan_file` directly) so that the LLM boost pass runs. Refactor `review` to use `_do_scan` with its existing validation merge logic, passing through `llm_boost` and `llm_provider`.

### Environment Variables

- `ANTHROPIC_API_KEY` — required when using `--llm-provider anthropic` (default)
- `OPENAI_API_KEY` — required when using `--llm-provider openai`

If `--llm-boost` is passed without a valid API key, exit with a clear error message.

## Model Changes

### Finding model (`goldencheck/models/finding.py`)

Add an optional `source` field to the `Finding` dataclass:

```python
@dataclass
class Finding:
    severity: Severity
    column: str
    check: str
    message: str
    affected_rows: int = 0
    sample_values: list[str] = field(default_factory=list)
    suggestion: str | None = None
    pinned: bool = False
    source: str | None = None  # NEW: None = profiler (backward compat), "llm" = LLM-sourced
```

Valid values: `None` (profiler-generated, default for backward compatibility), `"llm"` (LLM-generated or LLM-upgraded).

### JSON Reporter

The JSON output schema gains an optional `source` field per finding:
```json
{
  "severity": "error",
  "column": "last_name",
  "check": "type_inference",
  "message": "...",
  "source": "llm"
}
```

Omitted when `None` (profiler-only findings) for backward compatibility. The JSON reporter must conditionally include `source` only when it is not `None` — use a dict comprehension or explicit check, not blind serialization of all fields.

## Architecture

```
Scanner runs all profilers → collects findings + profile
    ↓
SampleBlockBuilder constructs representative sample per column
    ↓
LLMBooster sends ONE API call with all columns + existing findings
    ↓
ResponseParser validates LLM response against Pydantic schema
    ↓
FindingsMerger merges: new issues added, severities upgraded/downgraded
    ↓
TUI/CLI displays enhanced findings (LLM-sourced findings tagged)
```

## Representative Sample Block

For each column, the sample block includes:

### Metadata
- Column name
- Polars dtype
- Row count
- Null count and percentage
- Unique count and percentage

### Values (~20-30 per column, deduplicated)
- Top 5 most frequent values (with counts)
- Bottom 5 least frequent values (with counts)
- 5 random values from the middle of the distribution
- Any values already flagged by profilers (from `finding.sample_values`)

### Profiler Context
- List of findings already generated for this column (severity + check + message)

### Budget
- ~100-150 tokens per column
- ~1500-2000 tokens total for a 15-column dataset
- One API call per scan, not one per column
- **Wide dataset limit:** If column count exceeds 50, only send the 50 columns with the most profiler findings. Log a warning: "LLM boost limited to 50 columns (dataset has N). Columns with most findings prioritized."

## LLM Prompt

### System Prompt

```
You are a data quality analyst. You are given a dataset summary with representative
samples for each column, along with findings from automated profilers.

Your job is to:
1. Identify data quality issues the profilers missed
2. Upgrade severity of findings that are worse than the profiler assessed
3. Downgrade severity of findings that are false positives
4. Identify cross-column relationships (temporal ordering, semantic dependencies)

For each column, determine its semantic type (person_name, email, phone, date,
currency, address, country_code, state_code, enum, identifier, free_text, etc.)
and use that understanding to assess data quality.

Respond with valid JSON matching the schema provided.
```

### Response Schema

```json
{
  "columns": {
    "<column_name>": {
      "semantic_type": "person_name",
      "issues": [
        {
          "severity": "error",
          "check": "type_inference",
          "message": "3 values are numeric in a person name column",
          "affected_values": ["12345", "99999"]
        }
      ],
      "upgrades": [
        {
          "original_check": "nullability",
          "original_severity": "info",
          "new_severity": "warning",
          "reason": "Person names should not be null in a customer database"
        }
      ],
      "downgrades": [
        {
          "original_check": "pattern_consistency",
          "original_severity": "warning",
          "new_severity": "info",
          "reason": "Mixed phone formats are common and not necessarily an error"
        }
      ]
    }
  },
  "relations": [
    {
      "type": "temporal_order",
      "columns": ["signup_date", "last_login"],
      "reasoning": "A user must sign up before they can log in"
    }
  ]
}
```

### Pydantic Response Models

```python
class LLMIssue(BaseModel):
    severity: str  # "error", "warning", "info"
    check: str
    message: str
    affected_values: list[str] = []

class LLMUpgrade(BaseModel):
    original_check: str
    original_severity: str
    new_severity: str
    reason: str

class LLMDowngrade(BaseModel):
    original_check: str
    original_severity: str
    new_severity: str
    reason: str

class LLMColumnAssessment(BaseModel):
    semantic_type: str
    issues: list[LLMIssue] = []
    upgrades: list[LLMUpgrade] = []
    downgrades: list[LLMDowngrade] = []

class LLMRelation(BaseModel):
    type: str
    columns: list[str]
    reasoning: str

class LLMResponse(BaseModel):
    columns: dict[str, LLMColumnAssessment] = {}
    relations: list[LLMRelation] = []
```

## New Files

```
goldencheck/
├── llm/
│   ├── __init__.py
│   ├── sample_block.py    # Build representative samples from DataFrame + findings
│   ├── prompts.py         # System prompt, user prompt template, response Pydantic models
│   ├── providers.py       # Anthropic and OpenAI API call wrappers
│   ├── parser.py          # Validate LLM JSON response against Pydantic schema
│   └── merger.py          # Merge LLM response into existing findings list
tests/
├── llm/
│   ├── __init__.py
│   ├── test_sample_block.py
│   ├── test_parser.py
│   └── test_merger.py
```

## Findings Merger

### New Issues
- Created as `Finding` objects with `source="llm"`
- Severity parsed from LLM response string to `Severity` enum
- `affected_values` mapped to `sample_values`

### Upgrades
- Find matching existing finding by `(column, check)`
- Replace severity with the LLM's upgraded severity
- Set `source="llm"` to indicate the finding was LLM-enhanced
- Append LLM's reason to the finding message

### Downgrades
- Find matching existing finding by `(column, check)`
- Replace severity with the LLM's downgraded severity
- Set `source="llm"`
- Append LLM's reason to the finding message

### Relation Discoveries
- Create `Finding` objects directly from LLM relation data (do NOT dispatch `TemporalOrderProfiler`)
- `column` field uses comma-joined format: `"signup_date,last_login"` (matching existing temporal profiler convention)
- `check` = the relation type (e.g., `"temporal_order"`)
- `source="llm"`

### Conflict Resolution
- If LLM suggests an upgrade but the finding doesn't exist, create it as a new issue with: `severity` = `new_severity`, `check` = `original_check`, `message` = `reason`, `source` = `"llm"`
- If LLM suggests a downgrade for a non-existent finding, ignore it
- If LLM response fails Pydantic validation, discard entirely and log a warning
- Cross-column finding lookup normalizes column lists: sort `columns` alphabetically and join with `,` for matching

### Required Test Cases for Merger
- New issue created with `source="llm"`
- Upgrade changes severity and sets source
- Downgrade changes severity and sets source
- Upgrade for non-existent finding becomes new issue
- Downgrade for non-existent finding is silently ignored
- Malformed response is discarded, original findings returned unchanged
- Cross-column relation creates finding with comma-joined column name

## Dependencies

```toml
[project.optional-dependencies]
llm = [
    "anthropic>=0.30",
    "openai>=1.30",
]
```

## Provider Configuration

| Provider | Default Model | Env Var | Structured Output |
|----------|--------------|---------|-------------------|
| Anthropic | claude-haiku-4-5-20251001 | `ANTHROPIC_API_KEY` | JSON mode via system prompt |
| OpenAI | gpt-4o-mini | `OPENAI_API_KEY` | `response_format={"type": "json_object"}` |

**Note:** Model IDs should be treated as configurable defaults. If the Anthropic model ID is invalid at implementation time, use the latest available Haiku model. The model can also be overridden via `GOLDENCHECK_LLM_MODEL` environment variable.

## Error Handling

| Scenario | Behavior |
|----------|----------|
| `--llm-boost` without API key | Exit with: "LLM boost requires ANTHROPIC_API_KEY (or OPENAI_API_KEY with --llm-provider openai)" |
| `llm` extras not installed | Exit with: "LLM boost requires extra dependencies. Install with: pip install goldencheck[llm]" |
| API rate limit / timeout | Warning: "LLM boost failed: {error}. Showing profiler-only results." Continue without LLM findings. |
| Malformed LLM response | Warning: "LLM response could not be parsed. Showing profiler-only results." Discard response. |
| Dataset has >50 columns | Warning: "LLM boost limited to 50 columns. Columns with most findings prioritized." Send top 50 only. |

## TUI Integration

LLM-sourced findings display a `[LLM]` badge in the Findings tab to distinguish them from profiler-only findings. Upgraded findings show `[LLM upgraded]`. Downgraded findings show the reduced severity with `[LLM downgraded]` note.

## Cost Estimate

| Model | Input (~2000 tokens) | Output (~500 tokens) | Total |
|-------|---------------------|---------------------|-------|
| Claude Haiku | ~$0.002 | ~$0.005 | ~$0.007 |
| GPT-4o-mini | ~$0.003 | ~$0.006 | ~$0.009 |

## Out of Scope

- Caching LLM responses across scans
- Fine-tuned models
- Local/self-hosted LLM support
- LLM-generated fix suggestions (v2)
- Multi-turn LLM conversation for ambiguous cases
- AI Gateway / Vercel OIDC integration
- LLM boost for the `validate` command (only `scan` and `review`)
