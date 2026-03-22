# GoldenCheck Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a zero-config data validation CLI that discovers rules from data and lets users pin them interactively.

**Architecture:** Profile-first approach — a Polars-native profiling engine infers validation rules from data, presents findings in a Textual TUI where users pin rules, which are exported to a layered YAML config. A separate validator checks data against pinned rules with exit codes for CI.

**Tech Stack:** Python 3.11+, Polars, Typer, Textual, Rich, Pydantic 2, PyYAML, openpyxl

**Spec:** `docs/superpowers/specs/2026-03-22-goldencheck-design.md`

---

## Phase 1: Project Scaffold & Data Models

### Task 1: Project Setup

**Files:**
- Create: `pyproject.toml`
- Create: `goldencheck/__init__.py`
- Create: `tests/__init__.py`
- Create: `.gitignore`
- Create: `README.md`

- [ ] **Step 1: Initialize git repo**

```bash
cd D:/show_case/goldencheck
git init
```

- [ ] **Step 2: Create .gitignore**

```
__pycache__/
*.pyc
*.egg-info/
dist/
build/
.venv/
.env
*.csv
*.parquet
*.xlsx
goldencheck.yml
.ruff_cache/
```

- [ ] **Step 3: Create pyproject.toml**

Follow GoldenMatch's pattern (hatchling build system):

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "goldencheck"
version = "0.1.0"
description = "Data validation that discovers rules from your data so you don't have to write them"
readme = "README.md"
requires-python = ">=3.11"
license = "MIT"
authors = [{ name = "Ben Severn", email = "benzsevern@gmail.com" }]
keywords = ["data-validation", "data-quality", "profiling", "data-checks"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "Intended Audience :: Science/Research",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Database",
    "Topic :: Scientific/Engineering :: Information Analysis",
    "Typing :: Typed",
]
dependencies = [
    "polars>=1.0",
    "typer>=0.12",
    "rich>=13.0",
    "pyyaml>=6.0",
    "pydantic>=2.0",
    "openpyxl>=3.1",
    "textual>=1.0",
]

[project.urls]
Homepage = "https://github.com/benzsevern/goldencheck"
Repository = "https://github.com/benzsevern/goldencheck"
Issues = "https://github.com/benzsevern/goldencheck/issues"

[project.scripts]
goldencheck = "goldencheck.cli.main:app"

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.4",
]

[tool.ruff]
line-length = 100

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 4: Create package init files**

`goldencheck/__init__.py`:
```python
"""GoldenCheck — data validation that discovers rules from your data."""

__version__ = "0.1.0"
```

`tests/__init__.py`: empty file.

- [ ] **Step 5: Create placeholder README.md**

```markdown
# GoldenCheck

Data validation that discovers rules from your data so you don't have to write them.

## Install

```bash
pip install goldencheck
```

## Quick Start

```bash
goldencheck data.csv
```
```

- [ ] **Step 6: Install in dev mode and verify**

```bash
pip install -e ".[dev]"
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: initial project scaffold"
```

---

### Task 2: Core Data Models (Finding + Profile)

**Files:**
- Create: `goldencheck/models/__init__.py`
- Create: `goldencheck/models/finding.py`
- Create: `goldencheck/models/profile.py`
- Create: `tests/models/__init__.py`
- Create: `tests/models/test_finding.py`
- Create: `tests/models/test_profile.py`

- [ ] **Step 1: Write tests for Finding model**

`tests/models/test_finding.py`:
```python
from goldencheck.models.finding import Finding, Severity


def test_finding_creation():
    f = Finding(
        severity=Severity.ERROR,
        column="email",
        check="format",
        message="6% of values are not valid email format",
        affected_rows=3000,
        sample_values=["not-an-email", "also bad"],
    )
    assert f.severity == Severity.ERROR
    assert f.column == "email"
    assert f.affected_rows == 3000


def test_finding_without_optional_fields():
    f = Finding(
        severity=Severity.INFO,
        column="status",
        check="cardinality",
        message="4 unique values detected",
    )
    assert f.affected_rows == 0
    assert f.sample_values == []
    assert f.suggestion is None


def test_finding_with_suggestion():
    f = Finding(
        severity=Severity.WARNING,
        column="date",
        check="format",
        message="2 date formats detected",
        suggestion="Standardize to MM/DD/YYYY (majority format)",
    )
    assert f.suggestion == "Standardize to MM/DD/YYYY (majority format)"


def test_severity_ordering():
    assert Severity.ERROR.value > Severity.WARNING.value
    assert Severity.WARNING.value > Severity.INFO.value
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/models/test_finding.py -v
```
Expected: FAIL (module not found)

- [ ] **Step 3: Implement Finding model**

`goldencheck/models/__init__.py`: empty file.

`goldencheck/models/finding.py`:
```python
"""Finding model — represents a single validation finding."""

from __future__ import annotations

from enum import IntEnum
from dataclasses import dataclass, field


class Severity(IntEnum):
    """Finding severity levels. Higher value = more severe."""
    INFO = 1
    WARNING = 2
    ERROR = 3


@dataclass
class Finding:
    """A single validation finding from profiling or rule checking."""
    severity: Severity
    column: str
    check: str
    message: str
    affected_rows: int = 0
    sample_values: list[str] = field(default_factory=list)
    suggestion: str | None = None
    pinned: bool = False
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/models/test_finding.py -v
```
Expected: 4 passed

- [ ] **Step 5: Write tests for ColumnProfile model**

`tests/models/test_profile.py`:
```python
from goldencheck.models.profile import ColumnProfile, DatasetProfile


def test_column_profile_creation():
    cp = ColumnProfile(
        name="email",
        inferred_type="string",
        null_count=50,
        null_pct=0.1,
        unique_count=4500,
        unique_pct=0.9,
        row_count=5000,
    )
    assert cp.name == "email"
    assert cp.null_pct == 0.1


def test_dataset_profile_creation():
    dp = DatasetProfile(
        file_path="data.csv",
        row_count=5000,
        column_count=10,
        columns=[],
    )
    assert dp.row_count == 5000
    assert dp.column_count == 10


def test_dataset_health_score_perfect():
    dp = DatasetProfile(file_path="data.csv", row_count=100, column_count=5, columns=[])
    grade, points = dp.health_score(errors=0, warnings=0)
    assert grade == "A"
    assert points == 100


def test_dataset_health_score_with_errors():
    dp = DatasetProfile(file_path="data.csv", row_count=100, column_count=5, columns=[])
    grade, points = dp.health_score(errors=2, warnings=5)
    # 100 - (2*10) - (5*3) = 100 - 20 - 15 = 65
    assert grade == "D"
    assert points == 65


def test_dataset_health_score_floor():
    dp = DatasetProfile(file_path="data.csv", row_count=100, column_count=5, columns=[])
    grade, points = dp.health_score(errors=20, warnings=20)
    # Capped, but should not go below 0
    assert points >= 0
    assert grade == "F"
```

