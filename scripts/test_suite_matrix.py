"""Gate: the repo-level suite surface matrix must match the live cross-package
surface (and render deterministically). Mirrors the `--check` CI run.
Regenerate a stale page with: python scripts/gen_suite_matrix.py --write
"""
from __future__ import annotations

import gen_suite_matrix as g


def test_suite_matrix_current():
    committed = g.PAGE.read_text(encoding="utf-8") if g.PAGE.exists() else ""
    assert committed == g._compose(g.render_block()), (
        "docs-site/suite-matrix.mdx is stale vs the live cross-package surface. "
        "Run: python scripts/gen_suite_matrix.py --write"
    )


def test_suite_matrix_deterministic():
    # No memory addresses / set-ordering may leak into the generated block.
    assert g.render_block() == g.render_block()
