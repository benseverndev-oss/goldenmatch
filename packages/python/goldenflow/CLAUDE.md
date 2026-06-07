# GoldenFlow

Data transformation toolkit -- standardize, reshape, and normalize messy data. DQBench Transform Score: 100/100.

## Related Projects
Sibling packages in this monorepo at `packages/python/{goldencheck,goldenmatch,goldenpipe,infermap}/`. Pre-fold history in `_archive/goldenmatch-pre-fold/`. Branch/merge SOP, GitHub auth dance, and env setup live in root CLAUDE.md.

## Commands

```bash
pip install -e ".[dev]"             # Dev install
pip install -e ".[check]"           # With GoldenCheck integration
pip install -e ".[mcp]"             # With MCP server
pip install -e ".[all]"             # Everything
pytest --tb=short -v                # Run tests (220 passing)
ruff check .                        # Lint
ruff check . --fix                  # Auto-fix lint

# 14 CLI commands:
goldenflow transform data.csv                    # Zero-config: auto-detect and fix
goldenflow transform data.csv -c goldenflow.yaml # Apply saved config
goldenflow transform data.csv --domain healthcare # Use a domain pack
goldenflow transform data.csv --strict           # Fail on any transform error
goldenflow transform data.csv --llm              # Enable LLM-enhanced transforms
goldenflow data.csv                              # Shorthand: auto-routes to transform
goldenflow map -s a.csv -t b.csv                 # Auto-map schemas between files
goldenflow learn data.csv -o config.yaml         # Generate config from data patterns
goldenflow validate data.csv                     # Dry-run: show what would change
goldenflow diff before.csv after.csv             # Compare pre/post transform
goldenflow profile data.csv                      # Show column profiles
goldenflow watch ./data/                         # Auto-transform new/changed files
goldenflow schedule data.csv --every 1h          # Run on a schedule
goldenflow stream large_file.csv                 # Stream-process in batches
goldenflow init data.csv                         # Interactive setup wizard
goldenflow demo                                  # Generate sample data to try
goldenflow history                               # Show recent transform runs
goldenflow interactive data.csv                  # Launch TUI
goldenflow serve                                 # REST API for real-time transforms
goldenflow mcp-serve                             # MCP server for Claude Desktop
```

## TypeScript Package (packages/goldenflow-js/)

Full TS port with feature parity. Edge-safe core (`goldenflow/core`) + Node layer (`goldenflow/node`).

```bash
cd packages/goldenflow-js
npm install                      # Install deps
npm run typecheck                # tsc --noEmit (0 errors required)
npm run test                     # vitest (71 tests)
npm run build                    # tsup: ESM + CJS + .d.ts
npx goldenflow-js transform data.csv  # CLI
```

- 54 source files, ~5,200 LOC, 83 transforms (76 core + 7 domain)
- Strict TS: `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`
- Tests: `tests/smoke.test.ts`, `tests/parity/`, `tests/unit/`
- npm package name: `goldenflow`
- CLI binary: `goldenflow-js`
- Publish: tag `goldenflow-js-vX.Y.Z` triggers `.github/workflows/npm-publish.yml`
- `NPM_TOKEN` secret is set on the GitHub repo

## Architecture

```
goldenflow/
├── cli/           # Typer CLI (main.py -- all 14 commands; errors.py, init_wizard.py, watch.py, schedule.py)
├── engine/        # TransformEngine, Manifest, profiler_bridge, selector, differ
├── transforms/    # Transform library: text, phone, names, address, dates, categorical, numeric, auto_correct, email, identifiers, url
├── mapping/       # Schema mapping: name_similarity, profile_similarity, schema_mapper
├── config/        # GoldenFlowConfig (Pydantic), YAML loader, config learner
├── connectors/    # file.py (CSV/Excel/Parquet), database.py (connectorx), s3.py, gcs.py
├── domains/       # Domain packs: base.py, people_hr.py, healthcare.py, finance.py, ecommerce.py, real_estate.py
├── llm/           # LLM-assisted config correction (corrector.py) -- wired via --llm flag
├── mcp/           # MCP server (server.py)
├── reporters/     # rich_console.py, json_reporter.py
├── tui/           # Textual TUI (app.py)
├── streaming.py   # StreamProcessor -- batch/incremental processing
├── history.py     # Run history tracking (~/.goldenflow/history/)
└── notebook.py    # Jupyter _repr_html_ for TransformResult, Manifest, DatasetProfile
```

