# LLM Boost Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional LLM enhancement pass (`--llm-boost`) that sends representative sample blocks to an LLM to improve profiler accuracy.

**Architecture:** Post-profiler pass — scanner runs all profilers first, then SampleBlockBuilder creates column summaries, LLM provider sends one API call, ResponseParser validates the JSON, FindingsMerger integrates results into the existing findings list.

**Tech Stack:** Anthropic SDK, OpenAI SDK (optional deps), Pydantic 2 (already installed)

**Spec:** `docs/superpowers/specs/2026-03-23-llm-boost-design.md`

---

## Task 1: Add `source` Field to Finding Model

**Files:**
- Modify: `goldencheck/models/finding.py`
- Modify: `goldencheck/reporters/json_reporter.py`
- Modify: `tests/models/test_finding.py`
- Modify: `tests/reporters/test_reporters.py`

- [ ] **Step 1: Write test for source field**

Add to `tests/models/test_finding.py`:
```python
def test_finding_default_source_is_none():
    f = Finding(severity=Severity.INFO, column="x", check="y", message="z")
    assert f.source is None

def test_finding_with_llm_source():
    f = Finding(severity=Severity.ERROR, column="x", check="y", message="z", source="llm")
    assert f.source == "llm"
```

- [ ] **Step 2: Run tests — verify fail**

```bash
pytest tests/models/test_finding.py -v
```

- [ ] **Step 3: Add source field to Finding**

In `goldencheck/models/finding.py`, add after `pinned: bool = False`:
```python
    source: str | None = None
```

- [ ] **Step 4: Run tests — verify pass**

```bash
pytest tests/models/test_finding.py -v
```

- [ ] **Step 5: Write test for JSON reporter source field**

Add to `tests/reporters/test_reporters.py`:
```python
def test_json_reporter_includes_source_when_llm():
    findings = [Finding(severity=Severity.ERROR, column="x", check="y", message="bad", source="llm")]
    profile = DatasetProfile(file_path="test.csv", row_count=100, column_count=5, columns=[])
    buf = io.StringIO()
    report_json(findings, profile, buf)
    data = json.loads(buf.getvalue())
    assert data["findings"][0]["source"] == "llm"

def test_json_reporter_omits_source_when_none():
    findings = [Finding(severity=Severity.INFO, column="x", check="y", message="ok")]
    profile = DatasetProfile(file_path="test.csv", row_count=100, column_count=5, columns=[])
    buf = io.StringIO()
    report_json(findings, profile, buf)
    data = json.loads(buf.getvalue())
    assert "source" not in data["findings"][0]
```

- [ ] **Step 6: Update JSON reporter**

In `goldencheck/reporters/json_reporter.py`, replace the findings list comprehension with:
```python
        "findings": [
            {
                k: v for k, v in {
                    "severity": f.severity.name.lower(),
                    "column": f.column,
                    "check": f.check,
                    "message": f.message,
                    "affected_rows": f.affected_rows,
                    "sample_values": f.sample_values,
                    "source": f.source,
                }.items() if v is not None
            }
            for f in findings
        ],
```

- [ ] **Step 7: Run all tests**

```bash
pytest -v
```

- [ ] **Step 8: Commit**

```bash
git add goldencheck/models/finding.py goldencheck/reporters/json_reporter.py tests/models/test_finding.py tests/reporters/test_reporters.py
git commit -m "feat: add source field to Finding model and JSON reporter"
```

---

## Task 2: Pydantic Response Models + Prompts

**Files:**
- Create: `goldencheck/llm/__init__.py`
- Create: `goldencheck/llm/prompts.py`
- Create: `tests/llm/__init__.py`
- Create: `tests/llm/test_prompts.py`

- [ ] **Step 1: Write tests for response models**