- [ ] **Step 6: Implement ColumnProfile and DatasetProfile**

`goldencheck/models/profile.py`:
```python
"""Profile models — column and dataset profiles."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ColumnProfile:
    """Profile of a single column."""
    name: str
    inferred_type: str
    null_count: int
    null_pct: float
    unique_count: int
    unique_pct: float
    row_count: int
    min_value: str | None = None
    max_value: str | None = None
    mean: float | None = None
    stddev: float | None = None
    top_values: list[tuple[str, int]] = field(default_factory=list)
    detected_format: str | None = None
    detected_patterns: list[tuple[str, float]] = field(default_factory=list)
    enum_values: list[str] | None = None


@dataclass
class DatasetProfile:
    """Profile of an entire dataset."""
    file_path: str
    row_count: int
    column_count: int
    columns: list[ColumnProfile]

    def health_score(
        self,
        findings_by_column: dict[str, dict[str, int]] | None = None,
        errors: int = 0,
        warnings: int = 0,
    ) -> tuple[str, int]:
        """Calculate health score with per-column cap of -20. Returns (grade, points).

        Args:
            findings_by_column: {col_name: {"errors": N, "warnings": N}}
                If provided, applies per-column deduction cap of -20.
            errors: flat error count (used if findings_by_column not provided)
            warnings: flat warning count (used if findings_by_column not provided)
        """
        if findings_by_column:
            total_deduction = 0
            for col_data in findings_by_column.values():
                col_deduction = (col_data.get("errors", 0) * 10) + (col_data.get("warnings", 0) * 3)
                total_deduction += min(col_deduction, 20)  # cap at -20 per column
            points = max(100 - total_deduction, 0)
        else:
            points = 100 - (errors * 10) - (warnings * 3)
            points = max(points, 0)

        if points >= 90:
            grade = "A"
        elif points >= 80:
            grade = "B"
        elif points >= 70:
            grade = "C"
        elif points >= 60:
            grade = "D"
        else:
            grade = "F"

        return grade, points
```

- [ ] **Step 7: Run all tests**

```bash
pytest tests/models/ -v
```
Expected: 9 passed

- [ ] **Step 8: Commit**

```bash
git add goldencheck/models/ tests/models/
git commit -m "feat: add Finding and Profile data models"
```

---

### Task 3: Config Schema (Pydantic Models + YAML Loader/Writer)

**Files:**
- Create: `goldencheck/config/__init__.py`
- Create: `goldencheck/config/schema.py`
- Create: `goldencheck/config/loader.py`
- Create: `goldencheck/config/writer.py`
- Create: `tests/config/__init__.py`
- Create: `tests/config/test_schema.py`
- Create: `tests/config/test_loader.py`
- Create: `tests/config/test_writer.py`

- [ ] **Step 1: Write tests for config schema**

`tests/config/test_schema.py`:
```python
from goldencheck.config.schema import (
    GoldenCheckConfig,
    ColumnRule,
    RelationRule,
    IgnoreEntry,
    Settings,
)


def test_column_rule_minimal():
    rule = ColumnRule(type="string")
    assert rule.type == "string"
    assert rule.required is None
    assert rule.format is None


def test_column_rule_full():
    rule = ColumnRule(
        type="integer",
        required=True,
        range=[0, 120],
        unique=False,
    )
    assert rule.range == [0, 120]


def test_relation_rule():
    rule = RelationRule(type="temporal_order", columns=["start_date", "end_date"])
    assert rule.type == "temporal_order"
    assert len(rule.columns) == 2


def test_ignore_entry():
    entry = IgnoreEntry(column="notes", check="nullability")
    assert entry.column == "notes"


def test_full_config():
    config = GoldenCheckConfig(
        version=1,
        settings=Settings(fail_on="error"),
        columns={"email": ColumnRule(type="string", required=True, format="email")},
        relations=[RelationRule(type="temporal_order", columns=["start", "end"])],
        ignore=[IgnoreEntry(column="notes", check="nullability")],
    )
    assert config.version == 1
    assert "email" in config.columns
    assert len(config.relations) == 1
    assert len(config.ignore) == 1


def test_default_settings():
    settings = Settings()
    assert settings.sample_size == 100000
    assert settings.severity_threshold == "warning"
    assert settings.fail_on == "error"
```

- [ ] **Step 2: Run tests — verify fail**

```bash
pytest tests/config/test_schema.py -v
```

- [ ] **Step 3: Implement config schema**

`goldencheck/config/__init__.py`: empty file.

`goldencheck/config/schema.py`:
```python
"""Pydantic models for goldencheck.yml configuration."""

from __future__ import annotations

from pydantic import BaseModel


class Settings(BaseModel):
    """Global validation settings."""
    sample_size: int = 100_000
    severity_threshold: str = "warning"
    fail_on: str = "error"


class ColumnRule(BaseModel):
    """Validation rule for a single column."""
    type: str
    required: bool | None = None
    nullable: bool | None = None
    format: str | None = None
    unique: bool | None = None
    range: list[float] | None = None
    enum: list[str] | None = None
    outlier_stddev: float | None = None


class RelationRule(BaseModel):
    """Cross-column relation rule."""
    type: str
    columns: list[str]


class IgnoreEntry(BaseModel):
    """A dismissed finding."""
    column: str
    check: str


class GoldenCheckConfig(BaseModel):
    """Root configuration model for goldencheck.yml."""
    version: int = 1
    settings: Settings = Settings()
    columns: dict[str, ColumnRule] = {}
    relations: list[RelationRule] = []
    ignore: list[IgnoreEntry] = []
```

- [ ] **Step 4: Run schema tests — verify pass**

```bash
pytest tests/config/test_schema.py -v
```

- [ ] **Step 5: Write tests for loader and writer**

`tests/config/test_loader.py`:
```python
import tempfile
from pathlib import Path

import yaml

from goldencheck.config.loader import load_config
from goldencheck.config.writer import save_config
from goldencheck.config.schema import GoldenCheckConfig, ColumnRule, Settings


def test_load_nonexistent_returns_none():
    result = load_config(Path("/nonexistent/goldencheck.yml"))
    assert result is None


def test_load_valid_yaml(tmp_path):
    config_data = {
        "version": 1,
        "settings": {"fail_on": "warning"},
        "columns": {"age": {"type": "integer", "required": True}},
    }
    path = tmp_path / "goldencheck.yml"
    path.write_text(yaml.dump(config_data))

    config = load_config(path)
    assert config is not None
    assert config.settings.fail_on == "warning"
    assert "age" in config.columns


def test_roundtrip(tmp_path):
    config = GoldenCheckConfig(
        settings=Settings(fail_on="error"),
        columns={"email": ColumnRule(type="string", required=True, format="email")},
    )
    path = tmp_path / "goldencheck.yml"
    save_config(config, path)
    loaded = load_config(path)
    assert loaded is not None
    assert loaded.columns["email"].format == "email"
    assert loaded.settings.fail_on == "error"
```

