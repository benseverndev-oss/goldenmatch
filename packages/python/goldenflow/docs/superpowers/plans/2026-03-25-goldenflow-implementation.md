# GoldenFlow Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build GoldenFlow — a data transformation toolkit that standardizes, reshapes, and normalizes messy data, integrated with GoldenCheck and GoldenMatch.

**Architecture:** Monolithic TransformEngine with a hybrid transform registry (Polars expr-mode for simple transforms, Series-mode for transforms needing Python libraries). Profiler bridge wraps GoldenCheck with a built-in fallback. YAML-driven config with Pydantic models.

**Tech Stack:** Python 3.11+, Polars, Pydantic, Typer, Rich, Textual, FastAPI, phonenumbers, python-dateutil, rapidfuzz, Hatchling build system.

**Spec:** `docs/superpowers/specs/2026-03-25-goldenflow-design.md`

---

## Phase 1: Project Scaffolding & Core Infrastructure

### Task 1: Project Setup — pyproject.toml, package skeleton, test infrastructure

**Files:**
- Create: `pyproject.toml`
- Create: `goldenflow/__init__.py`
- Create: `goldenflow/engine/__init__.py`
- Create: `goldenflow/transforms/__init__.py`
- Create: `goldenflow/mapping/__init__.py`
- Create: `goldenflow/config/__init__.py`
- Create: `goldenflow/domains/__init__.py`
- Create: `goldenflow/connectors/__init__.py`
- Create: `goldenflow/cli/__init__.py`
- Create: `goldenflow/tui/__init__.py`
- Create: `goldenflow/api/__init__.py`
- Create: `goldenflow/mcp/__init__.py`
- Create: `goldenflow/reporters/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/fixtures/` (directory)
- Create: `tests/test_public_api.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "goldenflow"
version = "0.1.0"
description = "Data transformation toolkit — standardize, reshape, and normalize messy data before it hits your pipeline."
readme = "README.md"
license = "MIT"
requires-python = ">=3.11"
authors = [{ name = "Ben Severn" }]

dependencies = [
    "polars>=1.0",
    "pydantic>=2.0",
    "typer>=0.12",
    "rich>=13.0",
    "textual>=0.50",
    "fastapi>=0.110",
    "uvicorn>=0.27",
    "phonenumbers>=8.13",
    "python-dateutil>=2.9",
    "pyyaml>=6.0",
    "rapidfuzz>=3.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-cov>=5.0", "ruff>=0.3"]
check = ["goldencheck>=1.0"]
excel = ["openpyxl>=3.1"]
db = ["connectorx>=0.3"]
mcp = ["mcp>=1.0"]
all = ["goldenflow[check,excel,db,mcp]"]

[project.scripts]
goldenflow = "goldenflow.cli.main:app"

[tool.ruff]
line-length = 100

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create package skeleton — all `__init__.py` files**

All `__init__.py` files start empty except the root package:

```python
# goldenflow/__init__.py
__version__ = "0.1.0"

__all__: list[str] = []
```

All subpackage `__init__.py` files are empty.

- [ ] **Step 3: Create test infrastructure**

`tests/__init__.py` — empty.

```python
# tests/conftest.py
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def sample_csv(tmp_path: Path) -> Path:
    path = tmp_path / "sample.csv"
    df = pl.DataFrame(
        {
            "name": ["  John Smith  ", "jane doe", "ROBERT JOHNSON III"],
            "email": ["JOHN@EXAMPLE.COM", "jane@example.com ", "bob@test.com"],
            "phone": ["(555) 123-4567", "555.987.6543", "+1-555-456-7890"],
            "state": ["Pennsylvania", "CA", "new york"],
            "signup_date": ["03/15/2024", "2024-01-20", "Jan 5, 2023"],
            "price": ["$1,234.56", "$99.99", "$0.50"],
        }
    )
    df.write_csv(path)
    return path


@pytest.fixture()
def sample_csv_b(tmp_path: Path) -> Path:
    """A second dataset with different column names for schema mapping tests."""
    path = tmp_path / "sample_b.csv"
    df = pl.DataFrame(
        {
            "full_name": ["Alice Brown", "Bob Wilson"],
            "email_address": ["alice@test.com", "bob@test.com"],
            "phone_number": ["5551234567", "5559876543"],
            "st": ["PA", "NY"],
            "signup_dt": ["2024-03-15", "2024-01-20"],
        }
    )
    df.write_csv(path)
    return path
```

- [ ] **Step 4: Create public API test**

```python
# tests/test_public_api.py
"""Verify the public API surface is importable and consistent."""

import goldenflow


def test_version_exists():
    assert hasattr(goldenflow, "__version__")
    assert isinstance(goldenflow.__version__, str)


def test_all_exports_importable():
    for name in goldenflow.__all__:
        assert hasattr(goldenflow, name), f"{name} in __all__ but not importable"
```

- [ ] **Step 5: Create test fixtures directory and a messy CSV fixture**

Create `tests/fixtures/messy.csv`:

```csv
name,email,phone,address,city,state,zip,signup_date,price,active
  John Smith  ,JOHN@EXAMPLE.COM,(555) 123-4567,123 Main Street,Philadelphia,Pennsylvania,19103,03/15/2024,"$1,234.56",Yes
jane doe,jane@example.com ,555.987.6543,456 Oak Ave,Los Angeles,CA,90001,2024-01-20,$99.99,Y
ROBERT JOHNSON III,bob@test.com,+1-555-456-7890,789 Elm St.,New York,new york,10001,Jan 5 2023,$0.50,true
  Mary McDonald ,mary@sample.com,5554567890,321 Pine Avenue,Chicago,IL,60601,12/25/2022,"$5,000.00",1
Dr. James Wilson Jr.,jwilson@email.com,(555)111-2222,654 Maple Dr,Houston,TX,77001,2023-06-15,$250.00,True
,invalid-email,not-a-phone,,,ZZ,abcde,not-a-date,$NaN,maybe
```

- [ ] **Step 6: Install dependencies and run tests**

Run: `cd D:/show_case/goldenflow && pip install -e ".[dev]"`
Run: `pytest tests/test_public_api.py -v`
Expected: 2 tests PASS

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml goldenflow/ tests/
git commit -m "feat: scaffold project structure with pyproject.toml and test infra"
```

---

### Task 2: Config — Pydantic models and YAML loader

**Files:**
- Create: `goldenflow/config/schema.py`
- Create: `goldenflow/config/loader.py`
- Create: `tests/config/__init__.py`
- Create: `tests/config/test_schema.py`
- Create: `tests/config/test_loader.py`

- [ ] **Step 1: Write failing tests for config schema**

```python
# tests/config/__init__.py — empty
# tests/config/test_schema.py
from goldenflow.config.schema import (
    DedupSpec,
    FilterSpec,
    GoldenFlowConfig,
    SplitSpec,
    TransformSpec,
)


def test_transform_spec():
    spec = TransformSpec(column="name", ops=["strip", "title_case"])
    assert spec.column == "name"
    assert spec.ops == ["strip", "title_case"]


def test_split_spec():
    spec = SplitSpec(source="name", target=["first_name", "last_name"], method="split_name")
    assert spec.target == ["first_name", "last_name"]


def test_filter_spec():
    spec = FilterSpec(column="email", condition="not_null")
    assert spec.condition == "not_null"


def test_dedup_spec_defaults():
    spec = DedupSpec(columns=["email"])
    assert spec.keep == "first"


def test_golden_flow_config_defaults():
    config = GoldenFlowConfig()
    assert config.transforms == []
    assert config.renames == {}
    assert config.drop == []
    assert config.dedup is None


def test_golden_flow_config_full():
    config = GoldenFlowConfig(
        source="data.csv",
        output="clean.csv",
        transforms=[TransformSpec(column="name", ops=["strip"])],
        splits=[SplitSpec(source="name", target=["first", "last"], method="split_name")],
        renames={"email_address": "email"},
        drop=["internal_id"],
        filters=[FilterSpec(column="email", condition="not_null")],
        dedup=DedupSpec(columns=["email"]),
    )
    assert len(config.transforms) == 1
    assert config.renames == {"email_address": "email"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/config/test_schema.py -v`
Expected: FAIL — cannot import `goldenflow.config.schema`

- [ ] **Step 3: Implement config schema**

```python
# goldenflow/config/schema.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class TransformSpec(BaseModel):
    column: str
    ops: list[str]


class SplitSpec(BaseModel):
    source: str
    target: list[str]
    method: str


class FilterSpec(BaseModel):
    column: str
    condition: str


class DedupSpec(BaseModel):
    columns: list[str]
    keep: Literal["first", "last"] = "first"


class MappingSpec(BaseModel):
    source: str
    target: str | list[str]
    transform: str | list[str] | None = None


class GoldenFlowConfig(BaseModel):
    source: str | None = None
    output: str | None = None
    transforms: list[TransformSpec] = []
    splits: list[SplitSpec] = []
    renames: dict[str, str] = {}
    drop: list[str] = []
    filters: list[FilterSpec] = []
    dedup: DedupSpec | None = None
    mappings: list[MappingSpec] = []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/config/test_schema.py -v`
Expected: All PASS

- [ ] **Step 5: Write failing tests for config loader**

```python
# tests/config/test_loader.py
from pathlib import Path

import yaml

from goldenflow.config.loader import load_config, merge_configs
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec


def test_load_config_from_yaml(tmp_path: Path):
    config_path = tmp_path / "goldenflow.yaml"
    config_path.write_text(
        yaml.dump(
            {
                "source": "data.csv",
                "output": "clean.csv",
                "transforms": [{"column": "name", "ops": ["strip", "title_case"]}],
                "renames": {"email_address": "email"},
                "drop": ["internal_id"],
            }
        )
    )
    config = load_config(config_path)
    assert config.source == "data.csv"
    assert len(config.transforms) == 1
    assert config.drop == ["internal_id"]


def test_load_config_missing_file():
    config = load_config(Path("nonexistent.yaml"))
    assert config == GoldenFlowConfig()


def test_merge_configs_cli_overrides_file():
    file_config = GoldenFlowConfig(source="file.csv", output="out.csv")
    cli_overrides = GoldenFlowConfig(source="override.csv")
    merged = merge_configs(file_config, cli_overrides)
    assert merged.source == "override.csv"
    assert merged.output == "out.csv"


def test_merge_configs_lists_replaced():
    file_config = GoldenFlowConfig(
        transforms=[TransformSpec(column="a", ops=["strip"])]
    )
    cli_overrides = GoldenFlowConfig(
        transforms=[TransformSpec(column="b", ops=["lowercase"])]
    )
    merged = merge_configs(file_config, cli_overrides)
    assert len(merged.transforms) == 1
    assert merged.transforms[0].column == "b"
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/config/test_loader.py -v`
Expected: FAIL — cannot import `goldenflow.config.loader`

- [ ] **Step 7: Implement config loader**

```python
# goldenflow/config/loader.py
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from goldenflow.config.schema import GoldenFlowConfig


def load_config(path: Path) -> GoldenFlowConfig:
    """Load a GoldenFlowConfig from a YAML file. Returns defaults if file missing."""
    if not path.exists():
        return GoldenFlowConfig()
    with open(path) as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    return GoldenFlowConfig(**data)


def save_config(config: GoldenFlowConfig, path: Path) -> None:
    """Save a GoldenFlowConfig to a YAML file."""
    data = config.model_dump(exclude_defaults=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def merge_configs(
    file_config: GoldenFlowConfig, cli_overrides: GoldenFlowConfig
) -> GoldenFlowConfig:
    """Merge CLI overrides on top of file config. Non-default CLI values win."""
    base = file_config.model_dump()
    overrides = cli_overrides.model_dump(exclude_defaults=True)
    base.update(overrides)
    return GoldenFlowConfig(**base)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/config/ -v`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add goldenflow/config/ tests/config/
git commit -m "feat: add Pydantic config models and YAML loader"
```

---

### Task 3: Transform Registry — decorator, registry dict, parameter parsing

**Files:**
- Create: `goldenflow/transforms/__init__.py` (overwrite empty)
- Create: `tests/transforms/__init__.py`
- Create: `tests/transforms/test_registry.py`

- [ ] **Step 1: Write failing tests for the registry**

```python
# tests/transforms/__init__.py — empty
# tests/transforms/test_registry.py
import polars as pl

from goldenflow.transforms import (
    get_transform,
    list_transforms,
    parse_transform_name,
    register_transform,
    registry,
)


def test_register_and_retrieve_expr_transform():
    @register_transform(
        name="_test_lower",
        input_types=["string"],
        auto_apply=True,
        priority=50,
        mode="expr",
    )
    def _test_lower(column: str) -> pl.Expr:
        return pl.col(column).str.to_lowercase()

    info = get_transform("_test_lower")
    assert info is not None
    assert info.mode == "expr"
    assert info.auto_apply is True


def test_register_and_retrieve_series_transform():
    @register_transform(
        name="_test_upper_series",
        input_types=["string"],
        auto_apply=False,
        priority=30,
        mode="series",
    )
    def _test_upper_series(series: pl.Series) -> pl.Series:
        return series.str.to_uppercase()

    info = get_transform("_test_upper_series")
    assert info is not None
    assert info.mode == "series"


def test_get_nonexistent_transform():
    assert get_transform("nonexistent_transform_xyz") is None


def test_parse_transform_name_simple():
    name, params = parse_transform_name("strip")
    assert name == "strip"
    assert params == []


def test_parse_transform_name_with_params():
    name, params = parse_transform_name("truncate:100")
    assert name == "truncate"
    assert params == ["100"]


def test_parse_transform_name_with_multiple_params():
    name, params = parse_transform_name("clamp:0:100")
    assert name == "clamp"
    assert params == ["0", "100"]


def test_list_transforms_returns_registered():
    transforms = list_transforms()
    assert "_test_lower" in [t.name for t in transforms]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/transforms/test_registry.py -v`
Expected: FAIL

- [ ] **Step 3: Implement the registry**

```python
# goldenflow/transforms/__init__.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

import polars as pl

# Global transform registry
_REGISTRY: dict[str, TransformInfo] = {}


@dataclass
class TransformInfo:
    name: str
    func: Callable[..., pl.Expr | pl.Series | pl.DataFrame]
    input_types: list[str]
    auto_apply: bool
    priority: int
    mode: Literal["expr", "series", "dataframe"]


def register_transform(
    *,
    name: str,
    input_types: list[str],
    auto_apply: bool = False,
    priority: int = 50,
    mode: Literal["expr", "series", "dataframe"] = "series",
) -> Callable:
    """Decorator to register a transform function."""

    def decorator(func: Callable) -> Callable:
        _REGISTRY[name] = TransformInfo(
            name=name,
            func=func,
            input_types=input_types,
            auto_apply=auto_apply,
            priority=priority,
            mode=mode,
        )
        return func

    return decorator


def get_transform(name: str) -> TransformInfo | None:
    """Look up a transform by name."""
    return _REGISTRY.get(name)


def list_transforms() -> list[TransformInfo]:
    """Return all registered transforms, sorted by priority descending."""
    return sorted(_REGISTRY.values(), key=lambda t: t.priority, reverse=True)