`tests/llm/test_prompts.py`:
```python
from goldencheck.llm.prompts import LLMResponse, LLMColumnAssessment, LLMIssue, LLMUpgrade, LLMRelation

def test_parse_full_response():
    data = {
        "columns": {
            "email": {
                "semantic_type": "email",
                "issues": [{"severity": "error", "check": "format", "message": "bad emails", "affected_values": ["x"]}],
                "upgrades": [{"original_check": "nullability", "original_severity": "info", "new_severity": "warning", "reason": "emails should not be null"}],
                "downgrades": [],
            }
        },
        "relations": [{"type": "temporal_order", "columns": ["start", "end"], "reasoning": "start before end"}],
    }
    resp = LLMResponse(**data)
    assert "email" in resp.columns
    assert resp.columns["email"].semantic_type == "email"
    assert len(resp.columns["email"].issues) == 1
    assert len(resp.relations) == 1

def test_parse_empty_response():
    resp = LLMResponse()
    assert resp.columns == {}
    assert resp.relations == []

def test_parse_minimal_column():
    col = LLMColumnAssessment(semantic_type="identifier")
    assert col.issues == []
    assert col.upgrades == []
    assert col.downgrades == []
```

- [ ] **Step 2: Implement prompts.py**

`goldencheck/llm/__init__.py`: empty file.
`tests/llm/__init__.py`: empty file.

`goldencheck/llm/prompts.py`:
```python
"""LLM prompt templates and response Pydantic models."""
from __future__ import annotations
from pydantic import BaseModel

SYSTEM_PROMPT = """You are a data quality analyst. You are given a dataset summary with representative samples for each column, along with findings from automated profilers.

Your job is to:
1. Identify data quality issues the profilers missed
2. Upgrade severity of findings that are worse than the profiler assessed
3. Downgrade severity of findings that are false positives
4. Identify cross-column relationships (temporal ordering, semantic dependencies)

For each column, determine its semantic type (person_name, email, phone, date, currency, address, country_code, state_code, enum, identifier, free_text, etc.) and use that understanding to assess data quality.

Respond with valid JSON matching this schema:
{
  "columns": {
    "<column_name>": {
      "semantic_type": "<type>",
      "issues": [{"severity": "error|warning|info", "check": "<check_name>", "message": "<description>", "affected_values": ["val1"]}],
      "upgrades": [{"original_check": "<check>", "original_severity": "<sev>", "new_severity": "<sev>", "reason": "<why>"}],
      "downgrades": [{"original_check": "<check>", "original_severity": "<sev>", "new_severity": "<sev>", "reason": "<why>"}]
    }
  },
  "relations": [{"type": "<relation_type>", "columns": ["col_a", "col_b"], "reasoning": "<why>"}]
}

Only include columns where you have something to report. Omit columns with no issues, upgrades, or downgrades."""


class LLMIssue(BaseModel):
    severity: str
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

- [ ] **Step 3: Run tests — verify pass**

```bash
pytest tests/llm/ -v
```

- [ ] **Step 4: Commit**

```bash
git add goldencheck/llm/ tests/llm/
git commit -m "feat: add LLM response models and system prompt"
```

---

## Task 3: Sample Block Builder

**Files:**
- Create: `goldencheck/llm/sample_block.py`
- Create: `tests/llm/test_sample_block.py`

- [ ] **Step 1: Write tests**

`tests/llm/test_sample_block.py`:
```python
import polars as pl
from goldencheck.llm.sample_block import build_sample_blocks
from goldencheck.models.finding import Finding, Severity

def test_sample_block_contains_metadata():
    df = pl.DataFrame({"name": ["Alice", "Bob", None, "Charlie", "Diana"] * 20})
    findings = [Finding(severity=Severity.WARNING, column="name", check="nullability", message="has nulls")]
    blocks = build_sample_blocks(df, findings)
    assert "name" in blocks
    block = blocks["name"]
    assert "column" in block
    assert block["column"] == "name"
    assert "row_count" in block
    assert block["row_count"] == 100
    assert "null_count" in block

def test_sample_block_contains_values():
    df = pl.DataFrame({"status": ["active"] * 90 + ["inactive"] * 8 + ["UNKNOWN"] * 2})
    blocks = build_sample_blocks(df, [])
    block = blocks["status"]
    assert "top_values" in block
    assert "rare_values" in block
    assert len(block["top_values"]) <= 5

def test_sample_block_includes_flagged_values():
    df = pl.DataFrame({"email": ["a@b.com"] * 100})
    findings = [Finding(severity=Severity.WARNING, column="email", check="format",
                        message="bad", sample_values=["not-email", "also-bad"])]
    blocks = build_sample_blocks(df, findings)
    assert "not-email" in blocks["email"]["flagged_values"]

