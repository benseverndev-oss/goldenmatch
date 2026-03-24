# Adoption & Reach — Design Spec

## Goal

Ship four features that get GoldenCheck into production pipelines: a GitHub Action for CI, a `diff` command for schema drift, a `watch` command for continuous monitoring, and a dbt integration for the data engineering ecosystem.

## Scope

Four independent sub-projects, executed in order:

1. GitHub Action (`benzsevern/goldencheck-action@v1`)
2. `goldencheck diff` command
3. `goldencheck watch` command
4. `dbt-goldencheck` test adapter

---

## 1. GitHub Action

### Purpose

A feature-rich GitHub Action that validates data files in CI, posts PR comments with findings summaries, and provides a pass/fail status check.

### Usage

```yaml
- uses: benzsevern/goldencheck-action@v1
  with:
    files: "data/*.csv"
    fail-on: error          # optional, default: error
    config: goldencheck.yml # optional
    llm-boost: false        # optional
    llm-provider: anthropic # optional
```

### Behavior

1. **Install** — installs `goldencheck` (with caching for speed)
2. **Scan** — runs `goldencheck scan <file> --no-tui --json` on each matching file
3. **Status check** — pass/fail based on `fail-on` threshold
4. **PR comment** — posts a single comment summarizing all findings:

```
## GoldenCheck Results

| File | Health | Errors | Warnings | Grade |
|------|--------|--------|----------|-------|
| data/orders.csv | 82 | 2 | 5 | B |
| data/customers.csv | 95 | 0 | 1 | A |

**3 files scanned, 2 errors, 6 warnings**

<details><summary>Top findings</summary>

- ERROR [orders.csv → email] 6% malformed emails (120 rows)
- ERROR [orders.csv → age] Values outside [0, 120]: -5, 999
- WARN [customers.csv → status] 3 case variants: active, Active, ACTIVE

</details>
```

5. **Updates existing comment** — on subsequent pushes, updates the same comment instead of creating a new one (uses a hidden marker `<!-- goldencheck -->`)

### Architecture

- **Repo:** `benzsevern/goldencheck-action` (separate repo)
- **Type:** Composite action (runs: composite) — no Docker build needed
- **Files:** `action.yml` + `scripts/run.sh` + `scripts/comment.py`
- `run.sh` — installs goldencheck, expands glob, runs scans, collects JSON
- `comment.py` — parses JSON results, formats markdown, posts/updates PR comment via GitHub API

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `files` | yes | — | Glob pattern for data files |
| `fail-on` | no | `error` | Severity threshold: `error` or `warning` |
| `config` | no | — | Path to goldencheck.yml |
| `llm-boost` | no | `false` | Enable LLM enhancement |
| `llm-provider` | no | `anthropic` | LLM provider |
| `python-version` | no | `3.12` | Python version to use |

### Outputs

| Output | Description |
|--------|-------------|
| `errors` | Total error count |
| `warnings` | Total warning count |
| `health-grade` | Worst health grade across files |

---

## 2. `goldencheck diff` Command

### Purpose

Compare two versions of a data file and report schema changes, distribution shifts, and new/resolved findings.

### CLI Interface

```bash
goldencheck diff data.csv                  # auto: compare against git HEAD
goldencheck diff old.csv new.csv           # explicit: two files
goldencheck diff data.csv --ref HEAD~3     # compare against specific git commit
goldencheck diff data.csv --ref main       # compare against a branch
```

### Auto-Detection

When given a single file, `diff` checks if the file is tracked in git:
- **In git with history:** compares current file against the version at HEAD (or `--ref`)
- **Not in git / new file:** prints error asking for two files

### Output

```
goldencheck diff — data.csv (current vs HEAD)

Schema changes:
  + new_column (added)
  - old_column (removed)
  ~ status: type changed String → Int64

Finding changes:
  NEW   [email] 12 malformed emails (was 0)
  FIXED [age] range violation resolved
  WORSE [status] 3 → 7 case variants

Stats:
  Rows: 10,000 → 10,500 (+5%)
  Columns: 12 → 13 (+1)
  Health: B (82) → C (71) ↓
```

### Architecture

- **New file:** `goldencheck/engine/differ.py`
- **Entry point:** `diff_files(old_df, new_df, old_findings, new_findings) -> DiffReport`
- **DiffReport dataclass:** schema_changes, finding_changes, stat_changes
- Git integration: uses `git show <ref>:<path>` to read the old version, writes to a temp file, scans both
- The `diff` command runs `scan_file()` on both versions and compares results