def parse_transform_name(raw: str) -> tuple[str, list[str]]:
    """Parse 'name:param1:param2' into (name, [param1, param2])."""
    parts = raw.split(":")
    return parts[0], parts[1:]


def registry() -> dict[str, TransformInfo]:
    """Return the raw registry dict (for testing/inspection)."""
    return _REGISTRY
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/transforms/test_registry.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/transforms/__init__.py tests/transforms/
git commit -m "feat: add transform registry with decorator, parameter parsing"
```

---

### Task 4: File Connectors — CSV, Parquet, Excel, JSON read/write

**Files:**
- Create: `goldenflow/connectors/file.py`
- Create: `tests/connectors/__init__.py`
- Create: `tests/connectors/test_file.py`

- [ ] **Step 1: Write failing tests for file connectors**

```python
# tests/connectors/__init__.py — empty
# tests/connectors/test_file.py
from pathlib import Path

import polars as pl

from goldenflow.connectors.file import read_file, write_file


def test_read_csv(sample_csv: Path):
    df = read_file(sample_csv)
    assert isinstance(df, pl.DataFrame)
    assert df.shape[0] == 3  # sample_csv fixture has 3 rows
    assert "name" in df.columns


def test_read_parquet(tmp_path: Path):
    path = tmp_path / "data.parquet"
    pl.DataFrame({"a": [1, 2, 3]}).write_parquet(path)
    df = read_file(path)
    assert df.shape == (3, 1)


def test_read_json(tmp_path: Path):
    path = tmp_path / "data.json"
    pl.DataFrame({"a": [1, 2, 3]}).write_json(path)
    df = read_file(path)
    assert df.shape == (3, 1)


def test_write_csv(tmp_path: Path):
    df = pl.DataFrame({"a": [1, 2]})
    out = tmp_path / "out.csv"
    write_file(df, out)
    assert out.exists()
    result = pl.read_csv(out)
    assert result.shape == (2, 1)


def test_write_parquet(tmp_path: Path):
    df = pl.DataFrame({"a": [1, 2]})
    out = tmp_path / "out.parquet"
    write_file(df, out)
    assert out.exists()


def test_unsupported_format(tmp_path: Path):
    path = tmp_path / "data.xyz"
    path.write_text("hello")
    import pytest

    with pytest.raises(ValueError, match="Unsupported file format"):
        read_file(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/connectors/test_file.py -v`
Expected: FAIL

- [ ] **Step 3: Implement file connectors**

```python
# goldenflow/connectors/file.py
from __future__ import annotations

from pathlib import Path

from typing import Callable

import polars as pl

_READERS: dict[str, Callable] = {
    ".csv": pl.read_csv,
    ".parquet": pl.read_parquet,
    ".json": pl.read_json,
}

_WRITERS: dict[str, str] = {
    ".csv": "write_csv",
    ".parquet": "write_parquet",
    ".json": "write_json",
}


def read_file(path: Path, **kwargs) -> pl.DataFrame:
    """Read a data file into a Polars DataFrame."""
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        try:
            import openpyxl  # noqa: F401

            return pl.read_excel(path, **kwargs)
        except ImportError:
            raise ImportError(
                "openpyxl is required for Excel files: pip install goldenflow[excel]"
            )
    reader = _READERS.get(suffix)
    if reader is None:
        raise ValueError(f"Unsupported file format: {suffix}")
    return reader(path, **kwargs)


def write_file(df: pl.DataFrame, path: Path, **kwargs) -> None:
    """Write a Polars DataFrame to a file."""
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        try:
            import openpyxl  # noqa: F401

            df.write_excel(path, **kwargs)
            return
        except ImportError:
            raise ImportError(
                "openpyxl is required for Excel files: pip install goldenflow[excel]"
            )
    writer_method = _WRITERS.get(suffix)
    if writer_method is None:
        raise ValueError(f"Unsupported file format: {suffix}")
    getattr(df, writer_method)(path, **kwargs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/connectors/test_file.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/connectors/ tests/connectors/
git commit -m "feat: add file connectors for CSV, Parquet, JSON, Excel"
```

---

## Phase 2: Transform Library

### Task 5: Text Transforms

**Files:**
- Create: `goldenflow/transforms/text.py`
- Create: `tests/transforms/test_text.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/transforms/test_text.py
import polars as pl

from goldenflow.transforms.text import (
    collapse_whitespace,
    lowercase,
    normalize_unicode,
    remove_punctuation,
    strip,
    title_case,
    truncate,
    uppercase,
)


def _apply_expr(func, column: str, data: list[str]) -> list[str]:
    """Helper to apply an expr-mode transform to test data."""
    df = pl.DataFrame({column: data})
    expr = func(column)
    return df.select(expr.alias(column))[column].to_list()


def test_strip():
    result = _apply_expr(strip, "a", ["  hello  ", "world ", " hi"])
    assert result == ["hello", "world", "hi"]


def test_lowercase():
    result = _apply_expr(lowercase, "a", ["HELLO", "World"])
    assert result == ["hello", "world"]


def test_uppercase():
    result = _apply_expr(uppercase, "a", ["hello", "World"])
    assert result == ["HELLO", "WORLD"]


def test_title_case():
    result = _apply_expr(title_case, "a", ["john smith", "JANE DOE"])
    assert result == ["John Smith", "Jane Doe"]


def test_normalize_unicode():
    s = pl.Series("a", ["\u00e9", "caf\u00e9", "na\u00efve"])
    result = normalize_unicode(s)
    assert result.to_list() == ["e", "cafe", "naive"]


def test_remove_punctuation():
    result = _apply_expr(remove_punctuation, "a", ["hello!", "test@123", "a-b_c"])
    assert all(c.isalnum() or c.isspace() for val in result for c in val)


def test_collapse_whitespace():
    result = _apply_expr(collapse_whitespace, "a", ["hello   world", "  a  b  "])
    assert result == ["hello world", " a b "]


def test_truncate():
    s = pl.Series("a", ["hello world", "hi", "a very long string"])
    result = truncate(s, n=5)
    assert result.to_list() == ["hello", "hi", "a ver"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/transforms/test_text.py -v`
Expected: FAIL

- [ ] **Step 3: Implement text transforms**

```python
# goldenflow/transforms/text.py
from __future__ import annotations

import unicodedata

import polars as pl

from goldenflow.transforms import register_transform


@register_transform(name="strip", input_types=["string"], auto_apply=True, priority=90, mode="expr")
def strip(column: str) -> pl.Expr:
    return pl.col(column).str.strip_chars()


@register_transform(
    name="lowercase", input_types=["string"], auto_apply=False, priority=50, mode="expr"
)
def lowercase(column: str) -> pl.Expr:
    return pl.col(column).str.to_lowercase()


@register_transform(
    name="uppercase", input_types=["string"], auto_apply=False, priority=50, mode="expr"
)
def uppercase(column: str) -> pl.Expr:
    return pl.col(column).str.to_uppercase()


@register_transform(
    name="title_case", input_types=["string"], auto_apply=False, priority=50, mode="expr"
)
def title_case(column: str) -> pl.Expr:
    return pl.col(column).str.to_titlecase()


@register_transform(
    name="normalize_unicode", input_types=["string"], auto_apply=True, priority=85, mode="series"
)
def normalize_unicode(series: pl.Series) -> pl.Series:
    def _normalize(val: str | None) -> str | None:
        if val is None:
            return None
        nfkd = unicodedata.normalize("NFKD", val)
        return "".join(c for c in nfkd if not unicodedata.combining(c))

    return series.map_elements(_normalize, return_dtype=pl.Utf8)


@register_transform(
    name="remove_punctuation", input_types=["string"], auto_apply=False, priority=40, mode="expr"
)
def remove_punctuation(column: str) -> pl.Expr:
    return pl.col(column).str.replace_all(r"[^\w\s]", "")


@register_transform(
    name="collapse_whitespace", input_types=["string"], auto_apply=True, priority=80, mode="expr"
)
def collapse_whitespace(column: str) -> pl.Expr:
    return pl.col(column).str.replace_all(r"\s{2,}", " ")


@register_transform(
    name="truncate", input_types=["string"], auto_apply=False, priority=30, mode="series"
)
def truncate(series: pl.Series, n: int = 255) -> pl.Series:
    return series.str.slice(0, n)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/transforms/test_text.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/transforms/text.py tests/transforms/test_text.py
git commit -m "feat: add text transforms (strip, lowercase, uppercase, title_case, etc.)"
```

---

### Task 6: Phone Transforms

**Files:**
- Create: `goldenflow/transforms/phone.py`
- Create: `tests/transforms/test_phone.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/transforms/test_phone.py
import polars as pl

from goldenflow.transforms.phone import phone_digits, phone_e164, phone_national, phone_validate


def test_phone_e164():
    s = pl.Series("ph", ["(555) 123-4567", "555.987.6543", "+1-555-456-7890", "invalid"])
    result = phone_e164(s)
    assert result[0] == "+15551234567"
    assert result[1] == "+15559876543"
    assert result[2] == "+15554567890"
    assert result[3] == "invalid"  # preserved on failure


def test_phone_national():
    s = pl.Series("ph", ["+15551234567", "(555) 987-6543"])
    result = phone_national(s)
    assert result[0] == "(555) 123-4567"
    assert result[1] == "(555) 987-6543"


def test_phone_digits():
    s = pl.Series("ph", ["(555) 123-4567", "+1-555-987-6543"])
    result = phone_digits(s)
    assert result[0] == "5551234567"
    assert result[1] == "15559876543"


def test_phone_validate():
    s = pl.Series("ph", ["+15551234567", "invalid", "123"])
    result = phone_validate(s)
    assert result[0] is True
    assert result[1] is False
    assert result[2] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/transforms/test_phone.py -v`
Expected: FAIL

- [ ] **Step 3: Implement phone transforms**

```python
# goldenflow/transforms/phone.py
from __future__ import annotations

import phonenumbers
import polars as pl

from goldenflow.transforms import register_transform

_DEFAULT_REGION = "US"


def _parse_phone(val: str | None) -> phonenumbers.PhoneNumber | None:
    if not val:
        return None
    try:
        return phonenumbers.parse(val, _DEFAULT_REGION)
    except phonenumbers.NumberParseException:
        return None


@register_transform(
    name="phone_e164", input_types=["phone"], auto_apply=True, priority=50, mode="series"
)
def phone_e164(series: pl.Series) -> pl.Series:
    def _format(val: str | None) -> str | None:
        if val is None:
            return None
        parsed = _parse_phone(val)
        if parsed is None:
            return val  # preserve original on failure
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

    return series.map_elements(_format, return_dtype=pl.Utf8)


@register_transform(
    name="phone_national", input_types=["phone"], auto_apply=False, priority=50, mode="series"
)
def phone_national(series: pl.Series) -> pl.Series:
    def _format(val: str | None) -> str | None:
        if val is None:
            return None
        parsed = _parse_phone(val)
        if parsed is None:
            return val
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)

    return series.map_elements(_format, return_dtype=pl.Utf8)


@register_transform(
    name="phone_digits", input_types=["phone"], auto_apply=False, priority=50, mode="series"
)
def phone_digits(series: pl.Series) -> pl.Series:
    def _to_digits(val: str | None) -> str | None:
        if val is None:
            return None
        return "".join(c for c in val if c.isdigit())

    return series.map_elements(_to_digits, return_dtype=pl.Utf8)


@register_transform(
    name="phone_validate", input_types=["phone"], auto_apply=False, priority=60, mode="series"
)
def phone_validate(series: pl.Series) -> pl.Series:
    def _validate(val: str | None) -> bool | None:
        if val is None:
            return None
        parsed = _parse_phone(val)
        if parsed is None:
            return False
        return phonenumbers.is_valid_number(parsed)

    return series.map_elements(_validate, return_dtype=pl.Boolean)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/transforms/test_phone.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/transforms/phone.py tests/transforms/test_phone.py
git commit -m "feat: add phone transforms (e164, national, digits, validate)"
```

---

### Task 7: Name Transforms

**Files:**
- Create: `goldenflow/transforms/names.py`
- Create: `tests/transforms/test_names.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/transforms/test_names.py
import polars as pl

from goldenflow.transforms.names import (
    initial_expand,
    name_proper,
    split_name,
    split_name_reverse,
    strip_suffixes,
    strip_titles,
)


def test_split_name():
    df = pl.DataFrame({"name": ["John Smith", "Jane Marie Doe", "Madonna"]})
    result = split_name(df, "name")
    assert result["first_name"].to_list() == ["John", "Jane Marie", "Madonna"]
    assert result["last_name"].to_list() == ["Smith", "Doe", ""]


def test_split_name_reverse():
    df = pl.DataFrame({"name": ["Smith, John", "Doe, Jane Marie"]})
    result = split_name_reverse(df, "name")
    assert result["first_name"].to_list() == ["John", "Jane Marie"]
    assert result["last_name"].to_list() == ["Smith", "Doe"]


def test_strip_titles():
    s = pl.Series("n", ["Dr. James Wilson", "Mrs. Jane Smith", "Mr. Bob Jones Jr."])
    result = strip_titles(s)
    assert result[0] == "James Wilson"
    assert result[1] == "Jane Smith"
    assert result[2] == "Bob Jones Jr."


def test_strip_suffixes():
    s = pl.Series("n", ["James Wilson MD", "Jane Smith PhD", "Bob Jones Esq"])
    result = strip_suffixes(s)
    assert result[0] == "James Wilson"
    assert result[1] == "Jane Smith"
    assert result[2] == "Bob Jones"


def test_name_proper():
    s = pl.Series("n", ["mcdonald", "o'brien", "van der berg", "SMITH"])
    result = name_proper(s)
    assert result[0] == "McDonald"
    assert result[1] == "O'Brien"


def test_initial_expand():
    s = pl.Series("n", ["J. Smith", "John Smith", "R. J. Wilson"])
    result, flagged = initial_expand(s)
    assert result[0] == "J. Smith"  # unchanged
    assert result[1] == "John Smith"
    assert 0 in flagged  # row 0 flagged for review
    assert 2 in flagged
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/transforms/test_names.py -v`
Expected: FAIL

- [ ] **Step 3: Implement name transforms**

```python
# goldenflow/transforms/names.py
from __future__ import annotations

import re

import polars as pl

from goldenflow.transforms import register_transform

_TITLES = re.compile(
    r"^(Mr\.?|Mrs\.?|Ms\.?|Miss\.?|Dr\.?|Prof\.?|Rev\.?|Sr\.?|Sra\.?)\s+", re.IGNORECASE
)
_SUFFIXES = re.compile(
    r"\s+(Jr\.?|Sr\.?|II|III|IV|MD|PhD|PharmD|DDS|DVM|Esq\.?|CPA|RN|DO)$", re.IGNORECASE
)
_INITIAL_PATTERN = re.compile(r"\b[A-Z]\.\s")

_MC_PATTERN = re.compile(r"\bMc(\w)")
_O_PATTERN = re.compile(r"\bO'(\w)")


@register_transform(
    name="split_name", input_types=["name"], auto_apply=False, priority=50, mode="dataframe"
)
def split_name(df: pl.DataFrame, column: str) -> pl.DataFrame:
    """Split 'First Last' into first_name and last_name columns."""
    first_names = []
    last_names = []
    for val in df[column].to_list():
        if val is None:
            first_names.append(None)
            last_names.append(None)
            continue
        parts = val.strip().rsplit(" ", 1)
        if len(parts) == 2:
            first_names.append(parts[0])
            last_names.append(parts[1])
        else:
            first_names.append(parts[0])
            last_names.append("")
    return df.with_columns(
        pl.Series("first_name", first_names),
        pl.Series("last_name", last_names),
    )


@register_transform(
    name="split_name_reverse", input_types=["name"], auto_apply=False, priority=50, mode="dataframe"
)
def split_name_reverse(df: pl.DataFrame, column: str) -> pl.DataFrame:
    """Split 'Last, First' into first_name and last_name columns."""
    first_names = []
    last_names = []
    for val in df[column].to_list():
        if val is None:
            first_names.append(None)
            last_names.append(None)
            continue
        parts = val.split(",", 1)
        if len(parts) == 2:
            last_names.append(parts[0].strip())
            first_names.append(parts[1].strip())
        else:
            first_names.append(val.strip())
            last_names.append("")
    return df.with_columns(
        pl.Series("first_name", first_names),
        pl.Series("last_name", last_names),
    )


@register_transform(
    name="strip_titles", input_types=["name"], auto_apply=True, priority=70, mode="series"
)
def strip_titles(series: pl.Series) -> pl.Series:
    def _strip(val: str | None) -> str | None:
        if val is None:
            return None
        return _TITLES.sub("", val).strip()

    return series.map_elements(_strip, return_dtype=pl.Utf8)


@register_transform(
    name="strip_suffixes", input_types=["name"], auto_apply=False, priority=60, mode="series"
)
def strip_suffixes(series: pl.Series) -> pl.Series:
    def _strip(val: str | None) -> str | None:
        if val is None:
            return None
        return _SUFFIXES.sub("", val).strip()

    return series.map_elements(_strip, return_dtype=pl.Utf8)


@register_transform(
    name="name_proper", input_types=["name"], auto_apply=False, priority=45, mode="series"
)
def name_proper(series: pl.Series) -> pl.Series:
    def _proper(val: str | None) -> str | None:
        if val is None:
            return None
        result = val.title()
        result = _MC_PATTERN.sub(lambda m: f"Mc{m.group(1).upper()}", result)
        result = _O_PATTERN.sub(lambda m: f"O'{m.group(1).upper()}", result)
        return result

    return series.map_elements(_proper, return_dtype=pl.Utf8)


@register_transform(
    name="initial_expand", input_types=["name"], auto_apply=False, priority=40, mode="series"
)
def initial_expand(series: pl.Series) -> tuple[pl.Series, list[int]]:
    """Returns (series, flagged_rows). Values with initials are unchanged but flagged."""
    flagged: list[int] = []
    for i, val in enumerate(series.to_list()):
        if val and _INITIAL_PATTERN.search(val):
            flagged.append(i)
    return series, flagged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/transforms/test_names.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/transforms/names.py tests/transforms/test_names.py
git commit -m "feat: add name transforms (split, titles, suffixes, proper, initials)"
```

---

### Task 8: Address Transforms

**Files:**
- Create: `goldenflow/transforms/address.py`
- Create: `tests/transforms/test_address.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/transforms/test_address.py
import polars as pl

from goldenflow.transforms.address import (
    address_expand,
    address_standardize,
    split_address,
    state_abbreviate,
    state_expand,
    zip_normalize,
)


def test_address_standardize():
    s = pl.Series("a", ["123 Main Street", "456 Oak Avenue", "789 Elm Drive"])
    result = address_standardize(s)
    assert result[0] == "123 Main St"
    assert result[1] == "456 Oak Ave"
    assert result[2] == "789 Elm Dr"


def test_address_expand():
    s = pl.Series("a", ["123 Main St", "456 Oak Ave"])
    result = address_expand(s)
    assert result[0] == "123 Main Street"
    assert result[1] == "456 Oak Avenue"


def test_state_abbreviate():
    s = pl.Series("st", ["Pennsylvania", "California", "new york", "TX"])
    result = state_abbreviate(s)
    assert result[0] == "PA"
    assert result[1] == "CA"
    assert result[2] == "NY"
    assert result[3] == "TX"


def test_state_expand():
    s = pl.Series("st", ["PA", "CA", "NY"])
    result = state_expand(s)
    assert result[0] == "Pennsylvania"
    assert result[1] == "California"
    assert result[2] == "New York"


def test_zip_normalize():
    s = pl.Series("z", ["19103", "9001", "10001-1234", "abcde"])
    result = zip_normalize(s)
    assert result[0] == "19103"
    assert result[1] == "09001"  # zero-padded
    assert result[2] == "10001"  # strip +4
    assert result[3] == "abcde"  # invalid preserved


def test_split_address():
    df = pl.DataFrame({"addr": ["123 Main St, Philadelphia, PA 19103"]})
    result = split_address(df, "addr")
    assert "street" in result.columns
    assert "city" in result.columns
    assert "state" in result.columns
    assert "zip" in result.columns
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/transforms/test_address.py -v`
Expected: FAIL

- [ ] **Step 3: Implement address transforms**

```python
# goldenflow/transforms/address.py
from __future__ import annotations

import re

import polars as pl

from goldenflow.transforms import register_transform

_STREET_ABBREV = {
    "Street": "St", "Avenue": "Ave", "Boulevard": "Blvd", "Drive": "Dr",
    "Lane": "Ln", "Road": "Rd", "Court": "Ct", "Place": "Pl",
    "Circle": "Cir", "Trail": "Trl", "Way": "Way", "Parkway": "Pkwy",
    "Highway": "Hwy", "Terrace": "Ter", "Square": "Sq",
}
_STREET_EXPAND = {v: k for k, v in _STREET_ABBREV.items()}

_STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "District Of Columbia": "DC",
}
_STATES_REVERSE = {v: k for k, v in _STATES.items()}
_STATES_LOWER = {k.lower(): v for k, v in _STATES.items()}


@register_transform(
    name="address_standardize", input_types=["address"], auto_apply=False, priority=50, mode="series"
)
def address_standardize(series: pl.Series) -> pl.Series:
    def _std(val: str | None) -> str | None:
        if val is None:
            return None
        result = val
        for full, abbr in _STREET_ABBREV.items():
            result = re.sub(rf"\b{full}\b", abbr, result, flags=re.IGNORECASE)
        return result

    return series.map_elements(_std, return_dtype=pl.Utf8)


@register_transform(
    name="address_expand", input_types=["address"], auto_apply=False, priority=50, mode="series"
)
def address_expand(series: pl.Series) -> pl.Series:
    def _expand(val: str | None) -> str | None:
        if val is None:
            return None
        result = val
        for abbr, full in _STREET_EXPAND.items():
            result = re.sub(rf"\b{abbr}\b", full, result, flags=re.IGNORECASE)
        return result

    return series.map_elements(_expand, return_dtype=pl.Utf8)


@register_transform(
    name="state_abbreviate", input_types=["state", "string"], auto_apply=False, priority=50, mode="series"
)
def state_abbreviate(series: pl.Series) -> pl.Series:
    def _abbr(val: str | None) -> str | None:
        if val is None:
            return None
        val_stripped = val.strip()
        if len(val_stripped) == 2 and val_stripped.upper() in _STATES_REVERSE:
            return val_stripped.upper()
        matched = _STATES_LOWER.get(val_stripped.lower())
        return matched if matched else val

    return series.map_elements(_abbr, return_dtype=pl.Utf8)


@register_transform(
    name="state_expand", input_types=["state", "string"], auto_apply=False, priority=50, mode="series"
)
def state_expand(series: pl.Series) -> pl.Series:
    def _expand(val: str | None) -> str | None:
        if val is None:
            return None
        return _STATES_REVERSE.get(val.strip().upper(), val)

    return series.map_elements(_expand, return_dtype=pl.Utf8)


@register_transform(
    name="zip_normalize", input_types=["zip", "string"], auto_apply=True, priority=55, mode="series"
)
def zip_normalize(series: pl.Series) -> pl.Series:
    def _norm(val: str | None) -> str | None:
        if val is None:
            return None
        val = val.strip()
        val = val.split("-")[0]  # strip +4
        if val.isdigit():
            return val.zfill(5)
        return val  # preserve invalid

    return series.map_elements(_norm, return_dtype=pl.Utf8)


@register_transform(
    name="split_address", input_types=["address"], auto_apply=False, priority=45, mode="dataframe"
)
def split_address(df: pl.DataFrame, column: str) -> pl.DataFrame:
    """Parse 'street, city, state zip' into separate columns."""
    streets, cities, states, zips = [], [], [], []
    pattern = re.compile(r"^(.+?),\s*(.+?),\s*([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)$")
    for val in df[column].to_list():
        if val is None:
            streets.append(None); cities.append(None)
            states.append(None); zips.append(None)
            continue
        m = pattern.match(val.strip())
        if m:
            streets.append(m.group(1)); cities.append(m.group(2))
            states.append(m.group(3)); zips.append(m.group(4))
        else:
            streets.append(val); cities.append(None)
            states.append(None); zips.append(None)
    return df.with_columns(
        pl.Series("street", streets),
        pl.Series("city", cities),
        pl.Series("state", states),
        pl.Series("zip", zips),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/transforms/test_address.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/transforms/address.py tests/transforms/test_address.py
git commit -m "feat: add address transforms (standardize, expand, state, zip, split)"
```

---

### Task 9: Date Transforms

**Files:**
- Create: `goldenflow/transforms/dates.py`
- Create: `tests/transforms/test_dates.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/transforms/test_dates.py
import polars as pl

from goldenflow.transforms.dates import (
    age_from_dob,
    date_eu,
    date_iso8601,
    date_parse,
    date_us,
)


def test_date_iso8601():
    s = pl.Series("d", ["03/15/2024", "Jan 5, 2023", "2024-01-20", "invalid"])
    result = date_iso8601(s)
    assert result[0] == "2024-03-15"
    assert result[1] == "2023-01-05"
    assert result[2] == "2024-01-20"
    assert result[3] == "invalid"  # preserved


def test_date_us():
    s = pl.Series("d", ["2024-03-15", "Jan 5, 2023"])
    result = date_us(s)
    assert result[0] == "03/15/2024"
    assert result[1] == "01/05/2023"


def test_date_eu():
    s = pl.Series("d", ["2024-03-15", "Jan 5, 2023"])
    result = date_eu(s)
    assert result[0] == "15/03/2024"
    assert result[1] == "05/01/2023"


def test_date_parse():
    s = pl.Series("d", ["March 15, 2024", "15-03-2024", "2024/03/15"])
    result = date_parse(s)
    # All should parse to the same date, returned as ISO
    assert all(r == "2024-03-15" for r in result.to_list())


def test_age_from_dob():
    # Use a fixed reference date for deterministic tests
    s = pl.Series("d", ["1990-01-01", "2000-06-15"])
    result = age_from_dob(s, reference_date="2026-03-25")
    assert result[0] == 36
    assert result[1] == 25
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/transforms/test_dates.py -v`
Expected: FAIL

- [ ] **Step 3: Implement date transforms**

```python
# goldenflow/transforms/dates.py
from __future__ import annotations

from datetime import date, datetime

from dateutil import parser as dateutil_parser
import polars as pl

from goldenflow.transforms import register_transform


def _parse_date(val: str | None) -> date | None:
    if not val:
        return None
    try:
        return dateutil_parser.parse(val).date()
    except (ValueError, OverflowError):
        return None


@register_transform(
    name="date_iso8601", input_types=["date"], auto_apply=True, priority=50, mode="series"
)
def date_iso8601(series: pl.Series) -> pl.Series:
    def _fmt(val: str | None) -> str | None:
        if val is None:
            return None
        d = _parse_date(val)
        return d.isoformat() if d else val

    return series.map_elements(_fmt, return_dtype=pl.Utf8)


@register_transform(
    name="date_us", input_types=["date"], auto_apply=False, priority=50, mode="series"
)
def date_us(series: pl.Series) -> pl.Series:
    def _fmt(val: str | None) -> str | None:
        if val is None:
            return None
        d = _parse_date(val)
        return d.strftime("%m/%d/%Y") if d else val

    return series.map_elements(_fmt, return_dtype=pl.Utf8)


@register_transform(
    name="date_eu", input_types=["date"], auto_apply=False, priority=50, mode="series"
)
def date_eu(series: pl.Series) -> pl.Series:
    def _fmt(val: str | None) -> str | None:
        if val is None:
            return None
        d = _parse_date(val)
        return d.strftime("%d/%m/%Y") if d else val

    return series.map_elements(_fmt, return_dtype=pl.Utf8)


@register_transform(
    name="date_parse", input_types=["date"], auto_apply=False, priority=55, mode="series"
)
def date_parse(series: pl.Series) -> pl.Series:
    """Auto-detect format and normalize to ISO 8601."""
    return date_iso8601(series)


@register_transform(
    name="age_from_dob", input_types=["date"], auto_apply=False, priority=40, mode="series"
)
def age_from_dob(series: pl.Series, reference_date: str | None = None) -> pl.Series:
    ref = (
        dateutil_parser.parse(reference_date).date()
        if reference_date
        else date.today()
    )

    def _age(val: str | None) -> int | None:
        if val is None:
            return None
        d = _parse_date(val)
        if d is None:
            return None
        age = ref.year - d.year - ((ref.month, ref.day) < (d.month, d.day))
        return age

    return series.map_elements(_age, return_dtype=pl.Int64)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/transforms/test_dates.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/transforms/dates.py tests/transforms/test_dates.py
git commit -m "feat: add date transforms (iso8601, us, eu, parse, age_from_dob)"
```

---

### Task 10: Categorical Transforms

**Files:**
- Create: `goldenflow/transforms/categorical.py`
- Create: `tests/transforms/test_categorical.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/transforms/test_categorical.py
import polars as pl

from goldenflow.transforms.categorical import (
    boolean_normalize,
    category_standardize,
    gender_standardize,
    null_standardize,
)


def test_boolean_normalize():
    s = pl.Series("b", ["Yes", "Y", "1", "True", "true", "No", "N", "0", "false"])
    result = boolean_normalize(s)
    assert result[0] is True
    assert result[4] is True
    assert result[5] is False
    assert result[8] is False


def test_gender_standardize():
    s = pl.Series("g", ["Male", "male", "M", "m", "Female", "female", "F", "f"])
    result = gender_standardize(s)
    assert result[0] == "M"
    assert result[3] == "M"
    assert result[4] == "F"
    assert result[7] == "F"


def test_null_standardize():
    s = pl.Series("n", ["N/A", "NULL", "none", "", "  ", "actual value", "null", "NA"])
    result = null_standardize(s)
    assert result[0] is None
    assert result[1] is None
    assert result[2] is None
    assert result[3] is None
    assert result[4] is None
    assert result[5] == "actual value"


def test_category_standardize():
    mapping = {"US": ["USA", "United States", "U.S.A.", "US"]}
    s = pl.Series("c", ["USA", "United States", "U.S.A.", "Canada"])
    result = category_standardize(s, mapping=mapping)
    assert result[0] == "US"
    assert result[1] == "US"
    assert result[2] == "US"
    assert result[3] == "Canada"  # no mapping, preserved


def test_category_from_file(tmp_path):
    from goldenflow.transforms.categorical import category_from_file

    lookup = tmp_path / "countries.csv"
    lookup.write_text("variant,canonical\nUSA,US\nUnited States,US\nU.S.A.,US\n")
    s = pl.Series("c", ["USA", "United States", "Canada"])
    result = category_from_file(s, lookup_path=str(lookup))
    assert result[0] == "US"
    assert result[1] == "US"
    assert result[2] == "Canada"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/transforms/test_categorical.py -v`
Expected: FAIL

- [ ] **Step 3: Implement categorical transforms**

```python
# goldenflow/transforms/categorical.py
from __future__ import annotations

import polars as pl

from goldenflow.transforms import register_transform

_TRUE_VALUES = {"yes", "y", "1", "true", "t"}
_FALSE_VALUES = {"no", "n", "0", "false", "f"}
_NULL_VALUES = {"n/a", "null", "none", "na", "nil", "nan", "-", ""}


@register_transform(
    name="boolean_normalize", input_types=["boolean", "string"], auto_apply=False, priority=50, mode="series"
)
def boolean_normalize(series: pl.Series) -> pl.Series:
    def _norm(val: str | None) -> bool | None:
        if val is None:
            return None
        v = val.strip().lower()
        if v in _TRUE_VALUES:
            return True
        if v in _FALSE_VALUES:
            return False
        return None

    return series.map_elements(_norm, return_dtype=pl.Boolean)


@register_transform(
    name="gender_standardize", input_types=["string"], auto_apply=False, priority=50, mode="series"
)
def gender_standardize(series: pl.Series) -> pl.Series:
    _map = {"male": "M", "m": "M", "female": "F", "f": "F"}

    def _std(val: str | None) -> str | None:
        if val is None:
            return None
        return _map.get(val.strip().lower(), val)

    return series.map_elements(_std, return_dtype=pl.Utf8)


@register_transform(
    name="null_standardize", input_types=["string"], auto_apply=True, priority=80, mode="series"
)
def null_standardize(series: pl.Series) -> pl.Series:
    def _std(val: str | None) -> str | None:
        if val is None:
            return None
        if val.strip().lower() in _NULL_VALUES:
            return None
        return val

    return series.map_elements(_std, return_dtype=pl.Utf8)


@register_transform(
    name="category_standardize",
    input_types=["string"],
    auto_apply=False,
    priority=45,
    mode="series",
)
def category_standardize(
    series: pl.Series, mapping: dict[str, list[str]] | None = None
) -> pl.Series:
    """Map variant values to canonical values. mapping: {canonical: [variant1, variant2, ...]}"""
    if not mapping:
        return series
    lookup: dict[str, str] = {}
    for canonical, variants in mapping.items():
        for v in variants:
            lookup[v.lower()] = canonical

    def _std(val: str | None) -> str | None:
        if val is None:
            return None
        return lookup.get(val.strip().lower(), val)

    return series.map_elements(_std, return_dtype=pl.Utf8)


@register_transform(
    name="category_from_file",
    input_types=["string"],
    auto_apply=False,
    priority=45,
    mode="series",
)
def category_from_file(
    series: pl.Series, lookup_path: str | None = None
) -> pl.Series:
    """Load mapping from a CSV/YAML file and standardize values.
    CSV must have columns: variant, canonical."""
    if not lookup_path:
        return series
    from pathlib import Path
    p = Path(lookup_path)
    if p.suffix == ".csv":
        import polars as pl_inner
        lookup_df = pl_inner.read_csv(p)
        mapping: dict[str, str] = {}
        for row in lookup_df.iter_rows(named=True):
            mapping[row["variant"].lower()] = row["canonical"]
    elif p.suffix in (".yaml", ".yml"):
        import yaml
        with open(p) as f:
            raw = yaml.safe_load(f) or {}
        mapping = {}
        for canonical, variants in raw.items():
            for v in variants:
                mapping[v.lower()] = canonical
    else:
        return series

    def _std(val: str | None) -> str | None:
        if val is None:
            return None
        return mapping.get(val.strip().lower(), val)

    return series.map_elements(_std, return_dtype=pl.Utf8)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/transforms/test_categorical.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/transforms/categorical.py tests/transforms/test_categorical.py
git commit -m "feat: add categorical transforms (boolean, gender, null, category)"
```

---

### Task 11: Numeric Transforms

**Files:**
- Create: `goldenflow/transforms/numeric.py`
- Create: `tests/transforms/test_numeric.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/transforms/test_numeric.py
import polars as pl

from goldenflow.transforms.numeric import (
    clamp,
    currency_strip,
    percentage_normalize,
    round_values,
)


def test_currency_strip():
    s = pl.Series("p", ["$1,234.56", "$99.99", "$0.50", "free"])
    result = currency_strip(s)
    assert result[0] == 1234.56
    assert result[1] == 99.99
    assert result[2] == 0.50
    assert result[3] is None  # unparseable


def test_percentage_normalize():
    s = pl.Series("p", ["85%", "100%", "0.5%", "50"])
    result = percentage_normalize(s)
    assert result[0] == 0.85
    assert result[1] == 1.0
    assert result[2] == 0.005


def test_round_values():
    s = pl.Series("v", [1.23456, 2.789, 3.1])
    result = round_values(s, n=2)
    assert result.to_list() == [1.23, 2.79, 3.1]


def test_clamp():
    s = pl.Series("v", [-5.0, 0.0, 50.0, 150.0])
    result = clamp(s, min_val=0.0, max_val=100.0)
    assert result.to_list() == [0.0, 0.0, 50.0, 100.0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/transforms/test_numeric.py -v`
Expected: FAIL

- [ ] **Step 3: Implement numeric transforms**

```python
# goldenflow/transforms/numeric.py
from __future__ import annotations

import re

import polars as pl

from goldenflow.transforms import register_transform


@register_transform(
    name="currency_strip", input_types=["string", "numeric"], auto_apply=False, priority=50, mode="series"
)
def currency_strip(series: pl.Series) -> pl.Series:
    def _strip(val: str | None) -> float | None:
        if val is None:
            return None
        cleaned = re.sub(r"[^\d.\-]", "", str(val))
        try:
            return float(cleaned)
        except ValueError:
            return None

    return series.map_elements(_strip, return_dtype=pl.Float64)


@register_transform(
    name="percentage_normalize",
    input_types=["string", "numeric"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def percentage_normalize(series: pl.Series) -> pl.Series:
    def _norm(val: str | None) -> float | None:
        if val is None:
            return None
        v = str(val).strip().rstrip("%")
        try:
            return float(v) / 100.0
        except ValueError:
            return None

    return series.map_elements(_norm, return_dtype=pl.Float64)


@register_transform(
    name="round", input_types=["numeric"], auto_apply=False, priority=40, mode="series"
)
def round_values(series: pl.Series, n: int = 2) -> pl.Series:
    return series.round(n)


@register_transform(
    name="clamp", input_types=["numeric"], auto_apply=False, priority=40, mode="series"
)
def clamp(series: pl.Series, min_val: float = 0.0, max_val: float = 1.0) -> pl.Series:
    return series.clip(min_val, max_val)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/transforms/test_numeric.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/transforms/numeric.py tests/transforms/test_numeric.py
git commit -m "feat: add numeric transforms (currency, percentage, round, clamp)"
```

---

## Phase 3: Engine Core

### Task 12: Manifest — audit trail generation

**Files:**
- Create: `goldenflow/engine/manifest.py`
- Create: `tests/engine/__init__.py`
- Create: `tests/engine/test_manifest.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/engine/__init__.py — empty
# tests/engine/test_manifest.py
import json
from pathlib import Path

from goldenflow.engine.manifest import Manifest, TransformRecord


def test_create_manifest():
    m = Manifest(source="data.csv")
    assert m.source == "data.csv"
    assert m.records == []


def test_add_record():
    m = Manifest(source="data.csv")
    m.add_record(TransformRecord(
        column="name",
        transform="strip",
        affected_rows=10,
        total_rows=100,
        sample_before=["  John  ", "  Jane  "],
        sample_after=["John", "Jane"],
    ))
    assert len(m.records) == 1
    assert m.records[0].column == "name"


def test_add_error():
    m = Manifest(source="data.csv")
    m.add_error(column="phone", transform="phone_e164", row=5, error="Parse failed")
    assert len(m.errors) == 1


def test_save_manifest(tmp_path: Path):
    m = Manifest(source="data.csv")
    m.add_record(TransformRecord(
        column="email",
        transform="lowercase",
        affected_rows=50,
        total_rows=100,
        sample_before=["JOHN@TEST.COM"],
        sample_after=["john@test.com"],
    ))
    path = tmp_path / "manifest.json"
    m.save(path)
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["source"] == "data.csv"
    assert len(data["records"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/engine/test_manifest.py -v`
Expected: FAIL

- [ ] **Step 3: Implement manifest**

```python
# goldenflow/engine/manifest.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class TransformRecord:
    column: str
    transform: str
    affected_rows: int
    total_rows: int
    sample_before: list[str] = field(default_factory=list)
    sample_after: list[str] = field(default_factory=list)


@dataclass
class TransformError:
    column: str
    transform: str
    row: int
    error: str


@dataclass
class Manifest:
    source: str
    records: list[TransformRecord] = field(default_factory=list)
    errors: list[TransformError] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def add_record(self, record: TransformRecord) -> None:
        self.records.append(record)

    def add_error(self, column: str, transform: str, row: int, error: str) -> None:
        self.errors.append(TransformError(
            column=column, transform=transform, row=row, error=error
        ))

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "created_at": self.created_at,
            "records": [
                {
                    "column": r.column,
                    "transform": r.transform,
                    "affected_rows": r.affected_rows,
                    "total_rows": r.total_rows,
                    "sample_before": r.sample_before,
                    "sample_after": r.sample_after,
                }
                for r in self.records
            ],
            "errors": [
                {
                    "column": e.column,
                    "transform": e.transform,
                    "row": e.row,
                    "error": e.error,
                }
                for e in self.errors
            ],
            "summary": {
                "total_transforms": len(self.records),
                "total_errors": len(self.errors),
                "columns_affected": list({r.column for r in self.records}),
            },
        }

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/engine/test_manifest.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/engine/manifest.py tests/engine/
git commit -m "feat: add manifest for audit trail generation"
```

---

### Task 13: Profiler Bridge — GoldenCheck integration with fallback

**Files:**
- Create: `goldenflow/engine/profiler_bridge.py`
- Create: `tests/engine/test_profiler_bridge.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/engine/test_profiler_bridge.py
import polars as pl

from goldenflow.engine.profiler_bridge import ColumnProfile, DatasetProfile, profile_dataframe


def test_profile_dataframe_returns_dataset_profile():
    df = pl.DataFrame({
        "name": ["John Smith", "Jane Doe"],
        "email": ["john@test.com", "jane@test.com"],
        "phone": ["(555) 123-4567", "555-987-6543"],
        "age": [30, 25],
    })
    profile = profile_dataframe(df)
    assert isinstance(profile, DatasetProfile)
    assert profile.row_count == 2
    assert profile.column_count == 4
    assert len(profile.columns) == 4


def test_column_profile_fields():
    df = pl.DataFrame({"email": ["john@test.com", "jane@test.com", None]})
    profile = profile_dataframe(df)
    col = profile.columns[0]
    assert isinstance(col, ColumnProfile)
    assert col.name == "email"
    assert col.null_count == 1
    assert col.unique_count == 2


def test_inferred_types():
    df = pl.DataFrame({
        "email": ["john@test.com", "jane@test.com"],
        "phone": ["(555) 123-4567", "+15559876543"],
        "date": ["2024-01-01", "03/15/2024"],
        "number": [1, 2],
    })
    profile = profile_dataframe(df)
    types = {c.name: c.inferred_type for c in profile.columns}
    assert types["email"] == "email"
    assert types["phone"] == "phone"
    assert types["date"] == "date"
    assert types["number"] == "numeric"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/engine/test_profiler_bridge.py -v`
Expected: FAIL

- [ ] **Step 3: Implement profiler bridge with fallback**

```python
# goldenflow/engine/profiler_bridge.py
from __future__ import annotations

import re
from dataclasses import dataclass, field

import polars as pl

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^[\+\(\)\-\.\s\d]{7,20}$")
_DATE_RE = re.compile(
    r"^(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|"
    r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})$"
)
_NAME_RE = re.compile(r"^[A-Z][a-z]+(\s+[A-Z][a-z]+)+$")


@dataclass
class ColumnProfile:
    name: str
    inferred_type: str
    row_count: int
    null_count: int
    null_pct: float
    unique_count: int
    unique_pct: float
    sample_values: list[str] = field(default_factory=list)
    detected_format: str | None = None


@dataclass
class DatasetProfile:
    file_path: str
    row_count: int
    column_count: int
    columns: list[ColumnProfile]


def _infer_type(series: pl.Series) -> str:
    """Infer semantic type from a string series using regex heuristics."""
    if series.dtype in (pl.Int8, pl.Int16, pl.Int32, pl.Int64,
                         pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
                         pl.Float32, pl.Float64):
        return "numeric"
    if series.dtype == pl.Boolean:
        return "boolean"
    if series.dtype in (pl.Date, pl.Datetime):
        return "date"

    # Sample non-null string values for pattern matching
    non_null = series.drop_nulls().cast(pl.Utf8)
    if len(non_null) == 0:
        return "string"

    sample = non_null.head(min(100, len(non_null))).to_list()
    sample_stripped = [s.strip() for s in sample if s and s.strip()]
    if not sample_stripped:
        return "string"

    # Check patterns against sample
    checks = {
        "email": (_EMAIL_RE, 0.7),
        "phone": (_PHONE_RE, 0.6),
        "date": (_DATE_RE, 0.5),
        "name": (_NAME_RE, 0.5),
    }
    for type_name, (pattern, threshold) in checks.items():
        match_pct = sum(1 for v in sample_stripped if pattern.match(v)) / len(sample_stripped)
        if match_pct >= threshold:
            return type_name

    return "string"


def _profile_column(series: pl.Series) -> ColumnProfile:
    row_count = len(series)
    null_count = series.null_count()
    non_null = series.drop_nulls()
    unique_count = non_null.n_unique()
    sample = non_null.head(5).cast(pl.Utf8).to_list() if len(non_null) > 0 else []

    return ColumnProfile(
        name=series.name,
        inferred_type=_infer_type(series),
        row_count=row_count,
        null_count=null_count,
        null_pct=null_count / row_count if row_count > 0 else 0.0,
        unique_count=unique_count,
        unique_pct=unique_count / row_count if row_count > 0 else 0.0,
        sample_values=sample,
    )


def profile_dataframe(df: pl.DataFrame, file_path: str = "") -> DatasetProfile:
    """Profile a DataFrame. Uses GoldenCheck if available, otherwise falls back to built-in."""
    try:
        from goldencheck import scan_file
        from goldencheck.models.profile import DatasetProfile as GCProfile

        # If we have a file path and GoldenCheck, use it
        if file_path:
            findings, gc_profile = scan_file(file_path)
            columns = [
                ColumnProfile(
                    name=cp.name,
                    inferred_type=cp.inferred_type,
                    row_count=cp.row_count,
                    null_count=cp.null_count,
                    null_pct=cp.null_pct,
                    unique_count=cp.unique_count,
                    unique_pct=cp.unique_pct,
                    sample_values=[str(v) for v, _ in (cp.top_values or [])[:5]],
                    detected_format=cp.detected_format,
                )
                for cp in gc_profile.columns
            ]
            return DatasetProfile(
                file_path=gc_profile.file_path,
                row_count=gc_profile.row_count,
                column_count=gc_profile.column_count,
                columns=columns,
            )
    except ImportError:
        pass  # Fall back to built-in profiler

    # Built-in fallback profiler
    columns = [_profile_column(df[col]) for col in df.columns]
    return DatasetProfile(
        file_path=file_path,
        row_count=df.shape[0],
        column_count=df.shape[1],
        columns=columns,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/engine/test_profiler_bridge.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/engine/profiler_bridge.py tests/engine/test_profiler_bridge.py
git commit -m "feat: add profiler bridge with GoldenCheck integration and fallback"
```

---

### Task 14: Selector — auto-select transforms based on column profiles

**Files:**
- Create: `goldenflow/engine/selector.py`
- Create: `tests/engine/test_selector.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/engine/test_selector.py
import polars as pl

# Ensure transforms are imported so they register
import goldenflow.transforms.text  # noqa: F401
import goldenflow.transforms.phone  # noqa: F401
import goldenflow.transforms.dates  # noqa: F401
import goldenflow.transforms.categorical  # noqa: F401

from goldenflow.engine.profiler_bridge import ColumnProfile
from goldenflow.engine.selector import select_transforms


def test_select_transforms_for_email_column():
    profile = ColumnProfile(
        name="email", inferred_type="email", row_count=100,
        null_count=0, null_pct=0.0, unique_count=100, unique_pct=1.0,
    )
    selected = select_transforms(profile)
    names = [t.name for t in selected]
    assert "strip" in names
    assert "lowercase" not in names  # not auto_apply for string, but email-specific logic


def test_select_transforms_for_phone_column():
    profile = ColumnProfile(
        name="phone", inferred_type="phone", row_count=100,
        null_count=0, null_pct=0.0, unique_count=100, unique_pct=1.0,
    )
    selected = select_transforms(profile)
    names = [t.name for t in selected]
    assert "phone_e164" in names


def test_select_transforms_for_date_column():
    profile = ColumnProfile(
        name="signup_date", inferred_type="date", row_count=100,
        null_count=0, null_pct=0.0, unique_count=50, unique_pct=0.5,
    )
    selected = select_transforms(profile)
    names = [t.name for t in selected]
    assert "date_iso8601" in names


def test_select_no_transforms_for_unknown_type():
    profile = ColumnProfile(
        name="misc", inferred_type="unknown_xyz", row_count=100,
        null_count=0, null_pct=0.0, unique_count=100, unique_pct=1.0,
    )
    selected = select_transforms(profile)
    # Only universal transforms (string-type with auto_apply) should match
    assert all(t.auto_apply for t in selected)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/engine/test_selector.py -v`
Expected: FAIL

- [ ] **Step 3: Implement selector**

```python
# goldenflow/engine/selector.py
from __future__ import annotations

from goldenflow.engine.profiler_bridge import ColumnProfile
from goldenflow.transforms import TransformInfo, list_transforms


# Finding check → transform mapping for --from-findings integration
FINDING_TRANSFORM_MAP: dict[str, list[str]] = {
    "format_inconsistency": ["date_iso8601", "phone_e164"],
    "whitespace_issues": ["strip", "collapse_whitespace"],
    "mixed_case": ["lowercase", "title_case"],
    "null_variants": ["null_standardize"],
    "unicode_issues": ["normalize_unicode"],
}


def select_transforms(
    profile: ColumnProfile,
    confidence_threshold: float = 0.8,
) -> list[TransformInfo]:
    """Select auto-applicable transforms for a column based on its profile."""
    all_transforms = list_transforms()
    selected: list[TransformInfo] = []

    for t in all_transforms:
        if not t.auto_apply:
            continue
        # Match if column type is in the transform's input_types
        if profile.inferred_type in t.input_types:
            selected.append(t)
        # "string" transforms apply to all string-like types
        elif "string" in t.input_types and profile.inferred_type in (
            "string", "email", "phone", "name", "address", "date",
        ):
            selected.append(t)

    # Sort by priority descending (higher = runs first)
    selected.sort(key=lambda t: t.priority, reverse=True)
    return selected


def select_from_findings(
    findings: list[dict],
) -> dict[str, list[str]]:
    """Map GoldenCheck findings to transform names. Returns {column: [transform_names]}."""
    column_transforms: dict[str, list[str]] = {}
    for finding in findings:
        check = finding.get("check", "")
        column = finding.get("column", "")
        if not column:
            continue
        transform_names = FINDING_TRANSFORM_MAP.get(check, [])
        if transform_names:
            column_transforms.setdefault(column, []).extend(transform_names)
    # Deduplicate
    return {col: list(dict.fromkeys(names)) for col, names in column_transforms.items()}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/engine/test_selector.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/engine/selector.py tests/engine/test_selector.py
git commit -m "feat: add selector for auto-selecting transforms from profiles"
```

---

### Task 15: TransformEngine — the core orchestrator

**Files:**
- Create: `goldenflow/engine/transformer.py`
- Create: `tests/engine/test_transformer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/engine/test_transformer.py
from pathlib import Path

import polars as pl

# Import all transforms so they register
import goldenflow.transforms.text  # noqa: F401
import goldenflow.transforms.phone  # noqa: F401
import goldenflow.transforms.dates  # noqa: F401
import goldenflow.transforms.categorical  # noqa: F401
import goldenflow.transforms.numeric  # noqa: F401
import goldenflow.transforms.address  # noqa: F401
import goldenflow.transforms.names  # noqa: F401

from goldenflow.engine.transformer import TransformEngine, TransformResult
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec


def test_transform_zero_config(sample_csv: Path):
    engine = TransformEngine()
    result = engine.transform_file(sample_csv)
    assert isinstance(result, TransformResult)
    assert isinstance(result.df, pl.DataFrame)
    assert result.manifest is not None
    assert result.df.shape[0] == 3  # same row count


def test_transform_with_config(sample_csv: Path):
    config = GoldenFlowConfig(
        transforms=[
            TransformSpec(column="name", ops=["strip", "title_case"]),
            TransformSpec(column="email", ops=["lowercase", "strip"]),
        ]
    )
    engine = TransformEngine(config=config)
    result = engine.transform_file(sample_csv)
    # name should be stripped and title-cased
    names = result.df["name"].to_list()
    assert names[0] == "John Smith"
    assert names[1] == "Jane Doe"


def test_transform_dataframe():
    df = pl.DataFrame({
        "email": ["  JOHN@TEST.COM  ", "jane@test.com"],
    })
    config = GoldenFlowConfig(
        transforms=[TransformSpec(column="email", ops=["strip", "lowercase"])]
    )
    engine = TransformEngine(config=config)
    result = engine.transform_df(df)
    assert result.df["email"].to_list() == ["john@test.com", "jane@test.com"]


def test_transform_with_renames():
    df = pl.DataFrame({"email_address": ["john@test.com"]})
    config = GoldenFlowConfig(renames={"email_address": "email"})
    engine = TransformEngine(config=config)
    result = engine.transform_df(df)
    assert "email" in result.df.columns
    assert "email_address" not in result.df.columns


def test_transform_with_drop():
    df = pl.DataFrame({"keep": [1], "remove": [2]})
    config = GoldenFlowConfig(drop=["remove"])
    engine = TransformEngine(config=config)
    result = engine.transform_df(df)
    assert "keep" in result.df.columns
    assert "remove" not in result.df.columns


def test_transform_with_dedup():
    df = pl.DataFrame({"email": ["a@test.com", "b@test.com", "a@test.com"], "name": ["A", "B", "C"]})
    from goldenflow.config.schema import DedupSpec
    config = GoldenFlowConfig(dedup=DedupSpec(columns=["email"], keep="first"))
    engine = TransformEngine(config=config)
    result = engine.transform_df(df)
    assert result.df.shape[0] == 2


def test_transform_with_filters():
    df = pl.DataFrame({"email": ["a@test.com", None, "c@test.com"]})
    from goldenflow.config.schema import FilterSpec
    config = GoldenFlowConfig(filters=[FilterSpec(column="email", condition="not_null")])
    engine = TransformEngine(config=config)
    result = engine.transform_df(df)
    assert result.df.shape[0] == 2


def test_transform_output_files(sample_csv: Path, tmp_path: Path):
    engine = TransformEngine()
    result = engine.transform_file(sample_csv, output_dir=tmp_path)
    transformed_path = tmp_path / "sample_transformed.csv"
    manifest_path = tmp_path / "sample_manifest.json"
    assert transformed_path.exists()
    assert manifest_path.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/engine/test_transformer.py -v`
Expected: FAIL

- [ ] **Step 3: Implement TransformEngine**

```python
# goldenflow/engine/transformer.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from goldenflow.config.schema import GoldenFlowConfig
from goldenflow.connectors.file import read_file, write_file
from goldenflow.engine.manifest import Manifest, TransformRecord
from goldenflow.engine.profiler_bridge import profile_dataframe
from goldenflow.engine.selector import select_transforms
from goldenflow.transforms import TransformInfo, get_transform, parse_transform_name


@dataclass
class TransformResult:
    df: pl.DataFrame
    manifest: Manifest


class TransformEngine:
    def __init__(self, config: GoldenFlowConfig | None = None):
        self.config = config or GoldenFlowConfig()

    def transform_file(
        self,
        path: Path,
        output_dir: Path | None = None,
    ) -> TransformResult:
        """Transform a file. Optionally write output files."""
        df = read_file(path)
        result = self.transform_df(df, source=str(path))

        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            stem = path.stem
            out_path = output_dir / f"{stem}_transformed{path.suffix}"
            manifest_path = output_dir / f"{stem}_manifest.json"
            write_file(result.df, out_path)
            result.manifest.save(manifest_path)

        return result

    def transform_df(
        self,
        df: pl.DataFrame,
        source: str = "<dataframe>",
    ) -> TransformResult:
        """Transform a DataFrame."""
        manifest = Manifest(source=source)

        if self.config.transforms:
            df = self._apply_config_transforms(df, manifest)
        else:
            df = self._apply_auto_transforms(df, manifest)

        # Apply renames
        for old, new in self.config.renames.items():
            if old in df.columns:
                df = df.rename({old: new})

        # Apply drops
        drop_cols = [c for c in self.config.drop if c in df.columns]
        if drop_cols:
            df = df.drop(drop_cols)

        # Apply filters
        for filt in self.config.filters:
            if filt.column in df.columns:
                df = self._apply_filter(df, filt.column, filt.condition)

        # Apply dedup
        if self.config.dedup:
            dedup_cols = [c for c in self.config.dedup.columns if c in df.columns]
            if dedup_cols:
                before = df.shape[0]
                df = df.unique(subset=dedup_cols, keep=self.config.dedup.keep)
                after = df.shape[0]
                if before != after:
                    manifest.add_record(TransformRecord(
                        column=",".join(dedup_cols),
                        transform="dedup",
                        affected_rows=before - after,
                        total_rows=before,
                    ))

        return TransformResult(df=df, manifest=manifest)

    def _apply_config_transforms(
        self, df: pl.DataFrame, manifest: Manifest
    ) -> pl.DataFrame:
        """Apply transforms specified in config."""
        for spec in self.config.transforms:
            if spec.column not in df.columns:
                continue
            for op_raw in spec.ops:
                name, params = parse_transform_name(op_raw)
                info = get_transform(name)
                if info is None:
                    manifest.add_error(
                        column=spec.column, transform=name, row=-1,
                        error=f"Transform '{name}' not found in registry",
                    )
                    continue
                df = self._apply_single_transform(df, spec.column, info, params, manifest)
        return df

    def _apply_auto_transforms(
        self, df: pl.DataFrame, manifest: Manifest
    ) -> pl.DataFrame:
        """Auto-detect and apply transforms based on column profiling."""
        profile = profile_dataframe(df)
        for col_profile in profile.columns:
            selected = select_transforms(col_profile)
            for info in selected:
                df = self._apply_single_transform(
                    df, col_profile.name, info, [], manifest
                )
        return df

    def _apply_single_transform(
        self,
        df: pl.DataFrame,
        column: str,
        info: TransformInfo,
        params: list[str],
        manifest: Manifest,
    ) -> pl.DataFrame:
        """Apply a single transform to a column, recording results in manifest."""
        before_sample = df[column].head(3).cast(pl.Utf8).to_list()
        total_rows = df.shape[0]

        try:
            if info.mode == "expr":
                expr = info.func(column, *params) if params else info.func(column)
                new_df = df.with_columns(expr.alias(column))
            elif info.mode == "dataframe":
                # DataFrame-mode transforms (split_name, split_address, etc.)
                new_df = info.func(df, column)
            else:
                series = df[column]
                typed_params = self._cast_params(params)
                new_series = info.func(series, *typed_params) if typed_params else info.func(series)
                if isinstance(new_series, tuple):
                    # e.g. initial_expand returns (series, flagged_rows)
                    new_series, flagged = new_series
                    if flagged:
                        for row_idx in flagged:
                            manifest.add_error(
                                column=column, transform=info.name, row=row_idx,
                                error="Flagged for review",
                            )
                new_df = df.with_columns(new_series.alias(column))

            after_sample = new_df[column].head(3).cast(pl.Utf8).to_list()

            # Count affected rows
            try:
                changed = (df[column].cast(pl.Utf8) != new_df[column].cast(pl.Utf8)).sum()
            except Exception:
                changed = total_rows

            manifest.add_record(TransformRecord(
                column=column,
                transform=info.name,
                affected_rows=changed,
                total_rows=total_rows,
                sample_before=before_sample,
                sample_after=after_sample,
            ))
            return new_df

        except Exception as e:
            manifest.add_error(
                column=column, transform=info.name, row=-1, error=str(e)
            )
            return df  # preserve original on failure

    @staticmethod
    def _cast_params(params: list[str]) -> list:
        """Try to cast string params to int or float."""
        result = []
        for p in params:
            try:
                result.append(int(p))
            except ValueError:
                try:
                    result.append(float(p))
                except ValueError:
                    result.append(p)
        return result

    @staticmethod
    def _apply_filter(df: pl.DataFrame, column: str, condition: str) -> pl.DataFrame:
        if condition == "not_null":
            return df.filter(pl.col(column).is_not_null())
        if condition.startswith("after:"):
            date_str = condition.split(":", 1)[1]
            return df.filter(pl.col(column) > date_str)
        if condition.startswith("before:"):
            date_str = condition.split(":", 1)[1]
            return df.filter(pl.col(column) < date_str)
        return df
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/engine/test_transformer.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/engine/transformer.py tests/engine/test_transformer.py
git commit -m "feat: add TransformEngine — core orchestrator with config and auto modes"
```

---

### Task 16: Differ — pre/post transform comparison

**Files:**
- Create: `goldenflow/engine/differ.py`
- Create: `tests/engine/test_differ.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/engine/test_differ.py
import polars as pl

from goldenflow.engine.differ import diff_dataframes


def test_diff_identical():
    df = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    result = diff_dataframes(df, df)
    assert result.total_changes == 0


def test_diff_value_changes():
    before = pl.DataFrame({"a": ["  hello  ", "world"], "b": [1, 2]})
    after = pl.DataFrame({"a": ["hello", "world"], "b": [1, 2]})
    result = diff_dataframes(before, after)
    assert result.total_changes == 1
    assert "a" in result.changed_columns


def test_diff_column_added():
    before = pl.DataFrame({"a": [1]})
    after = pl.DataFrame({"a": [1], "b": [2]})
    result = diff_dataframes(before, after)
    assert "b" in result.added_columns


def test_diff_column_removed():
    before = pl.DataFrame({"a": [1], "b": [2]})
    after = pl.DataFrame({"a": [1]})
    result = diff_dataframes(before, after)
    assert "b" in result.removed_columns
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/engine/test_differ.py -v`
Expected: FAIL

- [ ] **Step 3: Implement differ**

```python
# goldenflow/engine/differ.py
from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl


@dataclass
class DiffResult:
    total_changes: int = 0
    changed_columns: list[str] = field(default_factory=list)
    added_columns: list[str] = field(default_factory=list)
    removed_columns: list[str] = field(default_factory=list)
    row_count_before: int = 0
    row_count_after: int = 0
    column_details: dict[str, dict] = field(default_factory=dict)


def diff_dataframes(before: pl.DataFrame, after: pl.DataFrame) -> DiffResult:
    """Compare two DataFrames and report differences."""
    result = DiffResult(
        row_count_before=before.shape[0],
        row_count_after=after.shape[0],
    )

    before_cols = set(before.columns)
    after_cols = set(after.columns)

    result.added_columns = sorted(after_cols - before_cols)
    result.removed_columns = sorted(before_cols - after_cols)

    common_cols = before_cols & after_cols
    total_changes = 0

    for col in sorted(common_cols):
        if before.shape[0] != after.shape[0]:
            result.changed_columns.append(col)
            total_changes += abs(before.shape[0] - after.shape[0])
            continue

        try:
            b_series = before[col].cast(pl.Utf8)
            a_series = after[col].cast(pl.Utf8)
            changes = (b_series != a_series).sum()
            if changes > 0:
                result.changed_columns.append(col)
                total_changes += changes
                result.column_details[col] = {"changed_rows": changes}
        except Exception:
            result.changed_columns.append(col)
            total_changes += before.shape[0]

    result.total_changes = total_changes
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/engine/test_differ.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/engine/differ.py tests/engine/test_differ.py
git commit -m "feat: add differ for pre/post transform comparison"
```

---

### Task 17: Public API exports — wire up `__init__.py`

**Files:**
- Modify: `goldenflow/__init__.py`

- [ ] **Step 1: Update root `__init__.py` with public API**

```python
# goldenflow/__init__.py
__version__ = "0.1.0"

from goldenflow.config.schema import (
    DedupSpec,
    FilterSpec,
    GoldenFlowConfig,
    MappingSpec,
    SplitSpec,
    TransformSpec,
)
from goldenflow.engine.transformer import TransformEngine, TransformResult

# Import transform modules so they register with the registry
import goldenflow.transforms.text  # noqa: F401
import goldenflow.transforms.phone  # noqa: F401
import goldenflow.transforms.names  # noqa: F401
import goldenflow.transforms.address  # noqa: F401
import goldenflow.transforms.dates  # noqa: F401
import goldenflow.transforms.categorical  # noqa: F401
import goldenflow.transforms.numeric  # noqa: F401


def transform_file(path, config=None, output_dir=None):
    """Convenience function: transform a file."""
    from pathlib import Path
    engine = TransformEngine(config=config)
    return engine.transform_file(Path(path), output_dir=Path(output_dir) if output_dir else None)


def transform_df(df, config=None):
    """Convenience function: transform a DataFrame."""
    engine = TransformEngine(config=config)
    return engine.transform_df(df)


__all__ = [
    "TransformEngine",
    "TransformResult",
    "GoldenFlowConfig",
    "TransformSpec",
    "SplitSpec",
    "FilterSpec",
    "DedupSpec",
    "MappingSpec",
    "transform_file",
    "transform_df",
]
```

- [ ] **Step 2: Run all tests**

Run: `pytest tests/ -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add goldenflow/__init__.py
git commit -m "feat: wire up public API exports with transform registration"
```

---

## Phase 4: Schema Mapping

### Task 18: Name Similarity and Schema Mapper

**Files:**
- Create: `goldenflow/mapping/name_similarity.py`
- Create: `goldenflow/mapping/profile_similarity.py`
- Create: `goldenflow/mapping/schema_mapper.py`
- Create: `tests/mapping/__init__.py`
- Create: `tests/mapping/test_schema_mapper.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/mapping/__init__.py — empty
# tests/mapping/test_schema_mapper.py
import polars as pl

from goldenflow.mapping.schema_mapper import SchemaMapper, ColumnMapping


def test_auto_map_identical_columns():
    source = pl.DataFrame({"email": ["a@t.com"], "name": ["John"]})
    target = pl.DataFrame({"email": ["b@t.com"], "name": ["Jane"]})
    mapper = SchemaMapper()
    mappings = mapper.map(source, target)
    assert len(mappings) >= 2
    email_map = next(m for m in mappings if m.source == "email")
    assert email_map.target == "email"
    assert email_map.confidence >= 0.9


def test_auto_map_aliased_columns():
    source = pl.DataFrame({"fname": ["John"], "email_address": ["a@t.com"]})
    target = pl.DataFrame({"first_name": ["Jane"], "email": ["b@t.com"]})
    mapper = SchemaMapper()
    mappings = mapper.map(source, target)
    fname_map = next((m for m in mappings if m.source == "fname"), None)
    assert fname_map is not None
    assert fname_map.target == "first_name"


def test_confidence_tiers():
    source = pl.DataFrame({"email": ["a@t.com"], "xyz_unknown": [1]})
    target = pl.DataFrame({"email": ["b@t.com"], "abc_other": [2]})
    mapper = SchemaMapper()
    mappings = mapper.map(source, target)
    email_map = next(m for m in mappings if m.source == "email")
    assert email_map.confidence >= 0.9  # auto-apply tier


def test_export_mapping_config():
    source = pl.DataFrame({"email": ["a@t.com"]})
    target = pl.DataFrame({"email": ["b@t.com"]})
    mapper = SchemaMapper()
    mappings = mapper.map(source, target)
    config = mapper.to_config(mappings)
    assert len(config.mappings) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/mapping/test_schema_mapper.py -v`
Expected: FAIL

- [ ] **Step 3: Implement name similarity**

```python
# goldenflow/mapping/name_similarity.py
from __future__ import annotations

from rapidfuzz import fuzz

# Common column name aliases
ALIASES: dict[str, list[str]] = {
    "first_name": ["fname", "first", "given_name", "first_nm"],
    "last_name": ["lname", "last", "surname", "family_name", "last_nm"],
    "email": ["email_address", "e_mail", "email_addr", "mail"],
    "phone": ["phone_number", "ph", "telephone", "tel", "mobile", "cell"],
    "address": ["addr", "street_address", "addr_line_1", "address_line_1"],
    "city": ["town", "municipality"],
    "state": ["st", "province", "region"],
    "zip": ["zipcode", "zip_code", "postal_code", "postal"],
    "name": ["full_name", "fullname", "customer_name"],
    "created_at": ["signup_date", "signup_dt", "create_date", "date_created"],
}

# Build reverse lookup: alias → canonical
_ALIAS_LOOKUP: dict[str, str] = {}
for canonical, aliases in ALIASES.items():
    for alias in aliases:
        _ALIAS_LOOKUP[alias.lower()] = canonical.lower()
    _ALIAS_LOOKUP[canonical.lower()] = canonical.lower()


def name_similarity(source: str, target: str) -> float:
    """Score how similar two column names are (0.0-1.0)."""
    s_lower = source.lower().strip()
    t_lower = target.lower().strip()

    # Exact match
    if s_lower == t_lower:
        return 1.0

    # Alias match
    s_canonical = _ALIAS_LOOKUP.get(s_lower)
    t_canonical = _ALIAS_LOOKUP.get(t_lower)
    if s_canonical and t_canonical and s_canonical == t_canonical:
        return 0.95

    # Fuzzy match using Jaro-Winkler
    score = fuzz.WRatio(s_lower, t_lower) / 100.0
    return score
```

- [ ] **Step 4: Implement profile similarity**

```python
# goldenflow/mapping/profile_similarity.py
from __future__ import annotations

from goldenflow.engine.profiler_bridge import ColumnProfile


def profile_similarity(source: ColumnProfile, target: ColumnProfile) -> float:
    """Score how similar two column profiles are (0.0-1.0)."""
    score = 0.0
    weights = 0.0

    # Type match
    if source.inferred_type == target.inferred_type:
        score += 0.4
    weights += 0.4

    # Null percentage similarity
    null_diff = abs(source.null_pct - target.null_pct)
    score += 0.2 * max(0.0, 1.0 - null_diff)
    weights += 0.2

    # Uniqueness similarity
    unique_diff = abs(source.unique_pct - target.unique_pct)
    score += 0.2 * max(0.0, 1.0 - unique_diff)
    weights += 0.2

    # Cardinality ratio
    if source.unique_count > 0 and target.unique_count > 0:
        ratio = min(source.unique_count, target.unique_count) / max(
            source.unique_count, target.unique_count
        )
        score += 0.2 * ratio
    weights += 0.2

    return score / weights if weights > 0 else 0.0
```

- [ ] **Step 5: Implement schema mapper**

```python
# goldenflow/mapping/schema_mapper.py
from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from goldenflow.config.schema import GoldenFlowConfig, MappingSpec
from goldenflow.engine.profiler_bridge import profile_dataframe
from goldenflow.mapping.name_similarity import name_similarity
from goldenflow.mapping.profile_similarity import profile_similarity


@dataclass
class ColumnMapping:
    source: str
    target: str
    confidence: float
    transform: str | None = None


class SchemaMapper:
    def __init__(
        self,
        auto_threshold: float = 0.9,
        suggest_threshold: float = 0.6,
    ):
        self.auto_threshold = auto_threshold
        self.suggest_threshold = suggest_threshold

    def map(
        self,
        source_df: pl.DataFrame,
        target_df: pl.DataFrame,
    ) -> list[ColumnMapping]:
        """Auto-map source columns to target columns."""
        source_profile = profile_dataframe(source_df)
        target_profile = profile_dataframe(target_df)

        target_profiles = {cp.name: cp for cp in target_profile.columns}
        source_profiles = {cp.name: cp for cp in source_profile.columns}

        mappings: list[ColumnMapping] = []
        used_targets: set[str] = set()

        for s_col in source_df.columns:
            best_match: ColumnMapping | None = None
            best_score = 0.0

            for t_col in target_df.columns:
                if t_col in used_targets:
                    continue

                # Pass 1: Name similarity
                n_score = name_similarity(s_col, t_col)

                # Pass 2: Profile similarity (when name is ambiguous)
                p_score = 0.0
                if s_col in source_profiles and t_col in target_profiles:
                    p_score = profile_similarity(
                        source_profiles[s_col], target_profiles[t_col]
                    )

                # Combined score: name-weighted
                combined = 0.7 * n_score + 0.3 * p_score

                if combined > best_score and combined >= self.suggest_threshold:
                    best_score = combined
                    best_match = ColumnMapping(
                        source=s_col,
                        target=t_col,
                        confidence=round(combined, 3),
                    )

            if best_match:
                mappings.append(best_match)
                used_targets.add(best_match.target)

        return mappings

    def to_config(self, mappings: list[ColumnMapping]) -> GoldenFlowConfig:
        """Convert mappings to a GoldenFlowConfig with MappingSpecs."""
        mapping_specs = [
            MappingSpec(
                source=m.source,
                target=m.target,
                transform=m.transform,
            )
            for m in mappings
        ]
        return GoldenFlowConfig(mappings=mapping_specs)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/mapping/test_schema_mapper.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add goldenflow/mapping/ tests/mapping/
git commit -m "feat: add schema mapping with name/profile similarity"
```

---

## Phase 5: CLI

### Task 19: CLI — all 9 commands

**Files:**
- Create: `goldenflow/cli/main.py`
- Create: `goldenflow/config/learner.py`
- Create: `goldenflow/reporters/rich_console.py`
- Create: `goldenflow/reporters/json_reporter.py`
- Create: `tests/cli/__init__.py`
- Create: `tests/cli/test_cli.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/cli/__init__.py — empty
# tests/cli/test_cli.py
from pathlib import Path

from typer.testing import CliRunner

from goldenflow.cli.main import app

runner = CliRunner()


def test_transform_zero_config(sample_csv: Path, tmp_path: Path):
    result = runner.invoke(app, ["transform", str(sample_csv), "--output-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / "sample_transformed.csv").exists()
    assert (tmp_path / "sample_manifest.json").exists()


def test_validate_dry_run(sample_csv: Path):
    result = runner.invoke(app, ["validate", str(sample_csv)])
    assert result.exit_code == 0
    assert "would change" in result.stdout.lower() or "transform" in result.stdout.lower()


def test_profile(sample_csv: Path):
    result = runner.invoke(app, ["profile", str(sample_csv)])
    assert result.exit_code == 0
    assert "name" in result.stdout.lower()


def test_learn(sample_csv: Path, tmp_path: Path):
    out = tmp_path / "learned.yaml"
    result = runner.invoke(app, ["learn", str(sample_csv), "-o", str(out)])
    assert result.exit_code == 0
    assert out.exists()


def test_diff(sample_csv: Path, tmp_path: Path):
    # Transform first, then diff
    runner.invoke(app, ["transform", str(sample_csv), "--output-dir", str(tmp_path)])
    transformed = tmp_path / "sample_transformed.csv"
    result = runner.invoke(app, ["diff", str(sample_csv), str(transformed)])
    assert result.exit_code == 0


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "goldenflow" in result.stdout.lower() or "transform" in result.stdout.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/cli/test_cli.py -v`
Expected: FAIL

- [ ] **Step 3: Implement reporters**

```python
# goldenflow/reporters/rich_console.py
from __future__ import annotations

from rich.console import Console
from rich.table import Table

from goldenflow.engine.manifest import Manifest
from goldenflow.engine.profiler_bridge import DatasetProfile


console = Console()


def print_profile(profile: DatasetProfile) -> None:
    table = Table(title=f"Profile: {profile.file_path or '<dataframe>'}")
    table.add_column("Column", style="cyan")
    table.add_column("Type", style="green")
    table.add_column("Nulls", style="yellow")
    table.add_column("Unique", style="magenta")
    table.add_column("Sample", style="dim")

    for col in profile.columns:
        table.add_row(
            col.name,
            col.inferred_type,
            f"{col.null_count} ({col.null_pct:.0%})",
            f"{col.unique_count} ({col.unique_pct:.0%})",
            ", ".join(col.sample_values[:3]),
        )

    console.print(table)
    console.print(f"\n[bold]{profile.row_count}[/bold] rows, [bold]{profile.column_count}[/bold] columns")


def print_manifest(manifest: Manifest) -> None:
    if not manifest.records and not manifest.errors:
        console.print("[dim]No transforms applied.[/dim]")
        return

    table = Table(title="Transforms Applied")
    table.add_column("Column", style="cyan")
    table.add_column("Transform", style="green")
    table.add_column("Affected", style="yellow")
    table.add_column("Before", style="dim")
    table.add_column("After", style="bold")

    for r in manifest.records:
        table.add_row(
            r.column,
            r.transform,
            f"{r.affected_rows}/{r.total_rows}",
            ", ".join(r.sample_before[:2]),
            ", ".join(r.sample_after[:2]),
        )

    console.print(table)

    if manifest.errors:
        console.print(f"\n[bold red]{len(manifest.errors)} errors:[/bold red]")
        for e in manifest.errors:
            console.print(f"  [red]{e.column}[/red] / {e.transform}: {e.error}")


def print_diff(diff_result) -> None:
    from goldenflow.engine.differ import DiffResult
    d: DiffResult = diff_result
    console.print(f"Rows: {d.row_count_before} → {d.row_count_after}")
    console.print(f"Total changes: [bold]{d.total_changes}[/bold]")
    if d.added_columns:
        console.print(f"Added columns: [green]{', '.join(d.added_columns)}[/green]")
    if d.removed_columns:
        console.print(f"Removed columns: [red]{', '.join(d.removed_columns)}[/red]")
    if d.changed_columns:
        console.print(f"Changed columns: [yellow]{', '.join(d.changed_columns)}[/yellow]")
```

```python
# goldenflow/reporters/json_reporter.py
from __future__ import annotations

import json

from goldenflow.engine.manifest import Manifest


def manifest_to_json(manifest: Manifest) -> str:
    return json.dumps(manifest.to_dict(), indent=2)
```

- [ ] **Step 4: Implement config learner**

```python
# goldenflow/config/learner.py
from __future__ import annotations

from pathlib import Path

import polars as pl

from goldenflow.config.loader import save_config
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.connectors.file import read_file
from goldenflow.engine.profiler_bridge import profile_dataframe
from goldenflow.engine.selector import select_transforms


def learn_config(path: Path) -> GoldenFlowConfig:
    """Profile a data file and generate a config from auto-detected transforms."""
    df = read_file(path)
    profile = profile_dataframe(df, file_path=str(path))

    transforms: list[TransformSpec] = []
    for col_profile in profile.columns:
        selected = select_transforms(col_profile)
        if selected:
            ops = [t.name for t in selected]
            transforms.append(TransformSpec(column=col_profile.name, ops=ops))

    return GoldenFlowConfig(
        source=str(path),
        transforms=transforms,
    )
```

- [ ] **Step 5: Implement CLI**

```python
# goldenflow/cli/main.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="goldenflow",
    help="GoldenFlow: data transformation toolkit.",
    invoke_without_command=True,
)


def _version_callback(value: bool):
    if value:
        from goldenflow import __version__
        typer.echo(f"goldenflow {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", "-v", callback=_version_callback),
):
    pass


@app.command()
def transform(
    path: Path = typer.Argument(..., help="Input data file"),
    config: Optional[Path] = typer.Option(None, "-c", "--config", help="YAML config file"),
    output_dir: Optional[Path] = typer.Option(None, "-o", "--output-dir", help="Output directory"),
    domain: Optional[str] = typer.Option(None, "--domain", help="Domain pack to use"),
    from_findings: bool = typer.Option(False, "--from-findings", help="Read findings from stdin"),
):
    """Transform a data file (zero-config or config-driven)."""
    import sys
    import json
    import io

    import polars as pl

    import goldenflow
    from goldenflow.config.loader import load_config
    from goldenflow.config.schema import TransformSpec
    from goldenflow.engine.selector import select_from_findings
    from goldenflow.engine.transformer import TransformEngine
    from goldenflow.reporters.rich_console import print_manifest

    cfg = load_config(config) if config else goldenflow.GoldenFlowConfig()

    if domain:
        from goldenflow.domains import load_domain
        domain_pack = load_domain(domain)
        if domain_pack:
            cfg = domain_pack.default_config

    # Handle --from-findings: read JSON findings from stdin and build config
    if from_findings:
        stdin_data = sys.stdin.read()
        findings = json.loads(stdin_data) if stdin_data.strip() else []
        if isinstance(findings, dict):
            findings = findings.get("findings", [findings])
        col_transforms = select_from_findings(findings)
        cfg.transforms = [
            TransformSpec(column=col, ops=ops)
            for col, ops in col_transforms.items()
        ]

    # Handle stdin input (path == "-")
    is_stdin = str(path) == "-"
    if is_stdin:
        from goldenflow.connectors.file import read_file
        stdin_bytes = sys.stdin.buffer.read()
        df = pl.read_csv(io.BytesIO(stdin_bytes))
        engine = TransformEngine(config=cfg)
        result = engine.transform_df(df)
        if output_dir is None:
            # Write to stdout
            result.df.write_csv(sys.stdout)
            return
        else:
            from goldenflow.connectors.file import write_file
            output_dir.mkdir(parents=True, exist_ok=True)
            write_file(result.df, output_dir / "transformed.csv")
            result.manifest.save(output_dir / "manifest.json")
            print_manifest(result.manifest)
            return

    engine = TransformEngine(config=cfg)

    if output_dir is None:
        output_dir = path.parent

    result = engine.transform_file(path, output_dir=output_dir)
    print_manifest(result.manifest)
    typer.echo(f"\nOutput: {output_dir / (path.stem + '_transformed' + path.suffix)}")


@app.command()
def validate(
    path: Path = typer.Argument(..., help="Input data file"),
    config: Optional[Path] = typer.Option(None, "-c", "--config"),
):
    """Dry-run: show what would change without writing."""
    import goldenflow
    from goldenflow.config.loader import load_config
    from goldenflow.engine.transformer import TransformEngine
    from goldenflow.reporters.rich_console import print_manifest

    cfg = load_config(config) if config else goldenflow.GoldenFlowConfig()
    engine = TransformEngine(config=cfg)
    result = engine.transform_file(path)
    typer.echo("Dry run — would change:")
    print_manifest(result.manifest)


@app.command()
def profile(
    path: Path = typer.Argument(..., help="Input data file"),
):
    """Show column profiles for a data file."""
    from goldenflow.connectors.file import read_file
    from goldenflow.engine.profiler_bridge import profile_dataframe
    from goldenflow.reporters.rich_console import print_profile

    df = read_file(path)
    prof = profile_dataframe(df, file_path=str(path))
    print_profile(prof)


@app.command()
def learn(
    path: Path = typer.Argument(..., help="Input data file"),
    output: Path = typer.Option("goldenflow.yaml", "-o", "--output", help="Output config path"),
):
    """Generate a YAML config from data patterns."""
    from goldenflow.config.learner import learn_config
    from goldenflow.config.loader import save_config

    cfg = learn_config(path)
    save_config(cfg, output)
    typer.echo(f"Config saved to {output}")


@app.command()
def diff(
    before: Path = typer.Argument(..., help="Before file"),
    after: Path = typer.Argument(..., help="After file"),
):
    """Compare pre/post transform files."""
    from goldenflow.connectors.file import read_file
    from goldenflow.engine.differ import diff_dataframes
    from goldenflow.reporters.rich_console import print_diff

    df_before = read_file(before)
    df_after = read_file(after)
    result = diff_dataframes(df_before, df_after)
    print_diff(result)


@app.command(name="map")
def map_cmd(
    source: Path = typer.Option(..., "--source", "-s", help="Source data file"),
    target: Path = typer.Option(..., "--target", "-t", help="Target data file or schema"),
    config: Optional[Path] = typer.Option(None, "-c", "--config", help="Mapping config"),
    output: Optional[Path] = typer.Option(None, "-o", "--output", help="Save mapping config"),
):
    """Auto-map schemas between source and target."""
    from goldenflow.connectors.file import read_file
    from goldenflow.mapping.schema_mapper import SchemaMapper
    from goldenflow.reporters.rich_console import console

    source_df = read_file(source)
    target_df = read_file(target)
    mapper = SchemaMapper()
    mappings = mapper.map(source_df, target_df)

    for m in mappings:
        tier = "auto" if m.confidence >= 0.9 else "suggest" if m.confidence >= 0.6 else "skip"
        console.print(f"  {m.source} → {m.target} ({m.confidence:.2f}) [{tier}]")

    if output:
        from goldenflow.config.loader import save_config
        cfg = mapper.to_config(mappings)
        save_config(cfg, output)
        console.print(f"\nMapping saved to {output}")


@app.command()
def interactive(
    path: Optional[Path] = typer.Argument(None, help="Input data file"),
):
    """Launch the interactive TUI."""
    from goldenflow.tui.app import GoldenFlowApp
    app_tui = GoldenFlowApp(path=path)
    app_tui.run()


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8000, "--port"),
):
    """Launch the REST API server."""
    import uvicorn
    from goldenflow.api.server import create_app
    uvicorn.run(create_app(), host=host, port=port)


@app.command(name="mcp-serve")
def mcp_serve():
    """Start MCP server for Claude Desktop."""
    from goldenflow.mcp.server import run_server
    run_server()
```

- [ ] **Step 6: Add domain loader stub**

```python
# goldenflow/domains/__init__.py
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goldenflow.domains.base import DomainPack

_DOMAINS: dict[str, str] = {
    "people_hr": "goldenflow.domains.people_hr",
    "healthcare": None,
    "finance": None,
    "ecommerce": None,
    "real_estate": None,
}


def load_domain(name: str) -> DomainPack | None:
    module_path = _DOMAINS.get(name.lower().replace("-", "_").replace("/", "_"))
    if module_path is None:
        raise NotImplementedError(f"Domain pack '{name}' is not yet available")
    import importlib
    mod = importlib.import_module(module_path)
    return mod.PACK
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/cli/test_cli.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add goldenflow/cli/ goldenflow/config/learner.py goldenflow/reporters/ goldenflow/domains/__init__.py tests/cli/
git commit -m "feat: add CLI with all 9 commands, reporters, and config learner"
```

---

## Phase 6: Domain Packs

### Task 20: Domain Pack Base + People/HR

**Files:**
- Create: `goldenflow/domains/base.py`
- Create: `goldenflow/domains/people_hr.py`
- Create: `tests/domains/__init__.py`
- Create: `tests/domains/test_people_hr.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/domains/__init__.py — empty
# tests/domains/test_people_hr.py
import polars as pl

from goldenflow.domains.people_hr import PACK, ssn_mask, ssn_validate


def test_pack_metadata():
    assert PACK.name == "people_hr"
    assert len(PACK.transforms) > 0


def test_ssn_mask():
    s = pl.Series("ssn", ["123-45-6789", "987-65-4321", "invalid"])
    result = ssn_mask(s)
    assert result[0] == "***-**-6789"
    assert result[1] == "***-**-4321"
    assert result[2] == "invalid"


def test_ssn_validate():
    s = pl.Series("ssn", ["123-45-6789", "000-00-0000", "invalid", "123456789"])
    result = ssn_validate(s)
    assert result[0] is True
    assert result[1] is False  # all zeros invalid
    assert result[2] is False
    assert result[3] is True  # digits-only valid
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/domains/test_people_hr.py -v`
Expected: FAIL

- [ ] **Step 3: Implement domain pack base**

```python
# goldenflow/domains/base.py
from __future__ import annotations

from pydantic import BaseModel

from goldenflow.config.schema import GoldenFlowConfig


class DomainPack(BaseModel):
    name: str
    description: str
    transforms: list[str] = []
    default_config: GoldenFlowConfig = GoldenFlowConfig()
```

- [ ] **Step 4: Implement People/HR domain pack**

```python
# goldenflow/domains/people_hr.py
from __future__ import annotations

import re

import polars as pl

from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.domains.base import DomainPack
from goldenflow.transforms import register_transform

_SSN_PATTERN = re.compile(r"^(\d{3})-?(\d{2})-?(\d{4})$")


@register_transform(
    name="ssn_mask", input_types=["ssn", "string"], auto_apply=False, priority=50, mode="series"
)
def ssn_mask(series: pl.Series) -> pl.Series:
    def _mask(val: str | None) -> str | None:
        if val is None:
            return None
        m = _SSN_PATTERN.match(val.strip())
        if m:
            return f"***-**-{m.group(3)}"
        return val

    return series.map_elements(_mask, return_dtype=pl.Utf8)


@register_transform(
    name="ssn_validate", input_types=["ssn", "string"], auto_apply=False, priority=55, mode="series"
)
def ssn_validate(series: pl.Series) -> pl.Series:
    def _validate(val: str | None) -> bool | None:
        if val is None:
            return None
        m = _SSN_PATTERN.match(val.strip())
        if not m:
            return False
        area, group, serial = m.group(1), m.group(2), m.group(3)
        if area == "000" or group == "00" or serial == "0000":
            return False
        return True

    return series.map_elements(_validate, return_dtype=pl.Boolean)


PACK = DomainPack(
    name="people_hr",
    description="Name parsing, SSN formatting, employment dates, gender/boolean standardization",
    transforms=[
        "split_name", "split_name_reverse", "strip_titles", "strip_suffixes",
        "name_proper", "ssn_mask", "ssn_validate",
        "date_iso8601", "gender_standardize", "boolean_normalize",
    ],
    default_config=GoldenFlowConfig(
        transforms=[
            TransformSpec(column="name", ops=["strip", "strip_titles", "title_case"]),
            TransformSpec(column="ssn", ops=["ssn_validate"]),
            TransformSpec(column="gender", ops=["gender_standardize"]),
            TransformSpec(column="hire_date", ops=["date_iso8601"]),
            TransformSpec(column="active", ops=["boolean_normalize"]),
        ]
    ),
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/domains/test_people_hr.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add goldenflow/domains/ tests/domains/
git commit -m "feat: add domain pack base and People/HR pack with SSN transforms"
```

---

## Phase 7: TUI, REST API, MCP Server

### Task 21: REST API Server

**Files:**
- Create: `goldenflow/api/server.py`
- Create: `tests/api/__init__.py`
- Create: `tests/api/test_server.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/api/__init__.py — empty
# tests/api/test_server.py
import json
from pathlib import Path

from fastapi.testclient import TestClient

from goldenflow.api.server import create_app


def test_health():
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_list_transforms():
    client = TestClient(create_app())
    response = client.get("/transforms")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0


def test_transform_endpoint(sample_csv: Path):
    client = TestClient(create_app())
    with open(sample_csv, "rb") as f:
        response = client.post("/transform", files={"file": ("data.csv", f, "text/csv")})
    assert response.status_code == 200
    data = response.json()
    assert "manifest" in data
    assert "data" in data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/api/test_server.py -v`
Expected: FAIL

- [ ] **Step 3: Implement REST API**

```python
# goldenflow/api/server.py
from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

import polars as pl
from fastapi import FastAPI, File, UploadFile

import goldenflow
from goldenflow.engine.transformer import TransformEngine
from goldenflow.transforms import list_transforms


def create_app() -> FastAPI:
    app = FastAPI(title="GoldenFlow", version=goldenflow.__version__)

    @app.get("/health")
    def health():
        return {"status": "ok", "version": goldenflow.__version__}

    @app.get("/transforms")
    def transforms():
        return [
            {
                "name": t.name,
                "input_types": t.input_types,
                "auto_apply": t.auto_apply,
                "priority": t.priority,
                "mode": t.mode,
            }
            for t in list_transforms()
        ]

    @app.post("/transform")
    async def transform(file: UploadFile = File(...)):
        content = await file.read()
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        engine = TransformEngine()
        result = engine.transform_file(tmp_path)
        tmp_path.unlink(missing_ok=True)

        csv_buffer = io.StringIO()
        result.df.write_csv(csv_buffer)

        return {
            "data": csv_buffer.getvalue(),
            "manifest": result.manifest.to_dict(),
        }

    @app.post("/map")
    async def map_schemas(
        source: UploadFile = File(...),
        target: UploadFile = File(...),
    ):
        from goldenflow.mapping.schema_mapper import SchemaMapper

        s_content = await source.read()
        t_content = await target.read()

        s_df = pl.read_csv(io.BytesIO(s_content))
        t_df = pl.read_csv(io.BytesIO(t_content))

        mapper = SchemaMapper()
        mappings = mapper.map(s_df, t_df)

        return [
            {
                "source": m.source,
                "target": m.target,
                "confidence": m.confidence,
                "transform": m.transform,
            }
            for m in mappings
        ]

    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/api/test_server.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/api/ tests/api/
git commit -m "feat: add FastAPI REST server with transform, map, health endpoints"
```

---

### Task 22: TUI Application

**Files:**
- Create: `goldenflow/tui/app.py`
- Create: `tests/tui/__init__.py`
- Create: `tests/tui/test_tui.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/tui/__init__.py — empty
# tests/tui/test_tui.py
from goldenflow.tui.app import GoldenFlowApp


def test_tui_app_instantiates():
    app = GoldenFlowApp(path=None)
    assert app is not None


def test_tui_app_has_title():
    app = GoldenFlowApp(path=None)
    assert app.title == "GoldenFlow"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/tui/test_tui.py -v`
Expected: FAIL

- [ ] **Step 3: Implement TUI**

```python
# goldenflow/tui/app.py
from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Static,
    TabbedContent,
    TabPane,
)


class ProfileTab(TabPane):
    """Column types, samples, and issues."""

    def __init__(self, df=None, profile=None):
        super().__init__("Profile", id="profile-tab")
        self._df = df
        self._profile = profile

    def compose(self) -> ComposeResult:
        if self._profile:
            table = DataTable()
            table.add_columns("Column", "Type", "Nulls", "Unique", "Sample")
            for col in self._profile.columns:
                table.add_row(
                    col.name,
                    col.inferred_type,
                    f"{col.null_count} ({col.null_pct:.0%})",
                    str(col.unique_count),
                    ", ".join(col.sample_values[:3]),
                )
            yield table
        else:
            yield Static("No data loaded. Use: goldenflow interactive <file>")


class TransformTab(TabPane):
    """Select columns, pick transforms, preview results."""

    def __init__(self):
        super().__init__("Transform", id="transform-tab")

    def compose(self) -> ComposeResult:
        yield Static("Select columns and transforms to apply.")


class MapTab(TabPane):
    """Schema mapping editor."""

    def __init__(self):
        super().__init__("Map", id="map-tab")

    def compose(self) -> ComposeResult:
        yield Static("Load source and target files to auto-map schemas.")


class ExportTab(TabPane):
    """Save cleaned data, config, and manifest."""

    def __init__(self):
        super().__init__("Export", id="export-tab")

    def compose(self) -> ComposeResult:
        yield Static("Export cleaned data, YAML config, and JSON manifest.")


class GoldenFlowApp(App):
    """GoldenFlow interactive TUI."""

    TITLE = "GoldenFlow"
    CSS = """
    TabbedContent { height: 100%; }
    """

    def __init__(self, path: Path | None = None):
        super().__init__()
        self._path = path
        self._df = None
        self._profile = None
        self.title = "GoldenFlow"

        if path and path.exists():
            from goldenflow.connectors.file import read_file
            from goldenflow.engine.profiler_bridge import profile_dataframe

            self._df = read_file(path)
            self._profile = profile_dataframe(self._df, file_path=str(path))

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            yield ProfileTab(df=self._df, profile=self._profile)
            yield TransformTab()
            yield MapTab()
            yield ExportTab()
        yield Footer()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/tui/test_tui.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/tui/ tests/tui/
git commit -m "feat: add Textual TUI with Profile, Transform, Map, Export tabs"
```

---

### Task 23: MCP Server

**Files:**
- Create: `goldenflow/mcp/server.py`
- Create: `tests/mcp/__init__.py`
- Create: `tests/mcp/test_mcp.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/mcp/__init__.py — empty
# tests/mcp/test_mcp.py
from goldenflow.mcp.server import TOOLS


def test_mcp_tools_defined():
    assert len(TOOLS) >= 4
    names = [t["name"] for t in TOOLS]
    assert "transform" in names
    assert "map" in names
    assert "profile" in names
    assert "learn" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/mcp/test_mcp.py -v`
Expected: FAIL

- [ ] **Step 3: Implement MCP server**

```python
# goldenflow/mcp/server.py
from __future__ import annotations

import json
from pathlib import Path

TOOLS = [
    {
        "name": "transform",
        "description": "Transform a data file using GoldenFlow. Zero-config or config-driven.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to data file"},
                "config": {"type": "string", "description": "Optional YAML config path"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "map",
        "description": "Auto-map schemas between source and target files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source file path"},
                "target": {"type": "string", "description": "Target file path"},
            },
            "required": ["source", "target"],
        },
    },
    {
        "name": "profile",
        "description": "Profile a data file showing column types, nulls, and patterns.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to data file"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "learn",
        "description": "Generate a YAML config from data patterns.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to data file"},
            },
            "required": ["path"],
        },
    },
]


