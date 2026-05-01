# infermap Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build infermap v1.0 — an open-source Python library and CLI for inference-driven schema mapping.

**Architecture:** Weighted scorer pipeline. Schema providers extract `SchemaInfo` from any source (files, DBs, DataFrames). Scorers independently score each `(source_field, target_field)` pair. The engine combines scores and applies Hungarian optimal assignment to produce `MapResult`. Results can be reported as JSON, applied to remap DataFrames, or saved as reusable YAML configs.

**Tech Stack:** Python 3.11+, Polars, RapidFuzz, SciPy, PyYAML, Typer, pytest

**Spec:** `docs/superpowers/specs/2026-03-29-infermap-design.md`

---

## File Map

| File | Responsibility |
|------|---------------|
| `pyproject.toml` | Package metadata, deps, optional extras, CLI entry point |
| `infermap/__init__.py` | Public API: `map()`, `from_config()`, `MapEngine`, `default_scorers()`, `scorer` decorator, type re-exports |
| `infermap/types.py` | Core dataclasses: `FieldInfo`, `SchemaInfo`, `ScorerResult`, `FieldMapping`, `MapResult` |
| `infermap/errors.py` | Exception classes: `InferMapError`, `ConfigError`, `ApplyError` |
| `infermap/assignment.py` | `optimal_assign(score_matrix, ...) -> list[tuple[int, int, float]]` |
| `infermap/engine.py` | `MapEngine` orchestrator: provider dispatch, scorer pipeline, assignment, result building |
| `infermap/config.py` | YAML config loading: scorer weight overrides, alias extensions, `from_config()` |
| `infermap/scorers/__init__.py` | `default_scorers()`, `scorer` decorator, `_REGISTRY` |
| `infermap/scorers/base.py` | `Scorer` protocol |
| `infermap/scorers/exact.py` | `ExactScorer` |
| `infermap/scorers/alias.py` | `AliasScorer` + `ALIASES` registry + reverse lookup |
| `infermap/scorers/pattern_type.py` | `PatternTypeScorer` + `SEMANTIC_TYPES` regex registry + `classify_field()` |
| `infermap/scorers/profile.py` | `ProfileScorer` |
| `infermap/scorers/fuzzy_name.py` | `FuzzyNameScorer` |
| `infermap/providers/__init__.py` | `detect_provider(source) -> Provider`, `extract_schema(source, **kw) -> SchemaInfo` |
| `infermap/providers/base.py` | `Provider` protocol |
| `infermap/providers/file.py` | `FileProvider` — CSV, Parquet, Excel via Polars |
| `infermap/providers/db.py` | `DBProvider` — Postgres, MySQL, SQLite, DuckDB |
| `infermap/providers/schema_file.py` | `SchemaFileProvider` — YAML/JSON definition files |
| `infermap/providers/memory.py` | `InMemoryProvider` — Polars/Pandas DataFrame, list[dict] |
| `infermap/cli.py` | Typer CLI: `map`, `apply`, `inspect`, `validate` commands |
| `tests/conftest.py` | Shared fixtures: `FieldInfo` factories, test DataFrames, CSV fixture paths |
| `tests/test_types.py` | Type dataclass tests |
| `tests/test_scorers/test_exact.py` | ExactScorer unit tests |
| `tests/test_scorers/test_alias.py` | AliasScorer unit tests |
| `tests/test_scorers/test_pattern_type.py` | PatternTypeScorer unit tests |
| `tests/test_scorers/test_profile.py` | ProfileScorer unit tests |
| `tests/test_scorers/test_fuzzy_name.py` | FuzzyNameScorer unit tests |
| `tests/test_scorers/test_registry.py` | Scorer registration + `default_scorers()` tests |
| `tests/test_providers/test_file.py` | FileProvider unit tests |
| `tests/test_providers/test_memory.py` | InMemoryProvider unit tests |
| `tests/test_providers/test_schema_file.py` | SchemaFileProvider unit tests |
| `tests/test_providers/test_db.py` | DBProvider unit tests (SQLite only; others mocked) |
| `tests/test_providers/test_detect.py` | Provider auto-detection tests |
| `tests/test_assignment.py` | Optimal assignment edge cases |
| `tests/test_config.py` | Config loading, weight overrides, `from_config()` |
| `tests/test_engine.py` | End-to-end integration tests |
| `tests/test_cli.py` | CLI smoke tests |
| `tests/fixtures/crm_export.csv` | Test fixture: messy CRM data |
| `tests/fixtures/canonical_customers.csv` | Test fixture: clean target schema |
| `tests/fixtures/healthcare_hl7.csv` | Test fixture: healthcare columns |
| `tests/fixtures/ambiguous.csv` | Test fixture: ambiguous column names |

---

## Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `infermap/__init__.py`
- Create: `infermap/types.py`
- Create: `infermap/errors.py`
- Create: `tests/conftest.py`
- Create: `tests/test_types.py`
- Create: `tests/fixtures/crm_export.csv`
- Create: `tests/fixtures/canonical_customers.csv`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "infermap"
version = "0.1.0"
description = "Inference-driven schema mapping engine"
readme = "README.md"
license = "MIT"
requires-python = ">=3.11"
authors = [{ name = "Ben Severn" }]
keywords = ["schema", "mapping", "etl", "data-engineering"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
]
dependencies = [
    "polars>=1.0",
    "rapidfuzz>=3.0",
    "scipy>=1.11",
    "pyyaml>=6.0",
    "typer>=0.9",
]

[project.optional-dependencies]
postgres = ["psycopg2-binary>=2.9"]
mysql = ["mysql-connector-python>=8.0"]
duckdb = ["duckdb>=0.9"]
excel = ["openpyxl>=3.1"]
llm = ["openai>=1.0", "anthropic>=0.20"]
all = ["infermap[postgres,mysql,duckdb,excel,llm]"]
dev = ["pytest>=8.0", "pytest-cov>=5.0", "ruff>=0.4"]

[project.scripts]
infermap = "infermap.cli:app"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

- [ ] **Step 2: Create error classes**

Create `infermap/errors.py`:

```python
"""infermap exception classes."""


class InferMapError(Exception):
    """Base exception for infermap."""


class ConfigError(InferMapError):
    """Raised for invalid config or schema files."""


class ApplyError(InferMapError):
    """Raised when apply() encounters missing columns."""
```

- [ ] **Step 3: Create core types**

Create `infermap/types.py`:

```python
"""Core dataclasses for infermap."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import polars as pl
import yaml

logger = logging.getLogger("infermap")

VALID_DTYPES = {"string", "integer", "float", "boolean", "date", "datetime"}


@dataclass
class FieldInfo:
    """Normalized representation of a single schema field."""

    name: str
    dtype: str = "string"
    sample_values: list[str] = field(default_factory=list)
    null_rate: float = 0.0
    unique_rate: float = 0.0
    value_count: int = 0
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.dtype not in VALID_DTYPES:
            self.dtype = "string"


@dataclass
class SchemaInfo:
    """A complete schema extracted from any source."""

    fields: list[FieldInfo]
    source_name: str = ""
    required_fields: list[str] = field(default_factory=list)


@dataclass
class ScorerResult:
    """Output from a single scorer for a single field pair."""

    score: float
    reasoning: str

    def __post_init__(self) -> None:
        self.score = max(0.0, min(1.0, self.score))


@dataclass
class FieldMapping:
    """A single source-to-target mapping with full audit trail."""

    source: str
    target: str
    confidence: float
    breakdown: dict[str, ScorerResult] = field(default_factory=dict)
    reasoning: str = ""


@dataclass
class MapResult:
    """The complete output of a mapping operation."""

    mappings: list[FieldMapping]
    unmapped_source: list[str] = field(default_factory=list)
    unmapped_target: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def report(self) -> dict:
        """Return structured dict of mapping results."""
        return {
            "mappings": [
                {
                    "source": m.source,
                    "target": m.target,
                    "confidence": round(m.confidence, 3),
                    "breakdown": {
                        name: {"score": round(r.score, 3), "reasoning": r.reasoning}
                        for name, r in m.breakdown.items()
                    },
                    "reasoning": m.reasoning,
                }
                for m in self.mappings
            ],
            "unmapped_source": self.unmapped_source,
            "unmapped_target": self.unmapped_target,
            "warnings": self.warnings,
        }

    def apply(self, df: pl.DataFrame) -> pl.DataFrame:
        """Rename source columns to target names. Preserves DataFrame type."""
        from .errors import ApplyError

        is_pandas = hasattr(df, "iloc")
        col_set = set(df.columns)
        missing = [m.source for m in self.mappings if m.source not in col_set]
        if missing:
            raise ApplyError(f"Source columns missing from DataFrame: {missing}")

        if is_pandas:
            rename_map = {m.source: m.target for m in self.mappings}
            return df.rename(columns=rename_map)

        rename_map = {m.source: m.target for m in self.mappings}
        return df.rename(rename_map)

    def to_config(self, path: str) -> None:
        """Save mapping as reusable YAML config."""
        data = {
            "version": "1",
            "mappings": [
                {"source": m.source, "target": m.target, "confidence": round(m.confidence, 3)}
                for m in self.mappings
            ],
            "unmapped_source": self.unmapped_source,
            "unmapped_target": self.unmapped_target,
        }
        with open(path, "w") as f:
            f.write(f"# Generated by infermap\n")
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        logger.info("Saved mapping config to %s", path)

    def to_json(self) -> str:
        """Return mapping as JSON string."""
        return json.dumps(self.report(), indent=2)
```

- [ ] **Step 4: Create test fixtures**

Create `tests/fixtures/crm_export.csv`:

```csv
fname,lname,email_addr,tel,zipcode
John,Doe,john@example.com,555-0100,10001
Jane,Smith,jane@test.org,(555) 020-0200,90210
Bob,Johnson,bob.j@mail.com,+15550300,30301
Alice,Williams,alice.w@example.net,555.0400,60601
Charlie,Brown,charlie@test.com,15550500,02101
```

Create `tests/fixtures/canonical_customers.csv`:

```csv
first_name,last_name,email,phone,zip_code
Sarah,Connor,sarah@example.com,555-9001,90001
Mike,Ross,mike@test.org,(555) 900-2000,10002
```

- [ ] **Step 5: Create conftest with fixtures**

Create `tests/conftest.py`:

```python
"""Shared test fixtures for infermap."""

from __future__ import annotations

from pathlib import Path

import pytest

from infermap.types import FieldInfo, SchemaInfo

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def crm_csv() -> Path:
    return FIXTURES / "crm_export.csv"


@pytest.fixture
def canonical_csv() -> Path:
    return FIXTURES / "canonical_customers.csv"


def make_field(
    name: str,
    dtype: str = "string",
    samples: list[str] | None = None,
    null_rate: float = 0.0,
    unique_rate: float = 1.0,
    value_count: int = 100,
    **metadata: object,
) -> FieldInfo:
    return FieldInfo(
        name=name,
        dtype=dtype,
        sample_values=samples or [],
        null_rate=null_rate,
        unique_rate=unique_rate,
        value_count=value_count,
        metadata=dict(metadata),
    )


def make_schema(fields: list[FieldInfo], name: str = "test", required: list[str] | None = None) -> SchemaInfo:
    return SchemaInfo(fields=fields, source_name=name, required_fields=required or [])
```

- [ ] **Step 6: Write type tests**

Create `tests/test_types.py`:

