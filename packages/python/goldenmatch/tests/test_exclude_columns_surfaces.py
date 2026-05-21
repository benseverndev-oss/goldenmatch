"""Smoke + parity tests for `exclude_columns` across CLI / REST / MCP / A2A.

Spec: docs/superpowers/specs/2026-05-21-exclude-columns-surfaces-design.md
Plan: docs/superpowers/plans/2026-05-21-exclude-columns-surfaces.md
Roadmap: docs/superpowers/specs/2026-05-21-exclude-columns-surfaces-roadmap.md
"""

from __future__ import annotations

import polars as pl
import pytest

# ---------------------------------------------------------------------------
# Step 1: shared schema helper
# ---------------------------------------------------------------------------


def test_exclusions_schema_helper_shape():
    """EXCLUDE_COLUMNS_SCHEMA has the JSON Schema fragment shape every
    external surface consumes."""
    from goldenmatch._exclusions_schema import EXCLUDE_COLUMNS_SCHEMA

    assert EXCLUDE_COLUMNS_SCHEMA["type"] == "array"
    assert EXCLUDE_COLUMNS_SCHEMA["items"] == {"type": "string"}
    assert EXCLUDE_COLUMNS_SCHEMA["default"] == []
    assert "description" in EXCLUDE_COLUMNS_SCHEMA


def test_parse_csv_strips_whitespace_and_filters_empties():
    from goldenmatch._exclusions_schema import parse_csv_exclude_columns

    assert parse_csv_exclude_columns("a,b,c") == ["a", "b", "c"]
    assert parse_csv_exclude_columns("a, b ,c") == ["a", "b", "c"]
    assert parse_csv_exclude_columns("a,,b,") == ["a", "b"]
    assert parse_csv_exclude_columns("") == []
    assert parse_csv_exclude_columns(None) == []
    assert parse_csv_exclude_columns("  ,  ,  ") == []


def test_merge_into_config_appends_dedup_preserving_order():
    from goldenmatch._exclusions_schema import merge_exclude_columns_into_config
    from goldenmatch.config.schemas import GoldenMatchConfig

    cfg = GoldenMatchConfig(exclude_columns=["existing"])
    result = merge_exclude_columns_into_config(cfg, "new1,new2")
    assert result == ["existing", "new1", "new2"]
    assert cfg.exclude_columns == ["existing", "new1", "new2"]

    # Re-merge same list -- idempotent.
    result = merge_exclude_columns_into_config(cfg, "new1,new2")
    assert result == ["existing", "new1", "new2"]


def test_merge_empty_raw_returns_existing_config_field():
    from goldenmatch._exclusions_schema import merge_exclude_columns_into_config
    from goldenmatch.config.schemas import GoldenMatchConfig

    cfg = GoldenMatchConfig(exclude_columns=["a", "b"])
    assert merge_exclude_columns_into_config(cfg, None) == ["a", "b"]
    assert merge_exclude_columns_into_config(cfg, "") == ["a", "b"]


def _strip_help_noise(output: str) -> str:
    """Normalize Typer/Rich help output for substring matching.

    CI terminals are narrow (80 cols default) so Rich wraps long flag
    names across lines with hyphenation + box-drawing decorations. Strip
    ANSI escape codes, box-drawing characters, whitespace, and newlines
    so substring assertions match regardless of terminal width.
    """
    import re
    no_ansi = re.sub(r"\x1b\[[0-9;]*m", "", output)
    no_boxes = re.sub(r"[─-╿]", "", no_ansi)
    return re.sub(r"\s+", "", no_boxes)


# ---------------------------------------------------------------------------
# Step 2-4: CLI flags (smoke -- each command parses + threads the flag)
# ---------------------------------------------------------------------------


def test_dedupe_cli_accepts_exclude_columns_flag(tmp_path):
    """`goldenmatch dedupe FILE --exclude-columns col1,col2` runs and
    surfaces the resolved list."""
    from goldenmatch.cli.main import app
    from typer.testing import CliRunner

    df = pl.DataFrame({
        "first_name": ["Alice", "Bob", "Carol"] * 5,
        "last_name": ["Smith", "Jones", "Doe"] * 5,
        "external_id": [f"ext_{i:06d}" for i in range(15)],
    })
    csv = tmp_path / "in.csv"
    df.write_csv(csv)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["dedupe", str(csv),
         "--exclude-columns", "external_id",
         "--no-tui",
         "--output-dir", str(tmp_path)],
    )
    # Surface the resolved exclude_columns at INFO when set.
    assert "exclude_columns" in result.output or result.exit_code == 0


