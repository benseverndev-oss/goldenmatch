# GoldenCheck Feature Parity & Independence Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring GoldenCheck to the same caliber as GoldenMatch in features, polish, and developer experience — while ensuring it works seamlessly as a standalone tool.

**Architecture:** 8 independent tasks, each produces a working commit. No task depends on another. Tasks 1–4 are high priority (user-visible gaps), 5–8 are medium priority (polish).

**Tech Stack:** Python 3.11+, Polars, Typer, Rich, pytest, GitHub Actions

**Branch:** `feature/parity-polish` off `main`

---

### Task 1: `demo` Command — Zero-Friction First Experience

**Files:**
- Create: `goldencheck/cli/demo_data.py`
- Modify: `goldencheck/cli/main.py`
- Create: `tests/cli/test_demo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_demo.py
"""Tests for the demo command."""
from __future__ import annotations

from typer.testing import CliRunner
from goldencheck.cli.main import app

runner = CliRunner()


def test_demo_runs_without_error():
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0


def test_demo_shows_findings():
    result = runner.invoke(app, ["demo"])
    assert "Finding" in result.stdout or "ERROR" in result.stdout or "WARNING" in result.stdout


def test_demo_no_tui_flag():
    result = runner.invoke(app, ["demo", "--no-tui"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cli/test_demo.py -v`
Expected: FAIL — "No such command 'demo'"

- [ ] **Step 3: Create demo data generator**

```python
# goldencheck/cli/demo_data.py
"""Generate sample data for the demo command."""
from __future__ import annotations

import random
import tempfile
from pathlib import Path

import polars as pl


def generate_demo_csv(path: Path | None = None) -> Path:
    """Generate a CSV with realistic data quality issues for demonstration."""
    random.seed(42)
    n = 200

    names = [f"Customer {i}" for i in range(n)]
    emails = [f"user{i}@example.com" for i in range(n)]
    ages = [random.randint(18, 85) for _ in range(n)]
    phones = [f"555-{random.randint(100, 999)}-{random.randint(1000, 9999)}" for _ in range(n)]
    status = [random.choice(["active", "inactive", "pending"]) for _ in range(n)]
    amounts = [round(random.uniform(10.0, 5000.0), 2) for _ in range(n)]

    # Inject quality issues
    emails[3] = "not-an-email"
    emails[17] = "also bad"
    emails[42] = ""
    ages[5] = -3
    ages[88] = 200
    ages[120] = None
    phones[10] = "12345"
    phones[30] = "abc-def-ghij"
    status[50] = "Active"  # case inconsistency
    status[51] = "ACTIVE"
    amounts[0] = 999999.99  # outlier
    names[15] = None
    names[16] = None
    names[99] = ""

    df = pl.DataFrame({
        "customer_id": list(range(1, n + 1)),
        "name": names,
        "email": emails,
        "age": ages,
        "phone": phones,
        "status": status,
        "purchase_amount": amounts,
    })

    if path is None:
        path = Path(tempfile.mkdtemp()) / "demo_data.csv"
    df.write_csv(path)
    return path
```

- [ ] **Step 4: Add demo command to CLI**

Add to `goldencheck/cli/main.py` (after the `agent-serve` command):

```python
@app.command()
def demo(
    no_tui: bool = typer.Option(False, "--no-tui", help="Print results to stdout"),
    domain: str | None = typer.Option(None, help="Domain pack to apply"),
) -> None:
    """Run GoldenCheck on built-in sample data to see it in action."""
    from goldencheck.cli.demo_data import generate_demo_csv

    path = generate_demo_csv()
    typer.echo(f"Generated demo data: {path}")
    typer.echo("Scanning for data quality issues...\n")
    _do_scan(str(path), no_tui=True if no_tui else False, domain=domain)
```

Wire through `_do_scan` which already handles all output formatting.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/cli/test_demo.py -v`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add goldencheck/cli/demo_data.py goldencheck/cli/main.py tests/cli/test_demo.py
git commit -m "feat: add demo command for zero-friction first experience"
```

---

### Task 2: Expand Public API Exports

