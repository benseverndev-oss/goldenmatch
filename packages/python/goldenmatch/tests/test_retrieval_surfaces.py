"""Surface coverage for semantic retrieval (#1089).

``retrieve_similar_records`` (the Python API) is covered in
``tests/test_retrieval.py``. This file verifies the over-the-wire exposure the
done-bar requires: the MCP ``retrieve_similar`` tool, the A2A
``retrieve_similar`` skill, and the REST ``POST /retrieve`` endpoint -- each
routing through the same core function and returning the ``RetrievedRecord``
shape. The zero-config in-house embedder keeps these offline (no cloud/torch).
"""
from __future__ import annotations

import json

import polars as pl
import pytest

try:
    import aiohttp  # noqa: F401  # availability check for the optional [agent] extra
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

_ROWS = [
    {"name": "Acme Corporation", "industry": "tech"},
    {"name": "Globex Industries", "industry": "manufacturing"},
    {"name": "Initech Software", "industry": "tech"},
]


@pytest.fixture
def corpus_csv(tmp_path):
    path = tmp_path / "corpus.csv"
    pl.DataFrame(_ROWS).write_csv(path)
    return str(path)


# ── MCP tool ──────────────────────────────────────────────────────────────────


def test_mcp_retrieve_similar_ranks_query_match(corpus_csv):
    from goldenmatch.mcp.agent_tools import handle_agent_tool

    out = handle_agent_tool(
        "retrieve_similar",
        {"file_path": corpus_csv, "query": "Acme Corporation", "column": "name", "k": 3},
    )
    payload = json.loads(out[0].text)
    assert payload["count"] >= 1
    assert payload["results"][0]["record"]["name"] == "Acme Corporation"
    # internal columns are stripped from the returned record
    assert all(not k.startswith("__") for k in payload["results"][0]["record"])


def test_mcp_retrieve_similar_filter_prefilters(corpus_csv):
    from goldenmatch.mcp.agent_tools import handle_agent_tool

    out = handle_agent_tool(
        "retrieve_similar",
        {
            "file_path": corpus_csv,
            "query": "software",
            "column": "name",
            "filters": {"industry": "tech"},
        },
    )
    payload = json.loads(out[0].text)
    industries = {r["record"]["industry"] for r in payload["results"]}
    assert industries <= {"tech"}


def test_mcp_retrieve_similar_missing_params_error(corpus_csv):
    from goldenmatch.mcp.agent_tools import handle_agent_tool

    out = handle_agent_tool("retrieve_similar", {"file_path": corpus_csv, "column": "name"})
    assert "error" in json.loads(out[0].text)


def test_mcp_retrieve_similar_bad_column_error(corpus_csv):
    from goldenmatch.mcp.agent_tools import handle_agent_tool

    out = handle_agent_tool(
        "retrieve_similar",
        {"file_path": corpus_csv, "query": "x", "column": "nope"},
    )
    assert "error" in json.loads(out[0].text)


def test_mcp_retrieve_similar_file_not_found():
    from goldenmatch.mcp.agent_tools import handle_agent_tool

    out = handle_agent_tool(
        "retrieve_similar",
        {"file_path": "/no/such/file.csv", "query": "x", "column": "name"},
    )
    assert "error" in json.loads(out[0].text)


def test_retrieve_similar_registered_on_mcp_surface():
    from goldenmatch.mcp.server import TOOLS

    assert "retrieve_similar" in {t.name for t in TOOLS}


# ── A2A skill ───────────────────────────────────────────────────────────────


def test_a2a_retrieve_similar_skill(corpus_csv):
    from goldenmatch.a2a.skills import dispatch_skill

    result = dispatch_skill(
        "retrieve_similar",
        {"file_path": corpus_csv, "query": "Acme Corporation", "column": "name", "k": 3},
    )
    assert result["count"] >= 1
    assert result["results"][0]["record"]["name"] == "Acme Corporation"


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp ([agent] extra) not installed")
def test_a2a_retrieve_similar_on_agent_card():
    from goldenmatch.a2a.server import build_agent_card

    ids = {s["id"] for s in build_agent_card("http://localhost:8080")["skills"]}
    assert "retrieve_similar" in ids


# ── REST endpoint ───────────────────────────────────────────────────────────


class _StubEngine:
    """Minimal engine stand-in: the REST retrieve path only reads ``.data``."""

    def __init__(self, df: pl.DataFrame):
        self.data = df


def _make_server():
    from goldenmatch.api.server import MatchServer

    df = pl.DataFrame(_ROWS).with_row_index("__row_id__")
    return MatchServer(_StubEngine(df), config=None)  # type: ignore[arg-type]


def test_rest_retrieve_similar_ranks_and_passes_row_id():
    server = _make_server()
    result = server.retrieve_similar("Acme Corporation", "name", k=3)
    assert result["count"] >= 1
    top = result["results"][0]
    assert top["record"]["name"] == "Acme Corporation"
    # row_id is threaded through from the loaded frame's __row_id__
    assert top["row_id"] == 0


def test_rest_retrieve_similar_bad_column():
    server = _make_server()
    result = server.retrieve_similar("x", "nope")
    assert "error" in result


def test_rest_retrieve_similar_filters():
    server = _make_server()
    result = server.retrieve_similar("software", "name", filters={"industry": "tech"})
    industries = {r["record"]["industry"] for r in result["results"]}
    assert industries <= {"tech"}
