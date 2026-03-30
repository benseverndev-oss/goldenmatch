"""Example 4: Use a YAML schema definition for precise control.

Schema files let you declare aliases, types, and required fields
for your target schema. This gives the scorers stronger signals
and generates warnings when required fields can't be mapped.
"""

import infermap

# Map healthcare records using a schema definition file
result = infermap.map(
    "data/healthcare_records.csv",
    "data/healthcare_records.csv",  # target is inferred from schema file
    schema_file="data/patient_schema.yaml",
    required=["patient_id", "full_name", "mrn"],
)

print("=== Healthcare Record Mapping (with schema file) ===\n")
for m in result.mappings:
    print(f"  {m.source:25s}  ->  {m.target:20s}  ({m.confidence:.3f})")

# Show per-scorer breakdown for one interesting mapping
print("\n=== Detailed Breakdown: PatientName -> full_name ===\n")
for m in result.mappings:
    if m.target == "full_name":
        for scorer_name, sr in m.breakdown.items():
            print(f"  {scorer_name:25s}  score={sr.score:.3f}  {sr.reasoning}")
        break

# Show warnings for required fields
if result.warnings:
    print("\n=== Warnings ===")
    for w in result.warnings:
        print(f"  {w}")
else:
    print("\nAll required fields mapped successfully.")

# Show the full JSON report
print("\n=== JSON Report (first mapping) ===\n")
import json
report = result.report()
print(json.dumps(report["mappings"][0], indent=2))