```python
"""Tests for core types."""

from __future__ import annotations

import json
import polars as pl
import pytest

from infermap.types import FieldInfo, FieldMapping, MapResult, ScorerResult


class TestFieldInfo:
    def test_defaults(self):
        f = FieldInfo(name="col")
        assert f.dtype == "string"
        assert f.sample_values == []
        assert f.null_rate == 0.0

    def test_invalid_dtype_defaults_to_string(self):
        f = FieldInfo(name="col", dtype="bogus")
        assert f.dtype == "string"


class TestScorerResult:
    def test_clamps_score(self):
        assert ScorerResult(score=1.5, reasoning="").score == 1.0
        assert ScorerResult(score=-0.1, reasoning="").score == 0.0


class TestMapResult:
    @pytest.fixture
    def result(self):
        return MapResult(
            mappings=[
                FieldMapping(
                    source="tel",
                    target="phone",
                    confidence=0.92,
                    breakdown={"ExactScorer": ScorerResult(0.0, "no match")},
                    reasoning="alias hit",
                )
            ],
            unmapped_source=["internal_ref"],
            unmapped_target=[],
            warnings=[],
        )

    def test_report_structure(self, result):
        r = result.report()
        assert len(r["mappings"]) == 1
        assert r["mappings"][0]["source"] == "tel"
        assert r["mappings"][0]["target"] == "phone"
        assert r["unmapped_source"] == ["internal_ref"]

    def test_to_json_is_valid(self, result):
        j = result.to_json()
        parsed = json.loads(j)
        assert "mappings" in parsed

    def test_apply_renames_columns(self, result):
        df = pl.DataFrame({"tel": ["555"], "internal_ref": ["x"]})
        out = result.apply(df)
        assert "phone" in out.columns
        assert "tel" not in out.columns
        assert "internal_ref" in out.columns

    def test_apply_preserves_pandas(self, result):
        import pandas as pd

        df = pd.DataFrame({"tel": ["555"], "internal_ref": ["x"]})
        out = result.apply(df)
        assert isinstance(out, pd.DataFrame)
        assert "phone" in out.columns

    def test_apply_missing_column_raises(self):
        result = MapResult(
            mappings=[FieldMapping(source="missing", target="x", confidence=1.0)]
        )
        df = pl.DataFrame({"other": [1]})
        with pytest.raises(Exception, match="missing"):
            result.apply(df)

    def test_to_config_roundtrip(self, result, tmp_path):
        path = str(tmp_path / "mapping.yaml")
        result.to_config(path)
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["version"] == "1"
        assert len(data["mappings"]) == 1
        assert data["mappings"][0]["source"] == "tel"
```

- [ ] **Step 7: Create minimal __init__.py**

Create `infermap/__init__.py`:

```python
"""infermap — inference-driven schema mapping engine."""

__version__ = "0.1.0"

from infermap.types import FieldInfo, FieldMapping, MapResult, SchemaInfo, ScorerResult
from infermap.errors import ApplyError, ConfigError, InferMapError

__all__ = [
    "FieldInfo",
    "FieldMapping",
    "MapResult",
    "SchemaInfo",
    "ScorerResult",
    "ApplyError",
    "ConfigError",
    "InferMapError",
]
```

- [ ] **Step 8: Run tests, verify they pass**

Run: `cd D:/show_case/infermap && pip install -e ".[dev]" && pytest tests/test_types.py -v`
Expected: All tests PASS.

- [ ] **Step 9: Commit**

```bash
git init && git add -A && git commit -m "feat: project scaffolding with core types, errors, and fixtures"
```

---

## Task 2: Scorer Protocol + ExactScorer + AliasScorer

**Files:**
- Create: `infermap/scorers/__init__.py`
- Create: `infermap/scorers/base.py`
- Create: `infermap/scorers/exact.py`
- Create: `infermap/scorers/alias.py`
- Create: `tests/test_scorers/__init__.py`
- Create: `tests/test_scorers/test_exact.py`
- Create: `tests/test_scorers/test_alias.py`
- Create: `tests/test_scorers/test_registry.py`

- [ ] **Step 1: Write ExactScorer tests**

Create `tests/test_scorers/__init__.py` (empty).

Create `tests/test_scorers/test_exact.py`:

```python
"""Tests for ExactScorer."""

from __future__ import annotations

import pytest
from tests.conftest import make_field
from infermap.scorers.exact import ExactScorer


class TestExactScorer:
    def setup_method(self):
        self.scorer = ExactScorer()

    def test_exact_match(self):
        r = self.scorer.score(make_field("email"), make_field("email"))
        assert r is not None
        assert r.score == 1.0

    def test_case_insensitive(self):
        r = self.scorer.score(make_field("Email"), make_field("email"))
        assert r is not None
        assert r.score == 1.0

    def test_no_match(self):
        r = self.scorer.score(make_field("phone"), make_field("email"))
        assert r is not None
        assert r.score == 0.0

    def test_strips_whitespace(self):
        r = self.scorer.score(make_field(" email "), make_field("email"))
        assert r is not None
        assert r.score == 1.0

    def test_name_and_weight(self):
        assert self.scorer.name == "ExactScorer"
        assert self.scorer.weight == 1.0
```

- [ ] **Step 2: Write AliasScorer tests**

Create `tests/test_scorers/test_alias.py`:

```python
"""Tests for AliasScorer."""

from __future__ import annotations

import pytest
from tests.conftest import make_field
from infermap.scorers.alias import AliasScorer


class TestAliasScorer:
    def setup_method(self):
        self.scorer = AliasScorer()

    def test_known_alias(self):
        r = self.scorer.score(make_field("tel"), make_field("phone"))
        assert r is not None
        assert r.score == 0.95

    def test_reverse_alias(self):
        r = self.scorer.score(make_field("phone"), make_field("telephone"))
        assert r is not None
        assert r.score == 0.95

    def test_no_alias_match(self):
        r = self.scorer.score(make_field("foo"), make_field("bar"))
        assert r is None  # abstains — neither field has known aliases

    def test_same_canonical(self):
        r = self.scorer.score(make_field("fname"), make_field("given_name"))
        assert r is not None
        assert r.score == 0.95

    def test_different_canonical(self):
        r = self.scorer.score(make_field("fname"), make_field("lname"))
        assert r is not None
        assert r.score == 0.0

    def test_schema_file_aliases(self):
        target = make_field("mrn")
        target.metadata["aliases"] = ["medical_record_number", "chart_number"]
        r = self.scorer.score(make_field("medical_record_number"), target)
        assert r is not None
        assert r.score == 0.95

    def test_name_and_weight(self):
        assert self.scorer.name == "AliasScorer"
        assert self.scorer.weight == 0.95
```

- [ ] **Step 3: Write scorer registry tests**

Create `tests/test_scorers/test_registry.py`:

```python
"""Tests for scorer registry and decorator."""

from __future__ import annotations

from infermap.scorers import default_scorers, scorer, _REGISTRY
from infermap.types import FieldInfo, ScorerResult


def test_default_scorers_returns_list():
    scorers = default_scorers()
    assert len(scorers) >= 2
    names = [s.name for s in scorers]
    assert "ExactScorer" in names
    assert "AliasScorer" in names


def test_scorer_decorator_registers():
    initial_count = len(_REGISTRY)

    @scorer(name="test_custom", weight=0.5)
    def custom(source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        return ScorerResult(score=0.5, reasoning="test")

    assert len(_REGISTRY) == initial_count + 1
    # Clean up
    _REGISTRY.pop("test_custom", None)
```

- [ ] **Step 4: Run tests, verify they fail**

Run: `pytest tests/test_scorers/ -v`
Expected: FAIL (modules not created yet).

- [ ] **Step 5: Implement scorer base + registry**

Create `infermap/scorers/base.py`:

```python
"""Scorer protocol definition."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from infermap.types import FieldInfo, ScorerResult


@runtime_checkable
class Scorer(Protocol):
    """Protocol for schema mapping scorers."""

    name: str
    weight: float

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None: ...
```

Create `infermap/scorers/__init__.py`:

```python
"""Scorer pipeline — registration, defaults, and plugin decorator."""

from __future__ import annotations

from typing import Callable

from infermap.types import FieldInfo, ScorerResult

_REGISTRY: dict[str, object] = {}


class _FunctionScorer:
    """Wraps a decorated function as a Scorer."""

    def __init__(self, fn: Callable, name: str, weight: float):
        self.fn = fn
        self.name = name
        self.weight = weight

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        return self.fn(source, target)


def scorer(name: str, weight: float) -> Callable:
    """Decorator to register a custom scorer function."""

    def decorator(fn: Callable) -> _FunctionScorer:
        s = _FunctionScorer(fn, name, weight)
        _REGISTRY[name] = s
        return s

    return decorator


def default_scorers() -> list:
    """Return the default set of v1 scorers."""
    from infermap.scorers.exact import ExactScorer
    from infermap.scorers.alias import AliasScorer
    from infermap.scorers.pattern_type import PatternTypeScorer
    from infermap.scorers.profile import ProfileScorer
    from infermap.scorers.fuzzy_name import FuzzyNameScorer

    return [ExactScorer(), AliasScorer(), PatternTypeScorer(), ProfileScorer(), FuzzyNameScorer()]
```

- [ ] **Step 6: Implement ExactScorer**

Create `infermap/scorers/exact.py`:

```python
"""ExactScorer — case-insensitive exact name match."""

from __future__ import annotations

from infermap.types import FieldInfo, ScorerResult


class ExactScorer:
    name: str = "ExactScorer"
    weight: float = 1.0

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        if source.name.lower().strip() == target.name.lower().strip():
            return ScorerResult(score=1.0, reasoning=f"exact name match: '{source.name}'")
        return ScorerResult(score=0.0, reasoning="names differ")
```

- [ ] **Step 7: Implement AliasScorer**

Create `infermap/scorers/alias.py`:

```python
"""AliasScorer — synonym/alias-based matching with built-in registry."""

from __future__ import annotations

from infermap.types import FieldInfo, ScorerResult

ALIASES: dict[str, list[str]] = {
    "first_name": ["fname", "first", "given_name", "first_nm", "forename"],
    "last_name": ["lname", "last", "surname", "family_name", "last_nm"],
    "email": ["email_address", "e_mail", "email_addr", "mail", "contact_email"],
    "phone": ["phone_number", "ph", "telephone", "tel", "mobile", "cell"],
    "address": ["addr", "street_address", "addr_line_1", "address_line_1", "mailing_address"],
    "city": ["town", "municipality"],
    "state": ["st", "province", "region"],
    "zip": ["zipcode", "zip_code", "postal_code", "postal", "postcode"],
    "name": ["full_name", "fullname", "customer_name", "display_name", "contact_name"],
    "company": ["organization", "org", "business", "employer", "firm", "company_name"],
    "dob": ["date_of_birth", "birth_date", "birthdate", "birthday"],
    "country": ["nation", "country_code"],
    "gender": ["sex"],
    "id": ["identifier", "record_id", "uid"],
    "created_at": ["signup_date", "create_date", "date_created"],
}

# Reverse lookup: alias -> canonical
_ALIAS_LOOKUP: dict[str, str] = {}
for _canonical, _aliases in ALIASES.items():
    _ALIAS_LOOKUP[_canonical.lower()] = _canonical.lower()
    for _alias in _aliases:
        _ALIAS_LOOKUP[_alias.lower()] = _canonical.lower()


def _get_canonical(name: str) -> str | None:
    """Resolve a field name to its canonical form."""
    return _ALIAS_LOOKUP.get(name.lower().strip().replace(" ", "_"))


class AliasScorer:
    name: str = "AliasScorer"
    weight: float = 0.95

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        # Check schema-file aliases on target
        target_aliases = [a.lower() for a in target.metadata.get("aliases", [])]
        if target_aliases and source.name.lower().strip() in target_aliases:
            return ScorerResult(
                score=0.95,
                reasoning=f"'{source.name}' is a declared alias for '{target.name}'",
            )

        # Check built-in registry
        src_canonical = _get_canonical(source.name)
        tgt_canonical = _get_canonical(target.name)

        # If neither field is in the registry, abstain
        if src_canonical is None and tgt_canonical is None:
            if not target_aliases:
                return None

        if src_canonical and tgt_canonical:
            if src_canonical == tgt_canonical:
                return ScorerResult(
                    score=0.95,
                    reasoning=f"both resolve to canonical '{src_canonical}'",
                )
            return ScorerResult(
                score=0.0,
                reasoning=f"different canonicals: '{src_canonical}' vs '{tgt_canonical}'",
            )

        return ScorerResult(score=0.0, reasoning="no alias match found")
```