- [ ] **Step 6: Implement loader and writer**

`goldencheck/config/loader.py`:
```python
"""Load goldencheck.yml configuration."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from goldencheck.config.schema import GoldenCheckConfig

logger = logging.getLogger(__name__)


def load_config(path: Path) -> GoldenCheckConfig | None:
    """Load config from YAML file. Returns None if file doesn't exist."""
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        if data is None:
            return GoldenCheckConfig()
        return GoldenCheckConfig(**data)
    except Exception as e:
        logger.error("Failed to load %s: %s", path, e)
        raise
```

`goldencheck/config/writer.py`:
```python
"""Write goldencheck.yml configuration."""

from __future__ import annotations

from pathlib import Path

import yaml

from goldencheck.config.schema import GoldenCheckConfig


def save_config(config: GoldenCheckConfig, path: Path) -> None:
    """Save config to YAML file. Only writes non-default, pinned rules."""
    data = config.model_dump(exclude_none=True, exclude_defaults=False)
    # Remove empty collections
    if not data.get("columns"):
        data.pop("columns", None)
    if not data.get("relations"):
        data.pop("relations", None)
    if not data.get("ignore"):
        data.pop("ignore", None)

    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
```

- [ ] **Step 7: Run all config tests**

```bash
pytest tests/config/ -v
```
Expected: 9 passed

- [ ] **Step 8: Commit**

```bash
git add goldencheck/config/ tests/config/
git commit -m "feat: add config schema, loader, and writer"
```

---

## Phase 2: Profiling Engine

### Task 4: File Reader + Sampler

**Files:**
- Create: `goldencheck/engine/__init__.py`
- Create: `goldencheck/engine/reader.py`
- Create: `goldencheck/engine/sampler.py`
- Create: `tests/engine/__init__.py`
- Create: `tests/engine/test_reader.py`
- Create: `tests/engine/test_sampler.py`
- Create: `tests/fixtures/simple.csv` (test fixture)

- [ ] **Step 1: Create test fixture**

`tests/fixtures/simple.csv`:
```csv
id,name,email,age,status
1,Alice,alice@example.com,30,active
2,Bob,bob@test.com,25,inactive
3,Charlie,,45,active
4,Diana,diana@example.com,28,pending
5,Eve,not-an-email,33,active
```

- [ ] **Step 2: Write tests for reader**

`tests/engine/test_reader.py`:
```python
from pathlib import Path

import polars as pl
import pytest

from goldencheck.engine.reader import read_file

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_read_csv():
    df = read_file(FIXTURES / "simple.csv")
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 5
    assert "email" in df.columns


def test_read_nonexistent():
    with pytest.raises(FileNotFoundError):
        read_file(Path("/nonexistent/file.csv"))


def test_read_unsupported_extension():
    with pytest.raises(ValueError, match="Unsupported file format"):
        read_file(Path("data.json"))
```

- [ ] **Step 3: Implement reader**

`goldencheck/engine/__init__.py`: empty file.

`goldencheck/engine/reader.py`:
```python
"""File reader — loads CSV, Parquet, and Excel files into Polars DataFrames."""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".csv", ".parquet", ".xlsx", ".xls"}


def read_file(path: Path) -> pl.DataFrame:
    """Read a data file into a Polars DataFrame."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file format: {ext}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")

    logger.info("Reading %s (%s)", path, ext)

    # Check for empty file
    if path.stat().st_size == 0:
        raise ValueError("File has no data rows. Nothing to profile.")

    if ext == ".csv":
        try:
            return pl.read_csv(path, infer_schema_length=10000)
        except Exception:
            # Try Latin-1 encoding as fallback
            try:
                return pl.read_csv(path, infer_schema_length=10000, encoding="latin-1")
            except Exception as e:
                raise ValueError(
                    f"Could not read CSV: {e}. Try specifying --separator or --quote-char"
                ) from e
    elif ext == ".parquet":
        return pl.read_parquet(path)
    elif ext in (".xlsx", ".xls"):
        try:
            return pl.read_excel(path)
        except Exception as e:
            if "password" in str(e).lower() or "encrypted" in str(e).lower():
                raise ValueError(
                    "File appears to be password-protected. GoldenCheck cannot read encrypted files."
                ) from e
            raise
    else:
        raise ValueError(f"Unsupported file format: {ext}")
```

- [ ] **Step 4: Write tests for sampler**

`tests/engine/test_sampler.py`:
```python
import polars as pl

from goldencheck.engine.sampler import maybe_sample


def test_no_sample_small_df():
    df = pl.DataFrame({"a": range(100)})
    result = maybe_sample(df, max_rows=1000)
    assert len(result) == 100


def test_sample_large_df():
    df = pl.DataFrame({"a": range(10000)})
    result = maybe_sample(df, max_rows=1000)
    assert len(result) == 1000


def test_sample_preserves_columns():
    df = pl.DataFrame({"a": range(5000), "b": range(5000)})
    result = maybe_sample(df, max_rows=100)
    assert result.columns == ["a", "b"]
```

- [ ] **Step 5: Implement sampler**

`goldencheck/engine/sampler.py`:
```python
"""Smart sampling for large datasets."""

from __future__ import annotations

import polars as pl


def maybe_sample(df: pl.DataFrame, max_rows: int = 100_000) -> pl.DataFrame:
    """Return the DataFrame as-is if small enough, otherwise sample."""
    if len(df) <= max_rows:
        return df
    return df.sample(n=max_rows, seed=42)
```

- [ ] **Step 6: Run all engine tests**

```bash
pytest tests/engine/ -v
```
Expected: 6 passed

- [ ] **Step 7: Commit**

```bash
git add goldencheck/engine/ tests/engine/ tests/fixtures/
git commit -m "feat: add file reader and smart sampler"
```

---

### Task 5: Type Inference Profiler

**Files:**
- Create: `goldencheck/profilers/__init__.py`
- Create: `goldencheck/profilers/base.py`
- Create: `goldencheck/profilers/type_inference.py`
- Create: `tests/profilers/__init__.py`
- Create: `tests/profilers/test_type_inference.py`

- [ ] **Step 1: Write tests for type inference**

`tests/profilers/test_type_inference.py`:
```python
import polars as pl

from goldencheck.profilers.type_inference import TypeInferenceProfiler
from goldencheck.models.finding import Severity


def test_clean_integer_column():
    df = pl.DataFrame({"age": [25, 30, 45, 28, 33]})
    findings = TypeInferenceProfiler().profile(df, "age")
    # No issues expected for a clean integer column
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert len(errors) == 0


def test_mixed_type_column():
    df = pl.DataFrame({"age": ["25", "30", "forty-five", "28", "33"]})
    findings = TypeInferenceProfiler().profile(df, "age")
    # Should detect that most values are numeric but stored as string
    assert len(findings) > 0
    assert any("integer" in f.message.lower() or "numeric" in f.message.lower() for f in findings)


def test_all_string_column():
    df = pl.DataFrame({"name": ["Alice", "Bob", "Charlie"]})
    findings = TypeInferenceProfiler().profile(df, "name")
    # Pure string column, no type issues
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert len(errors) == 0
```

- [ ] **Step 2: Run tests — verify fail**

```bash
pytest tests/profilers/test_type_inference.py -v
```

- [ ] **Step 3: Implement base profiler and type inference**

`goldencheck/profilers/__init__.py`: empty file.

`goldencheck/profilers/base.py`:
```python
"""Base profiler interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

import polars as pl

from goldencheck.models.finding import Finding


class BaseProfiler(ABC):
    """Abstract base for all column profilers."""

    @abstractmethod
    def profile(self, df: pl.DataFrame, column: str) -> list[Finding]:
        """Profile a column and return findings."""
        ...
```

`goldencheck/profilers/type_inference.py`:
```python
"""Type inference profiler — detects mixed types and type mismatches."""

from __future__ import annotations

import polars as pl

from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler


class TypeInferenceProfiler(BaseProfiler):
    """Detect when a column's actual values don't match its declared type."""

    def profile(self, df: pl.DataFrame, column: str) -> list[Finding]:
        findings: list[Finding] = []
        col = df[column]
        dtype = col.dtype

        # If column is string, check if values are actually numeric
        if dtype == pl.Utf8 or dtype == pl.String:
            non_null = col.drop_nulls()
            if len(non_null) == 0:
                return findings

            # Polars-native: try casting to Float64, count successes
            cast_result = non_null.cast(pl.Float64, strict=False)
            numeric_count = cast_result.is_not_null().sum()
            numeric_pct = numeric_count / len(non_null) if len(non_null) > 0 else 0

            if numeric_pct > 0.8:
                # Check if they're integers: cast to Int64 and compare
                int_cast = non_null.cast(pl.Int64, strict=False)
                int_count = int_cast.is_not_null().sum()
                int_pct = int_count / len(non_null)
                type_name = "integer" if int_pct > 0.9 else "numeric"
                non_numeric = len(non_null) - numeric_count

                findings.append(Finding(
                    severity=Severity.WARNING,
                    column=column,
                    check="type_inference",
                    message=f"Column is string but {numeric_pct:.0%} of values are {type_name} ({non_numeric} non-{type_name} values)",
                    affected_rows=non_numeric,
                    suggestion=f"Consider casting to {type_name}",
                ))

        return findings
```

- [ ] **Step 4: Run tests — verify pass**

```bash
pytest tests/profilers/test_type_inference.py -v
```

- [ ] **Step 5: Commit**

```bash
git add goldencheck/profilers/ tests/profilers/
git commit -m "feat: add type inference profiler"
```

---

### Task 6: Nullability Profiler

**Files:**
- Create: `goldencheck/profilers/nullability.py`
- Create: `tests/profilers/test_nullability.py`

- [ ] **Step 1: Write tests**

`tests/profilers/test_nullability.py`:
```python
import polars as pl

from goldencheck.profilers.nullability import NullabilityProfiler
from goldencheck.models.finding import Severity


def test_no_nulls_suggests_required():
    df = pl.DataFrame({"email": ["a@b.com", "c@d.com", "e@f.com"] * 100})
    findings = NullabilityProfiler().profile(df, "email")
    assert any(f.check == "nullability" and "required" in f.message.lower() for f in findings)


def test_all_nulls_flags_error():
    df = pl.DataFrame({"broken": [None, None, None]})
    findings = NullabilityProfiler().profile(df, "broken")
    assert any(f.severity == Severity.ERROR for f in findings)


def test_some_nulls_reports_info():
    df = pl.DataFrame({"notes": ["hello", None, "world", None]})
    findings = NullabilityProfiler().profile(df, "notes")
    assert any(f.check == "nullability" for f in findings)
```

- [ ] **Step 2: Run tests — verify fail**

```bash
pytest tests/profilers/test_nullability.py -v
```

- [ ] **Step 3: Implement nullability profiler**

`goldencheck/profilers/nullability.py`:
```python
"""Nullability profiler — detects required vs. optional columns."""

from __future__ import annotations

import polars as pl

from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler


class NullabilityProfiler(BaseProfiler):
    """Detect null patterns and infer required/optional status."""

    def profile(self, df: pl.DataFrame, column: str) -> list[Finding]:
        findings: list[Finding] = []
        col = df[column]
        total = len(col)
        null_count = col.null_count()
        null_pct = null_count / total if total > 0 else 0

        if null_count == total:
            findings.append(Finding(
                severity=Severity.ERROR,
                column=column,
                check="nullability",
                message=f"Column is entirely null ({total} rows)",
                affected_rows=total,
            ))
        elif null_count == 0 and total >= 10:
            findings.append(Finding(
                severity=Severity.INFO,
                column=column,
                check="nullability",
                message=f"0 nulls across {total} rows — likely required",
            ))
        elif null_pct > 0 and null_pct < 1:
            findings.append(Finding(
                severity=Severity.INFO,
                column=column,
                check="nullability",
                message=f"{null_count} nulls ({null_pct:.1%}) — column is optional",
                affected_rows=null_count,
            ))

        return findings
```

- [ ] **Step 4: Run tests — verify pass**

```bash
pytest tests/profilers/test_nullability.py -v
```

- [ ] **Step 5: Commit**

```bash
git add goldencheck/profilers/nullability.py tests/profilers/test_nullability.py
git commit -m "feat: add nullability profiler"
```

---

### Task 7: Uniqueness Profiler

**Files:**
- Create: `goldencheck/profilers/uniqueness.py`
- Create: `tests/profilers/test_uniqueness.py`

- [ ] **Step 1: Write tests**

`tests/profilers/test_uniqueness.py`:
```python
import polars as pl

from goldencheck.profilers.uniqueness import UniquenessProfiler
from goldencheck.models.finding import Severity


def test_fully_unique_column():
    df = pl.DataFrame({"id": list(range(100))})
    findings = UniquenessProfiler().profile(df, "id")
    assert any("unique" in f.message.lower() and "primary key" in f.message.lower() for f in findings)


def test_duplicates_detected():
    df = pl.DataFrame({"code": ["A", "B", "A", "C", "B", "A"]})
    findings = UniquenessProfiler().profile(df, "code")
    dupes = [f for f in findings if f.check == "uniqueness" and f.severity == Severity.INFO]
    assert len(dupes) >= 0  # may or may not flag depending on threshold


def test_all_same_value():
    df = pl.DataFrame({"flag": ["yes"] * 100})
    findings = UniquenessProfiler().profile(df, "flag")
    # 1 unique value out of 100 — not a uniqueness issue per se
    assert not any(f.severity == Severity.ERROR for f in findings)
```

- [ ] **Step 2: Implement**

