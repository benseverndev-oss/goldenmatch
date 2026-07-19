"""The generated config-matrix page must stay in lockstep with the config surface.

Mirrors tests/test_config_lint.py: the pydantic schema + VALID_* vocabularies +
scanned GOLDENMATCH_* env set are the source of truth, and the committed
`config-matrix.mdx` generated block must match a fresh render. Adding/removing a
config knob without regenerating turns this test (and the merge queue) red.
Regenerate with: python scripts/gen_config_matrix.py --write
"""
from __future__ import annotations

from goldenmatch.config import schemas as S
from goldenmatch.core.config_matrix import docgen


def test_config_matrix_is_current():
    assert docgen.docs_are_current(), (
        f"{docgen.DOC_PATH} is stale vs the config surface. "
        "Run: python scripts/gen_config_matrix.py --write"
    )


def test_generated_markers_present():
    text = docgen.DOC_PATH.read_text(encoding="utf-8")
    assert docgen.MARKER_START in text
    assert docgen.MARKER_END in text


def test_env_scan_finds_known_flags():
    flat = {name for names in docgen.scan_env_vars().values() for name in names}
    # A representative slice across native, FS, frame, and server areas.
    for expected in ("GOLDENMATCH_NATIVE", "GOLDENMATCH_FS_MISSING",
                     "GOLDENMATCH_FRAME", "GOLDENMATCH_MCP_TOKEN"):
        assert expected in flat, f"env scan missed {expected}"


def test_root_model_and_vocabs_rendered():
    block = docgen.render_generated_block()
    assert "### `GoldenMatchConfig`" in block
    # Every enumerated scorer/strategy value must appear in the vocab tables.
    for value in (*S.VALID_SCORERS, *S.VALID_STRATEGIES, *S.VALID_STANDARDIZERS):
        assert f"`{value}`" in block, f"vocabulary value {value} not rendered"
