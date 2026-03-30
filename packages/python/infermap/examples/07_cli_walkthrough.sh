#!/bin/bash
# Example 7: CLI walkthrough — copy-paste these commands to try infermap.
# Run from the examples/ directory.

echo "=== 1. Inspect a source file ==="
echo "Shows column names, types, null rates, and sample values"
echo ""
infermap inspect data/crm_export.csv
echo ""

echo "=== 2. Map source to target ==="
echo "Finds the best column-to-column alignment"
echo ""
infermap map data/crm_export.csv data/erp_customers.csv
echo ""

echo "=== 3. Map with JSON output ==="
echo "Machine-readable output with per-scorer breakdown"
echo ""
infermap map data/crm_export.csv data/erp_customers.csv --format json
echo ""

echo "=== 4. Save mapping to YAML ==="
echo "Reusable config file for production pipelines"
echo ""
infermap map data/crm_export.csv data/erp_customers.csv -o /tmp/mapping.yaml
cat /tmp/mapping.yaml
echo ""

echo "=== 5. Apply saved mapping ==="
echo "Remap a file using a saved config (no inference needed)"
echo ""
infermap apply data/crm_export.csv --config /tmp/mapping.yaml -o /tmp/remapped.csv
head -3 /tmp/remapped.csv
echo ""

echo "=== 6. Validate with required fields ==="
echo "Check that required target fields are mapped"
echo ""
infermap validate data/crm_export.csv --config /tmp/mapping.yaml --required email,phone
echo ""

echo "=== 7. Strict validation (CI gate) ==="
echo "Exits code 1 if required fields are missing"
echo ""
infermap validate data/crm_export.csv --config /tmp/mapping.yaml --required email,phone,ssn --strict
echo "Exit code: $?"
echo ""

# Cleanup
rm -f /tmp/mapping.yaml /tmp/remapped.csv
echo "Done!"
