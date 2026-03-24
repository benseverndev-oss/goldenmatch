# Auto Fix

Apply automated fixes to clean data quality issues.

## Usage

```bash
goldencheck fix data.csv                          # safe mode (default)
goldencheck fix data.csv --mode moderate          # + case standardization
goldencheck fix data.csv --mode aggressive --force # + type coercion
goldencheck fix data.csv --dry-run                # preview without writing
goldencheck fix data.csv -o cleaned.csv           # custom output path
```

## Modes

| Mode | Fixes | Risk |
|------|-------|------|
| `safe` (default) | Trim whitespace, remove invisible chars, normalize Unicode, fix smart quotes/encoding | Zero data loss |
| `moderate` | Safe + standardize enum case (match dominant casing) | Minimal — formatting only |
| `aggressive` | Moderate + coerce string→numeric types. Requires `--force` | Data modification |

## Output

```
Fixes applied (safe mode):
  name: trim_whitespace (150 rows)
  description: remove_invisible_chars (3 rows)
  notes: fix_smart_quotes (12 rows)

Total: 165 row-fixes across 3 operations
Written to: data_fixed.csv
```

## Behavior

- Default output: `{filename}_fixed.csv`
- Never overwrites the input file
- `--dry-run` shows the fix summary without writing
- If no fixes needed: prints "No issues found — file is clean"
- Excel input → CSV output (with warning)
