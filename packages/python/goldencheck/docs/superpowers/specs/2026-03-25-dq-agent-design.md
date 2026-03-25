# GoldenCheck DQ Agent — Design Spec

## Overview

An autonomous data quality agent that other AI systems can discover and invoke via A2A and MCP protocols. It analyzes data, picks the right profiling strategy, runs the pipeline, explains every finding, and manages a confidence-gated review queue — all without human configuration.

**Goal:** Make GoldenCheck the go-to data quality tool for AI agents, and the first stage in the Golden Suite pipeline (GoldenCheck → GoldenFlow → GoldenMatch).

**Protocols:**
- **MCP** — Tool-level integration for Claude/Cursor/coding agents (expand existing server)
- **A2A** — Agent-level discovery for any A2A-compatible framework (new)

**Trust model:** Confidence-gated (mirrors GoldenMatch)
- Auto-pin: ≥0.8 confidence + severity ≥ WARNING → rule in goldencheck.yml
- Review queue: 0.5–0.8 confidence → held for human/agent approval
- Auto-dismiss: <0.5 confidence or INFO severity → ignored

---

## A2A Agent Card

Served at `/.well-known/agent.json` by the A2A server (`goldencheck agent-serve`):

```json
{
  "name": "goldencheck-agent",
  "description": "Autonomous data quality agent. Profiles data, discovers validation rules, detects anomalies, explains findings, manages confidence-gated review queue. First stage in Golden Suite pipeline (GoldenCheck → GoldenFlow → GoldenMatch).",
  "url": "http://localhost:8100",
  "version": "1.0.0",
  "provider": {
    "organization": "GoldenCheck",
    "url": "https://github.com/benzsevern/goldencheck"
  },
  "capabilities": {
    "streaming": true,
    "pushNotifications": false
  },
  "skills": [
    {
      "id": "analyze_data",
      "name": "Analyze Data",
      "description": "Profile columns, detect domain, recommend profiling strategy and domain pack",
      "inputModes": ["application/json"],
      "outputModes": ["application/json"]
    },
    {
      "id": "configure",
      "name": "Auto-Configure",
      "description": "Generate optimal goldencheck.yml from data analysis",
      "inputModes": ["application/json"],
      "outputModes": ["application/json", "text/yaml"]
    },
    {
      "id": "scan",
      "name": "Scan",
      "description": "Run full profiling pipeline with confidence-gated output",
      "inputModes": ["application/json"],
      "outputModes": ["application/json"]
    },
    {
      "id": "validate",
      "name": "Validate",
      "description": "Validate against pinned rules in goldencheck.yml",
      "inputModes": ["application/json"],
      "outputModes": ["application/json"]
    },
    {
      "id": "explain",
      "name": "Explain Finding",
      "description": "Natural language explanation for why a finding was raised and what to do about it",
      "inputModes": ["application/json"],
      "outputModes": ["application/json", "text/plain"]
    },
    {
      "id": "review",
      "name": "Review Queue",
      "description": "Present borderline findings for approval, process decisions",
      "inputModes": ["application/json"],
      "outputModes": ["application/json"]
    },
    {
      "id": "fix",
      "name": "Auto-Fix",
      "description": "Apply automated fixes to data quality issues (safe/moderate/aggressive)",
      "inputModes": ["application/json"],
      "outputModes": ["application/json"]
    },
    {
      "id": "compare_domains",
      "name": "Compare Domains",
      "description": "Run scan with multiple domain packs, report which catches the most real issues",
      "inputModes": ["application/json"],
      "outputModes": ["application/json"]
    },
    {
      "id": "handoff",
      "name": "Pipeline Handoff",
      "description": "Export validated data + quality attestation for downstream pipeline stages (GoldenFlow, GoldenMatch)",
      "inputModes": ["application/json"],
      "outputModes": ["application/json"]
    }
  ],
  "authentication": {
    "schemes": ["bearer"]
  }
}
```

### A2A Task Endpoints

