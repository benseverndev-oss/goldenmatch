# API Stability

This document defines GoldenCheck's public API surface and stability guarantees.

## Stability Levels

| Level | Meaning |
|-------|---------|
| **Stable** | Will not change without a major version bump (2.0). Safe to depend on. |
| **Beta** | May change in minor versions with deprecation warnings. |
| **Experimental** | May change or be removed without notice. |

## Public API

### Stable (from `goldencheck`)

```python
from goldencheck import (
    scan_file,           # (path, sample_size, return_sample, domain) -> (findings, profile)
    scan_file_with_llm,  # (path, provider, sample_size, domain) -> (findings, profile)
    Finding,             # dataclass: severity, column, check, message, confidence, metadata
    Severity,            # IntEnum: INFO=1, WARNING=2, ERROR=3
    DatasetProfile,      # dataclass: file_path, row_count, column_count, columns
    ColumnProfile,       # dataclass: name, inferred_type, null_count, etc.
    ScanResult,          # Jupyter display wrapper
    __version__,         # str
)
```

### Stable (config)

```python
from goldencheck.config.schema import (
    GoldenCheckConfig,   # Pydantic model for goldencheck.yml
    ColumnRule,          # per-column rule
    Settings,            # sample_size, fail_on
    RelationRule,        # cross-column rule
    IgnoreEntry,         # suppress finding by (column, check)
)
```

### Beta

```python
from goldencheck.engine.confidence import (
    apply_confidence_downgrade,   # required after scan_file without LLM
    apply_corroboration_boost,    # already called inside scan_file
)

from goldencheck.engine.fixer import (
    apply_fixes,    # (df, findings, mode, force) -> (df, FixReport)
    FixReport,      # tracks per-column changes
)

from goldencheck.engine.differ import (
    diff_files,           # compare two DataFrames
    format_diff_report,   # human-readable output
)

from goldencheck.engine.triage import (
    auto_triage,    # classify findings into pin/dismiss/review
    TriageResult,   # pin, dismiss, review lists
)

from goldencheck.engine.history import (
    record_scan,        # append to .goldencheck/history.jsonl
    load_history,       # query history
    get_previous_scan,  # most recent scan for a file
)

from goldencheck.notebook import (
    findings_to_html,   # render findings as HTML table
    profile_to_html,    # render profile as HTML table
)
```

### Experimental

```python
from goldencheck.engine.notifier import send_webhook, should_notify
from goldencheck.engine.watcher import watch_directory
from goldencheck.llm.rule_generator import generate_rules, apply_rules
```

## CLI Commands

| Command | Stability |
|---------|-----------|
| `scan`, `validate`, `review` | Stable |
| `diff`, `fix`, `init`, `history` | Beta |
| `watch`, `learn`, `mcp-serve` | Experimental |

## What "Stable" Means

- Function signatures will not change without deprecation warnings
- Return types will not change
- Behavior changes are bug fixes only
- New optional parameters may be added with defaults that preserve existing behavior

## Breaking Changes

Breaking changes to stable APIs require:
1. Deprecation warning in the previous minor version
2. Migration guide in CHANGELOG
3. Major version bump (1.x → 2.0)

Beta and experimental APIs may change in any minor release.
