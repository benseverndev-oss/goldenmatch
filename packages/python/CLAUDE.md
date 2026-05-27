# Python packages — Claude notes

## Workspace

- uv workspace rooted at `/pyproject.toml`. Members listed in `[tool.uv.workspace]` and `[tool.uv.sources]`. Both must be updated when adding a package.
- Cross-package deps use `goldencheck-types`-style names (no `@workspace/` prefix). All resolved via `[tool.uv.sources] = { workspace = true }` at the root.

## Common friction

- An optional-dependency extra pointing at a package not yet on PyPI breaks `uv sync --all-packages` repo-wide (uv resolves the whole extra graph during lock). Fix: workspace-local `[tool.uv.sources] <pkg> = { path = "..." }` so dev/CI resolve locally; the published wheel still resolves from PyPI (uv.sources is workspace-local). Bit `goldenmatch[native]` -> `goldenmatch-native`.
- After adding a workspace member, `uv sync` may not install it editable. If `import <pkg>` fails, run `uv pip install -e packages/python/<pkg>`.
- `uv run pytest` sometimes misses workspace members on Windows. Prefer `.venv/Scripts/python.exe -m pytest <path>` for reliable runs.
- pyarrow is required for polars `.from_pandas()`; if `ImportError: pyarrow is required`, run `uv pip install pyarrow`.
- pytest is in `[dependency-groups] dev`; absent in fresh venvs after `uv sync` until it pulls dev groups.

## Pre-existing pyproject pitfalls

- `goldenmatch[pprl]` previously required `mp-spdz` (not on PyPI); cleanup removed that. Don't reintroduce; pprl users install mp-spdz manually.

## Per-package details

Each package has its own `CLAUDE.md`: `goldencheck/`, `goldenflow/`, `goldenmatch/`, `goldenpipe/`, `infermap/`, `goldencheck-types/`. Read those for package-specific patterns before changing internals.