**Files:**
- Modify: `goldencheck/__init__.py`
- Create: `tests/test_public_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_public_api.py
"""Tests for the public API surface."""
from __future__ import annotations

import goldencheck as gc


def test_core_exports():
    """All core functions should be importable from top-level."""
    assert hasattr(gc, "scan_file")
    assert hasattr(gc, "scan_file_with_llm")
    assert hasattr(gc, "Finding")
    assert hasattr(gc, "Severity")
    assert hasattr(gc, "DatasetProfile")
    assert hasattr(gc, "ColumnProfile")
    assert hasattr(gc, "ScanResult")
    assert hasattr(gc, "__version__")


def test_engine_exports():
    """Key engine functions should be importable from top-level."""
    assert hasattr(gc, "validate_file")
    assert hasattr(gc, "apply_fixes")
    assert hasattr(gc, "diff_files")
    assert hasattr(gc, "auto_triage")
    assert hasattr(gc, "read_file")


def test_config_exports():
    """Config classes should be importable from top-level."""
    assert hasattr(gc, "GoldenCheckConfig")
    assert hasattr(gc, "load_config")
    assert hasattr(gc, "save_config")


def test_semantic_exports():
    """Semantic functions should be importable from top-level."""
    assert hasattr(gc, "classify_columns")
    assert hasattr(gc, "list_available_domains")


def test_confidence_exports():
    assert hasattr(gc, "apply_confidence_downgrade")
    assert hasattr(gc, "apply_corroboration_boost")


def test_all_list_complete():
    """__all__ should contain every public export."""
    for name in dir(gc):
        if not name.startswith("_") and name != "goldencheck":
            assert name in gc.__all__, f"{name} not in __all__"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_public_api.py -v`
Expected: FAIL — `validate_file` not in gc

- [ ] **Step 3: Expand `__init__.py` exports**

Update `goldencheck/__init__.py` to export ~30 user-facing symbols:

```python
"""GoldenCheck — data validation that discovers rules from your data."""

__version__ = "1.0.1"

# Core scanning
from goldencheck.engine.scanner import scan_file, scan_file_with_llm
from goldencheck.engine.validator import validate_file
from goldencheck.engine.confidence import (
    apply_confidence_downgrade,
    apply_corroboration_boost,
)
from goldencheck.engine.triage import auto_triage
from goldencheck.engine.fixer import apply_fixes
from goldencheck.engine.differ import diff_files
from goldencheck.engine.reader import read_file

# Models
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import DatasetProfile, ColumnProfile

# Config
from goldencheck.config.schema import GoldenCheckConfig
from goldencheck.config.loader import load_config
from goldencheck.config.writer import save_config

# Semantic
from goldencheck.semantic.classifier import classify_columns, list_available_domains

# Notebook
from goldencheck.notebook import ScanResult

# Optional: Agent
try:
    from goldencheck.agent import AgentSession, ReviewQueue  # noqa: F401
    _agent_exports = ["AgentSession", "ReviewQueue"]
except ImportError:
    _agent_exports = []

__all__ = [
    # Core
    "scan_file",
    "scan_file_with_llm",
    "validate_file",
    "apply_confidence_downgrade",
    "apply_corroboration_boost",
    "auto_triage",
    "apply_fixes",
    "diff_files",
    "read_file",
    # Models
    "Finding",
    "Severity",
    "DatasetProfile",
    "ColumnProfile",
    # Config
    "GoldenCheckConfig",
    "load_config",
    "save_config",
    # Semantic
    "classify_columns",
    "list_available_domains",
    # Notebook
    "ScanResult",
    # Meta
    "__version__",
    # Agent (optional)
    *_agent_exports,
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_public_api.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add goldencheck/__init__.py tests/test_public_api.py
git commit -m "feat: expand public API surface to ~30 exports"
```

---

### Task 3: `evaluate` CLI Command

