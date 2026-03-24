# Finishing Touches — Design Spec

## Goal

Close the last 5 UX gaps before 1.0: progress indicator, multi-file scan, interactive dismiss, HTML report, exit code docs.

## Scope

5 small features, all shipped together.

---

## 1. Progress + Sampling Message

**Problem:** Scanning large files shows no output until complete. Users think it's hung.

**Fix:** Print a one-line status message before scanning starts:

```
Scanning data.csv (1,000,000 rows, 12 columns, sampled to 100,000)...
```

If not sampled:
```
Scanning data.csv (5,000 rows, 8 columns)...
```

**Implementation:** Add to `_do_scan()` in `cli/main.py`, after `scan_file()` reads the file but before profiling. The message comes from the profile's `row_count` and `column_count`. For sampling, check if `row_count > sample_size`.

Actually, simpler: print the message in `_do_scan` right after getting `findings, profile`:
```python
typer.echo(f"Scanned {profile.row_count:,} rows, {profile.column_count} columns", err=True)
```

Use `err=True` so it doesn't pollute `--json` stdout.

---

## 2. Multi-File Scan

**Problem:** `goldencheck scan *.csv` doesn't work — Typer takes one Path argument.

**Fix:** Accept multiple files:

```bash
goldencheck scan data1.csv data2.csv data3.csv --no-tui
goldencheck scan data/*.csv --no-tui
```

**Implementation:** Change `file: Path` to `files: list[Path]` in the `scan` command. Loop over each file, collect results, print per-file summary. The `--json` output wraps results in an array. TUI mode uses the first file only (with a note).

For the `main()` fallback parser: collect all non-flag positional args as files.

---

## 3. Interactive Dismiss in TUI

**Problem:** Dismissing a finding in the TUI doesn't persist — it comes back next scan.

**Fix:** Add `d` keybinding in TUI to dismiss a finding. Dismissed findings are added to the `ignore` list in `goldencheck.yml` on F2 save.

**Implementation:**
- Add `Binding("d", "dismiss_finding", "Dismiss")` to `BINDINGS`
- `action_dismiss_finding()`: marks the selected finding with `f.pinned = False` and adds `(column, check)` to a `self._dismissed` set
- On F2 save: dismissed findings become `IgnoreEntry(column=..., check=...)` in the config's `ignore` list

---

## 4. HTML Report

**Problem:** No shareable report format for stakeholders.

**Fix:** `goldencheck scan data.csv --html report.html`

**Implementation:**
- New file: `goldencheck/reporters/html_reporter.py`
- `report_html(findings, profile, path)` — generates a self-contained HTML file
- Template: simple HTML with inline CSS (no external deps). Shows health grade badge, findings table, column stats. Reuses the `_repr_html_` patterns from `notebook.py`.
- Add `--html` flag to `scan`, `review`, and the fallback parser

---

## 5. Exit Code in --help

**Problem:** `--help` doesn't mention exit codes.

**Fix:** Add to the app's help text:

```
Exit codes: 0 = pass, 1 = findings at/above --fail-on, 2 = usage error
```

**Implementation:** Update the `app` Typer help string and the `scan`/`validate` docstrings.

---

## Testing

| Feature | Test |
|---------|------|
| Progress message | CLI test: scan with `--no-tui`, check stderr for "Scanned" |
| Multi-file | CLI test: pass two fixture files, verify both scanned |
| Dismiss | Unit test: dismiss finding, save config, verify ignore list |
| HTML report | CLI test: `--html` flag, verify file exists and contains HTML |
| Exit code docs | Verify `--help` output contains "Exit codes" |

## Version

Ships as part of GoldenCheck 1.0.