## Pipeline Flow

```
read_file (connectors) -> profile_dataframe (profiler_bridge)
-> select_transforms (selector, by inferred type + auto_apply flag)
-> apply transforms (TransformEngine.transform_df)
-> record changes in Manifest
-> write output + manifest.json
-> save_run (history.py)
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

## Performance: vectorized fast paths + the native kernel

**Measured (2026-06-07):** on a realistic messy 1M-row frame the two
`series`-mode transforms `date_iso8601` and `phone_e164` were ~92% of the wall
(27.6 s + 16.5 s of ~48 s) because each called a Python library
(`dateutil` / `phonenumbers`) once per row via `map_elements` (~0.04-0.06 M
rows/s). Always re-measure with `benchmarks/speed_benchmark.py` or the
per-transform microbench before optimizing -- the win concentrates in a couple
of transforms, not the whole library.

- **`transforms/_fastpath.py::apply_with_residual`** is the shared three-tier
  resolver: (1) a vectorized Polars `fast_expr` resolves the well-formed common
  case in Rust, leaving uncertain rows null; (2) an optional native kernel runs
  on just the residual; (3) the per-row `dateutil`/`phonenumbers` reference
  settles whatever's left. On clean data tiers 2-3 never run. **Parity is safe
  by construction**: each tier must agree with the reference on the rows it
  resolves, so the fast path only claims rows it matches exactly. Result @ 1M:
  `date_iso8601` 76x, `phone_e164` 19x, `phone_digits` 4.9x; ~14x end-to-end.
- **Fast-path guards that are load-bearing for parity** (asserted over a random
  corpus in `tests/transforms/test_fastpath_parity.py`): dates require a 4-digit
  year (chrono's `%Y` greedily accepts 2-digit years -> 0093, but dateutil maps
  them to 1993); E.164 only claims NANP `^[2-9]\d{9}$` / `^1[2-9]\d{9}$` with no
  letters (a leading 1 on a 10-digit string is the country code to
  `phonenumbers`).

### goldenflow-native (optional compiled runtime)

Mirrors `goldenmatch-native`. Separate maturin/PyO3 **abi3** crate at
`packages/rust/extensions/native-flow/` (pymodule `_native`), shipped as the
`goldenflow-native` wheel; `pip install goldenflow[native]`. Loader discover
order in `goldenflow/core/_native_loader.py`: `goldenflow._native` (in-tree
build via `scripts/build_native.py`) -> `goldenflow_native._native` (wheel) ->
pure Python. In-tree `.so` is gitignored.

- **Scope = the international phone family** (`phone_e164/national/country_code/
  valid_arrow`), Arrow zero-copy in/out (`Series.to_arrow()` <-> kernel <->
  `pl.from_arrow`), GIL released. Wired as the tier-2 `native_fn` of
  `apply_with_residual`, so native only touches the residual the Polars fast
  path couldn't normalize. Dates are deliberately NOT native (Polars already
  vectorizes them; per-row chrono would be slower + reintroduce the 2-digit
  hazard).
- **`GOLDENFLOW_NATIVE`** env (mirrors GOLDENMATCH_NATIVE): `0` force Python,
  `1` require native for ALL components (parity/bench lane -- WILL change
  outputs where native diverges), `auto`/unset use native iff importable AND in
  `_GATED_ON`.
- **`_GATED_ON` is EMPTY by design (open parity gap).** The Rust `phonenumber`
  port is NOT byte-identical to the Python `phonenumbers` library: it formats
  some international national numbers differently (`+33 1 42 68 53 00` -> native
  `+3342685300` vs python `+33142685300`, dropping the national leading digit;
  ~6% of an intl-heavy corpus). It matches byte-for-byte on the NANP subset.
  Until reconciled (metadata-version alignment, or a curated parity-safe
  subset), phone stays ungated so `auto` never changes a cleaned value.
  `tests/transforms/test_native_parity.py` pins both the NANP match and the
  intl divergence. Measured native lift on the intl residual: ~5x (still
  parse-per-row in Rust, but no Python interpreter overhead).

## Streaming Module (streaming.py)

`StreamProcessor` wraps `TransformEngine` for incremental processing:

- `transform_one(record: dict)` -- single record, returns `TransformResult`
- `transform_batch(df: pl.DataFrame)` -- one batch
- `stream_file(path, chunk_size=10_000)` -- yields `TransformResult` per chunk
- `batches_processed` property -- count of batches completed

The `goldenflow stream` CLI command uses this with a Rich progress bar.

## Cloud Connectors

- `connectors/s3.py` -- `read_s3(uri)` / `write_s3(df, uri)` using boto3
- `connectors/gcs.py` -- `read_gcs(uri)` / `write_gcs(df, uri)` using google-cloud-storage

The file connector (`connectors/file.py`) detects `s3://` and `gs://` prefixes and delegates to the appropriate cloud connector automatically.