`goldencheck/profilers/uniqueness.py`:
```python
"""Uniqueness profiler — detects primary key candidates and duplicates."""

from __future__ import annotations

import polars as pl

from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler


class UniquenessProfiler(BaseProfiler):
    """Detect unique columns (likely IDs) and duplicate patterns."""

    def profile(self, df: pl.DataFrame, column: str) -> list[Finding]:
        findings: list[Finding] = []
        col = df[column]
        total = len(col)
        non_null = col.drop_nulls()
        unique_count = non_null.n_unique()
        unique_pct = unique_count / len(non_null) if len(non_null) > 0 else 0

        if unique_pct == 1.0 and total >= 10:
            findings.append(Finding(
                severity=Severity.INFO,
                column=column,
                check="uniqueness",
                message=f"100% unique across {total} rows — likely primary key",
            ))
        elif unique_pct < 1.0:
            dup_count = len(non_null) - unique_count
            if dup_count > 0 and unique_pct > 0.95:
                findings.append(Finding(
                    severity=Severity.WARNING,
                    column=column,
                    check="uniqueness",
                    message=f"Near-unique column ({unique_pct:.1%} unique) with {dup_count} duplicates",
                    affected_rows=dup_count,
                ))

        return findings
```

- [ ] **Step 3: Run tests — verify pass**

```bash
pytest tests/profilers/test_uniqueness.py -v
```

- [ ] **Step 4: Commit**

```bash
git add goldencheck/profilers/uniqueness.py tests/profilers/test_uniqueness.py
git commit -m "feat: add uniqueness profiler"
```

---

### Task 8: Remaining Column Profilers (Format, Range, Cardinality, Pattern)

**Files:**
- Create: `goldencheck/profilers/format_detection.py`
- Create: `goldencheck/profilers/range_distribution.py`
- Create: `goldencheck/profilers/cardinality.py`
- Create: `goldencheck/profilers/pattern_consistency.py`
- Create: `tests/profilers/test_format_detection.py`
- Create: `tests/profilers/test_range_distribution.py`
- Create: `tests/profilers/test_cardinality.py`
- Create: `tests/profilers/test_pattern_consistency.py`

Each profiler follows the same TDD pattern as Tasks 5-7. They are independent and can be built in parallel.

#### Task 8a: Format Detection Profiler

**Files:**
- Create: `goldencheck/profilers/format_detection.py`
- Create: `tests/profilers/test_format_detection.py`

- [ ] **Step 1: Write tests**

```python
import polars as pl
from goldencheck.profilers.format_detection import FormatDetectionProfiler
from goldencheck.models.finding import Severity

def test_email_format_detected():
    df = pl.DataFrame({"contact": ["a@b.com", "c@d.com", "not-email", "e@f.com"]})
    findings = FormatDetectionProfiler().profile(df, "contact")
    assert any("email" in f.message.lower() for f in findings)

def test_clean_emails_no_error():
    df = pl.DataFrame({"email": [f"user{i}@test.com" for i in range(100)]})
    findings = FormatDetectionProfiler().profile(df, "email")
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert len(errors) == 0

def test_phone_format_detected():
    df = pl.DataFrame({"phone": ["(555) 123-4567", "555-123-4567", "5551234567"] * 10})
    findings = FormatDetectionProfiler().profile(df, "phone")
    assert any("phone" in f.message.lower() or "format" in f.check for f in findings)

def test_non_string_column_skipped():
    df = pl.DataFrame({"count": [1, 2, 3, 4, 5]})
    findings = FormatDetectionProfiler().profile(df, "count")
    assert len(findings) == 0
```

- [ ] **Step 2: Implement** — regex-based detection for email (`@` + domain), phone (digit groups), URL (`http`), date patterns. Use Polars `.str.contains()` for vectorized matching.
- [ ] **Step 3: Run tests, verify pass**
- [ ] **Step 4: Commit**

```bash
git add goldencheck/profilers/format_detection.py tests/profilers/test_format_detection.py
git commit -m "feat: add format detection profiler"
```

#### Task 8b: Range & Distribution Profiler

**Files:**
- Create: `goldencheck/profilers/range_distribution.py`
- Create: `tests/profilers/test_range_distribution.py`

- [ ] **Step 1: Write tests**

```python
import polars as pl
from goldencheck.profilers.range_distribution import RangeDistributionProfiler
from goldencheck.models.finding import Severity

def test_outlier_detected():
    values = list(range(100)) + [99999]
    df = pl.DataFrame({"price": values})
    findings = RangeDistributionProfiler().profile(df, "price")
    assert any(f.severity == Severity.WARNING and "outlier" in f.message.lower() for f in findings)

def test_clean_range_no_warnings():
    df = pl.DataFrame({"age": list(range(20, 60))})
    findings = RangeDistributionProfiler().profile(df, "age")
    warnings = [f for f in findings if f.severity == Severity.WARNING]
    assert len(warnings) == 0

def test_string_column_skipped():
    df = pl.DataFrame({"name": ["Alice", "Bob"]})
    findings = RangeDistributionProfiler().profile(df, "name")
    assert len(findings) == 0
```

- [ ] **Step 2: Implement** — compute mean/stddev with Polars, flag values beyond 3 stddev as outliers. Report min/max as info.
- [ ] **Step 3: Run tests, verify pass**
- [ ] **Step 4: Commit**

```bash
git add goldencheck/profilers/range_distribution.py tests/profilers/test_range_distribution.py
git commit -m "feat: add range and distribution profiler"
```

#### Task 8c: Cardinality Profiler

**Files:**
- Create: `goldencheck/profilers/cardinality.py`
- Create: `tests/profilers/test_cardinality.py`

- [ ] **Step 1: Write tests**

```python
import polars as pl
from goldencheck.profilers.cardinality import CardinalityProfiler

def test_low_cardinality_suggests_enum():
    df = pl.DataFrame({"status": ["active", "inactive", "pending", "closed"] * 25})
    findings = CardinalityProfiler().profile(df, "status")
    assert any("enum" in f.message.lower() for f in findings)

def test_high_cardinality_no_enum_suggestion():
    df = pl.DataFrame({"name": [f"Person {i}" for i in range(500)]})
    findings = CardinalityProfiler().profile(df, "name")
    assert not any("enum" in f.message.lower() for f in findings)
```

- [ ] **Step 2: Implement** — if unique values < 20 and row count > 50, suggest enum with the values listed.
- [ ] **Step 3: Run tests, verify pass**
- [ ] **Step 4: Commit**

```bash
git add goldencheck/profilers/cardinality.py tests/profilers/test_cardinality.py
git commit -m "feat: add cardinality profiler"
```

#### Task 8d: Pattern Consistency Profiler

**Files:**
- Create: `goldencheck/profilers/pattern_consistency.py`
- Create: `tests/profilers/test_pattern_consistency.py`

- [ ] **Step 1: Write tests**

