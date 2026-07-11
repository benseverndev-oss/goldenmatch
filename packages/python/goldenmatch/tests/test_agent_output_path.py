# Task 1.1 spike result — golden extraction, verified working:
#   raw = AgentSession().deduplicate(file_path)
#   golden_df = raw["results"].golden          # polars.DataFrame | None, populated by default
# The match side exposes the linked frame as `raw["results"].matched` (MatchResult.matched).
# Both frames may carry `__`-prefixed internal columns which are stripped before write_csv.
from pathlib import Path

import polars as pl

from goldenmatch.core.agent import AgentSession


def _fixture_csv(tmp_path: Path) -> str:
    df = pl.DataFrame({
        "name": ["John Smith", "Jon Smith", "Mary Jones", "Karen White"],
        "email": ["j@x.com", "j@x.com", "m@y.com", "k@z.com"],
    })
    p = tmp_path / "in.csv"
    df.write_csv(p)
    return str(p)


def _two_fixtures(tmp_path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    pl.DataFrame({"name": ["John Smith", "Mary Jones"], "id": [1, 2]}).write_csv(a)
    pl.DataFrame({"name": ["Jon Smith", "Karen White"], "id": [9, 8]}).write_csv(b)
    return str(a), str(b)


def test_dedupe_result_exposes_golden(tmp_path):
    raw = AgentSession().deduplicate(_fixture_csv(tmp_path))
    result = raw["results"]
    assert getattr(result, "golden", None) is not None
    assert result.golden.height >= 1


def test_agent_deduplicate_writes_golden(tmp_path):
    from goldenmatch.mcp.agent_tools import _dispatch

    out = tmp_path / "golden.csv"
    res = _dispatch(
        "agent_deduplicate",
        {"file_path": _fixture_csv(tmp_path), "output_path": str(out)},
        AgentSession,
    )
    assert res["golden_path"] == str(out)
    assert res["golden_records"] >= 1
    assert out.exists()
    got = pl.read_csv(out)
    assert got.height == res["golden_records"]
    assert not any(c.startswith("__") for c in got.columns)


def test_agent_deduplicate_no_output_path_unchanged(tmp_path):
    from goldenmatch.mcp.agent_tools import _dispatch

    res = _dispatch("agent_deduplicate", {"file_path": _fixture_csv(tmp_path)}, AgentSession)
    assert "golden_path" not in res
    assert "results" in res


def test_agent_match_sources_writes_matches(tmp_path):
    from goldenmatch.mcp.agent_tools import _dispatch

    a, b = _two_fixtures(tmp_path)
    out = tmp_path / "matches.csv"
    res = _dispatch(
        "agent_match_sources",
        {"file_a": a, "file_b": b, "output_path": str(out)},
        AgentSession,
    )
    assert res["matches_path"] == str(out)
    assert out.exists()


def test_agent_match_sources_no_output_path_unchanged(tmp_path):
    from goldenmatch.mcp.agent_tools import _dispatch

    a, b = _two_fixtures(tmp_path)
    res = _dispatch("agent_match_sources", {"file_a": a, "file_b": b}, AgentSession)
    assert "matches_path" not in res
    assert "results" in res


def test_agent_deduplicate_output_path_rejected_surfaces_top_level_error(
    tmp_path, monkeypatch
):
    """A path outside GOLDENMATCH_ALLOWED_ROOT must fail loudly (top-level error).

    The `safe_path` guard reads GOLDENMATCH_ALLOWED_ROOT; point it at a
    sandbox dir and aim output_path at a SIBLING outside it. The guard
    rejects, and the fix must surface a top-level `error` (not just the
    buried golden_error) so an agent can't mistake it for a successful write.
    """
    from goldenmatch.mcp.agent_tools import _dispatch

    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(root))

    # Fixture lives INSIDE the root (so ingest/read isn't what's rejected);
    # the OUTPUT target is a sibling OUTSIDE the root.
    fixture = _fixture_csv(root)
    outside = tmp_path / "escape.csv"

    res = _dispatch(
        "agent_deduplicate",
        {"file_path": fixture, "output_path": str(outside)},
        AgentSession,
    )

    assert res["golden_path"] is None
    assert "error" in res  # top-level, unmissable
    assert "golden_error" in res
    assert not outside.exists()  # nothing written outside the root


def test_agent_deduplicate_no_golden_frame_is_benign(tmp_path, monkeypatch):
    """A None golden frame is a benign 'nothing to write' — namespaced error
    only, NO top-level `error` (that's reserved for path rejection)."""
    from goldenmatch.mcp import agent_tools
    from goldenmatch.mcp.agent_tools import _dispatch

    # Force golden to None on whatever result the pipeline produced, without
    # depending on data that happens to yield an empty golden frame.
    real_write = agent_tools._write_frame_csv

    def _none_frame_write(output_path, frame, label, **kw):
        return real_write(output_path, None, label, **kw)

    monkeypatch.setattr(agent_tools, "_write_frame_csv", _none_frame_write)

    out = tmp_path / "golden.csv"
    res = _dispatch(
        "agent_deduplicate",
        {"file_path": _fixture_csv(tmp_path), "output_path": str(out)},
        AgentSession,
    )

    assert res["golden_path"] is None
    assert res.get("golden_error") == "no golden frame produced"
    assert "error" not in res  # benign: no top-level failure signal
    assert not out.exists()