def test_sample_block_includes_findings():
    df = pl.DataFrame({"age": list(range(100))})
    findings = [Finding(severity=Severity.WARNING, column="age", check="range", message="outliers detected")]
    blocks = build_sample_blocks(df, findings)
    assert len(blocks["age"]["existing_findings"]) == 1

def test_wide_dataset_limited_to_50():
    data = {f"col_{i}": list(range(100)) for i in range(100)}
    df = pl.DataFrame(data)
    findings = [Finding(severity=Severity.ERROR, column="col_0", check="x", message="y")]
    blocks = build_sample_blocks(df, findings, max_columns=50)
    assert len(blocks) == 50
    assert "col_0" in blocks  # column with findings should be included
```

- [ ] **Step 2: Implement sample_block.py**

`goldencheck/llm/sample_block.py`:
```python
"""Build representative sample blocks from DataFrame + findings."""
from __future__ import annotations
import logging
import random
from collections import defaultdict
import polars as pl
from goldencheck.models.finding import Finding

logger = logging.getLogger(__name__)


def build_sample_blocks(
    df: pl.DataFrame,
    findings: list[Finding],
    max_columns: int = 50,
) -> dict[str, dict]:
    """Build a representative sample block for each column."""
    random.seed(42)

    # If too many columns, prioritize those with most findings
    columns = list(df.columns)
    if len(columns) > max_columns:
        logger.warning(
            "LLM boost limited to %d columns (dataset has %d). "
            "Columns with most findings prioritized.", max_columns, len(columns)
        )
        finding_counts = defaultdict(int)
        for f in findings:
            finding_counts[f.column] += 1
        columns = sorted(columns, key=lambda c: finding_counts[c], reverse=True)[:max_columns]

    # Index findings by column
    findings_by_col = defaultdict(list)
    for f in findings:
        findings_by_col[f.column].append(f)

    blocks = {}
    for col_name in columns:
        col = df[col_name]
        non_null = col.drop_nulls()

        # Metadata
        block: dict = {
            "column": col_name,
            "dtype": str(col.dtype),
            "row_count": len(df),
            "null_count": col.null_count(),
            "null_pct": round(col.null_count() / len(df), 3) if len(df) > 0 else 0,
            "unique_count": non_null.n_unique() if len(non_null) > 0 else 0,
        }

        # Top values (most frequent)
        if len(non_null) > 0:
            vc = non_null.value_counts().sort("count", descending=True)
            col_val_name = vc.columns[0]
            top = vc.head(5)
            block["top_values"] = [
                {"value": str(row[col_val_name]), "count": row["count"]}
                for row in top.iter_rows(named=True)
            ]

            # Rare values (least frequent)
            rare = vc.tail(5)
            block["rare_values"] = [
                {"value": str(row[col_val_name]), "count": row["count"]}
                for row in rare.iter_rows(named=True)
            ]

            # Random sample from middle
            all_vals = non_null.to_list()
            sample_size = min(5, len(all_vals))
            block["random_sample"] = [str(v) for v in random.sample(all_vals, sample_size)]
        else:
            block["top_values"] = []
            block["rare_values"] = []
            block["random_sample"] = []

        # Flagged values from profiler findings
        flagged = set()
        for f in findings_by_col.get(col_name, []):
            flagged.update(f.sample_values)
        block["flagged_values"] = list(flagged)

        # Existing findings
        block["existing_findings"] = [
            {"severity": f.severity.name.lower(), "check": f.check, "message": f.message}
            for f in findings_by_col.get(col_name, [])
        ]

        blocks[col_name] = block

    return blocks
```

- [ ] **Step 3: Run tests — verify pass**

```bash
pytest tests/llm/test_sample_block.py -v
```

- [ ] **Step 4: Commit**

```bash
git add goldencheck/llm/sample_block.py tests/llm/test_sample_block.py
git commit -m "feat: add representative sample block builder"
```

---

## Task 4: Response Parser

**Files:**
- Create: `goldencheck/llm/parser.py`
- Create: `tests/llm/test_parser.py`

- [ ] **Step 1: Write tests**

`tests/llm/test_parser.py`:
```python
import json
from goldencheck.llm.parser import parse_llm_response
from goldencheck.llm.prompts import LLMResponse

def test_parse_valid_json():
    raw = json.dumps({
        "columns": {"email": {"semantic_type": "email", "issues": [], "upgrades": [], "downgrades": []}},
        "relations": [],
    })
    result = parse_llm_response(raw)
    assert isinstance(result, LLMResponse)
    assert "email" in result.columns

