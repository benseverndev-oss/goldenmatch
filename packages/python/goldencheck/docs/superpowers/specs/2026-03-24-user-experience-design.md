# User Experience — Design Spec

## Goal

Close the gaps in the end-user journey: guided onboarding, smart finding triage, webhook notifications, and scan history tracking.

## Scope

Four features, executed in order:

1. `goldencheck init` — interactive setup wizard
2. Guided scan modes — `--guided` CLI, TUI guided mode, `--smart` auto-triage
3. Webhook notifications — `--webhook` + `--notify-on`
4. History tracking — `.goldencheck/history.jsonl` + `goldencheck history`

---

## 1. `goldencheck init`

### Purpose

One command to go from zero to fully configured: scan, pin rules, scaffold CI.

### Flow

```
$ goldencheck init data.csv

Scanning data.csv... found 24 issues (3 errors, 8 warnings, 13 info).

? What CI do you use? [GitHub / GitLab / None]
> GitHub

? Domain? Improves detection for industry data. [healthcare / finance / ecommerce / none]
> healthcare

? Enable LLM boost? ~$0.01/scan, catches semantic issues. [y/N]
> n

Auto-pinning 8 high-confidence findings as rules...

Created:
  ✓ goldencheck.yml                      (8 rules)
  ✓ .github/workflows/goldencheck.yml    (CI workflow)

Next: git add goldencheck.yml .github/ && git push
```

### CLI Signature

```python
@app.command()
def init(
    file: Path = typer.Argument(..., help="Data file to scan for initial rules."),
) -> None:
```

No flags — everything is asked interactively via `typer.prompt()` / `typer.confirm()`.

### What It Generates

**`goldencheck.yml`** — populated with auto-pinned rules from the scan. Uses the existing `save_config` writer. Only pins findings with confidence >= 0.8 and severity >= WARNING.

**CI workflow** (if GitHub selected):
```yaml
name: Data Quality
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: benzsevern/goldencheck-action@v1
        with:
          files: "<detected data file pattern>"
          fail-on: error
```

**CI workflow** (if GitLab selected):
```yaml
data-quality:
  image: python:3.12
  script:
    - pip install goldencheck
    - goldencheck validate <file> --no-tui --fail-on error
```

### Auto-Pin Logic

The same engine used by `--smart` (see Section 2c). Pins findings where:
- `severity >= WARNING`
- `confidence >= 0.8`
- Not suppressed (not INFO after suppression)

### Architecture

- **New file:** `goldencheck/cli/init_wizard.py` — keeps wizard logic out of `main.py`
- Called from a new `init` command in `main.py`
- Uses `typer.prompt()` for choices, `typer.confirm()` for yes/no
- Generates CI files via string templates (no Jinja dependency)

---

## 2. Guided Scan Modes

Three modes that share a common auto-triage engine.

### 2a. `--smart` Auto-Triage (zero interaction)

```bash
goldencheck scan data.csv --smart --no-tui
```

Automatically pins high-confidence findings, suppresses low-confidence ones, writes `goldencheck.yml`. Prints a summary:

```
Auto-triaged 24 findings:
  Pinned:     8 (high confidence, severity >= WARNING)
  Dismissed: 12 (low confidence or INFO)
  Review:     4 (medium confidence — run with --guided to decide)

Written to goldencheck.yml (8 rules)
```

### 2b. `--guided` CLI Walkthrough

```bash
goldencheck scan data.csv --guided --no-tui
```

Walks through findings one at a time:

```
[1/4] WARNING: 'email' has 6% malformed emails (120 rows)
      Confidence: HIGH  |  Samples: bob@example, not-an-email
      Pin this rule? [Y/n/skip]  > y

[2/4] WARNING: 'status' has 3 case variants (active, Active, ACTIVE)
      Confidence: HIGH  |  Samples: Active, ACTIVE
      Pin this rule? [Y/n/skip]  > n

...
Pinned 3 rules → goldencheck.yml
```

Only presents findings that `--smart` would mark as "review" (medium confidence) plus all WARNING/ERROR. Skips INFO.

### 2c. TUI Guided Mode

A new overlay/wizard in the TUI that presents findings sequentially. Activated by a keybinding (e.g., `g` for guided).

Each finding is shown fullscreen with:
- Severity, column, check, message
- Sample values
- Confidence indicator
- Three buttons: Pin / Dismiss / Skip

After all findings are reviewed, returns to the normal TUI with pins applied.

### Auto-Triage Engine

**New file:** `goldencheck/engine/triage.py`