Same task lifecycle as GoldenMatch:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/.well-known/agent.json` | GET | Agent card discovery |
| `/tasks/send` | POST | Submit a task (synchronous) |
| `/tasks/sendSubscribe` | POST | Submit a task with SSE streaming updates |
| `/tasks/{id}` | GET | Get task status and result |
| `/tasks/{id}/cancel` | POST | Cancel a running task |

**Task states:** `submitted` → `working` → `completed` | `failed` | `canceled`

**SSE event format** (for `/tasks/sendSubscribe`):

```
event: task-status
data: {"id": "abc123", "state": "working", "progress": "Profiling 50,000 rows × 12 columns..."}

event: task-status
data: {"id": "abc123", "state": "working", "progress": "Running 10 column profilers..."}

event: task-artifact
data: {"id": "abc123", "artifact": {"type": "result", "parts": [{"type": "data", "data": {...}}]}}

event: task-status
data: {"id": "abc123", "state": "completed"}
```

**Task request format:**

```json
{
  "id": "client-generated-uuid",
  "skill": "scan",
  "message": {
    "role": "user",
    "parts": [
      {"type": "data", "data": {"file_path": "/data/customers.csv"}},
      {"type": "text", "text": "Scan this customer file, prioritize format detection issues"}
    ]
  }
}
```

### Authentication

Bearer token via `GOLDENCHECK_AGENT_TOKEN` env var. If not set, no auth required (local use). Token validated on every task request via simple string comparison. No JWT/OAuth in v1.

---

## A2A Server Architecture

**Separate server** from the existing REST API. The A2A server runs on its own port (default 8100) using `aiohttp` for async support:

- `goldencheck agent-serve --port 8100` — new CLI command
- Async request handling (SSE streaming requires it)
- Background task execution via `asyncio.create_task()`
- Task registry (in-memory dict of task_id → state/result)

The existing REST API (`goldencheck serve`, port 8000, `http.server.HTTPServer`) is **unchanged**. No migration needed.

**Port convention across Golden Suite:**
- GoldenCheck A2A: 8100
- GoldenFlow A2A: 8150 (future)
- GoldenMatch A2A: 8200

---

## MCP Server Expansion

New agent-level tools (additive, existing 9 tools unchanged):

| Tool | Input | Output |
|------|-------|--------|
| `analyze_data` | `{"file_path": "..."}` | Domain detection, column profiles, strategy recommendation |
| `auto_configure` | `{"file_path": "...", "constraints": {...}}` | YAML config string |
| `explain_finding` | `{"finding": {...}}` | Natural language explanation + remediation |
| `explain_column` | `{"file_path": "...", "column": "..."}` | Column health narrative |
| `review_queue` | `{"job_name": "..."}` | Borderline findings needing approval |
| `approve_reject` | `{"item_id": "...", "decision": "pin|dismiss", "reason": "..."}` | Updated goldencheck.yml |
| `compare_domains` | `{"file_path": "..."}` | Domain comparison results |
| `suggest_fix` | `{"file_path": "...", "mode": "safe"}` | Preview of fixes before applying |
| `pipeline_handoff` | `{"file_path": "...", "job_name": "..."}` | Quality attestation for downstream |
| `review_stats` | `{"job_name": "..."}` | Counts by status (pending/pinned/dismissed) |

All inputs are JSON. File paths refer to files accessible on the server. No DataFrames over JSON — the tool loads the file internally.

**MCP state management:** New agent tools create their own `AgentSession` object (see Intelligence Layer) rather than sharing state with existing tools. This avoids stale state conflicts.

---

## Intelligence Layer

New module: `goldencheck/agent/intelligence.py`

### AgentSession

Encapsulates a single agent interaction. Holds its own state independent of MCP/REST global state.

```python
class AgentSession:
    sample: pl.DataFrame | None  # always use return_sample=True to populate
    profile: DatasetProfile | None
    findings: list[Finding]
    review_queue: ReviewQueue
    reasoning: dict          # why this strategy was chosen
    job_name: str            # for review queue tracking