def test_parse_malformed_json_returns_none():
    result = parse_llm_response("this is not json")
    assert result is None

def test_parse_invalid_schema_returns_none():
    raw = json.dumps({"columns": "not a dict"})
    result = parse_llm_response(raw)
    assert result is None

def test_parse_with_markdown_fences():
    raw = '```json\n{"columns": {}, "relations": []}\n```'
    result = parse_llm_response(raw)
    assert isinstance(result, LLMResponse)
```

- [ ] **Step 2: Implement parser.py**

`goldencheck/llm/parser.py`:
```python
"""Parse and validate LLM JSON responses."""
from __future__ import annotations
import json
import logging
import re
from goldencheck.llm.prompts import LLMResponse

logger = logging.getLogger(__name__)


def parse_llm_response(raw: str) -> LLMResponse | None:
    """Parse raw LLM text into validated LLMResponse. Returns None on failure."""
    # Strip markdown code fences if present
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', raw.strip())
    cleaned = re.sub(r'\n?```\s*$', '', cleaned)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning("LLM response is not valid JSON: %s", e)
        return None

    try:
        return LLMResponse(**data)
    except Exception as e:
        logger.warning("LLM response failed schema validation: %s", e)
        return None
```

- [ ] **Step 3: Run tests — verify pass**

```bash
pytest tests/llm/test_parser.py -v
```

- [ ] **Step 4: Commit**

```bash
git add goldencheck/llm/parser.py tests/llm/test_parser.py
git commit -m "feat: add LLM response parser with validation"
```

---

## Task 5: Findings Merger

**Files:**
- Create: `goldencheck/llm/merger.py`
- Create: `tests/llm/test_merger.py`

- [ ] **Step 1: Write tests**

`tests/llm/test_merger.py`:
```python
from goldencheck.llm.merger import merge_llm_findings
from goldencheck.llm.prompts import LLMResponse, LLMColumnAssessment, LLMIssue, LLMUpgrade, LLMDowngrade, LLMRelation
from goldencheck.models.finding import Finding, Severity

def test_new_issue_added():
    findings = []
    response = LLMResponse(columns={"name": LLMColumnAssessment(
        semantic_type="person_name",
        issues=[LLMIssue(severity="error", check="type_inference", message="numbers in name", affected_values=["123"])],
    )})
    result = merge_llm_findings(findings, response)
    assert len(result) == 1
    assert result[0].source == "llm"
    assert result[0].severity == Severity.ERROR
    assert result[0].sample_values == ["123"]

def test_upgrade_changes_severity():
    findings = [Finding(severity=Severity.INFO, column="email", check="nullability", message="optional")]
    response = LLMResponse(columns={"email": LLMColumnAssessment(
        semantic_type="email",
        upgrades=[LLMUpgrade(original_check="nullability", original_severity="info", new_severity="warning", reason="emails should be required")],
    )})
    result = merge_llm_findings(findings, response)
    assert result[0].severity == Severity.WARNING
    assert result[0].source == "llm"

def test_downgrade_changes_severity():
    findings = [Finding(severity=Severity.WARNING, column="phone", check="pattern_consistency", message="mixed")]
    response = LLMResponse(columns={"phone": LLMColumnAssessment(
        semantic_type="phone",
        downgrades=[LLMDowngrade(original_check="pattern_consistency", original_severity="warning", new_severity="info", reason="mixed formats are normal")],
    )})
    result = merge_llm_findings(findings, response)
    assert result[0].severity == Severity.INFO
    assert result[0].source == "llm"

def test_upgrade_nonexistent_creates_new_issue():
    findings = []
    response = LLMResponse(columns={"x": LLMColumnAssessment(
        semantic_type="id",
        upgrades=[LLMUpgrade(original_check="uniqueness", original_severity="info", new_severity="error", reason="IDs must be unique")],
    )})
    result = merge_llm_findings(findings, response)
    assert len(result) == 1
    assert result[0].severity == Severity.ERROR
    assert result[0].source == "llm"

def test_downgrade_nonexistent_ignored():
    findings = [Finding(severity=Severity.ERROR, column="a", check="b", message="c")]
    response = LLMResponse(columns={"x": LLMColumnAssessment(
        semantic_type="id",
        downgrades=[LLMDowngrade(original_check="z", original_severity="warning", new_severity="info", reason="not real")],
    )})
    result = merge_llm_findings(findings, response)
    assert len(result) == 1  # original unchanged, downgrade ignored

def test_malformed_response_returns_original():
    findings = [Finding(severity=Severity.INFO, column="a", check="b", message="c")]
    result = merge_llm_findings(findings, None)
    assert len(result) == 1
    assert result[0].source is None  # untouched

def test_relation_creates_finding():
    findings = []
    response = LLMResponse(relations=[
        LLMRelation(type="temporal_order", columns=["signup_date", "last_login"], reasoning="signup before login"),
    ])
    result = merge_llm_findings(findings, response)
    assert len(result) == 1
    assert result[0].column == "last_login,signup_date"  # alphabetically sorted
    assert result[0].check == "temporal_order"
    assert result[0].source == "llm"
```

- [ ] **Step 2: Implement merger.py**

`goldencheck/llm/merger.py`:
```python
"""Merge LLM response into existing findings list."""
from __future__ import annotations
import logging
from dataclasses import replace
from goldencheck.llm.prompts import LLMResponse
from goldencheck.models.finding import Finding, Severity

logger = logging.getLogger(__name__)

SEVERITY_MAP = {"error": Severity.ERROR, "warning": Severity.WARNING, "info": Severity.INFO}


def merge_llm_findings(
    findings: list[Finding],
    response: LLMResponse | None,
) -> list[Finding]:
    """Merge LLM response into findings. Returns a new list (never mutates originals)."""
    if response is None:
        return list(findings)

    result = list(findings)

    # Build lookup index: (column, check) -> index in result
    index = {}
    for i, f in enumerate(result):
        index[(f.column, f.check)] = i

    # Process per-column assessments
    for col_name, assessment in response.columns.items():
        # New issues
        for issue in assessment.issues:
            sev = SEVERITY_MAP.get(issue.severity.lower(), Severity.WARNING)
            result.append(Finding(
                severity=sev,
                column=col_name,
                check=issue.check,
                message=issue.message,
                sample_values=issue.affected_values,
                source="llm",
            ))

        # Upgrades (use dataclasses.replace to avoid mutation)
        for upgrade in assessment.upgrades:
            key = (col_name, upgrade.original_check)
            if key in index:
                old = result[index[key]]
                result[index[key]] = replace(
                    old,
                    severity=SEVERITY_MAP.get(upgrade.new_severity.lower(), old.severity),
                    message=f"{old.message} [LLM: {upgrade.reason}]",
                    source="llm",
                )
            else:
                # Create as new issue
                result.append(Finding(
                    severity=SEVERITY_MAP.get(upgrade.new_severity.lower(), Severity.WARNING),
                    column=col_name,
                    check=upgrade.original_check,
                    message=upgrade.reason,
                    source="llm",
                ))

        # Downgrades (use dataclasses.replace to avoid mutation)
        for downgrade in assessment.downgrades:
            key = (col_name, downgrade.original_check)
            if key in index:
                old = result[index[key]]
                result[index[key]] = replace(
                    old,
                    severity=SEVERITY_MAP.get(downgrade.new_severity.lower(), old.severity),
                    message=f"{old.message} [LLM: {downgrade.reason}]",
                    source="llm",
                )
            # else: silently ignore

    # Process relations
    for relation in response.relations:
        col_key = ",".join(sorted(relation.columns))
        result.append(Finding(
            severity=Severity.WARNING,
            column=col_key,
            check=relation.type,
            message=relation.reasoning,
            source="llm",
        ))

    return result
```

- [ ] **Step 3: Run tests — verify pass**

```bash
pytest tests/llm/test_merger.py -v
```

- [ ] **Step 4: Commit**

```bash
git add goldencheck/llm/merger.py tests/llm/test_merger.py
git commit -m "feat: add findings merger for LLM response integration"
```

---

## Task 6: LLM Providers (Anthropic + OpenAI)

**Files:**
- Create: `goldencheck/llm/providers.py`
- Modify: `pyproject.toml` (add llm optional deps)

- [ ] **Step 1: Add optional dependencies to pyproject.toml**

Add to `pyproject.toml` under `[project.optional-dependencies]`:
```toml
llm = [
    "anthropic>=0.30",
    "openai>=1.30",
]
```

- [ ] **Step 2: Implement providers.py**

`goldencheck/llm/providers.py`:
```python
"""LLM provider wrappers for Anthropic and OpenAI."""
from __future__ import annotations
import json
import logging
import os
from goldencheck.llm.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
}