```python
def auto_triage(findings: list[Finding]) -> TriageResult:
    """Classify findings into pin/dismiss/review buckets."""
```

- **Pin:** severity >= WARNING AND confidence >= 0.8
- **Dismiss:** severity == INFO OR confidence < 0.5
- **Review:** everything else (medium confidence warnings)

Used by `init`, `--smart`, and `--guided`.

---

## 3. Webhook Notifications

### CLI Interface

```bash
goldencheck scan data.csv --webhook https://hooks.slack.com/... --notify-on grade-drop
goldencheck watch data/ --webhook https://my-api.com/alerts --notify-on any-error
```

### `--notify-on` Options

| Value | Triggers when |
|-------|---------------|
| `grade-drop` (default) | Health grade decreased since last scan |
| `any-error` | Any ERROR-level finding exists |
| `any-warning` | Any WARNING or ERROR finding exists |

### Payload

HTTP POST with JSON body:

```json
{
  "tool": "goldencheck",
  "version": "0.5.0",
  "trigger": "grade-drop",
  "file": "data/orders.csv",
  "health_grade": "C",
  "health_score": 71,
  "previous_grade": "B",
  "errors": 3,
  "warnings": 8,
  "top_findings": [
    {"severity": "error", "column": "email", "check": "format_detection", "message": "..."}
  ]
}
```

### Architecture

- **New file:** `goldencheck/engine/notifier.py`
- `send_webhook(url, payload)` — simple `urllib.request.urlopen` POST, no dependencies
- `should_notify(current_result, previous_result, notify_on)` — compares grades/findings
- Previous result read from history (see Section 4) or `.goldencheck/last_scan.json`
- Timeout: 5 seconds. Failures logged but don't fail the scan.

### Slack Formatting

The payload works with Slack incoming webhooks as-is (they accept any JSON). For richer formatting, users can pipe through a Slack Block Kit transformer — we don't build Slack-specific formatting (YAGNI, users can wrap it).

---

## 4. History Tracking

### Storage

`.goldencheck/history.jsonl` — one JSON line per scan, append-only.

```jsonl
{"timestamp":"2026-03-20T14:30:00","file":"orders.csv","rows":10000,"columns":12,"grade":"C","score":72,"errors":3,"warnings":8,"findings_count":24}
{"timestamp":"2026-03-22T09:15:00","file":"orders.csv","rows":10000,"columns":12,"grade":"B","score":85,"errors":1,"warnings":4,"findings_count":15}
```

### Auto-Recording

Every `scan_file()` call appends to history. The CLI commands (`scan`, `review`, `fix`, `watch`) trigger this automatically. The `--no-history` flag disables it.

### CLI

```bash
goldencheck history                    # show all scans
goldencheck history orders.csv         # filter by file
goldencheck history --last 10          # last 10 scans
goldencheck history --json             # JSON output
```

### Output

```
Date                 File           Score  Grade  Errors  Warnings
2026-03-20 14:30     orders.csv     72     C      3       8
2026-03-22 09:15     orders.csv     85     B      1       4
2026-03-24 11:00     orders.csv     92     A      0       2       ↑

Trend: orders.csv improved 72 → 92 over 4 days
```

### Architecture

- **New file:** `goldencheck/engine/history.py`
- `record_scan(file, profile, findings)` — appends one JSONL line
- `load_history(file_filter=None, last_n=None) -> list[ScanRecord]`
- `ScanRecord` dataclass: timestamp, file, rows, columns, grade, score, errors, warnings
- Directory `.goldencheck/` auto-created on first scan
- Add `.goldencheck/` to the project's `.gitignore` recommendation in `init`

### Integration with Webhooks

`should_notify()` reads the previous scan from history to detect grade drops. If history is empty (first scan), no notification is sent.

---

## Testing Strategy

| Component | Test Approach |
|-----------|---------------|
| `init` wizard | Mock `typer.prompt`, verify generated files |
| Auto-triage | Unit test: findings with various confidence/severity → correct buckets |
| `--smart` | Integration test: scan fixture, verify goldencheck.yml written |
| `--guided` | Mock stdin, verify pin/dismiss behavior |
| Webhook | Mock `urllib.request`, verify payload format and trigger logic |
| History | Unit test: record + load round-trip; test JSONL append |
| `history` command | CLI test: invoke after recording, verify output |

## Non-Goals

- No web dashboard (future)
- No Slack-specific Block Kit formatting (users wrap the webhook)
- No history pruning/rotation (files stay small for months of daily scans)
- No TUI for `history` (CLI table only)

## Version

Ships as GoldenCheck v0.6.0.