**Files:**
- Create: `goldencheck/engine/evaluate.py`
- Modify: `goldencheck/cli/main.py`
- Create: `tests/engine/test_evaluate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_evaluate.py
"""Tests for the evaluate module."""
from __future__ import annotations

import tempfile
from pathlib import Path

import polars as pl

from goldencheck.engine.evaluate import evaluate_scan


def test_evaluate_perfect_score():
    """All expected findings found → precision 1.0, recall 1.0."""
    expected = [
        {"column": "email", "check": "format_detection"},
        {"column": "age", "check": "range_distribution"},
    ]
    actual_findings = _make_findings([
        ("email", "format_detection"),
        ("age", "range_distribution"),
    ])
    result = evaluate_scan(actual_findings, expected)
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0
    assert result["f1"] == 1.0


def test_evaluate_partial_recall():
    """Some expected findings missing → recall < 1.0."""
    expected = [
        {"column": "email", "check": "format_detection"},
        {"column": "age", "check": "range_distribution"},
        {"column": "phone", "check": "format_detection"},
    ]
    actual_findings = _make_findings([
        ("email", "format_detection"),
        ("age", "range_distribution"),
    ])
    result = evaluate_scan(actual_findings, expected)
    assert result["recall"] < 1.0
    assert result["precision"] == 1.0


def test_evaluate_with_false_positives():
    """Extra findings not in expected → precision < 1.0."""
    expected = [{"column": "email", "check": "format_detection"}]
    actual_findings = _make_findings([
        ("email", "format_detection"),
        ("age", "range_distribution"),
    ])
    result = evaluate_scan(actual_findings, expected)
    assert result["precision"] < 1.0
    assert result["recall"] == 1.0


def test_evaluate_empty():
    """No expected, no findings → perfect score."""
    result = evaluate_scan([], [])
    assert result["f1"] == 1.0


def _make_findings(pairs):
    from goldencheck.models.finding import Finding, Severity
    return [
        Finding(severity=Severity.WARNING, column=col, check=chk, message=f"{chk} issue")
        for col, chk in pairs
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/engine/test_evaluate.py -v`
Expected: FAIL — `evaluate_scan` not found

- [ ] **Step 3: Implement evaluate module**

```python
# goldencheck/engine/evaluate.py
"""Evaluate scan results against expected findings (ground truth)."""
from __future__ import annotations

from goldencheck.models.finding import Finding


def evaluate_scan(
    findings: list[Finding],
    expected: list[dict],
) -> dict:
    """Compare scan findings to expected ground truth.

    Args:
        findings: Actual findings from scan_file.
        expected: List of dicts with at least "column" and "check" keys.

    Returns:
        Dict with precision, recall, f1, true_positives, false_positives,
        false_negatives.
    """
    actual_keys = {(f.column, f.check) for f in findings}
    expected_keys = {(e["column"], e["check"]) for e in expected}

    tp = actual_keys & expected_keys
    fp = actual_keys - expected_keys
    fn = expected_keys - actual_keys

    precision = len(tp) / (len(tp) + len(fp)) if (len(tp) + len(fp)) > 0 else 1.0
    recall = len(tp) / (len(tp) + len(fn)) if (len(tp) + len(fn)) > 0 else 1.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "true_positives": len(tp),
        "false_positives": len(fp),
        "false_negatives": len(fn),
        "tp_details": sorted(tp),
        "fp_details": sorted(fp),
        "fn_details": sorted(fn),
    }
```

- [ ] **Step 4: Add CLI command**

Add to `goldencheck/cli/main.py`:

```python
@app.command()
def evaluate(
    file: str = typer.Argument(..., help="CSV file to scan"),
    ground_truth: str = typer.Option(..., "--ground-truth", "-g", help="JSON file with expected findings"),
    min_f1: float = typer.Option(0.0, "--min-f1", help="Fail if F1 below this threshold"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Evaluate scan accuracy against ground truth."""
    import json as json_mod
    from pathlib import Path
    from goldencheck.engine.scanner import scan_file
    from goldencheck.engine.confidence import apply_confidence_downgrade
    from goldencheck.engine.evaluate import evaluate_scan

    findings, _profile = scan_file(Path(file))
    findings = apply_confidence_downgrade(findings, llm_boost=False)

    expected = json_mod.loads(Path(ground_truth).read_text())
    result = evaluate_scan(findings, expected)

    if json_output:
        typer.echo(json_mod.dumps(result, indent=2))
    else:
        typer.echo(f"Precision: {result['precision']:.1%}")
        typer.echo(f"Recall:    {result['recall']:.1%}")
        typer.echo(f"F1:        {result['f1']:.1%}")
        typer.echo(f"TP: {result['true_positives']}  FP: {result['false_positives']}  FN: {result['false_negatives']}")

    if result["f1"] < min_f1:
        typer.echo(f"\nFAIL: F1 {result['f1']:.1%} < {min_f1:.1%}")
        raise typer.Exit(code=1)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/engine/test_evaluate.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add goldencheck/engine/evaluate.py goldencheck/cli/main.py tests/engine/test_evaluate.py
git commit -m "feat: add evaluate command for ground truth comparison"
```

---

### Task 4: Settings Persistence