## History Module (history.py)

- Stores `RunRecord` JSON files in `~/.goldenflow/history/<run_id>.json`
- `save_run(record)` -- called by `TransformEngine.transform_file` after each run
- `list_runs(limit=20)` -- returns newest-first list of `RunRecord` objects
- `RunRecord` fields: `run_id`, `source`, `timestamp`, `rows`, `columns`, `transforms_applied`, `errors`, `duration_seconds`, `config_hash`, `manifest_path`

## Notebook Module (notebook.py)

Monkey-patches `_repr_html_` onto three classes at import time:
- `TransformResult._repr_html_` -- summary table + transform list + DataFrame preview
- `Manifest._repr_html_` -- transform audit trail with before/after samples
- `DatasetProfile._repr_html_` -- column profile table

Imported in `goldenflow/__init__.py` as a side-effect import (no symbols exported).

## LLM Corrector (llm/corrector.py)

Registers an additional transform that calls an LLM API for categorical correction. Activated by:
1. Setting `GOLDENFLOW_LLM=1` environment variable
2. Using `--llm` flag on the CLI (which does both the env var and the import)

Requires `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. Gracefully skips if no key is found.

## Domain Packs (All 5 Implemented)

Each domain pack lives in `goldenflow/domains/<name>.py` and subclasses `DomainPack` from `base.py`:

| Module | `load_domain()` key | Focus |
|--------|---------------------|-------|
| `people_hr.py` | `"people_hr"` | Names, SSNs, employment dates, gender |
| `healthcare.py` | `"healthcare"` | Patient IDs, diagnosis codes, clinical dates |
| `finance.py` | `"finance"` | Currency, account numbers, transaction dates |
| `ecommerce.py` | `"ecommerce"` | SKUs, prices, order dates, addresses |
| `real_estate.py` | `"real_estate"` | Property addresses, listing dates, prices |

`load_domain(name)` is exported from `goldenflow/domains/__init__.py` and returns the pack or `None`.

## CLI Modules

- `cli/main.py` -- all 14 commands (Typer app)
- `cli/errors.py` -- `cli_error_handler()` context manager for friendly error messages
- `cli/init_wizard.py` -- `run_wizard()` interactive setup wizard
- `cli/watch.py` -- `watch_directory()` polling loop
- `cli/schedule.py` -- `run_schedule()` interval parser + loop

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
- 234 tests passing
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

CI auto-publishes via `.github/workflows/publish-goldenflow.yml` (PR #166) on `release: published` for `goldenflow-v*` tags. The MCP Registry listing auto-syncs off the same event via `publish-mcp.yml`. Manual fallback:

```bash
python -m build && source .testing/.env && python -m twine upload dist/*
```

- **Version source**: `pyproject.toml` `[project] version` is canonical. `goldenflow/__init__.py:__version__` MUST match. They drifted in v1.1.x (pyproject=1.1.2, `__init__`=1.1.1) and shipped that way — fixed in 1.1.5. Bumping a release means touching both atomically.

## Remote MCP Server

Hosted on Railway, registered on Smithery:
- **Endpoint:** `https://goldenflow-mcp-production.up.railway.app/mcp/`
- **Smithery:** `https://smithery.ai/servers/benzsevern/goldenflow`
- **Server card:** `https://goldenflow-mcp-production.up.railway.app/.well-known/mcp/server-card.json`
- **Transport:** Streamable HTTP (via `StreamableHTTPSessionManager`)
- **Dockerfile:** `Dockerfile.mcp` (Python 3.12-slim, installs `.[mcp]`)
- **Railway project:** `golden-suite-mcp` (service: `goldenflow-mcp`, port 8150)
- **Local HTTP:** `goldenflow mcp-serve --transport http --port 8150`

## Gotchas

- `utf8-lossy` encoding on CSV reads (streaming.py, cli/main.py, api/server.py)
- `*.csv` is in `.gitignore` -- test fixtures need `!tests/fixtures/*.csv` exception
- `__version__` is defined ONLY in `goldenflow/__init__.py` -- don't add a second copy in `cli/main.py`
- Transform module imports in `__init__.py` are load-order sensitive -- modules that depend on others (e.g. `auto_correct` depends on `categorical`) must be imported after
- `mode="dataframe"` transforms receive the **entire** DataFrame and must return one with the same or more columns -- do not drop columns silently
- `category_auto_correct` is suppressed for high-cardinality columns (>10% unique values) by `selector.py` -- this is intentional
- GoldenCheck `FINDING_TRANSFORM_MAP` uses real check names (14 total) -- keys must match `goldencheck/profilers/*.py` check names exactly
- Transform count source of truth: `python -c "from goldenflow.transforms import registry; print(len(registry()))"`
- Ruff line length: 100 chars
- `config/learner.py` generates a YAML config from profiles; `config/loader.py` reads it back -- keep the Pydantic schema in `config/schema.py` as the single source of truth
- Cloud connectors (s3.py, gcs.py) have optional dependencies -- `pip install goldenflow[s3]` or `pip install goldenflow[gcs]`; they raise `ImportError` with a helpful message if the dependency is missing
- `streaming.py` reads the full file before batching (currently) -- for truly out-of-core processing, use Polars LazyFrame directly
- `history.py` stores runs in `~/.goldenflow/history/` -- this directory is created on first run and is not cleaned up automatically
- GitHub push protection will block commits containing NPM tokens -- never hardcode tokens in docs/code
- `packages/goldenflow-js/node_modules/` and `dist/` are gitignored -- don't `git add packages/goldenflow-js/` without the .gitignore in place
- TS date transforms must use UTC methods (`getUTCFullYear` etc.) -- local timezone methods produce different results across environments
- TS CSV parser must NOT coerce leading-zero strings to numbers (`"01234"` is a zip code, not `1234`)
- TS `TabularData.column()` converts "N/A" to null -- use `rawColumn()` when profiling to avoid inflating null counts
- TS history module lives in `src/node/` (not `src/core/`) -- it uses `node:fs`
- TS REST API and MCP server must sanitize file paths to prevent traversal
- `PORTING_GUIDE.md` has the master playbook for porting Golden Suite repos to TypeScript

## API Quick Reference

### transform_df() — Transform a DataFrame
```python
import goldenflow

# Zero-config (auto-detects and applies safe transforms)
result = goldenflow.transform_df(df)
cleaned = result.df
print(f"Applied {len(result.manifest.records)} transforms")

# Configured (explicit transforms per column)
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec

config = GoldenFlowConfig(transforms=[
    TransformSpec(column="first_name", ops=["strip", "title_case"]),
    TransformSpec(column="last_name", ops=["strip", "title_case"]),
    TransformSpec(column="email", ops=["strip", "lowercase"]),
    TransformSpec(column="phone", ops=["strip", "phone_national"]),
    TransformSpec(column="city", ops=["strip", "title_case"]),
    TransformSpec(column="address", ops=["strip", "collapse_whitespace"]),
])
result = goldenflow.transform_df(df, config=config)
```

### TransformResult fields
```python
result.df          # pl.DataFrame — transformed data
result.manifest    # Manifest — audit trail
result.manifest.records     # list[TransformRecord]
result.manifest.created_at  # str
```

### Available transforms (76)
**Text:** strip, lowercase, uppercase, title_case, normalize_unicode, normalize_quotes, collapse_whitespace, truncate, remove_punctuation, remove_html_tags, remove_urls, remove_digits, remove_emojis, fix_mojibake, normalize_line_endings, extract_numbers, pad_left, pad_right
**Phone:** phone_e164, phone_national, phone_digits, phone_validate, phone_country_code
**Name:** split_name, split_name_reverse, strip_titles, strip_suffixes, name_proper, initial_expand, nickname_standardize, merge_name
**Address:** address_standardize, address_expand, state_abbreviate, state_expand, zip_normalize, split_address, country_standardize, unit_normalize
**Date:** date_iso8601, date_us, date_eu, date_parse, age_from_dob, datetime_iso8601, extract_year, extract_month, extract_day, extract_quarter, extract_day_of_week, date_shift, date_validate
**Categorical:** category_auto_correct, category_standardize, category_from_file, boolean_normalize, gender_standardize, null_standardize
**Numeric:** currency_strip, percentage_normalize, round, clamp, to_integer, abs_value, fill_zero, comma_decimal, scientific_to_decimal
**Email:** email_lowercase, email_normalize, email_extract_domain, email_validate
**Identifiers:** ssn_format, ssn_mask, ein_format
**URL:** url_normalize, url_extract_domain

### Zero-config vs Configured — when to use which
- **Zero-config:** great for interactive exploration, finding what's wrong
- **Configured:** essential for pipelines and benchmarks where you need specific transforms
- Zero-config does NOT: title_case names, normalize phone formats, standardize addresses
- If you need title_case, phone_national, or address_standardize — use a config

### Schema mapping
```python
from goldenflow import SchemaMapper
import polars as pl

source = pl.DataFrame({"fname": ["John"], "lname": ["Smith"]})
target = pl.DataFrame({"first_name": [""], "last_name": [""]})
mapper = SchemaMapper()
mappings = mapper.map(source, target)  # returns list[ColumnMapping]
for m in mappings:
    print(f"{m.source} → {m.target} ({m.confidence:.0%})")
```

### Config schema
```python
GoldenFlowConfig(
    transforms=[TransformSpec(column="col", ops=["strip", "title_case"])],
    renames={"old_name": "new_name"},
    drop=["unwanted_column"],
    filters=[FilterSpec(column="age", condition="gt:0")],
    dedup=DedupSpec(columns=["email"], keep="first"),
)
```

## DQBench Integration
- **DQBench Transform Score: 100.00**
- Adapter: `dqbench/adapters/goldenflow.py`
- Run: `pip install dqbench && dqbench run goldenflow`

## Common Mistakes
- Expecting zero-config to title_case names — it only strips whitespace
- Expecting zero-config to normalize phone formats — use phone_national explicitly
- Using `result.manifest.total_transforms` — doesn't exist, use `len(result.manifest.records)`
- SchemaMapper.map() takes DataFrames, not file paths