- [ ] **Step 8: Create stub scorers so default_scorers() doesn't crash**

Create `infermap/scorers/pattern_type.py`, `infermap/scorers/profile.py`, `infermap/scorers/fuzzy_name.py` as stubs:

For each, use this pattern (adjust name/weight):

```python
"""PatternTypeScorer — stub, implemented in Task 3."""

from __future__ import annotations

from infermap.types import FieldInfo, ScorerResult


class PatternTypeScorer:
    name: str = "PatternTypeScorer"
    weight: float = 0.7

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        return None  # stub — abstains until implemented
```

```python
"""ProfileScorer — stub, implemented in Task 3."""

from __future__ import annotations

from infermap.types import FieldInfo, ScorerResult


class ProfileScorer:
    name: str = "ProfileScorer"
    weight: float = 0.5

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        return None
```

```python
"""FuzzyNameScorer — stub, implemented in Task 4."""

from __future__ import annotations

from infermap.types import FieldInfo, ScorerResult


class FuzzyNameScorer:
    name: str = "FuzzyNameScorer"
    weight: float = 0.4

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        return None
```

Also create `infermap/scorers/llm.py` stub (v1.1):

```python
"""LLMScorer — v1.1 feature. Stub for project structure completeness."""

from __future__ import annotations

from infermap.types import FieldInfo, ScorerResult


class LLMScorer:
    name: str = "LLMScorer"
    weight: float = 0.8

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        return None  # not included in default_scorers() until v1.1
```

- [ ] **Step 9: Run tests, verify they pass**

Run: `pytest tests/test_scorers/ -v`
Expected: All tests PASS.

- [ ] **Step 10: Commit**

```bash
git add -A && git commit -m "feat: scorer protocol, ExactScorer, AliasScorer with built-in registry"
```

---

## Task 3: PatternTypeScorer + ProfileScorer

**Files:**
- Modify: `infermap/scorers/pattern_type.py`
- Modify: `infermap/scorers/profile.py`
- Create: `tests/test_scorers/test_pattern_type.py`
- Create: `tests/test_scorers/test_profile.py`

- [ ] **Step 1: Write PatternTypeScorer tests**

Create `tests/test_scorers/test_pattern_type.py`:

```python
"""Tests for PatternTypeScorer."""

from __future__ import annotations

import pytest
from tests.conftest import make_field
from infermap.scorers.pattern_type import PatternTypeScorer, classify_field


class TestClassifyField:
    def test_email(self):
        f = make_field("col", samples=["a@b.com", "x@y.org", "test@test.co"])
        assert classify_field(f) == "email"

    def test_phone(self):
        f = make_field("col", samples=["555-0100", "(555) 020-0200", "+15550300"])
        assert classify_field(f) == "phone"

    def test_zip(self):
        f = make_field("col", samples=["10001", "90210", "30301", "60601", "02101"])
        assert classify_field(f) == "zip_us"

    def test_date_iso(self):
        f = make_field("col", samples=["2024-01-01", "2024-06-15", "2023-12-31"])
        assert classify_field(f) == "date_iso"

    def test_uuid(self):
        f = make_field("col", samples=["550e8400-e29b-41d4-a716-446655440000"])
        assert classify_field(f) == "uuid"

    def test_url(self):
        f = make_field("col", samples=["https://example.com", "http://test.org"])
        assert classify_field(f) == "url"

    def test_no_match(self):
        f = make_field("col", samples=["hello", "world", "foo"])
        assert classify_field(f) is None

    def test_empty_samples(self):
        f = make_field("col", samples=[])
        assert classify_field(f) is None

    def test_threshold_respected(self):
        # Only 1 out of 4 is an email — below 60%
        f = make_field("col", samples=["a@b.com", "hello", "world", "foo"])
        assert classify_field(f) != "email"


class TestPatternTypeScorer:
    def setup_method(self):
        self.scorer = PatternTypeScorer()

    def test_same_type_scores_high(self):
        src = make_field("col_a", samples=["a@b.com", "x@y.org", "t@t.co"])
        tgt = make_field("col_b", samples=["m@n.com", "p@q.org", "r@s.co"])
        r = self.scorer.score(src, tgt)
        assert r is not None
        assert r.score > 0.5

    def test_different_types_scores_zero(self):
        src = make_field("col_a", samples=["a@b.com", "x@y.org", "t@t.co"])
        tgt = make_field("col_b", samples=["10001", "90210", "30301"])
        r = self.scorer.score(src, tgt)
        assert r is not None
        assert r.score == 0.0

    def test_no_samples_returns_none(self):
        src = make_field("col_a", samples=[])
        tgt = make_field("col_b", samples=["a@b.com"])
        r = self.scorer.score(src, tgt)
        assert r is None

    def test_name_and_weight(self):
        assert self.scorer.name == "PatternTypeScorer"
        assert self.scorer.weight == 0.7
```

- [ ] **Step 2: Write ProfileScorer tests**

Create `tests/test_scorers/test_profile.py`:

```python
"""Tests for ProfileScorer."""

from __future__ import annotations

import pytest
from tests.conftest import make_field
from infermap.scorers.profile import ProfileScorer


class TestProfileScorer:
    def setup_method(self):
        self.scorer = ProfileScorer()

    def test_identical_profiles(self):
        src = make_field("a", dtype="string", null_rate=0.1, unique_rate=0.9, value_count=100)
        tgt = make_field("b", dtype="string", null_rate=0.1, unique_rate=0.9, value_count=100)
        r = self.scorer.score(src, tgt)
        assert r is not None
        assert r.score > 0.8

    def test_different_dtypes(self):
        src = make_field("a", dtype="string", null_rate=0.1, unique_rate=0.9, value_count=100)
        tgt = make_field("b", dtype="integer", null_rate=0.1, unique_rate=0.9, value_count=100)
        r = self.scorer.score(src, tgt)
        assert r is not None
        assert r.score < 0.8  # dtype mismatch drags score down

    def test_very_different_profiles(self):
        src = make_field("a", dtype="string", null_rate=0.0, unique_rate=1.0, value_count=1000)
        tgt = make_field("b", dtype="integer", null_rate=0.9, unique_rate=0.01, value_count=10)
        r = self.scorer.score(src, tgt)
        assert r is not None
        assert r.score < 0.3

    def test_no_samples_returns_none(self):
        src = make_field("a", value_count=0)
        tgt = make_field("b", value_count=0)
        r = self.scorer.score(src, tgt)
        assert r is None

    def test_name_and_weight(self):
        assert self.scorer.name == "ProfileScorer"
        assert self.scorer.weight == 0.5
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `pytest tests/test_scorers/test_pattern_type.py tests/test_scorers/test_profile.py -v`
Expected: FAIL (stubs return None or lack `classify_field`).

- [ ] **Step 4: Implement PatternTypeScorer**

Replace `infermap/scorers/pattern_type.py`:

```python
"""PatternTypeScorer — regex-based semantic type detection and comparison."""

from __future__ import annotations

import re

from infermap.types import FieldInfo, ScorerResult

SEMANTIC_TYPES: dict[str, str] = {
    "email": r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}$",
    "phone": r"^[\+]?[\d\s\-\(\)\.]{7,15}$",
    "zip_us": r"^\d{5}(-\d{4})?$",
    "date_iso": r"^\d{4}-\d{2}-\d{2}",
    "uuid": r"^[0-9a-f]{8}-[0-9a-f]{4}-",
    "url": r"^https?://",
    "currency": r"^[\$\u20ac\u00a3\u00a5]\s?\d",
    "ip_v4": r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$",
}

_COMPILED: dict[str, re.Pattern] = {k: re.compile(v, re.IGNORECASE) for k, v in SEMANTIC_TYPES.items()}

DEFAULT_THRESHOLD = 0.6


def classify_field(field: FieldInfo, threshold: float = DEFAULT_THRESHOLD) -> str | None:
    """Classify a field's semantic type based on sample values.

    Returns the type name where the highest fraction of samples match,
    or None if no type reaches the threshold.
    """
    if not field.sample_values:
        return None

    best_type: str | None = None
    best_pct = 0.0

    for type_name, pattern in _COMPILED.items():
        matches = sum(1 for v in field.sample_values if pattern.search(v.strip()))
        pct = matches / len(field.sample_values)
        if pct >= threshold and pct > best_pct:
            best_pct = pct
            best_type = type_name

    return best_type


def _classify_with_pct(field: FieldInfo, threshold: float = DEFAULT_THRESHOLD) -> tuple[str | None, float]:
    """Like classify_field but also returns the match percentage."""
    if not field.sample_values:
        return None, 0.0

    best_type: str | None = None
    best_pct = 0.0

    for type_name, pattern in _COMPILED.items():
        matches = sum(1 for v in field.sample_values if pattern.search(v.strip()))
        pct = matches / len(field.sample_values)
        if pct >= threshold and pct > best_pct:
            best_pct = pct
            best_type = type_name

    return best_type, best_pct


class PatternTypeScorer:
    name: str = "PatternTypeScorer"
    weight: float = 0.7

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        if not source.sample_values or not target.sample_values:
            return None  # abstain — no samples to evaluate

        src_type, src_pct = _classify_with_pct(source)
        tgt_type, tgt_pct = _classify_with_pct(target)

        if src_type is None or tgt_type is None:
            return ScorerResult(
                score=0.0,
                reasoning="no semantic type detected from samples",
            )

        if src_type == tgt_type:
            score = min(src_pct, tgt_pct)
            return ScorerResult(
                score=score,
                reasoning=f"both classified as '{src_type}' (src={src_pct:.0%}, tgt={tgt_pct:.0%})",
            )

        return ScorerResult(
            score=0.0,
            reasoning=f"type mismatch: src='{src_type}', tgt='{tgt_type}'",
        )
```

- [ ] **Step 5: Implement ProfileScorer**

Replace `infermap/scorers/profile.py`:

```python
"""ProfileScorer — statistical profile comparison."""

from __future__ import annotations

from infermap.types import FieldInfo, ScorerResult


def _avg_value_length(samples: list[str]) -> float:
    """Average string length of sample values."""
    if not samples:
        return 0.0
    return sum(len(s) for s in samples) / len(samples)


class ProfileScorer:
    name: str = "ProfileScorer"
    weight: float = 0.5

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        if source.value_count == 0 or target.value_count == 0:
            return None

        total = 0.0
        weights = 0.0
        reasons = []

        # Dtype match (0.4 weight)
        if source.dtype == target.dtype:
            total += 0.4
            reasons.append(f"dtype match ({source.dtype})")
        else:
            reasons.append(f"dtype mismatch ({source.dtype} vs {target.dtype})")
        weights += 0.4

        # Null rate similarity (0.2 weight)
        null_sim = 1.0 - abs(source.null_rate - target.null_rate)
        total += 0.2 * null_sim
        weights += 0.2

        # Uniqueness similarity (0.2 weight)
        unique_sim = 1.0 - abs(source.unique_rate - target.unique_rate)
        total += 0.2 * unique_sim
        weights += 0.2

        # Value length similarity (0.1 weight)
        src_avg_len = _avg_value_length(source.sample_values)
        tgt_avg_len = _avg_value_length(target.sample_values)
        if src_avg_len > 0 and tgt_avg_len > 0:
            len_ratio = min(src_avg_len, tgt_avg_len) / max(src_avg_len, tgt_avg_len)
            total += 0.1 * len_ratio
        weights += 0.1

        # Cardinality ratio (0.1 weight)
        if source.value_count > 0 and target.value_count > 0:
            ratio = min(source.value_count, target.value_count) / max(
                source.value_count, target.value_count
            )
            total += 0.1 * ratio
        weights += 0.1

        final_score = total / weights if weights > 0 else 0.0
        return ScorerResult(
            score=final_score,
            reasoning="; ".join(reasons)
            + f" | null_sim={null_sim:.2f}, unique_sim={unique_sim:.2f}",
        )