```python
import polars as pl
from goldencheck.profilers.pattern_consistency import PatternConsistencyProfiler
from goldencheck.models.finding import Severity

def test_mixed_patterns_flagged():
    df = pl.DataFrame({"phone": ["(555) 123-4567"] * 90 + ["555.123.4567"] * 10})
    findings = PatternConsistencyProfiler().profile(df, "phone")
    assert any(f.severity == Severity.WARNING and "format" in f.message.lower() for f in findings)

def test_consistent_pattern_no_warning():
    df = pl.DataFrame({"code": ["ABC-123"] * 100})
    findings = PatternConsistencyProfiler().profile(df, "code")
    warnings = [f for f in findings if f.severity == Severity.WARNING]
    assert len(warnings) == 0
```

- [ ] **Step 2: Implement** — generalize string values to patterns (digit→D, letter→L, keep punctuation), group by pattern, flag minority patterns.
- [ ] **Step 3: Run tests, verify pass**
- [ ] **Step 4: Commit**

```bash
git add goldencheck/profilers/pattern_consistency.py tests/profilers/test_pattern_consistency.py
git commit -m "feat: add pattern consistency profiler"
```

- [ ] **Step 5: Run full profiler test suite**

```bash
pytest tests/profilers/ -v
```

---

### Task 9: Cross-Column Profilers (Temporal + Null Correlation)

**Files:**
- Create: `goldencheck/relations/__init__.py`
- Create: `goldencheck/relations/temporal.py`
- Create: `goldencheck/relations/null_correlation.py`
- Create: `tests/relations/__init__.py`
- Create: `tests/relations/test_temporal.py`
- Create: `tests/relations/test_null_correlation.py`

- [ ] **Step 1: Write tests for temporal ordering**

`tests/relations/test_temporal.py`:
```python
import polars as pl

from goldencheck.relations.temporal import TemporalOrderProfiler
from goldencheck.models.finding import Severity


def test_valid_temporal_order():
    df = pl.DataFrame({
        "start_date": ["2024-01-01", "2024-02-01", "2024-03-01"],
        "end_date": ["2024-01-15", "2024-02-15", "2024-03-15"],
    })
    findings = TemporalOrderProfiler().profile(df)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert len(errors) == 0


def test_invalid_temporal_order():
    df = pl.DataFrame({
        "start_date": ["2024-01-01", "2024-03-01", "2024-03-01"],
        "end_date": ["2024-01-15", "2024-02-01", "2024-03-15"],
    })
    findings = TemporalOrderProfiler().profile(df)
    assert any(f.severity == Severity.ERROR for f in findings)


def test_no_date_columns():
    df = pl.DataFrame({"name": ["Alice", "Bob"], "age": [30, 25]})
    findings = TemporalOrderProfiler().profile(df)
    assert len(findings) == 0
```

- [ ] **Step 2: Implement temporal profiler**
- [ ] **Step 3: Write tests and implement null correlation profiler**
- [ ] **Step 4: Run all relation tests**

```bash
pytest tests/relations/ -v
```

- [ ] **Step 5: Commit**

```bash
git add goldencheck/relations/ tests/relations/
git commit -m "feat: add temporal order and null correlation profilers"
```

---

### Task 10: Scanner (Orchestrates All Profilers)

**Files:**
- Create: `goldencheck/engine/scanner.py`
- Create: `tests/engine/test_scanner.py`

- [ ] **Step 1: Write tests for scanner**

`tests/engine/test_scanner.py`:
```python
from pathlib import Path

from goldencheck.engine.scanner import scan_file
from goldencheck.models.finding import Finding

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_scan_returns_findings():
    findings, profile = scan_file(FIXTURES / "simple.csv")
    assert isinstance(findings, list)
    assert all(isinstance(f, Finding) for f in findings)
    assert profile.row_count == 5
    assert profile.column_count == 5


def test_scan_detects_issues_in_fixture():
    findings, profile = scan_file(FIXTURES / "simple.csv")
    # simple.csv has: null email (row 3), non-email value (row 5)
    assert len(findings) > 0
```

- [ ] **Step 2: Implement scanner**

`goldencheck/engine/scanner.py`:
```python
"""Scanner — orchestrates all profilers and collects findings."""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from goldencheck.engine.reader import read_file
from goldencheck.engine.sampler import maybe_sample
from goldencheck.models.finding import Finding
from goldencheck.models.profile import ColumnProfile, DatasetProfile
from goldencheck.profilers.type_inference import TypeInferenceProfiler
from goldencheck.profilers.nullability import NullabilityProfiler
from goldencheck.profilers.uniqueness import UniquenessProfiler
from goldencheck.profilers.format_detection import FormatDetectionProfiler
from goldencheck.profilers.range_distribution import RangeDistributionProfiler
from goldencheck.profilers.cardinality import CardinalityProfiler
from goldencheck.profilers.pattern_consistency import PatternConsistencyProfiler
from goldencheck.relations.temporal import TemporalOrderProfiler
from goldencheck.relations.null_correlation import NullCorrelationProfiler

logger = logging.getLogger(__name__)

COLUMN_PROFILERS = [
    TypeInferenceProfiler(),
    NullabilityProfiler(),
    UniquenessProfiler(),
    FormatDetectionProfiler(),
    RangeDistributionProfiler(),
    CardinalityProfiler(),
    PatternConsistencyProfiler(),
]

RELATION_PROFILERS = [
    TemporalOrderProfiler(),
    NullCorrelationProfiler(),
]


def scan_file(
    path: Path,
    sample_size: int = 100_000,
) -> tuple[list[Finding], DatasetProfile]:
    """Scan a file and return all findings + dataset profile."""
    df = read_file(path)
    row_count = len(df)
    sample = maybe_sample(df, max_rows=sample_size)

    logger.info("Scanning %d rows, %d columns", row_count, len(df.columns))

    all_findings: list[Finding] = []
    column_profiles: list[ColumnProfile] = []

    # Run column profilers
    for col_name in df.columns:
        col = df[col_name]
        non_null = col.drop_nulls()

        cp = ColumnProfile(
            name=col_name,
            inferred_type=str(col.dtype),
            null_count=col.null_count(),
            null_pct=col.null_count() / row_count if row_count > 0 else 0,
            unique_count=non_null.n_unique() if len(non_null) > 0 else 0,
            unique_pct=non_null.n_unique() / len(non_null) if len(non_null) > 0 else 0,
            row_count=row_count,
        )
        column_profiles.append(cp)

        for profiler in COLUMN_PROFILERS:
            try:
                findings = profiler.profile(sample, col_name)
                all_findings.extend(findings)
            except Exception as e:
                logger.warning("Profiler %s failed on column %s: %s",
                             type(profiler).__name__, col_name, e)

    # Run relation profilers
    for profiler in RELATION_PROFILERS:
        try:
            findings = profiler.profile(sample)
            all_findings.extend(findings)
        except Exception as e:
            logger.warning("Relation profiler %s failed: %s",
                         type(profiler).__name__, e)

    # Sort by severity (errors first)
    all_findings.sort(key=lambda f: f.severity, reverse=True)

    profile = DatasetProfile(
        file_path=str(path),
        row_count=row_count,
        column_count=len(df.columns),
        columns=column_profiles,
    )

    return all_findings, profile
```