def check_llm_available(provider: str) -> None:
    """Check that LLM dependencies and API key are available. Raises on failure."""
    if provider == "anthropic":
        try:
            import anthropic  # noqa: F401
        except ImportError:
            raise SystemExit("LLM boost requires extra dependencies. Install with: pip install goldencheck[llm]")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("LLM boost requires ANTHROPIC_API_KEY environment variable.")
    elif provider == "openai":
        try:
            import openai  # noqa: F401
        except ImportError:
            raise SystemExit("LLM boost requires extra dependencies. Install with: pip install goldencheck[llm]")
        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("LLM boost requires OPENAI_API_KEY environment variable.")
    else:
        raise SystemExit(f"Unknown LLM provider: {provider}. Use 'anthropic' or 'openai'.")


def call_llm(provider: str, user_prompt: str) -> str:
    """Send prompt to LLM and return raw response text."""
    model = os.environ.get("GOLDENCHECK_LLM_MODEL", DEFAULT_MODELS.get(provider, ""))

    if provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text

    elif provider == "openai":
        import openai
        client = openai.OpenAI()
        response = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content

    raise ValueError(f"Unknown provider: {provider}")
```

- [ ] **Step 3: Install LLM deps**

```bash
pip install -e ".[llm]"
```

- [ ] **Step 4: Commit**

```bash
git add goldencheck/llm/providers.py pyproject.toml
git commit -m "feat: add Anthropic and OpenAI provider wrappers"
```

---

## Task 7: Wire LLM Boost into Scanner + CLI

**Files:**
- Modify: `goldencheck/engine/scanner.py`
- Modify: `goldencheck/cli/main.py`
- Modify: `tests/cli/test_cli.py`

- [ ] **Step 1: Refactor `scan_file` to optionally return the sampled DataFrame**

Add `return_sample: bool = False` parameter to `scan_file`. When True, return a 3-tuple `(findings, profile, sample)` instead of 2-tuple. This avoids double file reads in `scan_file_with_llm`. Update existing callers to not break (default is False, returns 2-tuple).

Then add `scan_file_with_llm` to the bottom of `goldencheck/engine/scanner.py`:
```python
def scan_file_with_llm(
    path: Path,
    provider: str = "anthropic",
    sample_size: int = 100_000,
) -> tuple[list[Finding], DatasetProfile]:
    """Scan a file with profilers, then enhance with LLM boost."""
    import json
    from goldencheck.llm.sample_block import build_sample_blocks
    from goldencheck.llm.providers import call_llm, check_llm_available
    from goldencheck.llm.parser import parse_llm_response
    from goldencheck.llm.merger import merge_llm_findings

    # Check LLM is available BEFORE doing any work
    check_llm_available(provider)

    # Run profilers first — returns findings, profile, AND the sampled df
    findings, profile, sample = scan_file(path, sample_size=sample_size, return_sample=True)

    # Build sample blocks from the already-loaded sample (no double read)
    blocks = build_sample_blocks(sample, findings)

    # Build user prompt
    user_prompt = "Here is the dataset summary:\n\n" + json.dumps(blocks, indent=2, default=str)

    # Call LLM
    try:
        raw_response = call_llm(provider, user_prompt)
        llm_response = parse_llm_response(raw_response)
        if llm_response:
            findings = merge_llm_findings(findings, llm_response)
            logger.info("LLM boost: merged %d column assessments, %d relations",
                       len(llm_response.columns), len(llm_response.relations))
        else:
            logger.warning("LLM response could not be parsed. Showing profiler-only results.")
    except SystemExit:
        raise
    except Exception as e:
        logger.warning("LLM boost failed: %s. Showing profiler-only results.", e)

    # Re-sort by severity
    findings.sort(key=lambda f: f.severity, reverse=True)
    return findings, profile