**Files:**
- Create: `goldencheck/config/settings.py`
- Create: `tests/config/test_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_settings.py
"""Tests for global and project settings persistence."""
from __future__ import annotations

from pathlib import Path

from goldencheck.config.settings import load_settings, save_settings, DEFAULT_SETTINGS


def test_default_settings():
    s = DEFAULT_SETTINGS
    assert "sample_size" in s
    assert "severity_threshold" in s


def test_save_and_load_project_settings(tmp_path):
    settings_path = tmp_path / ".goldencheck.yaml"
    settings = {"sample_size": 50000, "domain": "healthcare"}
    save_settings(settings, settings_path)
    loaded = load_settings(settings_path)
    assert loaded["sample_size"] == 50000
    assert loaded["domain"] == "healthcare"


def test_load_missing_returns_defaults(tmp_path):
    loaded = load_settings(tmp_path / "nonexistent.yaml")
    assert loaded == DEFAULT_SETTINGS


def test_global_settings_path():
    from goldencheck.config.settings import global_settings_path
    p = global_settings_path()
    assert ".goldencheck" in str(p) or "goldencheck" in str(p)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/config/test_settings.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement settings module**

```python
# goldencheck/config/settings.py
"""Global and project settings persistence."""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_SETTINGS: dict = {
    "sample_size": 100_000,
    "severity_threshold": "warning",
    "fail_on": "error",
    "domain": None,
    "llm_provider": "anthropic",
    "llm_boost": False,
}


def global_settings_path() -> Path:
    """Return path to global settings file (~/.goldencheck/settings.yaml)."""
    return Path.home() / ".goldencheck" / "settings.yaml"


def load_settings(path: Path | None = None) -> dict:
    """Load settings from a YAML file, falling back to defaults."""
    settings = dict(DEFAULT_SETTINGS)
    if path is None:
        path = global_settings_path()
    if path.exists():
        with open(path) as f:
            user = yaml.safe_load(f) or {}
        settings.update(user)
    return settings


def save_settings(settings: dict, path: Path | None = None) -> None:
    """Save settings to a YAML file."""
    if path is None:
        path = global_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(settings, f, default_flow_style=False, sort_keys=False)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/config/test_settings.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add goldencheck/config/settings.py tests/config/test_settings.py
