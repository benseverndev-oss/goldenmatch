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
