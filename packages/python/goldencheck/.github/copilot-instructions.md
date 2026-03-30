# GoldenCheck

Data validation that discovers rules from your data. DQBench Score: 88.40.

## Commands

```bash
pip install -e ".[dev]"          # Dev install
pip install -e ".[llm]"          # With LLM boost
pip install -e ".[mcp]"          # With MCP server
pytest --tb=short -v             # Run tests (189+ passing)
ruff check .                     # Lint
ruff check . --fix               # Auto-fix lint
goldencheck data.csv --no-tui    # Scan a file (CLI output)
goldencheck data.csv             # Scan with TUI
goldencheck validate data.csv    # Validate against goldencheck.yml
goldencheck diff old.csv new.csv # Compare two files
goldencheck fix data.csv         # Auto-fix (safe mode)
goldencheck watch data/          # Poll directory for changes
goldencheck scan data.csv --domain healthcare  # Domain-specific types
```

## Architecture

```
goldencheck/
├── cli/           # Typer CLI (9 commands: scan, validate, review, diff, watch, fix, learn, mcp-serve)
├── engine/        # Scanner, validator, confidence, fixer, differ, watcher
├── profilers/     # 10 column profilers (BaseProfiler ABC)
├── relations/     # Cross-column profilers (temporal, null correlation, numeric cross, age validation)
├── semantic/      # Type classifier + suppression engine + domain packs (healthcare, finance, ecommerce)
├── llm/           # LLM boost (providers, prompts, merger, budget, rule generator)
├── mcp/           # MCP server (9 tools incl. domain discovery)
├── config/        # Pydantic YAML config (goldencheck.yml)
├── models/        # Finding (with metadata dict), Profile dataclasses
├── notebook.py    # ScanResult wrapper + HTML renderers for Jupyter/Colab
├── reporters/     # Rich, JSON, CI output
└── tui/           # Textual TUI (4 tabs)
```

## Pipeline Flow

```
read_file → maybe_sample → run profilers → classify semantic types
→ apply suppression → corroboration boost → sort by severity
→ (optional) LLM boost → confidence downgrade → report/TUI
```

## Key Patterns

- **All profilers extend `BaseProfiler`** with `profile(df, column, *, context=None) -> list[Finding]`
- **Findings are dataclasses** — use `dataclasses.replace()`, never mutate
- **Confidence 0.0-1.0** on every Finding — high (≥0.8), medium (0.5-0.79), low (<0.5)
- **Severity: ERROR > WARNING > INFO** (IntEnum)
- **`source` field**: None = profiler, "llm" = LLM-generated
- **Polars-native** — all data ops use Polars, never pandas
- **stdlib `random` only** — no numpy for randomness

## Testing

- TDD: tests first, then implementation
- Fixtures: `tests/fixtures/simple.csv`, `tests/fixtures/messy.csv`
- Convention: `tests/{module}/test_{file}.py`
- Commit messages: conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`)

## Environment

API keys for LLM testing live in `.testing/.env` (gitignored):
```bash
source .testing/.env   # loads OPENAI_API_KEY, TWINE credentials
```

## Benchmarks

```bash
python benchmarks/speed_benchmark.py                    # Speed test
python benchmarks/goldencheck_benchmark.py              # Detection (profiler-only)
source .testing/.env && python benchmarks/goldencheck_benchmark_llm.py  # With LLM
pip install dqbench && dqbench run goldencheck          # DQBench head-to-head
dqbench run all                                         # Compare against GX/Pandera/Soda
# Inline quick score check:
# python -c "import sys; sys.path.insert(0,'D:/show_case/dqbench'); from dqbench.runner import run_benchmark; from dqbench.adapters.goldencheck import GoldenCheckAdapter; s=run_benchmark(GoldenCheckAdapter()); print(f'Score: {s.dqbench_score:.2f}')"
```

## Publishing

```bash
python -m build && source .testing/.env && python -m twine upload dist/*
```

## Remote MCP Server

Hosted on Railway, registered on Smithery:
- **Endpoint:** `https://goldencheck-mcp-production.up.railway.app/mcp/`
- **Smithery:** `https://smithery.ai/servers/benzsevern/goldencheck`
- **Server card:** `https://goldencheck-mcp-production.up.railway.app/.well-known/mcp/server-card.json`
- **Transport:** Streamable HTTP (via `StreamableHTTPSessionManager`)
- **Dockerfile:** `Dockerfile.mcp` (Python 3.12-slim, installs `.[mcp]`)
- **Railway project:** `golden-suite-mcp` (service: `goldencheck-mcp`, port 8100)
- **Local HTTP:** `goldencheck mcp-serve --transport http --port 8100`

## Gotchas

- `*.csv` is in `.gitignore` — test fixtures need `!tests/fixtures/*.csv` exception
- The CLI has a hand-rolled arg parser in `main()` callback for the `goldencheck data.csv` shorthand — update it when adding new flags
- `scan_file_with_llm` calls `scan_file(..., return_sample=True)` — suppression and boost run inside `scan_file`, not in the LLM path
- GitHub auth: `gh auth switch --user benzsevern` then `GIT_ASKPASS=$(which echo) git -c credential.helper="!gh auth git-credential" push origin main` — Windows Credential Manager ignores `gh auth switch`
- Ruff line length: 100 chars
- `__version__` is defined ONLY in `goldencheck/__init__.py` — `cli/main.py` imports it, don't add a second copy
- Wiki repo: `git clone https://github.com/benzsevern/goldencheck.wiki.git /tmp/goldencheck.wiki` — sync with `cp docs/wiki/*.md /tmp/goldencheck.wiki/ && cd /tmp/goldencheck.wiki && git add -A && git commit -m "docs: sync" && git push`
- GitHub Pages: Jekyll + just-the-docs (dark), source in `docs/`, workflow in `.github/workflows/pages.yml`, live at `benzsevern.github.io/goldencheck`
- Jekyll link anchors: `{% link file.md %}#anchor` NOT `{% link file.md#anchor %}`
- Classifier hint matching: hints ending with `_` are prefix-only (NOT substring) — `is_` matches `is_active` but NOT `diagnosis_desc`
- `Finding.metadata` dict is used by pattern_consistency for structured pattern data — suppression reads it
- Domain pack loading priority: user types > domain types > base types (dict insertion order matters)
- Cross-column findings: use only the "violating" column name to avoid FP on clean columns in benchmarks
- DQBench adapter does NOT call `apply_confidence_downgrade` — raw `scan_file()` output is scored

## API Quick Reference

### scan_file() — Scan a CSV for quality issues
```python
import goldencheck

findings = goldencheck.scan_file("data.csv")
for f in findings:
    print(f"[{f.severity}] {f.column}: {f.check} — {f.message}")
```

### health_score() — Get a letter grade + numeric score
```python
score = goldencheck.health_score("data.csv")
print(score)  # e.g. "B (78/100)"
```

### CLI commands
```bash
goldencheck scan data.csv              # scan for issues
goldencheck profile data.csv           # column-level stats
goldencheck health-score data.csv      # health grade
goldencheck validate data.csv          # validate against pinned rules
goldencheck fix data.csv               # auto-fix safe issues
goldencheck mcp-serve                  # start MCP server (19 tools)
goldencheck demo --no-tui              # generate and scan demo data
```

### Domain packs
```bash
goldencheck scan data.csv --domain healthcare
goldencheck scan data.csv --domain finance
goldencheck scan data.csv --domain ecommerce
```

## DQBench Integration
- **DQBench Detect Score: 88.40**
- Adapter: `dqbench/adapters/goldencheck.py`
- Run: `pip install dqbench && dqbench run goldencheck`
