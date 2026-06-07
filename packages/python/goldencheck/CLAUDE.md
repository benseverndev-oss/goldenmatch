# GoldenCheck

Data validation that discovers rules from your data. DQBench Score: 88.40.

## Commands

```bash
pip install -e ".[dev]"          # Dev install
pip install -e ".[llm]"          # With LLM boost
pip install -e ".[mcp]"          # With MCP server
pip install -e ".[baseline]"     # With deep profiling baseline
goldencheck baseline data.csv    # Create statistical baseline
goldencheck scan data.csv --baseline goldencheck_baseline.yaml  # Drift detection
pytest --tb=short -v             # Run tests (550+ passing)
ruff check .                     # Lint
ruff check . --fix               # Auto-fix lint
goldencheck data.csv --no-tui    # Scan a file (CLI output)
goldencheck data.csv --deep      # Profile the FULL dataset (skip the 100K sample cap)
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
├── cli/           # Typer CLI (15 commands incl. baseline, scan, validate, review, diff, watch, fix, learn, mcp-serve)
├── engine/        # Scanner, validator, confidence, fixer, differ, watcher
├── profilers/     # column profilers (BaseProfiler ABC; incl. fuzzy-values near-dup)
├── baseline/      # Deep profiling: statistical, constraints, semantic, correlation, patterns, priors
├── drift/         # Drift detector (13 check types against saved baseline)
├── relations/     # Cross-column profilers (temporal, null correlation, numeric cross, age validation, composite-key, approx-duplicate, functional-dependency)
├── semantic/      # Type classifier + suppression engine + domain packs (healthcare, finance, ecommerce)
├── llm/           # LLM boost (providers, prompts, merger, budget, rule generator)
├── mcp/           # MCP server (count varies; see goldencheck mcp-serve --help for current)
├── config/        # Pydantic YAML config (goldencheck.yml)
├── core/          # native-kernel loader/gate (_native_loader.py)
├── models/        # Finding (with metadata dict), Profile dataclasses
├── notebook.py    # ScanResult wrapper + HTML renderers for Jupyter/Colab
├── reporters/     # Rich, JSON, CI output
└── tui/           # Textual TUI (4 tabs)
```

## goldencheck-native (optional compiled runtime)

