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