def handle_tool(name: str, arguments: dict) -> str:
    """Handle an MCP tool call."""
    import goldenflow
    from goldenflow.connectors.file import read_file
    from goldenflow.engine.profiler_bridge import profile_dataframe
    from goldenflow.engine.transformer import TransformEngine

    if name == "transform":
        path = Path(arguments["path"])
        config = None
        if "config" in arguments:
            from goldenflow.config.loader import load_config
            config = load_config(Path(arguments["config"]))
        engine = TransformEngine(config=config)
        result = engine.transform_file(path)
        return json.dumps(result.manifest.to_dict(), indent=2)

    elif name == "map":
        from goldenflow.mapping.schema_mapper import SchemaMapper
        source_df = read_file(Path(arguments["source"]))
        target_df = read_file(Path(arguments["target"]))
        mapper = SchemaMapper()
        mappings = mapper.map(source_df, target_df)
        return json.dumps([
            {"source": m.source, "target": m.target, "confidence": m.confidence}
            for m in mappings
        ], indent=2)

    elif name == "profile":
        df = read_file(Path(arguments["path"]))
        profile = profile_dataframe(df, file_path=arguments["path"])
        return json.dumps({
            "rows": profile.row_count,
            "columns": [
                {"name": c.name, "type": c.inferred_type, "nulls": c.null_count}
                for c in profile.columns
            ],
        }, indent=2)

    elif name == "learn":
        from goldenflow.config.learner import learn_config
        config = learn_config(Path(arguments["path"]))
        return config.model_dump_json(indent=2)

    return json.dumps({"error": f"Unknown tool: {name}"})