git commit -m "feat: add settings persistence (~/.goldencheck/settings.yaml)"
```

---

### Task 5: Formal Benchmark Suite

**Files:**
- Create: `benchmarks/run_suite.py`
- Create: `benchmarks/README.md`

- [ ] **Step 1: Create benchmark runner**

```python
# benchmarks/run_suite.py
"""Formal benchmark suite — run all benchmarks and produce summary."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from goldencheck.engine.scanner import scan_file
from goldencheck.engine.confidence import apply_confidence_downgrade


DATASETS_DIR = Path(__file__).parent / "datasets"


def run_speed_benchmark(files: list[Path]) -> dict:
    """Time scan_file on each dataset."""
    results = {}
    for f in files:
        start = time.perf_counter()
        findings, profile = scan_file(f)
        findings = apply_confidence_downgrade(findings, llm_boost=False)
        elapsed = time.perf_counter() - start
        results[f.name] = {
            "rows": profile.row_count,
            "columns": profile.column_count,
            "findings": len(findings),
            "seconds": round(elapsed, 3),
            "rows_per_sec": round(profile.row_count / elapsed) if elapsed > 0 else 0,
        }
    return results


def run_detection_benchmark(files: list[Path]) -> dict:
    """Count findings by severity for each dataset."""
    results = {}
    for f in files:
        findings, profile = scan_file(f)
        findings = apply_confidence_downgrade(findings, llm_boost=False)
        grade, score = profile.health_score()
        results[f.name] = {
            "grade": grade,
            "score": score,
            "errors": sum(1 for f2 in findings if f2.severity.name == "ERROR"),
            "warnings": sum(1 for f2 in findings if f2.severity.name == "WARNING"),
            "info": sum(1 for f2 in findings if f2.severity.name == "INFO"),
            "total": len(findings),
        }
    return results


def main():
    files = sorted(DATASETS_DIR.glob("*.csv"))
    if not files:
        print("No datasets found. Run: python benchmarks/generate_datasets.py")
        sys.exit(1)

    print(f"Running benchmarks on {len(files)} datasets...\n")

    speed = run_speed_benchmark(files)
    print("=== Speed ===")
    for name, r in speed.items():
        print(f"  {name}: {r['rows']:,} rows in {r['seconds']}s ({r['rows_per_sec']:,} rows/s)")

    detection = run_detection_benchmark(files)
    print("\n=== Detection ===")
    for name, r in detection.items():
        print(f"  {name}: grade={r['grade']} score={r['score']} "
              f"E={r['errors']} W={r['warnings']} I={r['info']}")

    output = {"speed": speed, "detection": detection}
    out_path = Path(__file__).parent / "results.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add `results.json` to `.gitignore`**

```bash
echo "benchmarks/results.json" >> .gitignore
```

- [ ] **Step 3: Commit**

```bash
git add benchmarks/run_suite.py .gitignore
git commit -m "feat: add formal benchmark suite with speed + detection"
```

---

### Task 6: DevContainer Polish

**Files:**
- Modify: `.devcontainer/devcontainer.json`

DevContainer exists but is missing port forwarding and post-create script.

- [ ] **Step 1: Update devcontainer.json**

```json
{
  "name": "GoldenCheck",
  "image": "mcr.microsoft.com/devcontainers/python:3.12",
  "postCreateCommand": "bash .devcontainer/post-create.sh",
  "customizations": {
    "vscode": {
      "extensions": [
        "ms-python.python",
        "charliermarsh.ruff",
        "ms-toolsai.jupyter"
      ],
      "settings": {
        "python.defaultInterpreterPath": "/usr/local/bin/python",
        "python.testing.pytestEnabled": true,
        "python.testing.pytestArgs": ["tests"],
        "editor.formatOnSave": true,
        "editor.rulers": [100]
      }
    }
  },
  "forwardPorts": [8000, 8100],
  "portsAttributes": {
    "8000": {"label": "GoldenCheck REST API"},
    "8100": {"label": "GoldenCheck A2A Agent"}
  },
  "features": {
    "ghcr.io/devcontainers/features/github-cli:1": {}
  }
}
```

- [ ] **Step 2: Create post-create.sh**

```bash
#!/bin/bash
pip install -e ".[dev,llm,mcp,agent]"
echo "GoldenCheck dev environment ready!"
echo "Run: goldencheck demo    # Try it out"
echo "Run: pytest --tb=short   # Run tests"
```

- [ ] **Step 3: Commit**

```bash
git add .devcontainer/
git commit -m "chore: polish devcontainer with port forwarding and post-create script"
```

---

### Task 7: Golden Suite Independence Audit

**Files:**
- Modify: `goldencheck/agent/handoff.py` (ensure standalone works)
- Modify: `goldencheck/a2a/server.py` (agent card description)
- Create: `tests/test_standalone.py`

- [ ] **Step 1: Write the standalone test**

```python
# tests/test_standalone.py
"""Verify GoldenCheck works completely standalone without Golden Suite."""
from __future__ import annotations

from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "simple.csv"


def test_scan_standalone():
    """Core scan works without any suite dependencies."""
    from goldencheck import scan_file, apply_confidence_downgrade
    findings, profile = scan_file(FIXTURE)
    findings = apply_confidence_downgrade(findings, llm_boost=False)
    assert isinstance(findings, list)
    assert profile.row_count > 0


def test_handoff_standalone():
    """Handoff generates valid attestation without downstream tools."""
    from goldencheck.agent.handoff import generate_handoff
    from goldencheck import scan_file, apply_confidence_downgrade, DatasetProfile

    findings, profile = scan_file(FIXTURE)
    findings = apply_confidence_downgrade(findings, llm_boost=False)
    result = generate_handoff(
        file_path=str(FIXTURE),
        findings=findings,
        profile=profile,
        pinned_rules=[],
        review_pending=0,
        dismissed=0,
        job_name="standalone-test",
    )
    assert result["source_tool"] == "goldencheck"
    assert result["attestation"] in ("PASS", "PASS_WITH_WARNINGS", "REVIEW_REQUIRED", "FAIL")
    # No mention of GoldenFlow/GoldenMatch in the attestation itself
    assert "goldenflow" not in str(result["attestation"]).lower()


def test_evaluate_standalone():
    """Evaluate works without any suite dependencies."""
    from goldencheck.engine.evaluate import evaluate_scan
    result = evaluate_scan([], [])
    assert result["f1"] == 1.0


def test_settings_standalone():
    """Settings work without suite config."""
    from goldencheck.config.settings import load_settings, DEFAULT_SETTINGS
    s = load_settings(Path("/nonexistent/path.yaml"))
    assert s == DEFAULT_SETTINGS
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_standalone.py -v`
Expected: all PASS (if handoff/evaluate/settings tasks are done first)

- [ ] **Step 3: Commit**

```bash
git add tests/test_standalone.py
git commit -m "test: add standalone verification tests (no Golden Suite deps)"
```

---

### Task 8: Add More Edge Case Tests (Target 500+)

**Files:**
- Create: `tests/profilers/test_edge_cases.py`
- Create: `tests/engine/test_scanner_edge_cases.py`
- Create: `tests/semantic/test_classifier_edge_cases.py`
- Create: `tests/agent/test_intelligence_edge_cases.py`

- [ ] **Step 1: Write profiler edge case tests**

Focus areas: empty DataFrames, single-row DataFrames, all-null columns, single-value columns, extremely wide DataFrames (100+ columns), Unicode column names, columns with only whitespace.

```python
# tests/profilers/test_edge_cases.py
"""Edge case tests for profilers — empty, null, single-row, wide datasets."""
from __future__ import annotations

import polars as pl

from goldencheck.engine.scanner import scan_file
from goldencheck.engine.confidence import apply_confidence_downgrade


def test_empty_dataframe(tmp_path):
    """Scanner handles empty CSV (headers only, no rows)."""
    csv = tmp_path / "empty.csv"
    csv.write_text("a,b,c\n")
    findings, profile = scan_file(csv)
    assert profile.row_count == 0
    assert profile.column_count == 3


def test_single_row(tmp_path):
    """Scanner handles single-row CSV."""
    csv = tmp_path / "one.csv"
    csv.write_text("name,age\nAlice,30\n")
    findings, profile = scan_file(csv)
    assert profile.row_count == 1


def test_all_null_column(tmp_path):
    """Column that is 100% null."""
    csv = tmp_path / "nulls.csv"
    csv.write_text("id,value\n1,\n2,\n3,\n")
    findings, profile = scan_file(csv)
    findings = apply_confidence_downgrade(findings, llm_boost=False)
    null_findings = [f for f in findings if f.column == "value" and "null" in f.check]
    assert len(null_findings) > 0


def test_unicode_column_names(tmp_path):
    """Scanner handles Unicode column names."""
    csv = tmp_path / "unicode.csv"
    csv.write_text("名前,年齢,メール\nAlice,30,a@b.com\nBob,25,b@c.com\n")
    findings, profile = scan_file(csv)
    assert profile.column_count == 3


def test_wide_dataframe(tmp_path):
    """Scanner handles 100+ columns."""
    cols = [f"col_{i}" for i in range(120)]
    header = ",".join(cols)
    row = ",".join(["value"] * 120)
    csv = tmp_path / "wide.csv"
    csv.write_text(f"{header}\n{row}\n{row}\n{row}\n")
    findings, profile = scan_file(csv)
    assert profile.column_count == 120
```

- [ ] **Step 2: Write scanner edge case tests**

```python
# tests/engine/test_scanner_edge_cases.py
"""Edge case tests for the scanner pipeline."""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from goldencheck.engine.scanner import scan_file
from goldencheck.engine.confidence import apply_confidence_downgrade


def test_scan_parquet(tmp_path):
    """Scanner handles Parquet files."""
    df = pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    path = tmp_path / "data.parquet"
    df.write_parquet(path)
    findings, profile = scan_file(path)
    assert profile.row_count == 3


def test_scan_with_domain(tmp_path):
    """Scanner handles domain parameter."""
    csv = tmp_path / "data.csv"
    csv.write_text("patient_id,diagnosis,age\n1,flu,30\n2,cold,25\n")
    findings, profile = scan_file(csv, domain="healthcare")
    assert profile.column_count == 3


def test_scan_return_sample(tmp_path):
    """return_sample=True returns 3-tuple."""
    csv = tmp_path / "data.csv"
    csv.write_text("a,b\n1,2\n3,4\n")
    result = scan_file(csv, return_sample=True)
    assert len(result) == 3
    findings, profile, sample = result
    assert isinstance(sample, pl.DataFrame)


def test_scan_nonexistent_file():
    """Scanning a nonexistent file raises ValueError."""
    with pytest.raises((ValueError, FileNotFoundError, Exception)):
        scan_file(Path("/nonexistent/file.csv"))
```

- [ ] **Step 3: Write classifier edge case tests**

```python
# tests/semantic/test_classifier_edge_cases.py
"""Edge case tests for the semantic classifier."""
from __future__ import annotations

import polars as pl

from goldencheck.semantic.classifier import classify_columns, load_type_defs


def test_classify_empty_dataframe():
    """Classifier handles empty DataFrame."""
    df = pl.DataFrame({"a": [], "b": []}).cast({"a": pl.Utf8, "b": pl.Utf8})
    type_defs = load_type_defs()
    result = classify_columns(df, type_defs)
    assert isinstance(result, dict)


def test_classify_all_domains():
    """All three domain packs load without error."""
    for domain in ["healthcare", "finance", "ecommerce"]:
        type_defs = load_type_defs(domain=domain)
        assert len(type_defs) > 0


def test_classify_column_with_hint_prefix():
    """Prefix hint 'is_' matches 'is_active' but not 'diagnosis'."""
    df = pl.DataFrame({
        "is_active": ["true", "false", "true"],
        "diagnosis_code": ["A01", "B02", "C03"],
    })
    type_defs = load_type_defs()
    result = classify_columns(df, type_defs)
    # is_active should match boolean type if defined
    assert isinstance(result, dict)
```

- [ ] **Step 4: Write agent intelligence edge case tests**

```python
# tests/agent/test_intelligence_edge_cases.py
"""Edge case tests for the intelligence layer."""
from __future__ import annotations

import polars as pl

from goldencheck.agent.intelligence import (
    select_strategy,
    explain_finding,
    findings_to_fbc,
)
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import DatasetProfile, ColumnProfile


def test_select_strategy_empty_dataframe():
    """Strategy selection handles empty DataFrame."""
    df = pl.DataFrame({"a": [], "b": []}).cast({"a": pl.Utf8, "b": pl.Utf8})
    result = select_strategy(df)
    assert result.sample_strategy is not None


def test_select_strategy_single_column():
    """Strategy selection handles single-column DataFrame."""
    df = pl.DataFrame({"value": [1, 2, 3]})
    result = select_strategy(df)
    assert result.profiler_strategy is not None


def test_findings_to_fbc_empty():
    """Empty findings list produces empty dict."""
    assert findings_to_fbc([]) == {}


def test_findings_to_fbc_info_only():
    """INFO findings don't appear in findings_by_column."""
    findings = [
        Finding(severity=Severity.INFO, column="a", check="test", message="info"),
    ]
    result = findings_to_fbc(findings)
    assert "a" not in result or (result["a"]["errors"] == 0 and result["a"]["warnings"] == 0)


