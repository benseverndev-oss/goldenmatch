# Contributing to the Golden Suite

This is a polyglot monorepo — Python, Rust, TypeScript, dbt, and GitHub Actions
live side by side under `packages/`. This guide covers the shared workflow; each
package also has its own `CLAUDE.md` with package-specific notes worth reading
before changing internals.

## Repository layout

```
packages/
  python/      uv workspace: goldenmatch, goldencheck, goldenflow, goldenpipe,
               infermap, goldenanalysis, goldensuite-mcp, goldencheck-types
  rust/        cargo crates under extensions/ (native accelerators, FFI UDFs,
               score/graph/fingerprint cores, wasm, pgrx, embed)
  typescript/  pnpm workspace mirrors of the Python packages (+ wasm runtime)
  dbt/         dbt package
  actions/     composite GitHub Actions
```

## Local setup

**Python** (uv workspace rooted at the repo `pyproject.toml`):

```bash
uv sync --all-packages
uv run pytest packages/python/<pkg>        # run one package's tests
uv run ruff check packages/python/<pkg>    # lint (required in CI)
```

**TypeScript** (pnpm + Turborepo; `pnpm@9.15.0` is pinned — Corepack rejects
range specifiers):

```bash
corepack enable          # or: npm i -g pnpm@9.15.0
pnpm install
pnpm turbo run build test typecheck
```

**Rust**:

```bash
cargo test --workspace --manifest-path packages/rust/extensions/Cargo.toml
# native (maturin/abi3) extensions build via their scripts, e.g.
python scripts/build_native.py
```

**Pre-commit hooks** (fast lint + secret checks, shifted left of CI):

```bash
uv tool install pre-commit   # or: pipx install pre-commit
pre-commit install           # runs ruff + whitespace + private-key check on commit
pre-commit run --all-files   # run against the whole tree on demand
```

## Branches, commits, and PRs

- Branch off `main`; open a **pull request** back into `main` (draft until ready).
- Commit messages use a type prefix: `feat:`, `fix:`, `ci:`, `docs:`, `test:`,
  `chore:` (optionally scoped, e.g. `fix(goldenpipe): ...`). Keep them ASCII.
- CI is path-filtered: only the areas you touched run. The single required check
  is **`ci-required`** — it must be green to merge. A change to
  `.github/workflows/ci.yml` re-runs every job so the filter logic stays tested.

## Versioning — bump in lockstep

A package's version is declared in **several** files and they **must** agree;
nothing else will keep them honest (goldenflow once shipped 1.1.x with
`pyproject.toml` = 1.1.2 while `__init__.py` said 1.1.1). When you bump a
version, update **every** spot for that package:

- **Python dist:** `pyproject.toml` `[project].version`, the package
  `__init__.py` `__version__`, and `server.json` (if present).
- **Native (maturin) crate:** `Cargo.toml` `[package].version`,
  `pyproject.toml` `[project].version`, and any native `__init__.py` fallback.
- Don't hardcode the version elsewhere — derive it (e.g.
  `from <pkg> import __version__`) so it can't drift.

CI enforces this with `scripts/check_version_consistency.py`; run it before
pushing:

```bash
python scripts/check_version_consistency.py
```

## Releasing

Releases are tag-driven; the matching workflow publishes:

- Python → PyPI: tag `v<x.y.z>` (per-package `publish-*.yml`).
- TypeScript → npm: tag `<pkg>-js-v<x.y.z>`.
- Native wheels: tag `<pkg>-native-v<x.y.z>`.

Cut the version bump (lockstep, above) in a PR first, merge, then tag.

## Tests and quality bar

- New behavior needs tests. Keep each test self-contained — CI runs `pytest -n
  auto`, and tests cannot share global/registry state across xdist workers.
- Anchor fixture paths to `__file__`, not the CWD (CWD differs between local and
  CI runs).
- `ruff` (Python) and `tsc --noEmit` (TypeScript) must pass.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By taking
part you agree to uphold it. Report unacceptable behavior to `ben@bensevern.dev`
(subject: `[conduct]`).

Thanks for contributing!