```

- [ ] **Step 2: Update CLI — add flags to scan command**

In `goldencheck/cli/main.py`, update the `scan` command:
```python
@app.command()
def scan(
    file: Path = typer.Argument(..., help="Data file to profile."),
    no_tui: bool = typer.Option(False, "--no-tui", help="Disable TUI."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
    llm_boost: bool = typer.Option(False, "--llm-boost", help="Enable LLM enhancement."),
    llm_provider: str = typer.Option("anthropic", "--llm-provider", help="LLM provider: anthropic or openai."),
) -> None:
    """Profile a data file and report findings."""
    _do_scan(file, no_tui=no_tui, json_output=json_output, llm_boost=llm_boost, llm_provider=llm_provider)
```

- [ ] **Step 3: Update `_do_scan` to accept and use LLM flags**

Update `_do_scan` signature and body to call `scan_file_with_llm` when `llm_boost=True`.

- [ ] **Step 4: Update `main()` callback hand-rolled parser**

Add to the `while args` loop:
```python
elif arg == "--llm-boost":
    llm_boost = True
elif arg == "--llm-provider":
    llm_provider = args.pop(0)
```

Initialize `llm_boost = False` and `llm_provider = "anthropic"` before the loop. Pass them through to `_do_scan`.

- [ ] **Step 5: Update `review` command to use `_do_scan`**

Refactor `review` to call `_do_scan` with LLM flags, plus its additional validation merge logic.

- [ ] **Step 6: Write CLI test**

Add to `tests/cli/test_cli.py`:
```python
def test_llm_boost_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = runner.invoke(app, ["scan", str(FIXTURES / "simple.csv"), "--llm-boost", "--no-tui"])
    assert result.exit_code != 0
```

- [ ] **Step 7: Run all tests**

```bash
pytest -v
```

- [ ] **Step 8: Commit**

```bash
git add goldencheck/engine/scanner.py goldencheck/cli/main.py tests/cli/test_cli.py
git commit -m "feat: wire LLM boost into scanner and CLI"
```

---

## Task 8: TUI Badge + Update Findings Tab

**Files:**
- Modify: `goldencheck/tui/findings.py`

- [ ] **Step 1: Update Findings tab to show LLM badge**

In the `FindingsPane` DataTable construction, add an LLM badge column. When building rows, check `f.source == "llm"` and display `[LLM]` badge.

```python
for i, f in enumerate(self.findings):
    pin = "[x]" if f.pinned else "[ ]"
    sev = f.severity.name
    badge = "[LLM]" if f.source == "llm" else ""
    table.add_row(pin, sev, f.column, f.check, f.message[:55], str(f.affected_rows), badge, key=str(i))
```

- [ ] **Step 2: Run tests**

```bash
pytest -v
```

- [ ] **Step 3: Commit**

```bash
git add goldencheck/tui/findings.py
git commit -m "feat: show LLM badge in TUI findings tab"
```

---

## Task 9: Integration Test + Benchmark Rerun

**Files:**
- Create: `tests/llm/test_integration.py`
- Modify: `benchmarks/goldencheck_benchmark.py`

- [ ] **Step 1: Write integration test with mocked LLM**

`tests/llm/test_integration.py`:
```python
"""Integration test: full LLM boost flow with mocked provider."""
import json
from pathlib import Path
from unittest.mock import patch
from goldencheck.engine.scanner import scan_file_with_llm

FIXTURES = Path(__file__).parent.parent / "fixtures"

MOCK_RESPONSE = json.dumps({
    "columns": {
        "email": {
            "semantic_type": "email",
            "issues": [{"severity": "error", "check": "semantic", "message": "non-emails in email column", "affected_values": ["not-an-email"]}],
            "upgrades": [],
            "downgrades": [],
        }
    },
    "relations": [],
})

@patch("goldencheck.llm.providers.check_llm_available")
@patch("goldencheck.llm.providers.call_llm", return_value=MOCK_RESPONSE)
def test_llm_boost_integration(mock_call, mock_check):
    findings, profile = scan_file_with_llm(FIXTURES / "simple.csv", provider="anthropic")
    assert any(f.source == "llm" for f in findings)
    assert any(f.check == "semantic" for f in findings)
    mock_call.assert_called_once()
```

- [ ] **Step 2: Run full test suite**

```bash
pytest -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/llm/test_integration.py
git commit -m "test: add LLM boost integration test with mocked provider"
```

- [ ] **Step 4: Push all changes**

```bash
git push
```
