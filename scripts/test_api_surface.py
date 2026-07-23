"""Gate: the api-surface capability matrix must match the live surface (versions +
MCP counts from the manifests) and the inline figures that mirror it. Mirrors the
`--check` CI run. Regenerate a stale table with:
    python scripts/gen_api_surface.py --write
"""
from __future__ import annotations

import gen_api_surface as g


def test_api_surface_current():
    problems = g.check()
    assert problems == [], (
        "docs-site/reference/api-surface.mdx capability matrix is stale vs the live "
        "surface. Run: python scripts/gen_api_surface.py --write\n  - "
        + "\n  - ".join(problems)
    )


def test_api_surface_table_deterministic():
    # No set-ordering / addresses may leak into the generated block.
    assert g.render_block() == g.render_block()