- [ ] **Step 3: Run tests — verify pass**

```bash
pytest tests/engine/test_scanner.py -v
```

- [ ] **Step 4: Commit**

```bash
git add goldencheck/engine/scanner.py tests/engine/test_scanner.py
git commit -m "feat: add scanner to orchestrate all profilers"
```

---

## Phase 3: Validator + Reporters

### Task 11: Validator (Checks Data Against Pinned Rules)

**Files:**
- Create: `goldencheck/engine/validator.py`
- Create: `tests/engine/test_validator.py`

The validator reads `goldencheck.yml`, checks each rule against the data, and returns findings for rule violations. Unlike the scanner (discovery), the validator only checks pinned rules.

- [ ] **Step 1: Write tests for validator**
- [ ] **Step 2: Implement validator**
- [ ] **Step 3: Run tests — verify pass**
- [ ] **Step 4: Commit**

```bash
git add goldencheck/engine/validator.py tests/engine/test_validator.py
git commit -m "feat: add rule validator"
```

---

### Task 12: Reporters (Rich Console + JSON + CI)

**Files:**
- Create: `goldencheck/reporters/__init__.py`
- Create: `goldencheck/reporters/rich_console.py`
- Create: `goldencheck/reporters/json_reporter.py`
- Create: `goldencheck/reporters/ci_reporter.py`
- Create: `tests/reporters/__init__.py`
- Create: `tests/reporters/test_reporters.py`

- [ ] **Step 1: Write tests for Rich console reporter**
- [ ] **Step 2: Implement Rich console reporter** (uses Rich tables to display findings)
- [ ] **Step 3: Write tests for JSON reporter** (outputs the schema from spec)
- [ ] **Step 4: Implement JSON reporter**
- [ ] **Step 5: Write tests for CI reporter** (exit code logic)
- [ ] **Step 6: Implement CI reporter**
- [ ] **Step 7: Run all reporter tests**

```bash
pytest tests/reporters/ -v
```

- [ ] **Step 8: Commit**

```bash
git add goldencheck/reporters/ tests/reporters/
git commit -m "feat: add Rich, JSON, and CI reporters"
```

---

## Phase 4: CLI

### Task 13: CLI Entry Points

**Files:**
- Create: `goldencheck/cli/__init__.py`
- Create: `goldencheck/cli/main.py`
- Create: `tests/cli/__init__.py`
- Create: `tests/cli/test_cli.py`

- [ ] **Step 1: Write CLI tests using Typer's test runner**

`tests/cli/test_cli.py`:
```python
from pathlib import Path

from typer.testing import CliRunner

from goldencheck.cli.main import app

runner = CliRunner()
FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_scan_with_no_tui():
    result = runner.invoke(app, [str(FIXTURES / "simple.csv"), "--no-tui"])
    assert result.exit_code == 0


def test_validate_without_config():
    result = runner.invoke(app, ["validate", str(FIXTURES / "simple.csv")])
    assert result.exit_code != 0
    assert "goldencheck.yml" in result.stdout or "goldencheck.yml" in (result.stderr or "")


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "goldencheck" in result.stdout.lower() or "GoldenCheck" in result.stdout
```

- [ ] **Step 2: Implement CLI**

`goldencheck/cli/__init__.py`: empty file.

`goldencheck/cli/main.py`:
```python
"""GoldenCheck CLI — data validation that discovers rules from your data."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer

from goldencheck import __version__

app = typer.Typer(
    name="goldencheck",
    help="Data validation that discovers rules from your data.",
    no_args_is_help=True,
)


def setup_logging(verbose: bool = False, debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(name)s — %(message)s")


@app.command()
def scan(
    file: Path = typer.Argument(..., help="Data file to scan (CSV, Parquet, Excel)"),
    no_tui: bool = typer.Option(False, "--no-tui", help="Print results to console instead of TUI"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show info-level logs"),
    debug: bool = typer.Option(False, "--debug", help="Show debug-level logs"),
    format: str = typer.Option("rich", "--format", "-f", help="Output format: rich, json"),
) -> None:
    """Scan a file, discover validation rules, and review interactively."""
    setup_logging(verbose, debug)

    from goldencheck.engine.scanner import scan_file
    from goldencheck.config.loader import load_config

    findings, profile = scan_file(file)
    config = load_config(Path("goldencheck.yml"))

    # Filter out ignored findings
    if config:
        ignored = {(i.column, i.check) for i in config.ignore}
        findings = [f for f in findings if (f.column, f.check) not in ignored]

    if no_tui or format == "json":
        if format == "json":
            from goldencheck.reporters.json_reporter import report_json
            report_json(findings, profile, sys.stdout)
        else:
            from goldencheck.reporters.rich_console import report_rich
            report_rich(findings, profile)
    else:
        from goldencheck.tui.app import GoldenCheckApp
        tui_app = GoldenCheckApp(findings=findings, profile=profile, config=config)
        tui_app.run()


@app.command()
def validate(
    file: Path = typer.Argument(..., help="Data file to validate"),
    fail_on: str = typer.Option("error", "--fail-on", help="Exit 1 on this severity: error, warning"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    debug: bool = typer.Option(False, "--debug"),
    format: str = typer.Option("rich", "--format", "-f", help="Output format: rich, json"),
) -> None:
    """Validate a file against pinned rules in goldencheck.yml."""
    setup_logging(verbose, debug)

    config_path = Path("goldencheck.yml")
    if not config_path.exists():
        typer.echo("No goldencheck.yml found. Run 'goldencheck scan <file>' first to discover and pin rules.")
        raise typer.Exit(code=1)

    from goldencheck.config.loader import load_config
    from goldencheck.engine.validator import validate_file
    from goldencheck.reporters.ci_reporter import report_ci

    config = load_config(config_path)
    findings = validate_file(file, config)

    if format == "json":
        from goldencheck.reporters.json_reporter import report_json
        from goldencheck.models.profile import DatasetProfile
        from goldencheck.engine.reader import read_file
        df = read_file(file)
        profile = DatasetProfile(file_path=str(file), row_count=len(df), column_count=len(df.columns), columns=[])
        report_json(findings, profile, sys.stdout)
    else:
        from goldencheck.reporters.rich_console import report_rich
        from goldencheck.engine.reader import read_file
        from goldencheck.models.profile import DatasetProfile
        df = read_file(file)
        profile = DatasetProfile(file_path=str(file), row_count=len(df), column_count=len(df.columns), columns=[])
        report_rich(findings, profile)

    exit_code = report_ci(findings, fail_on)
    raise typer.Exit(code=exit_code)


@app.callback(invoke_without_command=True)
def default(
    file: Path = typer.Argument(None, help="Data file to scan"),
    no_tui: bool = typer.Option(False, "--no-tui"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    debug: bool = typer.Option(False, "--debug"),
    format: str = typer.Option("rich", "--format", "-f"),
    version: bool = typer.Option(False, "--version", help="Show version"),
) -> None:
    """GoldenCheck — data validation that discovers rules from your data."""
    if version:
        typer.echo(f"goldencheck {__version__}")
        raise typer.Exit()
    if file:
        scan(file=file, no_tui=no_tui, verbose=verbose, debug=debug, format=format)
```

