"""Shared test fixtures and helpers for infermap tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from infermap.types import FieldInfo, SchemaInfo

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CRM_EXPORT_CSV = FIXTURES_DIR / "crm_export.csv"
CANONICAL_CUSTOMERS_CSV = FIXTURES_DIR / "canonical_customers.csv"


def make_field(
    name: str,
    dtype: str = "string",
    sample_values: list[str] | None = None,
    null_rate: float = 0.0,
    unique_rate: float = 1.0,
    value_count: int = 5,
    metadata: dict | None = None,
) -> FieldInfo:
    """Factory helper to create a FieldInfo with sensible defaults."""
    return FieldInfo(
        name=name,
        dtype=dtype,
        sample_values=sample_values or [],
        null_rate=null_rate,
        unique_rate=unique_rate,
        value_count=value_count,
        metadata=metadata or {},
    )


def make_schema(
    field_names: list[str],
    source_name: str = "test_source",
    dtypes: dict[str, str] | None = None,
) -> SchemaInfo:
    """Factory helper to create a SchemaInfo from a list of field names."""
    dtypes = dtypes or {}
    fields = [make_field(name, dtype=dtypes.get(name, "string")) for name in field_names]
    return SchemaInfo(fields=fields, source_name=source_name)


@pytest.fixture
def crm_export_path() -> Path:
    return CRM_EXPORT_CSV


@pytest.fixture
def canonical_customers_path() -> Path:
    return CANONICAL_CUSTOMERS_CSV


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR
