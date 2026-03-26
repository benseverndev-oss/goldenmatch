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
    phones = [
        f"555-{random.randint(100, 999)}-{random.randint(1000, 9999)}"
        for _ in range(n)
    ]
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