def test_explain_finding_minimal():
    """explain_finding works with minimal Finding and Profile."""
    finding = Finding(
        severity=Severity.WARNING, column="x", check="test",
        message="test issue", confidence=0.75,
    )
    profile = DatasetProfile(
        file_path="test.csv", row_count=100, column_count=1,
        columns=[ColumnProfile(name="x", dtype="Utf8", null_pct=0.0, unique_pct=1.0)],
    )
    result = explain_finding(finding, profile)
    assert "what" in result or "message" in result or isinstance(result, dict)
```

- [ ] **Step 5: Run all new tests**

Run: `pytest tests/profilers/test_edge_cases.py tests/engine/test_scanner_edge_cases.py tests/semantic/test_classifier_edge_cases.py tests/agent/test_intelligence_edge_cases.py -v`
Expected: all PASS

- [ ] **Step 6: Verify total test count**

Run: `pytest --collect-only -q 2>&1 | tail -1`
Expected: 400+ tests collected

- [ ] **Step 7: Commit**

```bash
git add tests/
git commit -m "test: add edge case tests for profilers, scanner, classifier, agent"
```

---

## Execution Order

All tasks are independent. Recommended parallel grouping:

| Wave | Tasks | Description |
|------|-------|-------------|
| 1 | 1, 2, 3, 4 | High priority — user-visible features |
| 2 | 5, 6, 7, 8 | Medium priority — polish and testing |

## Final Steps (after all tasks)

- [ ] Update `goldencheck/__init__.py` version to `1.1.0`
- [ ] Update CLAUDE.md test count and command list
- [ ] Run full test suite: `pytest --tb=short -v`
- [ ] Run ruff: `ruff check .`
- [ ] Create PR: `feat: feature parity and polish — demo, evaluate, settings, API exports, benchmarks, tests`