Mirrors goldenmatch's native split. `goldencheck` stays pure-Python; `pip install
goldencheck[native]` pulls a separate maturin/abi3 wheel (`goldencheck-native`)
that accelerates the CPU-bound **deep-profiling** work (Benford conformance,
composite-key discovery, functional-dependency primitive). The sampled scan path
is already Polars/Arrow-vectorized and is NOT a native target.

- **Crates:** `packages/rust/extensions/goldencheck-core/` (pyo3-free kernels, the
  `score-core` analogue) + `goldencheck-native/` (abi3 PyO3 shim, standalone
  workspace, reads Arrow zero-copy via `PyArrowType<ArrayData>`, pinned `arrow=55`).
- **Loader:** `goldencheck/core/_native_loader.py` — discover order
  `goldencheck._native` (in-tree build) → `goldencheck_native._native` (wheel) →
  pure Python. `GOLDENCHECK_NATIVE=auto|0|1`. A component runs native only if it's
  in `_GATED_ON` AND its symbol is present (explicit capability probe, not a silent
  `AttributeError` fallback — the goldenmatch #688 footgun).
- **In-tree dev build:** `python scripts/build_goldencheck_native.py` (drops
  `goldencheck/_native.abi3.so`; gitignored). Needs `pyarrow` installed (the Arrow
  bridge). No maturin needed for in-tree.
- **Parity is the gate:** a kernel joins `_GATED_ON` only after
  `tests/core/test_native_parity.py` proves byte-identical output AND
  `benchmarks/deep_profile_benchmark.py` shows the wall moved. Benford: byte-identical
  (incl. exact powers-of-ten — the divisor is a correctly-rounded `1e{exp}` table,
  NOT `powi`, matching Python's bignum `10**exp`), ~16x faster on 1M rows.
- **Composite keys** (`relations/composite_key.py`): discovers minimal multi-column
  keys when no single-column key exists. Runs only after the cheap early-out (a
  single-column unique key short-circuits it), candidate cols capped at 12,
  key size ≤ 3.
- **LESSON — measure, the naive kernel LOST.** The first composite-key kernel hashed
  a `Box<[u64]>` tuple per row and was **0.4x** (2.5x slower than Polars `n_unique`,
  which is vectorized + multithreaded Rust). The gate caught it. Fix: interned ids
  are dense + key columns low-cardinality, so mixed-radix **pack each row-tuple into
  one `u128`** → allocation-free `FxHashSet<u128>` → **1.7x faster**. Don't gate a
  kernel on "it's Rust"; gate on the measured wall vs the *Polars* baseline, which is
  already fast.
- **Fuzzy values** (`profilers/fuzzy_values.py`): per-column near-duplicate VALUE
  detection (inconsistent categorical encodings: `California`/`Californa`/`CALIFORNIA`,
  `Jon`/`John`), `fuzzy_duplicate_values` check. Native kernel does trigram+prefix
  **blocking** + pairwise **Levenshtein** over a column's *distinct* values; the
  Python fallback uses the identical metric/blocking so clusters match. Whole-ROW
  fuzzy matching is deliberately NOT here — that's entity resolution (GoldenMatch).
  Guards: string dtype, ≥50 rows, distinct count in [3, 2000].
- **Strict FDs** (`relations/functional_dependency.py`): discovers exact
  single-column functional dependencies (`zip -> city`) = redundant/lookup
  columns, INFO, scan-path. The native `discover_functional_dependencies` kernel
  interns each column once + reuses across all pairs + **early-exits on the first
  violation** → **13.5x faster** than Polars recomputing a two-column `n_unique`
  per pair (early-exit is the edge; this kernel genuinely beats Polars). Guards
  (≥50 rows, skip constant deps / unique determinants, capped) keep it
  low-false-positive. Still NOT wired into `baseline/constraints.py` — that mines
  *approximate* FDs (confidence < 1.0); the kernel is strict-only, different
  semantics.

## `--deep` mode + duplicate-row detection

- **`--deep` / `scan_file(..., deep=True)`**: profiles the FULL population instead
  of the default 100K `maybe_sample` cap (`engine/scanner.py`). Removes sampling
  error on cardinality / uniqueness / rare-value / composite-key checks. Threaded
  through `scan_file` / `scan_dataframe` / `scan_file_with_llm` and both CLI paths
  (the `scan` command and the `goldencheck data.csv` shorthand parser in `main()`).
- **`relations/approx_duplicate.py`**: exact + near-duplicate ROW detection
  (`duplicate_rows`, `near_duplicate_rows` checks). Near = identical after
  lowercasing / collapsing whitespace / dropping punctuation. Pure-Polars by
  design — normalize+group-by is already a fast vectorized Polars path, so it is
  NOT a native kernel (gate is "beat Polars"). Edit-distance fuzzy matching (typos
  surviving normalization) is a heavier blocking+pairwise follow-up.
- **Release:** tag `goldencheck-native-v*` fires `publish-goldencheck-native.yml`
  (distinct from Python `v*` / TS `goldencheck-js-v*`). Bump BOTH
  `Cargo.toml` and `pyproject.toml` `[project].version` in lockstep (maturin reads
  pyproject; `skip-existing: true` silently no-ops a stale version).

## Pipeline Flow

```
read_file → maybe_sample → run profilers → (apply baseline priors if present)
→ classify semantic types → apply suppression → corroboration boost
→ (run drift checks if baseline) → sort by severity
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

- TDD: tests first, then implementation — 550+ tests total
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
- Wiki repo: `git clone https://github.com/benseverndev-oss/goldencheck.wiki.git /tmp/goldencheck.wiki` — sync with `cp docs/wiki/*.md /tmp/goldencheck.wiki/ && cd /tmp/goldencheck.wiki && git add -A && git commit -m "docs: sync" && git push`
- GitHub Pages: Jekyll + just-the-docs (dark), source in `docs/`, workflow in `.github/workflows/pages.yml`, live at `benseverndev-oss.github.io/goldencheck`
- Jekyll link anchors: `{% link file.md %}#anchor` NOT `{% link file.md#anchor %}`
- Classifier hint matching: hints ending with `_` are prefix-only (NOT substring) — `is_` matches `is_active` but NOT `diagnosis_desc`
- `Finding.metadata` dict is used by pattern_consistency for structured pattern data — suppression reads it
- Domain pack loading priority: user types > domain types > base types (dict insertion order matters)
- Cross-column findings: use only the "violating" column name to avoid FP on clean columns in benchmarks
- DQBench adapter does NOT call `apply_confidence_downgrade` — raw `scan_file()` output is scored
- `baseline/` and `drift/` modules may use numpy/scipy — keep isolated there, existing profilers stay numpy-free
- CI workflow (`test.yml`) installs `.[dev,baseline]` — baseline tests import numpy/scipy directly at module level
- String date columns cast to `pl.Date` (not `pl.Datetime`) — `pl.Datetime` cast fails on date-only strings like `"2024-01-01"`
- Benford's Law drift check requires values spanning 2+ orders of magnitude — test data must cover a wide range
- `source="baseline_drift"` on drift findings — distinct from `None` (profiler) and `"llm"` (LLM-generated)
- `goldencheck_baseline.yaml` auto-detected by scanner — user gets a `[dim]` console notice when this happens
- Version tests should use `from goldencheck import __version__` — never hardcode the version string
- GitHub repo has 20 topic limit — swap topics when adding new ones, don't try to add beyond 20