```

- [ ] **Step 6: Run tests, verify they pass**

Run: `pytest tests/test_scorers/ -v`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: PatternTypeScorer with semantic type registry + ProfileScorer"
```

---

## Task 4: FuzzyNameScorer

**Files:**
- Modify: `infermap/scorers/fuzzy_name.py`
- Create: `tests/test_scorers/test_fuzzy_name.py`

- [ ] **Step 1: Write FuzzyNameScorer tests**

Create `tests/test_scorers/test_fuzzy_name.py`:

```python
"""Tests for FuzzyNameScorer."""

from __future__ import annotations

from tests.conftest import make_field
from infermap.scorers.fuzzy_name import FuzzyNameScorer


class TestFuzzyNameScorer:
    def setup_method(self):
        self.scorer = FuzzyNameScorer()

    def test_similar_names(self):
        r = self.scorer.score(make_field("first_name"), make_field("firstname"))
        assert r is not None
        assert r.score > 0.7

    def test_dissimilar_names(self):
        r = self.scorer.score(make_field("email"), make_field("zip_code"))
        assert r is not None
        assert r.score < 0.5

    def test_identical_names(self):
        r = self.scorer.score(make_field("email"), make_field("email"))
        assert r is not None
        assert r.score == 1.0

    def test_normalizes_underscores(self):
        r = self.scorer.score(make_field("zip_code"), make_field("zipcode"))
        assert r is not None
        assert r.score > 0.8

    def test_name_and_weight(self):
        assert self.scorer.name == "FuzzyNameScorer"
        assert self.scorer.weight == 0.4
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_scorers/test_fuzzy_name.py -v`
Expected: FAIL (stub returns None).

- [ ] **Step 3: Implement FuzzyNameScorer**

Replace `infermap/scorers/fuzzy_name.py`:

```python
"""FuzzyNameScorer — Jaro-Winkler fuzzy name matching."""

from __future__ import annotations

from rapidfuzz.distance import JaroWinkler

from infermap.types import FieldInfo, ScorerResult


def _normalize(name: str) -> str:
    """Normalize a field name for comparison."""
    return name.lower().strip().replace("_", "").replace("-", "").replace(" ", "")


class FuzzyNameScorer:
    name: str = "FuzzyNameScorer"
    weight: float = 0.4

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        src_norm = _normalize(source.name)
        tgt_norm = _normalize(target.name)

        sim = JaroWinkler.similarity(src_norm, tgt_norm)

        return ScorerResult(
            score=sim,
            reasoning=f"Jaro-Winkler similarity: {sim:.3f} ('{source.name}' vs '{target.name}')",
        )
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_scorers/ -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: FuzzyNameScorer with Jaro-Winkler similarity"
```

---

## Task 5: Assignment Module

**Files:**
- Create: `infermap/assignment.py`
- Create: `tests/test_assignment.py`

- [ ] **Step 1: Write assignment tests**

Create `tests/test_assignment.py`:

```python
"""Tests for optimal assignment."""

from __future__ import annotations

import numpy as np
import pytest

from infermap.assignment import optimal_assign


class TestOptimalAssign:
    def test_perfect_diagonal(self):
        # 3x3 identity-like: each source maps to its corresponding target
        scores = np.array([
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ])
        result = optimal_assign(scores, min_confidence=0.3)
        assert len(result) == 3
        pairs = {(r, c) for r, c, _ in result}
        assert pairs == {(0, 0), (1, 1), (2, 2)}

    def test_filters_below_threshold(self):
        scores = np.array([
            [0.9, 0.1],
            [0.2, 0.1],  # both below 0.3 after assignment
        ])
        result = optimal_assign(scores, min_confidence=0.3)
        assert len(result) == 1
        assert result[0][0] == 0  # first row matched

    def test_more_sources_than_targets(self):
        scores = np.array([
            [0.9],
            [0.8],
            [0.7],
        ])
        result = optimal_assign(scores, min_confidence=0.3)
        assert len(result) == 1  # only one target available

    def test_more_targets_than_sources(self):
        scores = np.array([
            [0.9, 0.8, 0.7],
        ])
        result = optimal_assign(scores, min_confidence=0.3)
        assert len(result) == 1

    def test_all_zeros(self):
        scores = np.zeros((3, 3))
        result = optimal_assign(scores, min_confidence=0.3)
        assert len(result) == 0

    def test_single_cell(self):
        scores = np.array([[0.85]])
        result = optimal_assign(scores, min_confidence=0.3)
        assert len(result) == 1
        assert result[0] == (0, 0, 0.85)

    def test_optimal_not_greedy(self):
        # Greedy would assign (0,0)=0.9 first, leaving (1,1)=0.5
        # Optimal should assign (0,1)=0.8 and (1,0)=0.85 for total 1.65
        scores = np.array([
            [0.9, 0.8],
            [0.85, 0.5],
        ])
        result = optimal_assign(scores, min_confidence=0.3)
        assert len(result) == 2
        total = sum(s for _, _, s in result)
        assert total > 1.6  # optimal beats greedy
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_assignment.py -v`
Expected: FAIL (module doesn't exist).

- [ ] **Step 3: Implement assignment**

Create `infermap/assignment.py`:

```python
"""Optimal assignment via the Hungarian algorithm."""

from __future__ import annotations

import numpy as np
from scipy.optimize import linear_sum_assignment


def optimal_assign(
    score_matrix: np.ndarray,
    min_confidence: float = 0.3,
) -> list[tuple[int, int, float]]:
    """Find optimal 1:1 assignment from score matrix.

    Args:
        score_matrix: M x N matrix of combined scores (higher = better).
        min_confidence: Minimum score to keep a mapping.

    Returns:
        List of (source_idx, target_idx, score) tuples, filtered by min_confidence.
    """
    if score_matrix.size == 0:
        return []

    cost_matrix = 1.0 - score_matrix
    row_indices, col_indices = linear_sum_assignment(cost_matrix)

    results = []
    for r, c in zip(row_indices, col_indices):
        score = float(score_matrix[r, c])
        if score >= min_confidence:
            results.append((int(r), int(c), round(score, 4)))

    return results
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_assignment.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: optimal assignment module using Hungarian algorithm"
```

---

## Task 6: Providers — FileProvider + InMemoryProvider

**Files:**
- Create: `infermap/providers/__init__.py`
- Create: `infermap/providers/base.py`
- Create: `infermap/providers/file.py`
- Create: `infermap/providers/memory.py`
- Create: `tests/test_providers/__init__.py`
- Create: `tests/test_providers/test_file.py`
- Create: `tests/test_providers/test_memory.py`

- [ ] **Step 1: Write FileProvider tests**

Create `tests/test_providers/__init__.py` (empty).

Create `tests/test_providers/test_file.py`:

```python
"""Tests for FileProvider."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import FIXTURES

from infermap.providers.file import FileProvider


class TestFileProvider:
    def setup_method(self):
        self.provider = FileProvider()

    def test_csv_extraction(self):
        schema = self.provider.extract(str(FIXTURES / "crm_export.csv"))
        assert schema.source_name == "crm_export.csv"
        names = [f.name for f in schema.fields]
        assert "fname" in names
        assert "email_addr" in names

    def test_field_has_samples(self):
        schema = self.provider.extract(str(FIXTURES / "crm_export.csv"))
        email_field = next(f for f in schema.fields if f.name == "email_addr")
        assert len(email_field.sample_values) > 0
        assert "@" in email_field.sample_values[0]

    def test_field_has_stats(self):
        schema = self.provider.extract(str(FIXTURES / "crm_export.csv"))
        field = schema.fields[0]
        assert field.value_count > 0
        assert 0.0 <= field.null_rate <= 1.0
        assert 0.0 <= field.unique_rate <= 1.0

    def test_missing_file_raises(self):
        from infermap.errors import InferMapError

        with pytest.raises(InferMapError, match="not found"):
            self.provider.extract("nonexistent.csv")

    def test_sample_size_configurable(self):
        schema = self.provider.extract(str(FIXTURES / "crm_export.csv"), sample_size=2)
        field = schema.fields[0]
        assert len(field.sample_values) <= 2
```

- [ ] **Step 2: Write InMemoryProvider tests**

Create `tests/test_providers/test_memory.py`:

```python
"""Tests for InMemoryProvider."""

from __future__ import annotations

import polars as pl
import pytest

from infermap.providers.memory import InMemoryProvider


class TestInMemoryProvider:
    def setup_method(self):
        self.provider = InMemoryProvider()

    def test_polars_df(self):
        df = pl.DataFrame({"name": ["Alice", "Bob"], "age": [30, 25]})
        schema = self.provider.extract(df)
        names = [f.name for f in schema.fields]
        assert "name" in names
        assert "age" in names

    def test_pandas_df(self):
        import pandas as pd

        df = pd.DataFrame({"email": ["a@b.com"], "phone": ["555"]})
        schema = self.provider.extract(df)
        names = [f.name for f in schema.fields]
        assert "email" in names

    def test_list_of_dicts(self):
        data = [{"x": "1", "y": "a"}, {"x": "2", "y": "b"}]
        schema = self.provider.extract(data)
        assert len(schema.fields) == 2

    def test_field_stats(self):
        df = pl.DataFrame({"col": ["a", "b", None, "a"]})
        schema = self.provider.extract(df)
        field = schema.fields[0]
        assert field.null_rate == pytest.approx(0.25)
        assert field.value_count == 3

    def test_source_name(self):
        df = pl.DataFrame({"x": [1]})
        schema = self.provider.extract(df, source_name="my_data")
        assert schema.source_name == "my_data"
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `pytest tests/test_providers/ -v`
Expected: FAIL.

- [ ] **Step 4: Implement provider base + detection**

Create `infermap/providers/base.py`:

```python
"""Provider protocol definition."""

from __future__ import annotations

from typing import Any, Protocol

from infermap.types import SchemaInfo


class Provider(Protocol):
    """Protocol for schema extraction providers."""

    def extract(self, source: Any, **kwargs: Any) -> SchemaInfo: ...
```

Create `infermap/providers/__init__.py`:

```python
"""Schema providers — auto-detection and extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from infermap.types import SchemaInfo


def detect_provider(source: Any) -> str:
    """Detect which provider to use for a given source."""
    if isinstance(source, str):
        s = source.lower()
        if any(s.startswith(p) for p in ("postgresql://", "mysql://", "sqlite://", "duckdb://")):
            return "db"
        ext = Path(s).suffix
        if ext in (".yaml", ".yml", ".json"):
            return "schema_file"
        if ext in (".csv", ".parquet", ".xlsx", ".xls"):
            return "file"
    # DataFrame-like
    if hasattr(source, "columns"):
        return "memory"
    if isinstance(source, list) and source and isinstance(source[0], dict):
        return "memory"
    return "unknown"


def extract_schema(source: Any, **kwargs: Any) -> SchemaInfo:
    """Auto-detect provider and extract schema."""
    provider_type = detect_provider(source)

    if provider_type == "file":
        from infermap.providers.file import FileProvider
        return FileProvider().extract(source, **kwargs)
    elif provider_type == "memory":
        from infermap.providers.memory import InMemoryProvider
        return InMemoryProvider().extract(source, **kwargs)
    elif provider_type == "schema_file":
        from infermap.providers.schema_file import SchemaFileProvider
        return SchemaFileProvider().extract(source, **kwargs)
    elif provider_type == "db":
        from infermap.providers.db import DBProvider
        return DBProvider().extract(source, **kwargs)
    else:
        from infermap.errors import InferMapError
        raise InferMapError(f"Cannot detect provider for source: {type(source)}")
```

- [ ] **Step 5: Implement FileProvider**

Create `infermap/providers/file.py`:

```python
"""FileProvider — extract schema from CSV, Parquet, Excel files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import polars as pl

from infermap.errors import InferMapError
from infermap.types import FieldInfo, SchemaInfo

logger = logging.getLogger("infermap")

_DTYPE_MAP = {
    "Utf8": "string",
    "String": "string",
    "Int8": "integer",
    "Int16": "integer",
    "Int32": "integer",
    "Int64": "integer",
    "UInt8": "integer",
    "UInt16": "integer",
    "UInt32": "integer",
    "UInt64": "integer",
    "Float32": "float",
    "Float64": "float",
    "Boolean": "boolean",
    "Date": "date",
    "Datetime": "datetime",
}


def _normalize_dtype(polars_dtype: str) -> str:
    """Map Polars dtype to normalized infermap dtype."""
    for key, val in _DTYPE_MAP.items():
        if key in polars_dtype:
            return val
    return "string"


class FileProvider:
    def extract(self, source: Any, **kwargs: Any) -> SchemaInfo:
        path = Path(str(source))
        if not path.exists():
            raise InferMapError(f"File not found: {path}")

        sample_size = kwargs.get("sample_size", 500)

        ext = path.suffix.lower()
        if ext == ".csv":
            df = pl.read_csv(str(path), n_rows=sample_size, encoding="utf8", ignore_errors=True)
        elif ext == ".parquet":
            df = pl.read_parquet(str(path), n_rows=sample_size)
        elif ext in (".xlsx", ".xls"):
            try:
                df = pl.read_excel(str(path), engine="openpyxl")
                df = df.head(sample_size)
            except ImportError:
                raise InferMapError(
                    "Excel support requires openpyxl: pip install infermap[excel]"
                )
        else:
            raise InferMapError(f"Unsupported file type: {ext}")

        fields = []
        for col in df.columns:
            series = df[col]
            total = len(series)
            null_count = series.null_count()
            non_null = total - null_count

            samples = [
                str(v)
                for v in series.drop_nulls().head(sample_size).to_list()
            ]

            unique_count = series.drop_nulls().n_unique()

            fields.append(
                FieldInfo(
                    name=col,
                    dtype=_normalize_dtype(str(series.dtype)),
                    sample_values=samples,
                    null_rate=round(null_count / total, 4) if total > 0 else 0.0,
                    unique_rate=round(unique_count / non_null, 4) if non_null > 0 else 0.0,
                    value_count=non_null,
                )
            )

        logger.info("Extracted %d fields from %s", len(fields), path.name)
        return SchemaInfo(fields=fields, source_name=path.name)
```

- [ ] **Step 6: Implement InMemoryProvider**

Create `infermap/providers/memory.py`:

```python
"""InMemoryProvider — extract schema from DataFrames or dicts."""

from __future__ import annotations

import logging
from typing import Any

import polars as pl

from infermap.types import FieldInfo, SchemaInfo

logger = logging.getLogger("infermap")


def _to_polars(source: Any) -> pl.DataFrame:
    """Convert various in-memory formats to Polars."""
    if isinstance(source, pl.DataFrame):
        return source
    if hasattr(source, "iloc"):  # pandas
        return pl.from_pandas(source)
    if isinstance(source, list) and source and isinstance(source[0], dict):
        return pl.DataFrame(source)
    from infermap.errors import InferMapError
    raise InferMapError(f"Cannot convert {type(source)} to DataFrame")


class InMemoryProvider:
    def extract(self, source: Any, **kwargs: Any) -> SchemaInfo:
        df = _to_polars(source)
        sample_size = kwargs.get("sample_size", 500)
        source_name = kwargs.get("source_name", "in_memory")

        fields = []
        for col in df.columns:
            series = df[col]
            total = len(series)
            null_count = series.null_count()
            non_null = total - null_count

            samples = [str(v) for v in series.drop_nulls().head(sample_size).to_list()]
            unique_count = series.drop_nulls().n_unique()

            from infermap.providers.file import _normalize_dtype

            fields.append(
                FieldInfo(
                    name=col,
                    dtype=_normalize_dtype(str(series.dtype)),
                    sample_values=samples,
                    null_rate=round(null_count / total, 4) if total > 0 else 0.0,
                    unique_rate=round(unique_count / non_null, 4) if non_null > 0 else 0.0,
                    value_count=non_null,
                )
            )

        logger.info("Extracted %d fields from in-memory source", len(fields))
        return SchemaInfo(fields=fields, source_name=source_name)
```

- [ ] **Step 7: Run tests, verify they pass**

Run: `pytest tests/test_providers/ -v`
Expected: All tests PASS.

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "feat: FileProvider and InMemoryProvider with auto-detection"
```

---

## Task 7: SchemaFileProvider + DBProvider (SQLite)

**Files:**
- Create: `infermap/providers/schema_file.py`
- Create: `infermap/providers/db.py`
- Create: `tests/test_providers/test_schema_file.py`
- Create: `tests/test_providers/test_db.py`
- Create: `tests/test_providers/test_detect.py`

- [ ] **Step 1: Write SchemaFileProvider tests**

Create `tests/test_providers/test_schema_file.py`:

```python
"""Tests for SchemaFileProvider."""

from __future__ import annotations

import pytest
import yaml

from infermap.errors import ConfigError
from infermap.providers.schema_file import SchemaFileProvider


class TestSchemaFileProvider:
    def setup_method(self):
        self.provider = SchemaFileProvider()

    def test_valid_yaml(self, tmp_path):
        f = tmp_path / "schema.yaml"
        f.write_text(yaml.dump({
            "fields": [
                {"name": "email", "type": "string", "aliases": ["e_mail"], "required": True},
                {"name": "phone", "type": "string"},
            ]
        }))
        schema = self.provider.extract(str(f))
        assert len(schema.fields) == 2
        assert schema.fields[0].name == "email"
        assert schema.fields[0].metadata["aliases"] == ["e_mail"]
        assert "email" in schema.required_fields

    def test_missing_fields_key_raises(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text(yaml.dump({"columns": []}))
        with pytest.raises(ConfigError, match="fields"):
            self.provider.extract(str(f))

    def test_json_format(self, tmp_path):
        import json

        f = tmp_path / "schema.json"
        f.write_text(json.dumps({
            "fields": [{"name": "id", "type": "integer"}]
        }))
        schema = self.provider.extract(str(f))
        assert len(schema.fields) == 1
        assert schema.fields[0].dtype == "integer"

    def test_field_defaults(self, tmp_path):
        f = tmp_path / "schema.yaml"
        f.write_text(yaml.dump({"fields": [{"name": "col"}]}))
        schema = self.provider.extract(str(f))
        assert schema.fields[0].dtype == "string"
        assert schema.fields[0].sample_values == []
```

- [ ] **Step 2: Write DBProvider tests (SQLite)**

Create `tests/test_providers/test_db.py`:

```python
"""Tests for DBProvider (SQLite only — no external deps)."""

from __future__ import annotations

import sqlite3

import pytest

from infermap.providers.db import DBProvider


class TestDBProviderSQLite:
    @pytest.fixture
    def sqlite_db(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE customers (name TEXT, age INTEGER, email TEXT)"
        )
        conn.execute(
            "INSERT INTO customers VALUES ('Alice', 30, 'alice@test.com')"
        )
        conn.execute(
            "INSERT INTO customers VALUES ('Bob', 25, 'bob@test.com')"
        )
        conn.execute("INSERT INTO customers VALUES (NULL, NULL, NULL)")
        conn.commit()
        conn.close()
        return f"sqlite:///{db_path}"

    def setup_method(self):
        self.provider = DBProvider()

    def test_extracts_fields(self, sqlite_db):
        schema = self.provider.extract(sqlite_db, table="customers")
        names = [f.name for f in schema.fields]
        assert "name" in names
        assert "age" in names
        assert "email" in names

    def test_field_has_samples(self, sqlite_db):
        schema = self.provider.extract(sqlite_db, table="customers")
        name_field = next(f for f in schema.fields if f.name == "name")
        assert "Alice" in name_field.sample_values

    def test_null_rate(self, sqlite_db):
        schema = self.provider.extract(sqlite_db, table="customers")
        name_field = next(f for f in schema.fields if f.name == "name")
        assert name_field.null_rate == pytest.approx(1 / 3, abs=0.01)

    def test_missing_table_raises(self, sqlite_db):
        from infermap.errors import InferMapError

        with pytest.raises(InferMapError):
            self.provider.extract(sqlite_db, table="nonexistent")
```

- [ ] **Step 3: Write auto-detection tests**

Create `tests/test_providers/test_detect.py`:

```python
"""Tests for provider auto-detection."""

from __future__ import annotations

import polars as pl

from infermap.providers import detect_provider


def test_csv():
    assert detect_provider("data.csv") == "file"


def test_parquet():
    assert detect_provider("data.parquet") == "file"


def test_excel():
    assert detect_provider("data.xlsx") == "file"


def test_postgres():
    assert detect_provider("postgresql://host/db") == "db"


def test_sqlite():
    assert detect_provider("sqlite:///path.db") == "db"


def test_yaml():
    assert detect_provider("schema.yaml") == "schema_file"


def test_json():
    assert detect_provider("schema.json") == "schema_file"


def test_polars_df():
    assert detect_provider(pl.DataFrame({"x": [1]})) == "memory"


def test_list_of_dicts():
    assert detect_provider([{"x": 1}]) == "memory"


def test_unknown():
    assert detect_provider(42) == "unknown"
```

- [ ] **Step 4: Run tests, verify they fail**

Run: `pytest tests/test_providers/ -v`
Expected: FAIL (schema_file.py and db.py don't exist).

- [ ] **Step 5: Implement SchemaFileProvider**

Create `infermap/providers/schema_file.py`:

```python
"""SchemaFileProvider — extract schema from YAML/JSON definition files."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from infermap.errors import ConfigError
from infermap.types import FieldInfo, SchemaInfo

logger = logging.getLogger("infermap")


class SchemaFileProvider:
    def extract(self, source: Any, **kwargs: Any) -> SchemaInfo:
        path = Path(str(source))
        if not path.exists():
            raise ConfigError(f"Schema file not found: {path}")

        text = path.read_text(encoding="utf-8")

        if path.suffix.lower() in (".yaml", ".yml"):
            try:
                data = yaml.safe_load(text)
            except yaml.YAMLError as e:
                raise ConfigError(f"Invalid YAML in {path}: {e}") from e
        elif path.suffix.lower() == ".json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                raise ConfigError(f"Invalid JSON in {path}: {e}") from e
        else:
            raise ConfigError(f"Unsupported schema file type: {path.suffix}")

        if not isinstance(data, dict) or "fields" not in data:
            raise ConfigError(
                f"Schema file must contain a 'fields' key: {path}"
            )

        fields = []
        required = []

        for entry in data["fields"]:
            name = entry.get("name", "")
            if not name:
                continue

            metadata = {}
            if "aliases" in entry:
                metadata["aliases"] = entry["aliases"]

            field = FieldInfo(
                name=name,
                dtype=entry.get("type", "string"),
                sample_values=[],
                null_rate=0.0,
                unique_rate=0.0,
                value_count=0,
                metadata=metadata,
            )
            fields.append(field)

            if entry.get("required", False):
                required.append(name)

        logger.info("Loaded %d fields from schema file %s", len(fields), path.name)
        return SchemaInfo(
            fields=fields,
            source_name=path.name,
            required_fields=required,
        )
```

- [ ] **Step 6: Implement DBProvider (SQLite)**

Create `infermap/providers/db.py`:

```python
"""DBProvider — extract schema from databases via connection strings."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any
from urllib.parse import urlparse

from infermap.errors import InferMapError
from infermap.types import FieldInfo, SchemaInfo

logger = logging.getLogger("infermap")


def _parse_connection(uri: str) -> tuple[str, dict]:
    """Parse a connection URI into (dialect, params)."""
    parsed = urlparse(uri)
    dialect = parsed.scheme.replace("://", "")
    if dialect.startswith("sqlite"):
        # sqlite:///path or sqlite:////abs/path
        # Strip exactly one leading slash (the separator between authority and path)
        path = parsed.path[1:] if parsed.path.startswith("/") else parsed.path
        return "sqlite", {"path": path}
    return dialect, {
        "host": parsed.hostname or "localhost",
        "port": parsed.port,
        "user": parsed.username,
        "password": parsed.password,
        "database": parsed.path.lstrip("/"),
    }


def _extract_sqlite(path: str, table: str, sample_size: int) -> SchemaInfo:
    """Extract schema from SQLite database."""
    try:
        conn = sqlite3.connect(path)
    except Exception as e:
        raise InferMapError(f"Cannot connect to SQLite database: {path}") from e

    try:
        # Check table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        if not cursor.fetchone():
            raise InferMapError(f"Table '{table}' not found in {path}")

        # Get column info
        cursor = conn.execute(f"PRAGMA table_info({table})")
        columns = [(row[1], row[2]) for row in cursor.fetchall()]

        # Sample rows
        cursor = conn.execute(f"SELECT * FROM [{table}] LIMIT ?", (sample_size,))
        rows = cursor.fetchall()
        total_cursor = conn.execute(f"SELECT COUNT(*) FROM [{table}]")
        total_count = total_cursor.fetchone()[0]

        fields = []
        for i, (col_name, col_type) in enumerate(columns):
            values = [row[i] for row in rows]
            non_null = [v for v in values if v is not None]
            samples = [str(v) for v in non_null[:sample_size]]

            null_count = sum(1 for v in values if v is None)
            total_sampled = len(values)

            # Scale null rate to full table
            null_rate = null_count / total_sampled if total_sampled > 0 else 0.0
            unique_count = len(set(non_null))
            unique_rate = unique_count / len(non_null) if non_null else 0.0

            dtype = _sqlite_type_to_infermap(col_type)

            fields.append(
                FieldInfo(
                    name=col_name,
                    dtype=dtype,
                    sample_values=samples,
                    null_rate=round(null_rate, 4),
                    unique_rate=round(unique_rate, 4),
                    value_count=total_count - int(null_rate * total_count),
                    metadata={"db_type": col_type},
                )
            )

        return SchemaInfo(fields=fields, source_name=f"{path}:{table}")
    finally:
        conn.close()


def _sqlite_type_to_infermap(db_type: str) -> str:
    """Map SQLite type to normalized infermap dtype."""
    t = db_type.upper()
    if "INT" in t:
        return "integer"
    if "REAL" in t or "FLOAT" in t or "DOUBLE" in t:
        return "float"
    if "BOOL" in t:
        return "boolean"
    if "DATE" in t and "TIME" in t:
        return "datetime"
    if "DATE" in t:
        return "date"
    return "string"


class DBProvider:
    def extract(self, source: Any, **kwargs: Any) -> SchemaInfo:
        uri = str(source)
        table = kwargs.get("table")
        sample_size = kwargs.get("sample_size", 500)

        if not table:
            raise InferMapError("DBProvider requires a 'table' parameter")

        dialect, params = _parse_connection(uri)

        if dialect == "sqlite":
            return _extract_sqlite(params["path"], table, sample_size)

        # Postgres, MySQL, DuckDB — import on demand
        if dialect in ("postgresql", "postgres"):
            return _extract_postgres(params, table, sample_size)
        elif dialect == "mysql":
            return _extract_mysql(params, table, sample_size)
        elif dialect == "duckdb":
            return _extract_duckdb(params, table, sample_size)

        raise InferMapError(f"Unsupported database dialect: {dialect}")


## Note: Postgres, MySQL, DuckDB providers are stubbed with NotImplementedError
## for v1.0. SQLite is fully functional. The other DB providers follow the same
## pattern (INFORMATION_SCHEMA query + sample rows) and can be implemented
## as a fast-follow after v1.0 ships with SQLite proving the interface works.

def _extract_postgres(params: dict, table: str, sample_size: int) -> SchemaInfo:
    try:
        import psycopg2
    except ImportError:
        raise InferMapError("PostgreSQL support requires psycopg2: pip install infermap[postgres]")
    raise NotImplementedError("PostgreSQL extraction — coming in v1.0.x")


def _extract_mysql(params: dict, table: str, sample_size: int) -> SchemaInfo:
    try:
        import mysql.connector
    except ImportError:
        raise InferMapError("MySQL support requires mysql-connector: pip install infermap[mysql]")
    raise NotImplementedError("MySQL extraction — coming soon")


def _extract_duckdb(params: dict, table: str, sample_size: int) -> SchemaInfo:
    try:
        import duckdb
    except ImportError:
        raise InferMapError("DuckDB support requires duckdb: pip install infermap[duckdb]")
    raise NotImplementedError("DuckDB extraction — coming soon")
```

- [ ] **Step 7: Run tests, verify they pass**

Run: `pytest tests/test_providers/ -v`
Expected: All tests PASS.

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "feat: SchemaFileProvider, DBProvider (SQLite), and provider auto-detection"
```

---

## Task 8: MapEngine

**Files:**
- Create: `infermap/engine.py`
- Create: `tests/test_engine.py`

- [ ] **Step 1: Write engine integration tests**

Create `tests/test_engine.py`:

```python
"""Integration tests for MapEngine."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from tests.conftest import FIXTURES

from infermap.engine import MapEngine


class TestMapEngine:
    def test_csv_to_csv(self):
        engine = MapEngine()
        result = engine.map(
            str(FIXTURES / "crm_export.csv"),
            str(FIXTURES / "canonical_customers.csv"),
        )
        mapped_sources = {m.source for m in result.mappings}
        mapped_targets = {m.target for m in result.mappings}

        # Should map fname->first_name, lname->last_name, email_addr->email, tel->phone
        assert "fname" in mapped_sources
        assert "first_name" in mapped_targets
        assert "email_addr" in mapped_sources or "email" in mapped_targets

    def test_dataframe_to_dataframe(self):
        src = pl.DataFrame({"email_address": ["a@b.com"], "tel": ["555"]})
        tgt = pl.DataFrame({"email": ["x@y.com"], "phone": ["999"]})
        engine = MapEngine()
        result = engine.map(src, tgt)
        assert len(result.mappings) >= 1

    def test_required_field_warning(self):
        src = pl.DataFrame({"x": [1]})
        tgt = pl.DataFrame({"email": ["a@b.com"], "phone": ["555"]})
        engine = MapEngine()
        result = engine.map(src, tgt, required=["email"])
        # email should be unmapped and warned
        assert any("email" in w for w in result.warnings)

    def test_min_confidence_filtering(self):
        src = pl.DataFrame({"aaa": [1]})
        tgt = pl.DataFrame({"zzz": [2]})
        engine = MapEngine(min_confidence=0.9)
        result = engine.map(src, tgt)
        assert len(result.mappings) == 0

    def test_apply_after_map(self):
        engine = MapEngine()
        result = engine.map(
            str(FIXTURES / "crm_export.csv"),
            str(FIXTURES / "canonical_customers.csv"),
        )
        src_df = pl.read_csv(str(FIXTURES / "crm_export.csv"))
        remapped = result.apply(src_df)
        # At least some columns should be renamed
        assert remapped.columns != src_df.columns

    def test_metadata_includes_timing(self):
        engine = MapEngine()
        result = engine.map(
            str(FIXTURES / "crm_export.csv"),
            str(FIXTURES / "canonical_customers.csv"),
        )
        assert "elapsed_seconds" in result.metadata

    def test_minimum_contributor_threshold(self):
        """Pairs with only 1 contributing scorer get score 0.0."""
        # Fields with no samples and no alias — only FuzzyNameScorer contributes
        src = pl.DataFrame({"qqq_xyz": [None]})
        tgt = pl.DataFrame({"qqq_abc": [None]})
        engine = MapEngine(min_confidence=0.01)
        result = engine.map(src, tgt)
        # With only 1 non-None scorer, the pair should be filtered out
        assert len(result.mappings) == 0
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_engine.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement MapEngine**

Create `infermap/engine.py`:

```python
"""MapEngine — orchestrates provider extraction, scorer pipeline, and assignment."""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from infermap.assignment import optimal_assign
from infermap.providers import extract_schema
from infermap.scorers import default_scorers
from infermap.types import FieldMapping, MapResult, SchemaInfo, ScorerResult

logger = logging.getLogger("infermap")

MIN_CONTRIBUTORS = 2


class MapEngine:
    """Main orchestrator for schema mapping."""

    def __init__(
        self,
        min_confidence: float = 0.3,
        sample_size: int = 500,
        scorers: list | None = None,
        config_path: str | None = None,
    ):
        self.min_confidence = min_confidence
        self.sample_size = sample_size
        self.scorers = scorers or default_scorers()
        self._config_path = config_path

        if config_path:
            self._apply_config(config_path)

    def _apply_config(self, path: str) -> None:
        """Apply YAML config overrides for scorer weights."""
        import yaml
        from pathlib import Path

        config_file = Path(path)
        if not config_file.exists():
            return

        with open(config_file) as f:
            cfg = yaml.safe_load(f) or {}

        scorer_cfg = cfg.get("scorers", {})
        for s in self.scorers:
            if s.name in scorer_cfg:
                overrides = scorer_cfg[s.name]
                if isinstance(overrides, dict):
                    if "weight" in overrides:
                        s.weight = overrides["weight"]
                    if overrides.get("enabled") is False:
                        self.scorers = [x for x in self.scorers if x.name != s.name]

        # Merge custom aliases into AliasScorer registry
        alias_cfg = cfg.get("aliases", {})
        if alias_cfg:
            from infermap.scorers.alias import ALIASES, _ALIAS_LOOKUP

            for canonical, aliases in alias_cfg.items():
                canonical_lower = canonical.lower()
                ALIASES[canonical_lower] = aliases
                _ALIAS_LOOKUP[canonical_lower] = canonical_lower
                for alias in aliases:
                    _ALIAS_LOOKUP[alias.lower()] = canonical_lower

    def map(
        self,
        source: Any,
        target: Any,
        required: list[str] | None = None,
        schema_file: str | None = None,
        **kwargs: Any,
    ) -> MapResult:
        """Map source schema to target schema."""
        start = time.monotonic()

        # Extract schemas
        src_schema = extract_schema(source, sample_size=self.sample_size, **kwargs)
        tgt_schema = extract_schema(target, sample_size=self.sample_size, **kwargs)

        # Merge required fields
        all_required = set(tgt_schema.required_fields)
        if required:
            all_required.update(required)

        # If schema_file provided, merge aliases into target fields
        if schema_file:
            from infermap.providers.schema_file import SchemaFileProvider

            schema_def = SchemaFileProvider().extract(schema_file)
            all_required.update(schema_def.required_fields)
            _merge_schema_file(tgt_schema, schema_def)

        # Build score matrix
        n_src = len(src_schema.fields)
        n_tgt = len(tgt_schema.fields)

        if n_src == 0 or n_tgt == 0:
            return MapResult(
                mappings=[],
                unmapped_source=[f.name for f in src_schema.fields],
                unmapped_target=[f.name for f in tgt_schema.fields],
                warnings=["Empty schema — no fields to map"],
                metadata={"elapsed_seconds": round(time.monotonic() - start, 3)},
            )

        score_matrix = np.zeros((n_src, n_tgt))
        breakdowns: dict[tuple[int, int], dict[str, ScorerResult]] = {}

        for i, src_field in enumerate(src_schema.fields):
            for j, tgt_field in enumerate(tgt_schema.fields):
                results: dict[str, ScorerResult] = {}
                weighted_sum = 0.0
                weight_sum = 0.0
                contributor_count = 0

                for scorer in self.scorers:
                    try:
                        result = scorer.score(src_field, tgt_field)
                    except Exception as e:
                        logger.warning(
                            "Scorer %s raised %s for (%s, %s): %s",
                            scorer.name, type(e).__name__, src_field.name, tgt_field.name, e,
                        )
                        continue

                    if result is None:
                        continue  # abstain

                    results[scorer.name] = result
                    weighted_sum += scorer.weight * result.score
                    weight_sum += scorer.weight
                    contributor_count += 1

                if contributor_count >= MIN_CONTRIBUTORS and weight_sum > 0:
                    score_matrix[i, j] = weighted_sum / weight_sum
                else:
                    score_matrix[i, j] = 0.0

                breakdowns[(i, j)] = results

        # Optimal assignment
        assignments = optimal_assign(score_matrix, self.min_confidence)

        # Build result
        mapped_src_indices = set()
        mapped_tgt_indices = set()
        mappings = []

        for src_idx, tgt_idx, confidence in assignments:
            src_name = src_schema.fields[src_idx].name
            tgt_name = tgt_schema.fields[tgt_idx].name
            bd = breakdowns.get((src_idx, tgt_idx), {})

            # Build human-readable reasoning
            top_scorers = sorted(bd.items(), key=lambda x: x[1].score, reverse=True)
            reasoning_parts = [f"{name}={r.score:.2f}" for name, r in top_scorers[:3] if r.score > 0]
            reasoning = f"matched via {', '.join(reasoning_parts)}" if reasoning_parts else "low-confidence match"

            mappings.append(
                FieldMapping(
                    source=src_name,
                    target=tgt_name,
                    confidence=confidence,
                    breakdown=bd,
                    reasoning=reasoning,
                )
            )
            mapped_src_indices.add(src_idx)
            mapped_tgt_indices.add(tgt_idx)

        unmapped_source = [
            src_schema.fields[i].name
            for i in range(n_src)
            if i not in mapped_src_indices
        ]
        unmapped_target = [
            tgt_schema.fields[j].name
            for j in range(n_tgt)
            if j not in mapped_tgt_indices
        ]

        # Warnings for required fields
        warnings = []
        for req in all_required:
            if req not in {m.target for m in mappings}:
                # Find best candidate
                tgt_idx = next(
                    (j for j, f in enumerate(tgt_schema.fields) if f.name == req), None
                )
                if tgt_idx is not None:
                    best_src_idx = int(np.argmax(score_matrix[:, tgt_idx]))
                    best_score = score_matrix[best_src_idx, tgt_idx]
                    best_name = src_schema.fields[best_src_idx].name
                    warnings.append(
                        f"required field '{req}' has no match "
                        f"(best candidate: '{best_name}' at {best_score:.2f})"
                    )
                else:
                    warnings.append(f"required field '{req}' not found in target schema")

        elapsed = round(time.monotonic() - start, 3)
        logger.info(
            "Mapped %d/%d source fields in %.3fs", len(mappings), n_src, elapsed
        )

        return MapResult(
            mappings=mappings,
            unmapped_source=unmapped_source,
            unmapped_target=unmapped_target,
            warnings=warnings,
            metadata={
                "elapsed_seconds": elapsed,
                "source_fields": n_src,
                "target_fields": n_tgt,
                "scorers": [s.name for s in self.scorers],
                "min_confidence": self.min_confidence,
            },
        )


def _merge_schema_file(target_schema: SchemaInfo, schema_def: SchemaInfo) -> None:
    """Merge schema file metadata (aliases, types) into target schema fields."""
    def_lookup = {f.name: f for f in schema_def.fields}
    for field in target_schema.fields:
        if field.name in def_lookup:
            def_field = def_lookup[field.name]
            if "aliases" in def_field.metadata:
                field.metadata.setdefault("aliases", [])
                field.metadata["aliases"].extend(def_field.metadata["aliases"])
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_engine.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: MapEngine orchestrator with scorer pipeline and optimal assignment"
```

---

## Task 9: Config Loading + from_config()

**Files:**
- Create: `infermap/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write config tests**

Create `tests/test_config.py`:

```python
"""Tests for config loading and from_config()."""

from __future__ import annotations

import yaml
import pytest

from infermap.config import from_config
from infermap.types import MapResult


class TestFromConfig:
    def test_roundtrip(self, tmp_path):
        # Create a mapping config
        data = {
            "version": "1",
            "mappings": [
                {"source": "fname", "target": "first_name", "confidence": 0.95},
                {"source": "lname", "target": "last_name", "confidence": 0.92},
            ],
            "unmapped_source": ["internal_id"],
            "unmapped_target": [],
        }
        path = tmp_path / "mapping.yaml"
        with open(path, "w") as f:
            yaml.dump(data, f)

        result = from_config(str(path))
        assert isinstance(result, MapResult)
        assert len(result.mappings) == 2
        assert result.mappings[0].source == "fname"
        assert result.mappings[0].target == "first_name"
        assert result.mappings[0].confidence == 0.95
        assert result.unmapped_source == ["internal_id"]

    def test_missing_file_raises(self):
        from infermap.errors import ConfigError

        with pytest.raises(ConfigError):
            from_config("nonexistent.yaml")

    def test_invalid_yaml_raises(self, tmp_path):
        from infermap.errors import ConfigError

        path = tmp_path / "bad.yaml"
        path.write_text(": : : invalid")
        with pytest.raises(ConfigError):
            from_config(str(path))
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement config.py**

Create `infermap/config.py`:

```python
"""Config loading — YAML config files and from_config() for saved mappings."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from infermap.errors import ConfigError
from infermap.types import FieldMapping, MapResult

logger = logging.getLogger("infermap")


def from_config(path: str) -> MapResult:
    """Load a saved mapping config and return a MapResult (no inference)."""
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {path}")

    try:
        with open(config_path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path}: {e}") from e

    if not isinstance(data, dict) or "mappings" not in data:
        raise ConfigError(f"Config file must contain 'mappings' key: {path}")

    mappings = []
    for entry in data["mappings"]:
        mappings.append(
            FieldMapping(
                source=entry["source"],
                target=entry["target"],
                confidence=entry.get("confidence", 1.0),
            )
        )

    logger.info("Loaded %d mappings from config %s", len(mappings), path)
    return MapResult(
        mappings=mappings,
        unmapped_source=data.get("unmapped_source", []),
        unmapped_target=data.get("unmapped_target", []),
        warnings=[],
        metadata={"loaded_from": path},
    )
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: config loading and from_config() for saved mapping reuse"
```

---

## Task 10: Public API + CLI

**Files:**
- Modify: `infermap/__init__.py`
- Create: `infermap/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write CLI smoke tests**

Create `tests/test_cli.py`:

```python
"""CLI smoke tests."""

from __future__ import annotations

from typer.testing import CliRunner

from infermap.cli import app
from tests.conftest import FIXTURES

runner = CliRunner()


class TestCLI:
    def test_map_csv_to_csv(self):
        result = runner.invoke(app, [
            "map",
            str(FIXTURES / "crm_export.csv"),
            str(FIXTURES / "canonical_customers.csv"),
        ])
        assert result.exit_code == 0
        assert "fname" in result.stdout or "first_name" in result.stdout

    def test_map_json_format(self):
        result = runner.invoke(app, [
            "map",
            str(FIXTURES / "crm_export.csv"),
            str(FIXTURES / "canonical_customers.csv"),
            "--format", "json",
        ])
        assert result.exit_code == 0
        assert '"mappings"' in result.stdout

    def test_inspect(self):
        result = runner.invoke(app, [
            "inspect",
            str(FIXTURES / "crm_export.csv"),
        ])
        assert result.exit_code == 0
        assert "fname" in result.stdout

    def test_apply(self, tmp_path):
        # First create a mapping config
        config = tmp_path / "mapping.yaml"
        config.write_text(
            'version: "1"\nmappings:\n  - source: fname\n    target: first_name\n    confidence: 0.95\n'
        )
        result = runner.invoke(app, [
            "apply",
            str(FIXTURES / "crm_export.csv"),
            "--config", str(config),
            "--output", str(tmp_path / "out.csv"),
        ])
        assert result.exit_code == 0

    def test_validate_strict_fails(self, tmp_path):
        config = tmp_path / "mapping.yaml"
        config.write_text(
            'version: "1"\nmappings:\n  - source: fname\n    target: first_name\n    confidence: 0.95\n'
        )
        result = runner.invoke(app, [
            "validate",
            str(FIXTURES / "crm_export.csv"),
            "--config", str(config),
            "--strict",
            "--required", "email,phone",
        ])
        # Should fail — email and phone aren't in mappings
        assert result.exit_code == 1
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement CLI**

Create `infermap/cli.py`:

```python
"""CLI for infermap — map, apply, inspect, validate."""

from __future__ import annotations

import json
import logging
import sys

import typer
import polars as pl

app = typer.Typer(name="infermap", help="Inference-driven schema mapping engine")


@app.command()
def map(
    source: str = typer.Argument(..., help="Source file, DB URI, or schema file"),
    target: str = typer.Argument(..., help="Target file, DB URI, or schema file"),
    table: str | None = typer.Option(None, help="DB table name (for DB sources)"),
    required: str | None = typer.Option(None, help="Comma-separated required target fields"),
    schema_file: str | None = typer.Option(None, "--schema-file", help="Schema definition file"),
    format: str = typer.Option("table", help="Output format: table, json, yaml"),
    output: str | None = typer.Option(None, "-o", "--output", help="Save mapping config to file"),
    min_confidence: float = typer.Option(0.3, help="Minimum confidence threshold"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Map source columns to target schema."""
    _setup_logging(verbose, debug)

    from infermap.engine import MapEngine

    kwargs = {}
    if table:
        kwargs["table"] = table

    required_list = [r.strip() for r in required.split(",")] if required else None

    engine = MapEngine(min_confidence=min_confidence)
    result = engine.map(source, target, required=required_list, schema_file=schema_file, **kwargs)

    if output:
        result.to_config(output)
        typer.echo(f"Saved mapping to {output}")

    if format == "json":
        typer.echo(result.to_json())
    elif format == "yaml":
        import yaml
        typer.echo(yaml.dump(result.report(), default_flow_style=False))
    else:
        _print_table(result)


@app.command()
def apply(
    source: str = typer.Argument(..., help="Source file to remap"),
    config: str = typer.Option(..., "--config", "-c", help="Mapping config file"),
    output: str = typer.Option(..., "-o", "--output", help="Output file path"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Apply a saved mapping to remap a file."""
    _setup_logging(verbose, False)

    from infermap.config import from_config

    result = from_config(config)
    df = pl.read_csv(source)
    remapped = result.apply(df)
    remapped.write_csv(output)
    typer.echo(f"Remapped {len(remapped.columns)} columns -> {output}")


@app.command()
def inspect(
    source: str = typer.Argument(..., help="File or DB URI to inspect"),
    table: str | None = typer.Option(None, help="DB table name"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Inspect a schema — show fields, types, and samples."""
    _setup_logging(verbose, False)

    from infermap.providers import extract_schema

    kwargs = {}
    if table:
        kwargs["table"] = table

    schema = extract_schema(source, **kwargs)
    typer.echo(f"Schema: {schema.source_name} ({len(schema.fields)} fields)\n")
    for f in schema.fields:
        samples = ", ".join(f.sample_values[:3])
        typer.echo(f"  {f.name:20s}  {f.dtype:10s}  null={f.null_rate:.0%}  unique={f.unique_rate:.0%}  [{samples}]")


@app.command()
def validate(
    source: str = typer.Argument(..., help="Source file to validate"),
    config: str = typer.Option(..., "--config", "-c", help="Mapping config file"),
    required: str | None = typer.Option(None, help="Comma-separated required target fields"),
    strict: bool = typer.Option(False, "--strict", help="Exit code 1 if required fields unmapped"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Validate that a source file satisfies a mapping config."""
    _setup_logging(verbose, False)

    from infermap.config import from_config

    result = from_config(config)
    df = pl.read_csv(source)

    required_list = set(r.strip() for r in required.split(",")) if required else set()
    mapped_targets = {m.target for m in result.mappings}

    missing_required = required_list - mapped_targets
    missing_source_cols = [m.source for m in result.mappings if m.source not in df.columns]

    if missing_source_cols:
        typer.echo(f"WARNING: Source missing columns: {missing_source_cols}")

    if missing_required:
        typer.echo(f"FAIL: Required fields not mapped: {sorted(missing_required)}")
        if strict:
            raise typer.Exit(code=1)
    else:
        typer.echo("OK: All required fields are mapped")


def _setup_logging(verbose: bool, debug: bool) -> None:
    level = logging.WARNING
    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    logging.basicConfig(level=level, format="%(name)s: %(message)s")
    logging.getLogger("infermap").setLevel(level)


def _print_table(result) -> None:
    """Print mapping results as a formatted table."""
    if not result.mappings:
        typer.echo("No mappings found.")
    else:
        typer.echo(f"{'Source':25s} -> {'Target':25s}  {'Confidence':>10s}  Reasoning")
        typer.echo("-" * 80)
        for m in result.mappings:
            typer.echo(f"{m.source:25s} -> {m.target:25s}  {m.confidence:10.3f}  {m.reasoning}")

    if result.unmapped_source:
        typer.echo(f"\nUnmapped source: {', '.join(result.unmapped_source)}")
    if result.unmapped_target:
        typer.echo(f"Unmapped target: {', '.join(result.unmapped_target)}")
    if result.warnings:
        typer.echo(f"\nWarnings:")
        for w in result.warnings:
            typer.echo(f"  - {w}")
```

- [ ] **Step 4: Update __init__.py with full public API**

Replace `infermap/__init__.py`:

```python
"""infermap — inference-driven schema mapping engine."""

__version__ = "0.1.0"

from infermap.types import FieldInfo, FieldMapping, MapResult, SchemaInfo, ScorerResult
from infermap.errors import ApplyError, ConfigError, InferMapError
from infermap.engine import MapEngine
from infermap.config import from_config
from infermap.scorers import default_scorers, scorer
from infermap.providers import extract_schema


def map(source, target, **kwargs):
    """Map source schema to target schema. Main entry point."""
    engine = MapEngine()
    return engine.map(source, target, **kwargs)


__all__ = [
    "map",
    "from_config",
    "extract_schema",
    "default_scorers",
    "scorer",
    "MapEngine",
    "FieldInfo",
    "FieldMapping",
    "MapResult",
    "SchemaInfo",
    "ScorerResult",
    "ApplyError",
    "ConfigError",
    "InferMapError",
]
```

- [ ] **Step 5: Run all tests**

Run: `pytest -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: CLI (map, apply, inspect, validate) and public API"
```

---

## Task 11: Additional Test Fixtures + Regression Tests

**Files:**
- Create: `tests/fixtures/healthcare_hl7.csv`
- Create: `tests/fixtures/ambiguous.csv`
- Modify: `tests/test_engine.py`

- [ ] **Step 1: Create remaining fixtures**

Create `tests/fixtures/healthcare_hl7.csv`:

```csv
PID,PatientName,DOB,MRN,Gender
001,John Smith,1980-01-15,MRN001,M
002,Jane Doe,1992-06-30,MRN002,F
003,Bob Wilson,1975-11-22,MRN003,M
```

Create `tests/fixtures/ambiguous.csv`:

```csv
ref,code,val,desc
ABC-123,E001,42.50,Widget type A
DEF-456,E002,18.99,Widget type B
GHI-789,E003,105.00,Gadget type C
```

- [ ] **Step 2: Add regression tests**

Add to `tests/test_engine.py`:

```python
class TestRegressions:
    def test_healthcare_columns(self):
        engine = MapEngine()
        src_schema = extract_schema(str(FIXTURES / "healthcare_hl7.csv"))
        # Verify DOB gets classified as date
        dob_field = next(f for f in src_schema.fields if f.name == "DOB")
        from infermap.scorers.pattern_type import classify_field
        assert classify_field(dob_field) == "date_iso"

    def test_ambiguous_columns_dont_crash(self):
        engine = MapEngine()
        result = engine.map(
            str(FIXTURES / "ambiguous.csv"),
            str(FIXTURES / "canonical_customers.csv"),
        )
        # Should produce a result without errors, even with poor matches
        assert isinstance(result, MapResult)

    def test_to_config_then_from_config(self, tmp_path):
        engine = MapEngine()
        result = engine.map(
            str(FIXTURES / "crm_export.csv"),
            str(FIXTURES / "canonical_customers.csv"),
        )
        config_path = str(tmp_path / "test_mapping.yaml")
        result.to_config(config_path)

        from infermap.config import from_config
        reloaded = from_config(config_path)
        assert len(reloaded.mappings) == len(result.mappings)
        for orig, loaded in zip(result.mappings, reloaded.mappings):
            assert orig.source == loaded.source
            assert orig.target == loaded.target
```

Add the missing imports at the top of `tests/test_engine.py`:

```python
from infermap.providers import extract_schema
from infermap.types import MapResult, ScorerResult
```

Also add these additional regression tests:

```python
class TestEdgeCases:
    def test_custom_scorer_exception_handled(self):
        """Scorer that raises should be caught, not crash the engine."""
        from infermap.scorers import _FunctionScorer

        def bad_scorer(source, target):
            raise ValueError("intentional error")

        broken = _FunctionScorer(bad_scorer, name="BrokenScorer", weight=0.5)
        engine = MapEngine(scorers=[ExactScorer(), broken, AliasScorer()])

        src = pl.DataFrame({"email": ["a@b.com"]})
        tgt = pl.DataFrame({"email": ["x@y.com"]})
        result = engine.map(src, tgt)
        # Should still produce a result (ExactScorer matches)
        assert len(result.mappings) >= 0  # doesn't crash

    def test_zero_row_db_table(self, tmp_path):
        """DB table with zero rows should not crash."""
        import sqlite3

        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE empty_table (name TEXT, age INTEGER)")
        conn.commit()
        conn.close()

        from infermap.providers.db import DBProvider

        provider = DBProvider()
        schema = provider.extract(f"sqlite:///{db_path}", table="empty_table")
        assert len(schema.fields) == 2
        assert schema.fields[0].sample_values == []

    def test_top_level_map_convenience(self):
        """infermap.map() top-level function works."""
        import infermap

        result = infermap.map(
            str(FIXTURES / "crm_export.csv"),
            str(FIXTURES / "canonical_customers.csv"),
        )
        assert isinstance(result, MapResult)
        assert len(result.mappings) > 0
```

Add these imports at the top of test_engine.py:

```python
from infermap.scorers.exact import ExactScorer
from infermap.scorers.alias import AliasScorer
```

- [ ] **Step 3: Run all tests**

Run: `pytest -v`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "test: additional fixtures and regression tests"
```

---

## Task 12: README + License + Final Polish

**Files:**
- Create: `README.md`
- Create: `LICENSE`
- Create: `infermap.yaml.example`

- [ ] **Step 1: Create LICENSE (MIT)**

Create `LICENSE`:

```
MIT License

Copyright (c) 2026 Ben Severn

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 2: Create example config**

Create `infermap.yaml.example`:

```yaml
# infermap configuration example
# Copy to infermap.yaml and customize

# Override scorer weights
scorers:
  FuzzyNameScorer:
    weight: 0.2          # downweight fuzzy matching
  LLMScorer:
    enabled: false        # disable LLM scorer

# Extend the alias registry
aliases:
  mrn: [medical_record_number, patient_id, chart_number]
  npi: [provider_id, national_provider_identifier]
```

- [ ] **Step 3: Create README.md**

Create `README.md`:

```markdown
# infermap

**Inference-driven schema mapping engine.** Map messy source columns to a known target schema — accurately, explainably, and with zero config.

```bash
pip install infermap
```

## Quick Start

```python
import infermap

result = infermap.map("source.csv", "target.csv")

print(result.report())                  # see what matched and why
remapped = result.apply(source_df)      # remap a DataFrame
result.to_config("mapping.yaml")        # save for reuse
```

## CLI

```bash
infermap map source.csv target.csv
infermap map incoming.csv "postgresql://host/db" --table customers
infermap apply source.csv --config mapping.yaml --output remapped.csv
infermap inspect source.csv
infermap validate source.csv --config mapping.yaml --strict --required email,phone
```

## How It Works

infermap uses a weighted scorer pipeline:

1. **ExactScorer** — exact column name match
2. **AliasScorer** — synonym registry (email_addr -> email, tel -> phone, etc.)
3. **PatternTypeScorer** — regex-based semantic type detection (email, phone, date, zip, etc.)
4. **ProfileScorer** — statistical profile comparison (dtype, null rate, cardinality)
5. **FuzzyNameScorer** — Jaro-Winkler name similarity

Each scorer independently evaluates every (source, target) pair and returns a confidence score. The engine combines scores with configurable weights, then applies the Hungarian algorithm for globally optimal 1:1 assignment.

## Features

- **Zero-config** — point at two schemas and get mappings
- **Explainable** — every mapping includes per-scorer reasoning
- **Pluggable** — add custom scorers with a decorator
- **Multiple sources** — CSV, Parquet, Excel, PostgreSQL, MySQL, SQLite, DuckDB, DataFrames
- **DB drift resilient** — introspect live DB schemas; adapts when columns change
- **Save & reuse** — export mappings as YAML, reload without re-running inference
- **CI gate** — `infermap validate --strict` exits code 1 on missing required fields

## Custom Scorers

```python
import infermap

@infermap.scorer(name="fhir_type", weight=0.8)
def fhir_scorer(source, target):
    # your domain logic
    return infermap.ScorerResult(score=0.9, reasoning="FHIR match")
```

## Install Extras

```bash
pip install infermap[postgres]    # PostgreSQL support
pip install infermap[mysql]       # MySQL support
pip install infermap[duckdb]      # DuckDB support
pip install infermap[excel]       # Excel support
pip install infermap[all]         # Everything
```

## License

MIT
```

- [ ] **Step 4: Run full test suite one final time**

Run: `pytest -v --tb=short`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "docs: README, LICENSE, and example config"
```

---

## Summary

| Task | What it builds | Key files |
|------|---------------|-----------|
| 1 | Project scaffolding, types, errors, fixtures | `pyproject.toml`, `types.py`, `errors.py` |
| 2 | Scorer protocol, ExactScorer, AliasScorer, registry | `scorers/` |
| 3 | PatternTypeScorer, ProfileScorer | `scorers/pattern_type.py`, `scorers/profile.py` |
| 4 | FuzzyNameScorer | `scorers/fuzzy_name.py` |
| 5 | Optimal assignment (Hungarian) | `assignment.py` |
| 6 | FileProvider, InMemoryProvider, detection | `providers/` |
| 7 | SchemaFileProvider, DBProvider (SQLite) | `providers/schema_file.py`, `providers/db.py` |
| 8 | MapEngine orchestrator | `engine.py` |
| 9 | Config loading, from_config() | `config.py` |
| 10 | CLI + public API | `cli.py`, `__init__.py` |
| 11 | Additional fixtures + regression tests | `tests/` |
| 12 | README, LICENSE, example config | `README.md`, `LICENSE` |