### What It Compares

| Category | What | How |
|----------|------|-----|
| Schema | Added/removed columns | Set difference on column names |
| Schema | Type changes | Compare inferred types per column |
| Stats | Row count change | Simple comparison |
| Stats | Health score change | Compare grades |
| Findings | New findings | In new but not in old (by column+check) |
| Findings | Resolved findings | In old but not in new |
| Findings | Worsened findings | Same column+check but higher severity or more rows |

---

## 3. `goldencheck watch` Command

### Purpose

Poll a directory for data file changes and re-scan when files are modified.

### CLI Interface

```bash
goldencheck watch data/                        # poll every 60s (default)
goldencheck watch data/ --interval 30          # poll every 30s
goldencheck watch data/ --json                 # JSON output per scan
goldencheck watch data/ --fail-on error        # exit 1 on first error found
goldencheck watch data/ --pattern "*.csv"      # only watch CSV files
```

### Behavior

1. **Initial scan** — scans all matching files on startup
2. **Poll loop** — checks file modification times every `--interval` seconds
3. **Re-scan** — only re-scans files whose mtime changed since last check
4. **Output** — prints findings summary per scan (timestamp + file + findings count)
5. **Exit** — Ctrl+C to stop, or `--fail-on` exits on first threshold breach

### Architecture

- **New file:** `goldencheck/engine/watcher.py`
- **Entry point:** `watch_directory(path, interval, pattern, fail_on) -> None`
- Uses `pathlib.glob()` for pattern matching and `os.stat().st_mtime` for change detection
- No external dependencies (no watchdog)
- Tracks `{path: last_mtime}` dict, only re-scans changed files

### Output Format

```
[14:30:15] Watching data/ (*.csv, *.parquet) — polling every 30s
[14:30:15] Scanned data/orders.csv — 3 errors, 5 warnings (B)
[14:30:15] Scanned data/customers.csv — 0 errors, 1 warning (A)
[14:31:45] data/orders.csv changed — re-scanning...
[14:31:46] Scanned data/orders.csv — 2 errors, 5 warnings (B) [1 error fixed]
```

---

## 4. `dbt-goldencheck` Test Adapter

### Purpose

A dbt package that adds a `goldencheck` test type. Users add one line to their schema.yml and get zero-config data validation on any model.

### Usage

```yaml
# packages.yml
packages:
  - package: benzsevern/dbt-goldencheck
    version: ">=0.1.0"
```

```yaml
# models/schema.yml
models:
  - name: orders
    tests:
      - goldencheck              # zero-config
      - goldencheck:             # with options
          fail_on: error
          sample_size: 50000
```

### Architecture

- **Separate repo:** `benzsevern/dbt-goldencheck`
- **Package type:** dbt package (installable via `packages.yml`)
- **How it works:**
  1. dbt test calls the custom test macro
  2. Macro runs `SELECT * FROM {{ model }} LIMIT {{ sample_size }}` → writes to temp CSV
  3. Calls `goldencheck scan <temp.csv> --no-tui --json --fail-on <level>`
  4. Parses JSON output, returns pass/fail to dbt
- **Dependencies:** `goldencheck` must be installed in the Python environment alongside dbt
- **Warehouse support:** Works with any warehouse dbt supports (Postgres, BigQuery, Snowflake, DuckDB, etc.) since it pulls a sample to CSV

### Files

```
dbt-goldencheck/
├── macros/
│   └── goldencheck_test.sql     # Custom test macro
├── dbt_project.yml              # Package metadata
├── README.md
└── integration_tests/           # Test project with DuckDB
    ├── dbt_project.yml
    ├── models/
    │   └── test_model.sql
    └── tests/
        └── test_goldencheck.yml
```

---

## Testing Strategy

| Component | Test Approach |
|-----------|---------------|
| GitHub Action | Manual test with a test repo + workflow_dispatch |
| `diff` | Unit tests for DiffReport + integration test with two fixture CSVs |
| `watch` | Unit test for change detection logic + integration test with tmp_path |
| dbt adapter | Integration test with DuckDB + dbt-core |

## Execution Order

Ship in order: GitHub Action → diff → watch → dbt. Each is independently useful and doesn't depend on the others.

## Version

GitHub Action: v1. `diff` and `watch` ship as part of GoldenCheck (next minor). `dbt-goldencheck` is a separate package at v0.1.0.