```

**Important:** Agent scan paths always call `scan_file(..., return_sample=True)` to populate `session.sample`. All scan paths that do not use `llm_boost=True` must call `apply_confidence_downgrade(findings, llm_boost=False)` before handing findings to `ReviewQueue`.

### Decision Tree (concrete logic)

```python
def select_strategy(df: pl.DataFrame, column_types: dict) -> StrategyDecision:
    """Select profiling strategy based on data characteristics."""

    # Step 1: Detect domain from column names (use small sample for speed)
    preview = maybe_sample(df, max_rows=10_000)
    domain_scores = {}
    for domain_name in list_available_domains():
        type_defs = load_type_defs(domain=domain_name)
        classifications = classify_columns(preview, type_defs)
        matched = sum(1 for c in classifications.values() if c.type_name != "none")
        domain_scores[domain_name] = matched / len(df.columns)

    best_domain = max(domain_scores, key=domain_scores.get)
    domain_confidence = domain_scores[best_domain]

    # Step 2: Check dataset size → sampling strategy
    if len(df) > 500_000:
        sample_strategy = "aggressive"  # 50k sample
        why_sample = f"{len(df):,} rows — sampling 50k for speed"
    elif len(df) > 100_000:
        sample_strategy = "standard"    # 100k sample
        why_sample = f"{len(df):,} rows — standard 100k sample"
    else:
        sample_strategy = "full"
        why_sample = f"{len(df):,} rows — scanning all rows"

    # Step 3: Check column count → profiler selection
    if len(df.columns) > 50:
        profiler_strategy = "targeted"  # skip drift/sequence on low-info columns
        why_profilers = f"{len(df.columns)} columns — skipping expensive profilers on low-variance columns"
    else:
        profiler_strategy = "full"
        why_profilers = f"{len(df.columns)} columns — running all profilers"

    # Step 4: Check for LLM availability
    llm_available = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"))

    # Step 5: Build strategy
    return StrategyDecision(
        domain=best_domain if domain_confidence > 0.3 else None,
        domain_confidence=domain_confidence,
        sample_strategy=sample_strategy,
        profiler_strategy=profiler_strategy,
        llm_boost=llm_available,
        why={
            "domain": f"Detected {best_domain} ({domain_confidence:.0%} match)" if domain_confidence > 0.3
                      else "No domain detected — using base types",
            "sampling": why_sample,
            "profilers": why_profilers,
            "llm": "LLM boost available — will enhance findings" if llm_available
                   else "No LLM API key — profiler-only mode",
        },
    )
```

### Alternatives Reasoning

After selecting a strategy, the agent generates reasoning for alternatives not chosen:

```python
def build_alternatives(decision: StrategyDecision, domain_scores: dict) -> list[dict]:
    alternatives = []
    for domain, score in sorted(domain_scores.items(), key=lambda x: -x[1]):
        if domain != decision.domain and score > 0.1:
            alternatives.append({
                "domain": domain,
                "match_rate": score,
                "why_not": f"Lower match rate ({score:.0%} vs {decision.domain_confidence:.0%})"
            })
    return alternatives
```

### Explain Finding

Natural language explanation for any finding:

```python
def explain_finding(finding: Finding, profile: DatasetProfile) -> str:
    """Generate human-readable explanation of a finding."""
    col_profile = next((c for c in profile.columns if c.name == finding.column), None)

    explanation = {
        "what": finding.message,
        "severity": f"{finding.severity.name} (confidence: {finding.confidence:.0%})",
        "impact": _describe_impact(finding, col_profile),
        "suggestion": finding.suggestion or _generate_suggestion(finding),
        "affected": f"{finding.affected_rows:,} rows ({finding.affected_rows / profile.row_count:.1%})",
        "samples": finding.sample_values[:5],
    }
    return explanation
