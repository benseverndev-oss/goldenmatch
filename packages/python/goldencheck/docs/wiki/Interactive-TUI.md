# Interactive TUI

GoldenCheck includes an interactive terminal UI built with [Textual](https://textual.textualize.io/). It launches automatically after a scan unless `--no-tui` or `--json` is passed.

```bash
goldencheck data.csv          # launches TUI
goldencheck data.csv --no-tui # console output only
```

---

## Tabs

The TUI has four tabs. Switch between them with the number keys `1`–`4` or by clicking.

### Tab 1 — Overview

Displays a high-level summary of the dataset and scan results.

**Contents:**
- File path
- Health score (letter grade A–F with numeric points out of 100)
- Row count and column count
- Finding counts broken down by severity (errors / warnings / info)
- Per-column profile list: name, inferred type, and null count

The health score is color-coded:
- A (90–100): green
- B (80–89): yellow-green
- C (70–79): yellow
- D (60–69): orange
- F (0–59): red

### Tab 2 — Findings

A sortable data table of all findings from the scan.

**Columns:**
| Column | Description |
|--------|-------------|
| Pin | `[x]` if pinned, `[ ]` if not |
| Severity | ERROR / WARNING / INFO |
| Column | The column name the finding applies to |
| Check | The profiler check name |
| Message | Truncated finding description (first 55 chars) |
| Rows | Number of affected rows |
| Source | `[LLM]` badge if the finding came from LLM Boost |

**Interaction:** Select a row and press Space (or Enter) to toggle the pin. Pinned findings are promoted to permanent rules when you press F2.

### Tab 3 — Column Detail

A drill-down view for individual columns.

**Interaction:** Select a column name from the list on the left. The right panel shows:
- Column name and inferred data type
- Total rows, null count, null percentage
- Unique value count and unique percentage
- Min and max values (numeric columns)
- Mean (numeric columns)
- Detected format (e.g., `email`, `phone`)
- Enum values, if cardinality is low

### Tab 4 — Rules

Shows the current state of pinned rules and existing config rules.

**Contents:**
- Pinned rules from the Findings tab (promoted in this session)
- Rules already present in `goldencheck.yml` (if loaded via `validate` or `review`)

Each rule row shows: column name, check/constraint, and source (`config` or pinned).

Press F2 from any tab to save all pinned rules to `goldencheck.yml`.

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `1` | Switch to Overview tab |
| `2` | Switch to Findings tab |
| `3` | Switch to Column Detail tab |
| `4` | Switch to Rules tab |
| `Space` | Toggle pin on selected finding (Findings tab) |
| `F2` | Save pinned rules to `goldencheck.yml` |
| `?` | Show help notification |
| `q` | Quit |

---

## Health Score Algorithm

The health score is calculated from finding counts:

```
points = 100 - (errors × 10) - (warnings × 3)
points = max(points, 0)
```

When `findings_by_column` data is available (from a detailed scan), per-column deductions are capped at 20 points to prevent a single badly broken column from dominating the score:

```
per-column deduction = min((errors × 10) + (warnings × 3), 20)
total deduction = sum of all per-column deductions
points = max(100 - total_deduction, 0)
```

Grade thresholds:

| Grade | Points |
|-------|--------|
| A | 90–100 |
| B | 80–89 |
| C | 70–79 |
| D | 60–69 |
| F | 0–59 |

---

## Pin / Export Workflow

1. Run `goldencheck data.csv` to launch the TUI.
2. Navigate to the Findings tab (`2`).
3. Review findings sorted by severity. Use arrow keys to select a row.
4. Press Space to pin findings that represent real rules you want to enforce.
5. Press F2 to write pinned rules to `goldencheck.yml`.
6. Quit with `q`.
7. Edit `goldencheck.yml` by hand if needed (e.g., adjust a range, add enum values).
8. Run `goldencheck validate data.csv` in CI to enforce the rules.

Dismissed findings (not pinned) do not appear in `goldencheck.yml` but will still appear on future scans. To suppress a specific finding permanently, add it to the `ignore` list in `goldencheck.yml` — see [Configuration](Configuration.md#ignore).

---

## TUI in CI

The TUI is a terminal application that requires an interactive terminal. In CI environments, always pass `--no-tui`:

```bash
goldencheck validate data.csv --no-tui
goldencheck validate data.csv --no-tui --json
```
