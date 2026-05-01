"""Map schemas between two DataFrames with different column names.

GoldenFlow's SchemaMapper uses name similarity and profile similarity
to automatically map columns from a source schema to a target schema.

Usage:
    pip install goldenflow
    python examples/schema_mapping.py
"""
from __future__ import annotations

import polars as pl


if __name__ == "__main__":
    from goldenflow import SchemaMapper

    # Source has different column names than target
    source_df = pl.DataFrame({
        "fname": ["John", "Jane"],
        "lname": ["Smith", "Doe"],
        "mail": ["john@test.com", "jane@test.com"],
        "tel": ["555-1234", "555-5678"],
    })

    target_df = pl.DataFrame({
        "first_name": [""],
        "last_name": [""],
        "email": [""],
        "phone": [""],
    })

    mapper = SchemaMapper()
    mappings = mapper.map(source_df, target_df)

    print("Schema Mapping:")
    for m in mappings:
        print(f"  {m.source} -> {m.target} (confidence: {m.confidence:.0%})")
