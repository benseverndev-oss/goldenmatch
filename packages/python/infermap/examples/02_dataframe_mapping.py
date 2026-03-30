"""Example 2: Map in-memory DataFrames.

Works with both Polars and Pandas — infermap auto-detects
the type and returns the same type from apply().
"""

import polars as pl
import infermap

# --- Polars example ---
print("=== Polars DataFrames ===\n")

source = pl.DataFrame({
    "fname": ["John", "Jane", "Bob"],
    "lname": ["Doe", "Smith", "Johnson"],
    "email_addr": ["john@acme.com", "jane@globex.com", "bob@initech.com"],
    "tel": ["555-0100", "(555) 020-0200", "+15550300"],
    "zipcode": ["10001", "90210", "30301"],
})

target = pl.DataFrame({
    "first_name": ["Alice"],
    "last_name": ["Williams"],
    "email": ["alice@example.com"],
    "phone": ["555-9999"],
    "zip_code": ["00000"],
})

result = infermap.map(source, target)

for m in result.mappings:
    print(f"  {m.source:15s}  ->  {m.target:15s}  ({m.confidence:.3f})")

# Apply the mapping to rename columns
remapped = result.apply(source)
print(f"\nOriginal columns:  {source.columns}")
print(f"Remapped columns:  {remapped.columns}")
print(f"\nRemapped data (first 3 rows):")
print(remapped.head(3).to_pandas().to_string(index=False))

# --- Pandas example ---
print("\n=== Pandas DataFrames ===\n")

try:
    import pandas as pd

    src_pd = pd.DataFrame({
        "email_address": ["a@b.com", "x@y.com"],
        "telephone": ["555-1111", "555-2222"],
    })
    tgt_pd = pl.DataFrame({
        "email": ["test@test.com"],
        "phone": ["555-0000"],
    })

    result_pd = infermap.map(src_pd, tgt_pd)
    remapped_pd = result_pd.apply(src_pd)

    print(f"Input type:  {type(src_pd).__name__}")
    print(f"Output type: {type(remapped_pd).__name__}  (preserved!)")
    print(f"Columns:     {list(remapped_pd.columns)}")
except ImportError:
    print("(pandas not installed — skipping Pandas example)")