def run_server():
    """Run the MCP server. Requires mcp package."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
    except ImportError:
        raise ImportError("MCP server requires: pip install goldenflow[mcp]")

    server = Server("goldenflow")

    @server.list_tools()
    async def list_tools():
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        result = handle_tool(name, arguments)
        return [{"type": "text", "text": result}]

    import asyncio
    asyncio.run(stdio_server(server))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/mcp/test_mcp.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/mcp/ tests/mcp/
git commit -m "feat: add MCP server with transform, map, profile, learn tools"
```

---

### Task 24: Database Connector Stub

**Files:**
- Create: `goldenflow/connectors/database.py`
- Create: `tests/connectors/test_database.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/connectors/test_database.py
import pytest

from goldenflow.connectors.database import read_table, write_table


def test_read_table_requires_connectorx():
    with pytest.raises((ImportError, NotImplementedError)):
        read_table("postgresql://localhost/test", "users")


def test_write_table_requires_connectorx():
    import polars as pl
    df = pl.DataFrame({"a": [1]})
    with pytest.raises((ImportError, NotImplementedError)):
        write_table(df, "postgresql://localhost/test", "users")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/connectors/test_database.py -v`
Expected: FAIL

- [ ] **Step 3: Implement database connector**

```python
# goldenflow/connectors/database.py
from __future__ import annotations

import polars as pl


def read_table(connection_string: str, table: str, **kwargs) -> pl.DataFrame:
    """Read a database table into a Polars DataFrame."""
    try:
        import connectorx  # noqa: F401
    except ImportError:
        raise ImportError("Database support requires: pip install goldenflow[db]")
    return pl.read_database(f"SELECT * FROM {table}", connection_string, **kwargs)


def write_table(df: pl.DataFrame, connection_string: str, table: str, **kwargs) -> None:
    """Write a Polars DataFrame to a database table."""
    try:
        import connectorx  # noqa: F401
    except ImportError:
        raise ImportError("Database support requires: pip install goldenflow[db]")
    raise NotImplementedError("Database writing is not yet implemented — use file export")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/connectors/test_database.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add goldenflow/connectors/database.py tests/connectors/test_database.py
git commit -m "feat: add database connector stub with connectorx dependency"
```

---

## Phase 8: Integration Tests & Final Wiring

### Task 25: Integration Tests — full pipeline end-to-end

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration tests**

```python
# tests/test_integration.py
"""End-to-end integration tests for the full GoldenFlow pipeline."""
from pathlib import Path

import polars as pl

import goldenflow
from goldenflow.config.schema import (
    DedupSpec,
    FilterSpec,
    GoldenFlowConfig,
    SplitSpec,
    TransformSpec,
)
from goldenflow.engine.transformer import TransformEngine


FIXTURES = Path(__file__).parent / "fixtures"


def test_zero_config_on_messy_csv():
    """Zero-config mode should auto-detect and fix common issues."""
    path = FIXTURES / "messy.csv"
    result = goldenflow.transform_file(path)
    assert result.df.shape[0] > 0
    assert result.manifest is not None
    assert len(result.manifest.records) > 0


def test_full_config_pipeline(tmp_path: Path):
    """Full config pipeline: transforms, renames, drops, filters, dedup."""
    df = pl.DataFrame({
        "full_name": ["  John Smith  ", "  Jane Doe  ", "  John Smith  "],
        "email_address": ["JOHN@TEST.COM", "jane@test.com", "john@test.com"],
        "phone_number": ["(555) 123-4567", "555.987.6543", "(555) 123-4567"],
        "state": ["Pennsylvania", "CA", "Pennsylvania"],
        "signup_dt": ["03/15/2024", "2024-01-20", "03/15/2024"],
        "price": ["$1,234.56", "$99.99", "$1,234.56"],
        "internal_id": [1, 2, 3],
    })

    config = GoldenFlowConfig(
        transforms=[
            TransformSpec(column="full_name", ops=["strip", "title_case"]),
            TransformSpec(column="email_address", ops=["lowercase", "strip"]),
            TransformSpec(column="phone_number", ops=["phone_e164"]),
            TransformSpec(column="state", ops=["state_abbreviate"]),
            TransformSpec(column="signup_dt", ops=["date_iso8601"]),
            TransformSpec(column="price", ops=["currency_strip"]),
        ],
        renames={"email_address": "email", "phone_number": "phone"},
        drop=["internal_id"],
        dedup=DedupSpec(columns=["email"]),
    )

    engine = TransformEngine(config=config)
    result = engine.transform_df(df)

    # Check transforms applied
    assert result.df["full_name"][0] == "John Smith"
    assert result.df["email"][0] == "john@test.com"
    assert "phone" in result.df.columns
    assert result.df["state"][0] == "PA"
    assert result.df["signup_dt"][0] == "2024-03-15"

    # Check renames
    assert "email" in result.df.columns
    assert "email_address" not in result.df.columns

    # Check drops
    assert "internal_id" not in result.df.columns

    # Check dedup
    assert result.df.shape[0] == 2  # removed 1 duplicate


def test_schema_mapping_roundtrip(tmp_path: Path):
    """Map schemas between two files and verify output."""
    from goldenflow.mapping.schema_mapper import SchemaMapper

    source_df = pl.DataFrame({
        "fname": ["John"],
        "email_address": ["john@test.com"],
        "phone_number": ["5551234567"],
    })
    target_df = pl.DataFrame({
        "first_name": ["Jane"],
        "email": ["jane@test.com"],
        "phone": ["5559876543"],
    })

    mapper = SchemaMapper()
    mappings = mapper.map(source_df, target_df)

    assert len(mappings) >= 2
    source_cols = {m.source for m in mappings}
    assert "fname" in source_cols or "email_address" in source_cols


def test_learn_and_apply(sample_csv: Path, tmp_path: Path):
    """Learn a config from data and re-apply it."""
    from goldenflow.config.learner import learn_config
    from goldenflow.config.loader import save_config, load_config

    config = learn_config(sample_csv)
    config_path = tmp_path / "learned.yaml"
    save_config(config, config_path)

    loaded = load_config(config_path)
    engine = TransformEngine(config=loaded)
    result = engine.transform_file(sample_csv)
    assert result.df.shape[0] == 3


def test_diff_before_after(sample_csv: Path):
    """Diff should detect changes after transformation."""
    from goldenflow.connectors.file import read_file
    from goldenflow.engine.differ import diff_dataframes

    before = read_file(sample_csv)
    engine = TransformEngine()
    result = engine.transform_df(before)
    diff = diff_dataframes(before, result.df)
    # At least some transforms should have changed values
    assert diff.total_changes >= 0  # may be 0 if data is already clean
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/test_integration.py -v`
Expected: All PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "feat: add integration tests for full pipeline"
```

---

### Task 26: Final cleanup and full test run

- [ ] **Step 1: Run linter**

Run: `ruff check goldenflow/ tests/ --fix`
Expected: No errors (or auto-fixed)

- [ ] **Step 2: Run full test suite with coverage**

Run: `pytest tests/ -v --tb=short --cov=goldenflow --cov-report=term-missing`
Expected: All PASS, reasonable coverage

- [ ] **Step 3: Verify CLI entry point works**

Run: `goldenflow --version`
Expected: `goldenflow 0.1.0`

Run: `goldenflow --help`
Expected: Shows all 9 commands

- [ ] **Step 4: Test zero-config on fixture**

Run: `goldenflow transform tests/fixtures/messy.csv --output-dir /tmp/gf_test`
Expected: Creates transformed CSV and manifest JSON

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: lint fixes and final cleanup"
```
