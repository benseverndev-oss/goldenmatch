# GoldenFlow

Data transformation toolkit -- standardize, reshape, and normalize messy data. DQBench Transform Score: 100/100.

## Related Projects
- **GoldenCheck:** `D:\show_case\goldencheck` -- Data validation. Has its own CLAUDE.md.
- **GoldenMatch:** `D:\show_case\goldenmatch` -- Entity resolution. Has its own CLAUDE.md.
- **GitHub:** `benzsevern/goldenflow`, `benzsevern/goldencheck`, `benzsevern/goldenmatch`

## Branch & Merge SOP (all Golden Suite repos)
- Feature work goes on `feature/<name>` branches, never directly to main
- Merge via **squash merge PR** (watchers see PR activity, history stays clean)
- PR title format: `feat: <description>` or `fix: <description>`
- PR body: summary bullets + test plan
- Merge when: tests pass, docs updated. Days not weeks.
- After merge: delete remote branch

## Environment
- Windows 11, bash shell (Git Bash)
- Python 3.12 at `C:\Users\bsevern\AppData\Local\Programs\Python\Python312\python.exe`
- Two GitHub accounts: `benzsevern` (personal) and `benzsevern-mjh` (work)
- MUST `gh auth switch --user benzsevern` before push, switch back to `benzsevern-mjh` after

## Commands

```bash
pip install -e ".[dev]"             # Dev install
pip install -e ".[check]"           # With GoldenCheck integration
pip install -e ".[mcp]"             # With MCP server
pip install -e ".[all]"             # Everything
pytest --tb=short -v                # Run tests
ruff check .                        # Lint
ruff check . --fix                  # Auto-fix lint
goldenflow transform data.csv       # Zero-config: auto-detect and fix
goldenflow transform data.csv -c goldenflow.yaml  # Apply saved config
goldenflow map -s a.csv -t b.csv    # Auto-map schemas between files
goldenflow learn data.csv -o config.yaml  # Generate config from data patterns
goldenflow validate data.csv        # Dry-run: show what would change
goldenflow diff before.csv after.csv  # Compare pre/post transform
goldenflow profile data.csv         # Show column profiles
goldenflow interactive data.csv     # Launch TUI
goldenflow serve                    # REST API for real-time transforms
goldenflow mcp-serve                # MCP server for Claude Desktop
```

## Architecture

```
goldenflow/
├── cli/           # Typer CLI (main.py -- transform, map, learn, validate, diff, profile, interactive, serve, mcp-serve)
├── engine/        # TransformEngine, Manifest, profiler_bridge, selector, differ
├── transforms/    # Transform library: text, phone, names, address, dates, categorical, numeric, auto_correct
├── mapping/       # Schema mapping: name_similarity, profile_similarity, schema_mapper
├── config/        # GoldenFlowConfig (Pydantic), YAML loader, config learner
├── connectors/    # File connector (CSV/Excel/Parquet), database connector (connectorx)
├── domains/       # Domain packs: base.py, people_hr.py
├── llm/           # LLM-assisted config correction (corrector.py)
├── mcp/           # MCP server (server.py)
├── reporters/     # rich_console.py, json_reporter.py
└── tui/           # Textual TUI (app.py)
```

## Pipeline Flow

```
read_file (connectors) -> profile_dataframe (profiler_bridge)
-> select_transforms (selector, by inferred type + auto_apply flag)
-> apply transforms (TransformEngine.transform_df)
-> record changes in Manifest
-> write output + manifest.json
```

Zero-config mode: `profile_dataframe` infers a type per column, `select_transforms` picks `auto_apply=True` transforms that match the type, sorted by priority descending.

## Transform Registry

Transforms live in `goldenflow/transforms/` and self-register via decorator:

```python
from goldenflow.transforms import register_transform

@register_transform(
    name="phone_e164",
    input_types=["phone"],
    auto_apply=True,
    priority=70,
    mode="series",
)
def phone_e164(series: pl.Series) -> pl.Series:
    ...
```

All transform modules are imported in `goldenflow/__init__.py` at package load time -- that is the only registration mechanism. If you add a new module, add an import there.

## Hybrid expr / series / dataframe Mode System

The `mode` field on `TransformInfo` controls how the engine applies a transform:

| mode | Input | Output | When to use |
|------|-------|--------|-------------|
| `"expr"` | `pl.Expr` | `pl.Expr` | Pure Polars operations (strip, lowercase). Stays in Rust; fastest. |
| `"series"` | `pl.Series` | `pl.Series` | Python logic per column (phone parsing, date parsing). Uses `map_batches` internally. |
| `"dataframe"` | `pl.DataFrame` | `pl.DataFrame` | Multi-column transforms (split_name, split_address). Receives and returns full frame. |

The engine in `engine/transformer.py` dispatches based on `TransformInfo.mode` -- do not add mode-specific logic anywhere else.

## Key Patterns

- **All transforms use `@register_transform`** -- never add to `_REGISTRY` directly
- **`TransformResult`** is a dataclass with `.df` (clean Polars DataFrame) and `.manifest` (Manifest)
- **`Manifest`** tracks every `TransformRecord`: column, transform name, rows affected, before/after samples
- **Polars-native** -- all data ops use Polars, never pandas
- **`parse_transform_name("truncate:50")`** splits parameterized transform strings into `("truncate", ["50"])`
- **`select_from_findings`** in `engine/selector.py` maps GoldenCheck finding check names to transform names (the `--from-findings` CLI flag)

## Config Schema (goldenflow.yaml)

```yaml
source: customers.csv
output: customers_clean.csv

transforms:
  - column: phone
    ops: [phone_e164]

renames:
  email_address: email

drop: [internal_id]

dedup:
  columns: [email]
  keep: first
```

Config is a `GoldenFlowConfig` Pydantic model (`config/schema.py`). `config/learner.py` auto-generates it from data profiles.

## Integration with GoldenCheck and GoldenMatch

GoldenFlow sits in the middle of the Golden Suite pipeline:

```
Raw Data
   |
   v GoldenCheck   -- profile & discover quality issues
   | findings
   v GoldenFlow    -- fix issues, standardize, reshape
   | clean data
   v GoldenMatch   -- deduplicate, match, create golden records
   | golden records
   v Production
```

**GoldenCheck integration** (`pip install goldenflow[check]`):
- `engine/profiler_bridge.py` calls GoldenCheck's scanner to get column profiles without re-implementing profiling
- `engine/selector.py:select_from_findings()` maps GoldenCheck finding checks (e.g. `"whitespace_issues"`) to transform names
- CLI flag `goldenflow transform data.csv --from-findings findings.json` uses this path

**GoldenMatch integration**:
- GoldenFlow's output (clean CSV + manifest) feeds directly into `goldenmatch dedupe`
- Schema mapping (`goldenflow map`) resolves column name mismatches before matching

**Pipeline shorthand**:
```bash
goldencheck scan data.csv | goldenflow transform --from-findings | goldenmatch dedupe
```

## Testing

- TDD: tests first, then implementation
- Fixtures: `tests/fixtures/` (CSV files gitignored; add `!tests/fixtures/*.csv` exception if needed)
- Convention: `tests/{module}/test_{file}.py`
- Integration tests: `tests/test_integration.py`, `tests/test_public_api.py`
- Commit messages: conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`)

## Environment / Auth

API keys for LLM testing live in `.testing/.env` (gitignored):
```bash
source .testing/.env   # loads OPENAI_API_KEY, ANTHROPIC_API_KEY, TWINE credentials
```

GitHub auth on Windows (Credential Manager ignores `gh auth switch`):
```bash
gh auth switch --user benzsevern
GIT_ASKPASS=$(which echo) git -c credential.helper="!gh auth git-credential" push origin main
gh auth switch --user benzsevern-mjh   # switch back after
```

## Benchmarks

```bash
pip install dqbench && dqbench run goldenflow   # DQBench transform benchmark (100/100)
dqbench run all                                  # Compare against other tools
```

## Publishing

```bash
python -m build && source .testing/.env && python -m twine upload dist/*
```

## Gotchas

- `*.csv` is in `.gitignore` -- test fixtures need `!tests/fixtures/*.csv` exception
- `__version__` is defined ONLY in `goldenflow/__init__.py` -- don't add a second copy in `cli/main.py`
- Transform module imports in `__init__.py` are load-order sensitive -- modules that depend on others (e.g. `auto_correct` depends on `categorical`) must be imported after
- `mode="dataframe"` transforms receive the **entire** DataFrame and must return one with the same or more columns -- do not drop columns silently
- `category_auto_correct` is suppressed for high-cardinality columns (>10% unique values) by `selector.py` -- this is intentional
- Ruff line length: 100 chars
- `config/learner.py` generates a YAML config from profiles; `config/loader.py` reads it back -- keep the Pydantic schema in `config/schema.py` as the single source of truth