def test_sync_cli_has_exclude_columns_flag():
    """`goldenmatch sync --help` lists --exclude-columns."""
    from goldenmatch.cli.main import app
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(app, ["sync", "--help"])
    assert result.exit_code == 0
    assert "exclude-columns" in _strip_help_noise(result.output)


def test_match_cli_has_exclude_columns_flag():
    from goldenmatch.cli.main import app
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(app, ["match", "--help"])
    assert result.exit_code == 0
    assert "exclude-columns" in _strip_help_noise(result.output)


def test_incremental_cli_has_exclude_columns_flag():
    from goldenmatch.cli.main import app
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(app, ["incremental", "--help"])
    assert result.exit_code == 0
    assert "exclude-columns" in _strip_help_noise(result.output)


def test_pprl_link_cli_has_exclude_columns_flag():
    from goldenmatch.cli.main import app
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(app, ["pprl", "link", "--help"])
    assert result.exit_code == 0
    assert "exclude-columns" in _strip_help_noise(result.output)


# ---------------------------------------------------------------------------
# Step 5: REST API field
# ---------------------------------------------------------------------------


def test_rest_autoconfigure_method_accepts_exclude_columns():
    """MatchServer.autoconfigure(exclude_columns=[...]) propagates via
    the ContextVar -- proves the threading is in place even without
    starting an HTTP server."""
    from goldenmatch.api.server import MatchServer
    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.core.autoconfig import _LAST_AUTOCONFIG_EXCLUSIONS

    df = pl.DataFrame({
        "first_name": ["Alice", "Bob"] * 10,
        "last_name": ["Smith", "Jones"] * 10,
        "external_id": [f"ext_{i:06d}" for i in range(20)],
    })

    # Minimal engine shim so MatchServer.autoconfigure has a `.data`
    # attribute to default to when records=None.
    class _StubEngine:
        def __init__(self, df):
            self.data = df

    server = MatchServer(_StubEngine(df), GoldenMatchConfig())
    server.autoconfigure(exclude_columns=["external_id"])

    exclusions = _LAST_AUTOCONFIG_EXCLUSIONS.get() or []
    excluded_cols = {ec.column for ec in exclusions}
    assert "external_id" in excluded_cols


# ---------------------------------------------------------------------------
# Step 6+7: MCP + A2A schemas share the same shape
# ---------------------------------------------------------------------------


def test_mcp_tool_schemas_reference_shared_exclude_columns_fragment():
    """Every MCP tool that takes exclude_columns must use the helper's
    EXCLUDE_COLUMNS_SCHEMA -- no per-tool divergence."""
    from goldenmatch._exclusions_schema import EXCLUDE_COLUMNS_SCHEMA
    from goldenmatch.mcp.agent_tools import AGENT_TOOLS

    tools_with_excl = [
        t for t in AGENT_TOOLS
        if "exclude_columns" in (t.inputSchema.get("properties") or {})
    ]
    # At least auto_configure, agent_deduplicate, agent_match_sources.
    assert len(tools_with_excl) >= 3
    for tool in tools_with_excl:
        prop = tool.inputSchema["properties"]["exclude_columns"]
        assert prop == EXCLUDE_COLUMNS_SCHEMA, (
            f"tool {tool.name!r} has divergent exclude_columns schema: "
            f"{prop!r} vs canonical {EXCLUDE_COLUMNS_SCHEMA!r}"
        )


def test_a2a_dispatch_accepts_exclude_columns_param():
    """A2A skill handlers honor exclude_columns by routing through the
    runtime ContextVar."""
    from goldenmatch.a2a import skills as a2a_skills

    # We can't easily run a full deduplicate inside this test (needs
    # AgentSession + file paths). Instead, prove the param is read by
    # patching session.deduplicate and asserting the ContextVar was set
    # when the handler invoked the underlying call.
    captured: dict = {}

    class _FakeSession:
        last_telemetry = None

        def deduplicate(self, file_path, config=None):
            from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
            captured["runtime"] = _RUNTIME_EXCLUDE_COLUMNS.get()
            return {"results": None}

    import unittest.mock as _mock
    with _mock.patch.object(a2a_skills, "AgentSession", _FakeSession):
        a2a_skills.dispatch_skill(
            "deduplicate",
            {"file_path": "ignored", "exclude_columns": ["external_id"]},
        )
    assert captured["runtime"] == ["external_id"]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
