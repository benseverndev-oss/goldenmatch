# User Experience — Design Spec

## Goal

Close the gaps in the end-user journey: guided onboarding, smart finding triage, scan history tracking, and webhook notifications.

## Scope

Four features, executed in order:

1. `goldencheck init` — interactive setup wizard
2. Guided scan modes — `--guided` CLI, TUI guided mode, `--smart` auto-triage
3. History tracking — `.goldencheck/history.jsonl` + `goldencheck history`
4. Webhook notifications — `--webhook` + `--notify-on` (depends on history for `grade-drop`)

**Note:** History (3) is implemented before webhooks (4) because the `grade-drop` notification trigger needs to read previous scan results from history.

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
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept defaults, skip interactive prompts."),
) -> None:
```

The `--yes` flag accepts defaults (no CI, no domain, no LLM boost) for scripted/CI usage.

**Note:** `init` is a standard `@app.command()` subcommand. The hand-rolled fallback parser in `main()` only routes to `scan` — `init` does not need special handling there.

### What It Generates

**`goldencheck.yml`** — populated with auto-pinned rules from the scan. Uses the existing `save_config` writer. Uses `auto_triage()` from the triage engine (Section 2c) — pins all findings in the "pin" bucket.

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

### Architecture

- **New file:** `goldencheck/cli/init_wizard.py` — keeps wizard logic out of `main.py`
- Called from a new `init` command in `main.py`
- Uses `typer.prompt()` for choices, `typer.confirm()` for yes/no
- Generates CI files via string templates (no Jinja dependency)

---

## 2. Guided Scan Modes

Three modes that share a common auto-triage engine.

**Mutual exclusivity:** `--smart` and `--guided` are mutually exclusive. If both are passed, error out: "Cannot use --smart and --guided together."

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

A new overlay/wizard in the TUI that presents findings sequentially. Activated by keybinding `g` for guided.

**Keybinding conflict check:** The existing TUI bindings are `1-4` (tabs), `Space` (pin), `F2` (save), `e` (view rows), `q` (quit), `?` (help). `g` is unused and safe.

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
    """Classify findings into pin/dismiss/review buckets.

    Operates on POST-downgrade findings (after apply_confidence_downgrade).
    """
```

- **Pin:** severity >= WARNING AND confidence >= 0.8
- **Dismiss:** severity == INFO OR confidence < 0.5
- **Review:** everything else (medium confidence warnings)

Used by `init`, `--smart`, and `--guided`. Single source of truth for triage logic.

### Hand-Rolled Arg Parser Updates

The `main()` callback's hand-rolled arg parser must be extended for new flags on the `scan` shorthand path:

```python
# Add to the while args: loop in main()
elif arg == "--smart":
    smart = True
elif arg == "--guided":
    guided = True
elif arg == "--no-history":
    no_history = True
elif arg == "--webhook":
    webhook = args.pop(0)
elif arg == "--notify-on":
    notify_on = args.pop(0)
```

These must be passed through to `_do_scan()`.

---

## 3. History Tracking

### Storage

`.goldencheck/history.jsonl` — one JSON line per scan, append-only.

```jsonl
{"timestamp":"2026-03-20T14:30:00","file":"orders.csv","rows":10000,"columns":12,"grade":"C","score":72,"errors":3,"warnings":8,"findings_count":24}
{"timestamp":"2026-03-22T09:15:00","file":"orders.csv","rows":10000,"columns":12,"grade":"B","score":85,"errors":1,"warnings":4,"findings_count":15}
```

**Size estimate:** ~200 bytes/scan. One year of hourly scans = ~1.7 MB. No rotation needed.

### Auto-Recording

Every CLI scan command (`scan`, `review`, `fix`, `watch`) auto-appends to history after scanning. The `--no-history` flag disables it. The recording happens in `_do_scan()` and the `watch` loop, not inside `scan_file()` itself (engine stays pure).

### CLI Signature

```python
@app.command()
def history(
    file: Optional[Path] = typer.Argument(None, help="Filter history by file."),
    last: Optional[int] = typer.Option(None, "--last", "-n", help="Show last N scans."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
```

**Note:** `history` is a standard `@app.command()` subcommand. The hand-rolled parser routes "history" correctly as a subcommand (not a file path) since it has no file extension.

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
- `get_previous_scan(file) -> ScanRecord | None` — returns most recent scan for a file (used by webhooks)
- `ScanRecord` dataclass: timestamp, file, rows, columns, grade, score, errors, warnings
- Directory `.goldencheck/` auto-created on first record
- Add `.goldencheck/` to the project's `.gitignore` recommendation in `init`

---

## 4. Webhook Notifications

### CLI Signatures

Webhooks are available on `scan` and `watch` commands:

```python
# Added to scan command
webhook: Optional[str] = typer.Option(None, "--webhook", help="URL to POST findings to."),
notify_on: str = typer.Option("grade-drop", "--notify-on", help="Trigger: grade-drop, any-error, any-warning."),

# Added to watch command
webhook: Optional[str] = typer.Option(None, "--webhook", help="URL to POST findings to."),
notify_on: str = typer.Option("grade-drop", "--notify-on", help="Trigger: grade-drop, any-error, any-warning."),
```

Not added to `validate`, `review`, `fix`, `diff`, `learn`, or `init` — webhooks are for monitoring, not interactive commands.

### `--notify-on` Options

| Value | Triggers when |
|-------|---------------|
| `grade-drop` (default) | Health grade decreased since last scan (reads from history) |
| `any-error` | Any ERROR-level finding exists |
| `any-warning` | Any WARNING or ERROR finding exists |

### Payload

HTTP POST with JSON body:

```json
{
  "tool": "goldencheck",
  "version": "<from __version__>",
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

**Version:** Uses `goldencheck.__version__`, not a hardcoded string.

### Architecture

- **New file:** `goldencheck/engine/notifier.py`
- `send_webhook(url, payload)` — `urllib.request.urlopen` POST with `Content-Type: application/json`, 5-second timeout
- `should_notify(current_grade, current_findings, previous_scan, notify_on) -> bool`
- Previous scan read from `history.get_previous_scan(file)`. If no history exists (first scan), `grade-drop` does not trigger (no baseline to compare against). `any-error` and `any-warning` still trigger on first scan.
- **Error handling:** No retries (fire-and-forget). Non-2xx responses, timeouts, SSL errors, and invalid URLs are logged as warnings but never fail the scan.

---

## Testing Strategy

| Component | Test Approach |
|-----------|---------------|
| `init` wizard | Mock `typer.prompt`, verify generated files; test `--yes` mode |
| Auto-triage | Unit test: findings with various confidence/severity → correct buckets (post-downgrade) |
| `--smart` | Integration test: scan fixture, verify goldencheck.yml written |
| `--guided` | Mock stdin, verify pin/dismiss behavior |
| `--smart`/`--guided` mutual exclusivity | CLI test: both flags → error |
| Webhook | Mock `urllib.request`, verify payload format, trigger logic, error handling |
| History | Unit test: record + load round-trip; test JSONL append; test get_previous_scan |
| `history` command | CLI test: invoke after recording, verify output format |

## Non-Goals

- No web dashboard (future)
- No Slack-specific Block Kit formatting (users wrap the webhook)
- No history pruning/rotation (~200 bytes/scan, stays small)
- No TUI for `history` (CLI table only)
- No webhook retries (fire-and-forget)

## Version

Ships as GoldenCheck v0.6.0.
