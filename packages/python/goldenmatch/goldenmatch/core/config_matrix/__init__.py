"""Config-matrix doc generator: the config surface IS the documented surface.

The full GoldenMatch config knob matrix (`docs-site/goldenmatch/config-matrix.mdx`)
is generated from code so it cannot silently drift:

- the pydantic `GoldenMatchConfig` tree (every config object, field, type, Literal
  choice, and default) is introspected directly -- the schema is the source of
  truth (unlike `configuration.mdx`, which is hand-maintained prose);
- the enumerated string vocabularies (`VALID_SCORERS`, `VALID_STRATEGIES`, ...)
  are rendered from the frozensets in `config/schemas.py`;
- the `GOLDENMATCH_*` environment knobs are scanned out of the Python + Rust
  source so the index is complete (semantics live in `tuning.mdx`).

CI regenerates the block between the `config-matrix:generated` markers and diffs
it against the committed page (`scripts/gen_config_matrix.py --check`), so adding
or removing a knob without updating the matrix turns the merge queue red.
"""
from __future__ import annotations

from .docgen import (
    DOC_PATH,
    MARKER_END,
    MARKER_START,
    docs_are_current,
    render_generated_block,
    scan_env_vars,
    write_docs,
)

__all__ = [
    "DOC_PATH",
    "MARKER_START",
    "MARKER_END",
    "render_generated_block",
    "scan_env_vars",
    "docs_are_current",
    "write_docs",
]