## API Quick Reference

### scan_file() — Scan a CSV for quality issues
```python
import goldencheck

findings = goldencheck.scan_file("data.csv")
for f in findings:
    print(f"[{f.severity}] {f.column}: {f.check} — {f.message}")
```

### create_baseline() — Learn dataset statistical properties
```python
from goldencheck import create_baseline, load_baseline

baseline = create_baseline("data.csv")
baseline.save("goldencheck_baseline.yaml")
```

### scan_file() with baseline — Detect drift
```python
findings, profile = scan_file("data.csv", baseline="goldencheck_baseline.yaml")
drift_findings = [f for f in findings if f.source == "baseline_drift"]
```

### health_score() — Get a letter grade + numeric score
```python
score = goldencheck.health_score("data.csv")
print(score)  # e.g. "B (78/100)"
```

### CLI commands
```bash
goldencheck baseline data.csv                                       # create statistical baseline
goldencheck scan data.csv --baseline goldencheck_baseline.yaml      # drift detection
goldencheck scan data.csv              # scan for issues
goldencheck profile data.csv           # column-level stats
goldencheck health-score data.csv      # health grade
goldencheck validate data.csv          # validate against pinned rules
goldencheck fix data.csv               # auto-fix safe issues
goldencheck mcp-serve                  # start MCP server
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

## TypeScript Port (packages/typescript/goldencheck/)

```bash
cd packages/typescript/goldencheck
npm install                      # Install deps
npm run typecheck                # tsc --noEmit
npm run test                     # vitest (144+ tests)
npm run build                    # tsup (ESM + CJS + .d.ts)
npm run dev                      # tsup --watch
```

### Architecture
- `src/core/` — edge-safe, zero Node.js deps (browsers, Workers, Edge Runtime)
- `src/node/` — Node 20+ only (file I/O, MCP, A2A, TUI, DB scanner)
- `src/cli.ts` — Commander.js CLI (`goldencheck-js`)
- Build: tsup (4 entry points: index, core/index, node/index, cli)
- Tests: vitest, `tests/unit/` + `tests/parity/`
- Package: `goldencheck` on npm, dual ESM/CJS exports

### Key Patterns
- **TabularData** wraps `Record<string, unknown>[]` — edge-safe Polars replacement
- **Never use `Math.min(...array)` or `Math.max(...array)`** — crashes on >65K elements; use loop-based min/max
- **Never import `node:fs`/`node:path`/`process` in `src/core/`** — breaks edge-safety guarantee
- CSV reader coerces values via `coerceValue()` (strings to numbers/booleans) to match Polars auto-inference
- `nodejs-polars` is optional peer dep — only for Parquet reading in Node layer
- Profiler interface: `profile(data: TabularData, column: string, context?: Record<string, unknown>): Finding[]`
- Findings are immutable — use `replaceFinding()` (spread), never mutate
- Mulberry32 PRNG (not Mersenne Twister) — deterministic but NOT matching Python's `random.Random(seed)`
- **MCP tools (v0.4.0): 17 = 7 core + 10 agent.** `src/node/mcp/agent-tools.ts` ports `goldencheck/mcp/agent_tools.py` (analyze_data, auto_configure, explain_finding, explain_column, review_queue, approve_reject, compare_domains, suggest_fix, pipeline_handoff, review_stats), wiring the existing TS agent + engine primitives. Composed into `TOOL_DEFINITIONS` (`CORE_TOOL_DEFINITIONS` + `AGENT_TOOLS`) and routed via `AGENT_TOOL_NAMES` in the server. Handlers are synchronous and return plain objects (the server's `handleTool` convention). `auto_configure` hand-emits the small `goldencheck.yml` (no runtime `yaml` dep). Shared `ReviewQueue` singleton with `__resetReviewQueueForTests`.

### Publishing
- npm publish: push tag `goldencheck-js-v*` triggers `.github/workflows/npm-publish.yml`
- Requires `NPM_TOKEN` GitHub secret
- Root `package.json` is orchestrator only (not a workspace): `npm run build:js`, `npm run test:js`

### Gotchas
- `src/core/engine/history.ts` and `scheduler.ts` use `node:fs` — function exports are in `node/index.ts`, only types re-exported from `core/index.ts`
- Bare `catch {}` blocks are prohibited — always log the error or let it propagate
- `ksTwoSample()` returns `pValue: 1` when `maxD === 0` (identical distributions)
- Differ groups findings by `(column, check)` arrays — supports multiple findings per key
