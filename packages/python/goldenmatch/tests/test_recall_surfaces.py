"""MCP tool + REST endpoint surfaces for unsupervised recall estimation."""
from __future__ import annotations

import random

import polars as pl
import pytest


def _dupe_df(n_entities: int = 40) -> pl.DataFrame:
    rng = random.Random(7)
    fn = ["jonathan", "margaret", "robert", "elizabeth", "william", "patricia",
          "michael", "jennifer", "charles", "barbara"]
    ln = ["anderson", "richardson", "williams", "thompson", "martinez", "clark"]
    st = ["maple ave", "oak st", "cedar ln", "pine rd", "elm blvd", "main st"]
    rows = []
    rid = 0
    for _ in range(n_entities):
        g, s, a = rng.choice(fn), rng.choice(ln), rng.choice(st)
        for _ in range(rng.randint(1, 3)):
            gg = g if rng.random() < 0.5 else g[:-1]
            rows.append({"id": rid, "given": gg, "surname": s, "street": a})
            rid += 1
    return pl.DataFrame(rows)


def test_mcp_certify_recall_tool_present():
    from goldenmatch.mcp.agent_tools import AGENT_TOOLS
    names = {t.name for t in AGENT_TOOLS}
    assert "certify_recall" in names


def test_mcp_certify_recall_dispatch(tmp_path):
    from goldenmatch.mcp.agent_tools import _dispatch

    csv = tmp_path / "data.csv"
    _dupe_df().write_csv(csv)
    out = _dispatch("certify_recall", {"file_path": str(csv)}, object)
    assert set(out) >= {"estimated_recall", "n_systems", "found_pairs",
                        "system_overlap", "estimable", "note"}
    assert out["n_systems"] >= 3
    assert out["estimable"] is True
    assert 0.0 <= out["estimated_recall"] <= 1.0


def test_mcp_certify_recall_missing_file():
    from goldenmatch.mcp.agent_tools import _dispatch
    out = _dispatch("certify_recall", {"file_path": "/no/such/file.csv"}, object)
    assert "error" in out


def test_rest_certify_recall_method():
    from goldenmatch.api.server import MatchServer

    df = _dupe_df()
    cfg = __import__("goldenmatch").auto_configure_df(df, confidence_required=False)
    for mk in cfg.get_matchkeys():
        if getattr(mk, "rerank", False):
            mk.rerank = False

    class _StubEngine:
        def __init__(self, data):
            self.data = data

    out = MatchServer(_StubEngine(df), cfg).certify_recall()
    assert out["estimable"] is True
    assert out["n_systems"] >= 3
    assert 0.0 <= out["estimated_recall"] <= 1.0


def test_rest_certify_recall_no_data():
    from goldenmatch.api.server import MatchServer

    class _Empty:
        data = None

    out = MatchServer(_Empty(), None).certify_recall()
    assert "error" in out
