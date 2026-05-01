# Top-level tests

## Layout

- `tests/fixtures/` — CSVs and other shared fixtures used by integration + parity tests.
- `tests/integration/` — multi-package end-to-end tests (e.g., `infermap` → `goldencheck` handoff).
- `tests/parity/` (planned) — Python ↔ TS parity harness; not yet implemented.

## How they're discovered

Run from monorepo root: `.venv/Scripts/python.exe -m pytest tests/integration/`. The root `pyproject.toml` has no testpaths config; specify the dir explicitly.

## Adding a new fixture

Drop the CSV into `tests/fixtures/`. Reference via `Path(__file__).resolve().parent.parent / "fixtures" / "<name>.csv"`.

## Related per-package tests

Each package's own tests live under `packages/<lang>/<name>/tests/`. Top-level tests are reserved for cross-package contracts.