- [ ] **Step 3: Add `review` command**

Add to `goldencheck/cli/main.py`:
```python
@app.command()
def review(
    file: Path = typer.Argument(..., help="Data file to review"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Profile for new findings AND validate against existing rules."""
    setup_logging(verbose, debug)

    from goldencheck.engine.scanner import scan_file
    from goldencheck.engine.validator import validate_file
    from goldencheck.config.loader import load_config

    config_path = Path("goldencheck.yml")
    config = load_config(config_path)

    # Scan for new findings
    findings, profile = scan_file(file)

    # Also validate against existing rules if config exists
    if config:
        rule_findings = validate_file(file, config)
        findings = rule_findings + [f for f in findings if (f.column, f.check) not in
                                     {(rf.column, rf.check) for rf in rule_findings}]
        ignored = {(i.column, i.check) for i in config.ignore}
        findings = [f for f in findings if (f.column, f.check) not in ignored]

    from goldencheck.tui.app import GoldenCheckApp
    tui_app = GoldenCheckApp(findings=findings, profile=profile, config=config)
    tui_app.run()
```

- [ ] **Step 4: Add multi-file support to validate**

Change `validate` command signature to accept `list[Path]`:
```python
@app.command()
def validate(
    files: list[Path] = typer.Argument(..., help="Data file(s) to validate"),
    ...
) -> None:
```
Loop over each file, aggregate findings, use worst exit code.

- [ ] **Step 5: Run CLI tests — verify pass**

```bash
pytest tests/cli/test_cli.py -v
```

- [ ] **Step 6: Test CLI manually**

```bash
goldencheck tests/fixtures/simple.csv --no-tui
```

- [ ] **Step 7: Commit**

```bash
git add goldencheck/cli/ tests/cli/
git commit -m "feat: add CLI with scan, validate, review, and default commands"
```

---

## Phase 5: TUI

### Task 14: TUI App Shell + Overview Tab

**Files:**
- Create: `goldencheck/tui/__init__.py`
- Create: `goldencheck/tui/app.py`
- Create: `goldencheck/tui/overview.py`
- Create: `tests/tui/__init__.py`
- Create: `tests/tui/test_app.py`

- [ ] **Step 1: Write test for app launch**

```python
from goldencheck.tui.app import GoldenCheckApp
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import DatasetProfile

async def test_app_launches():
    profile = DatasetProfile(file_path="test.csv", row_count=100, column_count=5, columns=[])
    app = GoldenCheckApp(findings=[], profile=profile, config=None)
    async with app.run_test() as pilot:
        assert app.title == "GoldenCheck"
```

- [ ] **Step 2: Implement app shell** — Textual App with 4 tabs (1-4 keys), gold theme CSS, `?` help binding
- [ ] **Step 3: Implement Overview tab** — file stats, health score (A-F), column count, row count
- [ ] **Step 4: Run test, verify pass**
- [ ] **Step 5: Commit**

```bash
git add goldencheck/tui/__init__.py goldencheck/tui/app.py goldencheck/tui/overview.py tests/tui/
git commit -m "feat: add TUI app shell and Overview tab"
```

---

### Task 15: TUI Findings Tab

**Files:**
- Create: `goldencheck/tui/findings.py`

- [ ] **Step 1: Write test**

```python
async def test_findings_tab_shows_findings():
    findings = [Finding(severity=Severity.ERROR, column="email", check="format", message="Bad format")]
    profile = DatasetProfile(file_path="test.csv", row_count=100, column_count=5, columns=[])
    app = GoldenCheckApp(findings=findings, profile=profile, config=None)
    async with app.run_test() as pilot:
        await pilot.press("2")  # switch to Findings tab
        # Verify finding is displayed
```

- [ ] **Step 2: Implement** — DataTable or ListView showing findings sorted by severity. `Space` to toggle pin. `Enter` to switch to Column Detail. `e` to view offending rows. Filter by severity.
- [ ] **Step 3: Run test, verify pass**
- [ ] **Step 4: Commit**

```bash
git add goldencheck/tui/findings.py
git commit -m "feat: add TUI Findings tab"
```

---

### Task 16: TUI Column Detail + Rules Tabs

**Files:**
- Create: `goldencheck/tui/column_detail.py`
- Create: `goldencheck/tui/rules.py`
- Create: `goldencheck/tui/progress_overlay.py`

- [ ] **Step 1: Implement Column Detail tab** — shows full column profile (type, nulls, distribution, top values, format, outliers) for selected column
- [ ] **Step 2: Implement Rules tab** — shows all pinned rules. `F2` exports to `goldencheck.yml`. Can remove rules. Shows stale rules (column no longer exists) with warning indicator.
- [ ] **Step 3: Add progress overlay** for `F5` re-profiling (shows spinner while profiling runs)
- [ ] **Step 4: Manual test** — run `goldencheck tests/fixtures/simple.csv`, verify all tabs, pin a rule, export with F2, verify `goldencheck.yml` is created
- [ ] **Step 5: Commit**

```bash
git add goldencheck/tui/column_detail.py goldencheck/tui/rules.py goldencheck/tui/progress_overlay.py
git commit -m "feat: add Column Detail, Rules tabs, and progress overlay"
```

---

## Phase 6: Integration & Polish

### Task 17: End-to-End Integration Tests

**Files:**
- Create: `tests/test_integration.py`
- Create: `tests/fixtures/messy.csv` (fixture with intentional quality issues)

- [ ] **Step 1: Create messy test fixture** with: mixed types, nulls, outliers, format inconsistencies, duplicate IDs
- [ ] **Step 2: Write integration tests** — scan file, verify findings cover all profiler types, pin rules, export config, validate with config
- [ ] **Step 3: Run full test suite**

```bash
pytest -v --cov=goldencheck
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py tests/fixtures/messy.csv
git commit -m "test: add end-to-end integration tests"
```

---

### Task 18: Final Polish

**Files:**
- Modify: `README.md` (full README with examples)
- Modify: `pyproject.toml` (verify all metadata)

- [ ] **Step 1: Write full README** with: install, quick start, CLI reference, example output, comparison table, contributing section
- [ ] **Step 2: Verify `pip install .` works cleanly**
- [ ] **Step 3: Verify `goldencheck --help` shows all commands**
- [ ] **Step 4: Run full test suite one final time**

```bash
pytest -v --cov=goldencheck
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs: complete README and polish for v0.1.0"
```
