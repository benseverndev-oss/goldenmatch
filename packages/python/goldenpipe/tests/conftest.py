"""Shared test fixtures for GoldenPipe."""
import pytest
import polars as pl
from pathlib import Path


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    """5-row CSV with duplicates for pipeline testing."""
    data = (
        "name,email,phone\n"
        "John Smith,john@example.com,555-1234\n"
        "John Smith,jsmith@example.com,5551234\n"
        "John Smith,john.smith@example.com,555-1234\n"
        "Jane Doe,jane@example.com,555-5678\n"
        "Bob Jones,bob@example.com,555-9012\n"
    )
    p = tmp_path / "customers.csv"
    p.write_text(data)
    return p


@pytest.fixture
def sample_df() -> pl.DataFrame:
    """5-row DataFrame matching sample_csv."""
    return pl.DataFrame({
        "name": ["John Smith", "John Smith", "John Smith", "Jane Doe", "Bob Jones"],
        "email": ["john@example.com", "jsmith@example.com", "john.smith@example.com",
                   "jane@example.com", "bob@example.com"],
        "phone": ["555-1234", "5551234", "555-1234", "555-5678", "555-9012"],
    })
