"""Gate: the repo-thesis weakness page must match the thesis-conformance scorecard
(and render deterministically). Mirrors the `--check` CI run.
Regenerate a stale page with: python scripts/gen_thesis_weaknesses.py --write
"""
from __future__ import annotations

import gen_thesis_weaknesses as g


def test_thesis_weaknesses_current():
    committed = g.PAGE.read_text(encoding="utf-8") if g.PAGE.exists() else ""
    assert committed == g._compose(g.render_block()), (
        "docs-site/thesis-weaknesses.mdx is stale vs the thesis-conformance scorecard. "
        "Run: python scripts/gen_thesis_weaknesses.py --write"
    )


def test_thesis_weaknesses_deterministic():
    # No set-ordering / memory addresses may leak into the generated block.
    assert g.render_block() == g.render_block()
