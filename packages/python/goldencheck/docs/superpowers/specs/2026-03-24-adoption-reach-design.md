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
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}  # only needed if llm-boost: true
```

### Behavior

1. **Install** — installs `goldencheck` via pip, caches the pip download cache (`~/.cache/pip`) keyed on `python-version + goldencheck-version`
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

**LLM API keys:** When `llm-boost` is enabled, the user must pass the API key via `env:` block (e.g., `ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}`). The action does not have a separate `api-key` input — it reads from the environment like the CLI does.

### Outputs

| Output | Description |
|--------|-------------|
| `errors` | Total error count |
| `warnings` | Total warning count |
| `health-grade` | Worst health grade across files |

### JSON Contract

The action parses the output of `goldencheck scan --json`, which uses the existing `json_reporter.py` schema. This schema is treated as a stable contract — changes to the JSON reporter must maintain backward compatibility.

---

## 2. `goldencheck diff` Command

### Purpose

Compare two versions of a data file and report schema changes, distribution shifts, and new/resolved findings.

### CLI Signature

```python
@app.command()
def diff(
    file: Path = typer.Argument(..., help="Data file to compare."),
    file2: Optional[Path] = typer.Argument(None, help="Second file (omit to compare against git)."),
    ref: Optional[str] = typer.Option(None, "--ref", help="Git ref to compare against (default: HEAD)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
```

**Precedence rules:**
- `file2` provided → two-file mode (ignore `--ref` even if passed)
- `file2` not provided, `--ref` provided → git mode with specified ref
- `file2` not provided, `--ref` not provided → git mode with HEAD
- If git mode and file is not tracked → error: "File not tracked in git. Provide a second file to compare."

**Note:** `diff` is a standard `@app.command()` Typer subcommand. The hand-rolled fallback parser in `main()` only routes to `scan` — Typer handles `diff` before the fallback fires, so no changes to the arg parser are needed.

### CLI Interface

```bash
goldencheck diff data.csv                  # auto: compare against git HEAD
goldencheck diff old.csv new.csv           # explicit: two files
goldencheck diff data.csv --ref HEAD~3     # compare against specific git commit
goldencheck diff data.csv --ref main       # compare against a branch
goldencheck diff data.csv --json           # machine-readable output
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
| Findings | New findings | In new but not in old (by `(column, check, severity)` tuple) |
| Findings | Resolved findings | In old but not in new |
| Findings | Worsened findings | Same `(column, check)` but higher severity or more affected rows |

**Finding matching key:** `(column, check, severity)` for exact matches. For worsened/improved detection, match by `(column, check)` and compare severity + affected_rows. When multiple findings share the same `(column, check)`, compare them pairwise by severity descending.

---

## 3. `goldencheck watch` Command

### Purpose

Poll a directory for data file changes and re-scan when files are modified.

### CLI Interface

```bash
goldencheck watch data/                        # poll every 60s (default)
goldencheck watch data/ --interval 30          # poll every 30s
goldencheck watch data/ --json                 # JSON output per scan
goldencheck watch data/ --exit-on error        # exit 1 on first error found (CI mode)
goldencheck watch data/ --pattern "*.csv"      # only watch CSV files
```

### Behavior

1. **Initial scan** — scans all matching files on startup
2. **Poll loop** — checks file modification times every `--interval` seconds
3. **Re-scan** — only re-scans files whose mtime changed since last check
4. **Output** — prints findings summary per scan (timestamp + file + findings count)
5. **Exit modes:**
   - **Ctrl+C / SIGINT / SIGTERM** — graceful shutdown, returns exit code from the last completed scan (0 if all clean, 1 if errors found)
   - **`--exit-on <level>`** — exits immediately when a scan produces findings at or above the specified severity. Designed for CI pipelines that want to fail fast.
   - **No `--exit-on`** — runs indefinitely until interrupted (monitoring mode)

### Architecture

- **New file:** `goldencheck/engine/watcher.py`
- **Entry point:** `watch_directory(path, interval, pattern, exit_on) -> int` (returns exit code)
- Uses `pathlib.glob()` for pattern matching and `os.stat().st_mtime` for change detection
- No external dependencies (no watchdog)
- Tracks `{path: last_mtime}` dict, only re-scans changed files
- Registers `signal.signal(SIGINT, ...)` and `signal.signal(SIGTERM, ...)` for graceful shutdown

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
- **Package type:** dbt package with a Python helper script

### Execution Mechanism

dbt macros (Jinja SQL) cannot invoke shell commands or write to the filesystem. The actual execution uses a **wrapper script** approach:

1. **dbt custom test macro** (`goldencheck_test.sql`) generates a SQL query: `SELECT * FROM {{ model }} LIMIT {{ sample_size }}`
2. **Python helper** (`scripts/run_goldencheck.py`) is invoked as a dbt `pre-hook` or `run-operation`:
   - Connects to the warehouse via dbt's Python adapter (`dbt.adapters`)
   - Executes the sample query
   - Writes results to a temp CSV via Polars
   - Runs `goldencheck scan <temp.csv> --no-tui --json`
   - Returns pass/fail

**Alternative for dbt-core 1.8+:** Use a Python model (`model.py`) that calls GoldenCheck directly via the Python API (`from goldencheck import scan_file`), avoiding the CSV round-trip entirely. This is the preferred path for newer dbt versions.

### Type Fidelity Note

Writing query results to CSV loses some type information (timestamps become strings, decimals may lose precision). GoldenCheck's type inference profiler will re-detect types from the CSV, which may produce false-positive `type_inference` findings for columns that were originally typed in the warehouse. This is acceptable — the type inference finding is still useful ("this column looks numeric but is stored as string in the CSV") and can be suppressed via `goldencheck.yml` if needed.

### Dependencies

- `goldencheck` must be installed in the same Python environment as dbt
- No separate database drivers — uses dbt's existing adapter connections

### Files

```
dbt-goldencheck/
├── macros/
│   └── goldencheck_test.sql     # Custom test macro
├── scripts/
│   └── run_goldencheck.py       # Python helper for CLI invocation
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
| GitHub Action | Self-testing workflow in the action repo: matrix of scenarios (no errors, errors found, PR comment update) triggered on PR |
| `diff` | Unit tests for DiffReport + integration tests with two fixture CSVs + git-mode test using tmp repo |
| `watch` | Unit test for change detection logic + integration test with tmp_path (modify file, verify re-scan) |
| dbt adapter | Integration test with DuckDB + dbt-core in CI |

## Execution Order

Ship in order: GitHub Action → diff → watch → dbt. Each is independently useful and doesn't depend on the others.

## Version

GitHub Action: v1. `diff` and `watch` ship as part of GoldenCheck v0.5.0. `dbt-goldencheck` is a separate package at v0.1.0.