```

### compare_domains

Runs the scan with each available domain pack and compares results:

```python
def compare_domains(file_path: str) -> dict:
    domains = [None] + list_available_domains()  # None = base types
    results = {}
    for domain in domains:
        findings, profile = scan_file(file_path, domain=domain)
        findings = apply_confidence_downgrade(findings, llm_boost=False)
        # Build findings_by_column dict for health_score (same as MCP tools)
        fbc = {}
        for f in findings:
            fbc.setdefault(f.column, {"errors": 0, "warnings": 0})
            if f.severity == Severity.ERROR:
                fbc[f.column]["errors"] += 1
            elif f.severity == Severity.WARNING:
                fbc[f.column]["warnings"] += 1
        results[domain or "base"] = {
            "health_grade": profile.health_score(fbc)[0],
            "health_score": profile.health_score(fbc)[1],
            "errors": sum(1 for f in findings if f.severity == Severity.ERROR),
            "warnings": sum(1 for f in findings if f.severity == Severity.WARNING),
            "findings_count": len(findings),
            "high_confidence": sum(1 for f in findings if f.confidence >= 0.8),
        }
    return results
```

---

## Confidence-Gated Review Queue

New module: `goldencheck/agent/review_queue.py`

**Gating logic (extends existing auto_triage):**

```
confidence >= 0.8 AND severity >= WARNING  →  auto_pinned (added to goldencheck.yml)
0.5 <= confidence < 0.8 AND severity >= WARNING  →  review_queue (held)
confidence < 0.5 OR severity == INFO  →  auto_dismissed
```

Note: the boundary at 0.8 is strict — `>= 0.8` goes to auto_pin, `< 0.8` goes to review. This matches the existing `auto_triage()` in `engine/triage.py` which uses `f.confidence >= 0.8`.

These thresholds align with the existing `auto_triage` function in `engine/triage.py`. The review queue wraps triage with persistence and an approval API.

**Three storage backends (auto-detected, same pattern as GoldenMatch):**

| Backend | When | Persists? |
|---------|------|-----------|
| Memory | Default, no config needed | No — lost when process exits |
| SQLite | `.goldencheck/` directory exists | Yes — `.goldencheck/reviews.db` |
| Postgres | `DATABASE_URL` env var set | Yes — `goldencheck._reviews` table |

Auto-selection: Postgres if `DATABASE_URL` set, else SQLite if `.goldencheck/` exists, else memory. Storage tier communicated in every response.

**Review item schema:**

| Field | Type | Description |
|-------|------|-------------|
| job_name | str | Which scan job produced this finding |
| item_id | str | Unique ID (hash of column + check + message) |
| column | str | Column name |
| check | str | Profiler name |
| severity | str | ERROR / WARNING |
| confidence | float | 0.5–0.8 |
| message | str | Finding message |
| explanation | str | NL explanation |
| sample_values | list[str] | Sample affected values |
| status | str | pending / pinned / dismissed |
| decided_by | str | Who decided (human, agent name, auto) |
| decided_at | datetime | When |

**Schema migrations:** Same approach as GoldenMatch — SQLite uses `CREATE TABLE IF NOT EXISTS` with `schema_version` pragma; Postgres uses version column.

**API (same across MCP, A2A, REST):**
- `review_queue(job_name)` → list of pending findings with explanations
- `approve_reject(item_id, decision, reason)` → pins to goldencheck.yml or dismisses
- `review_stats(job_name)` → counts by status

**Integration with existing triage:** The existing `auto_triage()` in `engine/triage.py` continues to work for CLI `--smart` mode. The `ReviewQueue` wraps the same logic but adds persistence and the approval API. When a finding is approved (pinned), it's written to `goldencheck.yml` via the existing `config/writer.py`.

---

## Pipeline Handoff (Golden Suite Integration)

New module: `goldencheck/agent/handoff.py`

The `handoff` skill produces a quality attestation that downstream tools (GoldenFlow, GoldenMatch) can consume. This is the contract between pipeline stages.

### Handoff Artifact Schema

```json
{
  "schema_version": 1,
  "source_tool": "goldencheck",
  "source_version": "1.0.1",
  "timestamp": "2026-03-25T12:00:00Z",
  "job_name": "scan-customers-20260325",
  "file_path": "/data/customers.csv",
  "file_hash": "sha256:abc123...",
  "row_count": 50000,
  "column_count": 12,
  "health": {
    "grade": "B",
    "score": 82
  },
  "summary": {
    "errors": 2,
    "warnings": 5,
    "pinned_rules": 7,
    "review_pending": 3,
    "dismissed": 12
  },
  "columns": {
    "email": {"type": "email", "null_pct": 0.02, "unique_pct": 0.98, "issues": ["6% malformed"]},
    "name": {"type": "person_name", "null_pct": 0.0, "unique_pct": 0.85, "issues": []},
    "...": "..."
  },
  "pinned_rules": [
    {"column": "email", "check": "format_detection", "rule": "format: email"},
    {"column": "age", "check": "range_distribution", "rule": "range: [0, 120]"}
  ],
  "unresolved_findings": [
    {"column": "status", "check": "pattern_consistency", "confidence": 0.65, "message": "3 case variants"}
  ],
  "attestation": "PASS_WITH_WARNINGS"
}
```

### Attestation Levels

| Level | Criteria | Downstream Behavior |
|-------|----------|---------------------|
| `PASS` | No errors, no warnings, no pending reviews | Proceed automatically |
| `PASS_WITH_WARNINGS` | No errors, warnings present but all pinned/dismissed, no pending reviews | Proceed with caution |
| `REVIEW_REQUIRED` | Pending review items exist | Block until reviews resolved |
| `FAIL` | Unresolved errors | Do not proceed |

Downstream tools check `attestation` before proceeding. GoldenFlow/GoldenMatch can enforce policies like "only accept PASS or PASS_WITH_WARNINGS".

---

## Repo & Branch Strategy

**Branch:** `feature/dq-agent` off `main`

**New files:**

| File | Responsibility |
|------|---------------|
| `goldencheck/agent/__init__.py` | Agent package |
| `goldencheck/agent/intelligence.py` | AgentSession, select_strategy, explain_finding, compare_domains |
| `goldencheck/agent/review_queue.py` | ReviewQueue class with memory/SQLite/Postgres backends |
| `goldencheck/agent/handoff.py` | Pipeline handoff artifact generation |
| `goldencheck/a2a/__init__.py` | A2A package |
| `goldencheck/a2a/server.py` | A2A server (aiohttp): agent card, task CRUD, SSE streaming |
| `goldencheck/a2a/skills.py` | Skill dispatch: A2A task → AgentSession method |
| `goldencheck/mcp/agent_tools.py` | New agent-level MCP tools (10 tools) |
| `tests/test_agent.py` | Intelligence layer + strategy selection tests |
| `tests/test_review_queue.py` | Review queue tests (all three backends) |
| `tests/test_a2a.py` | A2A protocol tests (agent card, task lifecycle) |
| `tests/test_handoff.py` | Handoff artifact schema tests |

**Modified files:**

| File | Change |
|------|--------|
| `goldencheck/mcp/server.py` | Register new agent-level tools from agent_tools.py |
| `goldencheck/__init__.py` | Export AgentSession, ReviewQueue |
| `goldencheck/cli/main.py` | Add `goldencheck agent-serve` command (A2A server on port 8100) |
| `pyproject.toml` | Add `aiohttp` as optional dep: `pip install goldencheck[agent]` |

**What doesn't change:** Existing pipeline, profilers, semantic classifier, suppression, confidence, CLI commands, REST API server (has no review-related endpoints — no migration needed, unlike GoldenMatch), MCP tools 1–9, TUI.

**Merge criteria:**
- All new tests pass
- Existing test suite still passes (296 tests)
- A2A agent card validates against A2A spec
- MCP tools work in Claude Desktop
- Demo: another agent discovers and invokes GoldenCheck via A2A
- Review queue works with all three storage backends
- Handoff artifact consumed by GoldenMatch agent (cross-tool test)
- Ruff clean, 100-char line length
