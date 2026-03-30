"""Example 1: Basic file-to-file mapping.

Maps a messy CRM export to a clean ERP customer schema.
This is the most common use case — you have data from one system
and need to figure out which columns correspond to another.
"""

import infermap

# Map CRM columns to ERP columns
result = infermap.map("data/crm_export.csv", "data/erp_customers.csv")

# Show what matched
print("=== Column Mappings ===\n")
for m in result.mappings:
    print(f"  {m.source:20s}  ->  {m.target:20s}  (confidence: {m.confidence:.3f})")
    print(f"    Reasoning: {m.reasoning}\n")

# Show what didn't match
if result.unmapped_source:
    print(f"Unmapped source columns: {result.unmapped_source}")
if result.unmapped_target:
    print(f"Unmapped target columns: {result.unmapped_target}")

# Show warnings
for w in result.warnings:
    print(f"WARNING: {w}")

print(f"\nCompleted in {result.metadata.get('elapsed_seconds', '?')}s")
