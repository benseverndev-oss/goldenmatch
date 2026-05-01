"""03 — multi-source customer 360.

Unify customers across heterogeneous sources (CRM + warehouse + support):
  1. InferMap aligns each source's columns to a canonical schema.
  2. GoldenFlow standardizes each.
  3. Concatenate with a __source__ provenance column.
  4. GoldenMatch with multi-pass blocking dedupes the union.

Run:
    pip install infermap goldenflow goldenmatch polars
    python 03_multi_source_unify.py
"""
from __future__ import annotations

from pathlib import Path

import goldenflow
import goldenmatch
import infermap
import polars as pl
from goldenmatch.config.schemas import (
    BlockingConfig, BlockingKeyConfig,
    GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
)


# Toy data: three sources with differently-named columns.
SOURCES = {
    "crm": pl.DataFrame({
        "fname":  ["Jane", "Bob", "Alice", "John"],
        "lname":  ["Smith", "Jones", "Lee", "Doe"],
        "email":  ["jane@example.com", "bob@example.com", "alice@example.com",
                   "john@example.com"],
    }),
    "warehouse": pl.DataFrame({
        "first_name": ["Jane", "Robert", "Alice"],
        "last_name":  ["Smith", "Jones", "Li"],
        "email":      ["jane@example.com", "bob@example.com", "alice@example.com"],
    }),
    "support": pl.DataFrame({
        "given_name":  ["Jane", "Alicia"],
        "family_name": ["Smithe", "Lee"],
        "email_addr":  ["jane@example.com", "alice@example.com"],
    }),
}

# Stand-in canonical schema; the real version lives in YAML.
CANONICAL_FIELDS = ["first_name", "last_name", "email"]


def align(name: str, df: pl.DataFrame) -> pl.DataFrame:
    """InferMap → rename + drop unmapped → tag with source."""
    mapping_result = infermap.map(
        source=df.head(100).to_dicts(),
        target_fields=CANONICAL_FIELDS,
    )
    mapping = {m.source: m.target for m in mapping_result.mappings}
    print(f"  {name}: mapping {mapping}")

    keep = [c for c in df.columns if c in mapping]
    return df.select(keep).rename(mapping).with_columns(pl.lit(name).alias("__source__"))


def main() -> None:
    print("aligning sources to canonical schema:")
    aligned = [align(name, df) for name, df in SOURCES.items()]
    union = pl.concat(aligned, how="diagonal_relaxed")
    print(f"unified rows: {union.height}")

    # Standardize messy fields after the union.
    cleaned = goldenflow.transform_df(union).df

    # Multi-pass blocking — email is the strong key, last-name soundex catches typos.
    config = GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase", "strip"])],
            passes=[
                BlockingKeyConfig(fields=["email"],     transforms=["lowercase", "strip"]),
                BlockingKeyConfig(fields=["last_name"], transforms=["soundex"]),
            ],
        ),
        matchkeys=[MatchkeyConfig(
            name="identity", type="weighted", threshold=0.80,
            fields=[
                MatchkeyField(field="first_name", scorer="ensemble",     weight=0.7,
                              transforms=["lowercase", "strip"]),
                MatchkeyField(field="last_name",  scorer="ensemble",     weight=0.9,
                              transforms=["lowercase", "strip"]),
                MatchkeyField(field="email",      scorer="jaro_winkler", weight=1.0,
                              transforms=["lowercase", "strip"]),
            ],
        )],
    )
    result = goldenmatch.dedupe_df(cleaned, config=config)

    print(f"\n{result.total_clusters} canonical entities from {result.total_records} input rows")
    if result.golden is not None:
        out = Path("unified.csv")
        result.golden.write_csv(out)
        print(f"wrote → {out}")


if __name__ == "__main__":
    main()
