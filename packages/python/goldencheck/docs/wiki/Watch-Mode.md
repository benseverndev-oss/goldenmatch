# Watch Mode

Continuously monitor a directory for data file changes.

## Usage

```bash
goldencheck watch data/                        # poll every 60s (default)
goldencheck watch data/ --interval 30          # poll every 30s
goldencheck watch data/ --pattern "*.csv"      # only CSV files
goldencheck watch data/ --exit-on error        # CI: fail on first error
goldencheck watch data/ --json                 # JSON output per scan
```

## Behavior

1. **Initial scan** — scans all matching files on startup
2. **Poll loop** — checks file modification times every N seconds
3. **Re-scan** — only re-scans files whose mtime changed
4. **Ctrl+C** — graceful shutdown, returns last scan's exit code

## CI Mode

Use `--exit-on` for pipelines that should fail fast:

```bash
goldencheck watch data/ --exit-on error --interval 10
```

Exits with code 1 as soon as any scan produces an error.

## Output

```
[14:30:15] Watching data/ (*.csv, *.parquet) — polling every 30s
[14:30:15] Scanned orders.csv — 3 errors, 5 warnings (B)
[14:30:15] Scanned customers.csv — 0 errors, 1 warning (A)
[14:31:45] orders.csv changed — re-scanning...
[14:31:46] Scanned orders.csv — 2 errors, 5 warnings (B)
```
