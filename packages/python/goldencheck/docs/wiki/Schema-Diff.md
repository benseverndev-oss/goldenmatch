# Schema Diff

Compare two versions of a data file to detect schema changes, finding changes, and stat deltas.

## Usage

```bash
# Auto-detect: compare against git HEAD
goldencheck diff data.csv

# Explicit: two files
goldencheck diff old.csv new.csv

# Compare against a specific git ref
goldencheck diff data.csv --ref HEAD~3
goldencheck diff data.csv --ref main

# JSON output
goldencheck diff data.csv --json
```

## Output

```
goldencheck diff — data.csv (current vs HEAD)

Schema changes:
  + new_column (String)
  - old_column (Int64)
  ~ status: String -> Int64

Finding changes:
  NEW   [email] 12 malformed emails
  FIXED [age] range violation resolved
  WORSE [status] 3 → 7 case variants

Stats:
  Rows: 10,000 -> 10,500 (+5%)
  Columns: 12 -> 13 (+1)
```

## Git Integration

When given a single file, `diff` auto-detects if the file is tracked in git:
- **In git:** compares against HEAD (or `--ref`)
- **Not in git:** prints an error asking for a second file

## What It Compares

| Category | Detection |
|----------|-----------|
| Schema | Added/removed columns, type changes |
| Findings | New issues, resolved issues, worsened/improved |
| Stats | Row count changes, column count changes |
