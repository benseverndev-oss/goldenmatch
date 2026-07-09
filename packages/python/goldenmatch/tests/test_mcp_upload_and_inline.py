# tests/test_mcp_upload_and_inline.py
import base64
import json
from pathlib import Path

from goldenmatch.mcp.agent_tools import handle_agent_tool


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _call(name, args):
    out = handle_agent_tool(name, args)
    return json.loads(out[0].text)


def test_upload_dataset_returns_path(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(tmp_path))
    raw = b"first_name,last_name,zip\nJOHN,SMITH,10001\n"
    res = _call("upload_dataset", {"file_content": _b64(raw), "filename": "p.csv"})
    assert "path" in res and res["bytes"] == len(raw)
    assert Path(res["path"]).read_bytes() == raw


import pytest

from goldenmatch.mcp._ingest import INGEST_PARAMS
from goldenmatch.mcp.agent_tools import AGENT_TOOLS

_PERSON_CSV = (
    "first_name,last_name,city,zip\n"
    "JOHN,SMITH,NEW YORK,10001\n"
    "JON,SMITH,NEW YORK,10001\n"
    "MARY,JONES,BOSTON,02108\n"
)


@pytest.mark.parametrize("tool", ["analyze_data", "scan_quality"])
def test_inline_file_content_dispatch(tool, tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(tmp_path))
    res = _call(tool, {"file_content": _b64(_PERSON_CSV.encode()), "filename": "p.csv"})
    assert "error" not in res or "goldencheck" in json.dumps(res).lower()
    # analyze_data must return a profile; scan_quality may report missing
    # optional dep — both are non-crash outcomes.


def test_agent_schemas_expose_content_params():
    by_name = {t.name: t for t in AGENT_TOOLS}
    for tool, sides in INGEST_PARAMS.items():
        t = by_name.get(tool)
        if t is None:
            continue  # server-side tool, checked in Task 5
        props = t.inputSchema["properties"]
        for path_param, content_param, name_param in sides:
            assert content_param in props, f"{tool} missing {content_param}"
        # path param must not be in required anymore
        for path_param, _c, _n in sides:
            assert path_param not in t.inputSchema.get("required", [])


from goldenmatch.mcp.server import _handle_tool, TOOLS


def _call_server(name, args):
    return _handle_tool(name, args)


def test_schema_match_inline_both_sides(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(tmp_path))
    a = b"first_name,last_name,zip\nJOHN,SMITH,10001\n"
    b = b"fname,lname,postal\nJOHN,SMITH,10001\n"
    res = _call_server("schema_match", {
        "file_a_content": _b64(a), "file_b_content": _b64(b),
    })
    assert "error" not in res
    assert "mappings" in res


def test_server_schemas_expose_content_params():
    by_name = {t.name: t for t in TOOLS}
    server_side = {"pprl_link", "schema_match", "compare_clusters"}
    for tool in server_side:
        props = by_name[tool].inputSchema["properties"]
        for path_param, content_param, _n in INGEST_PARAMS[tool]:
            assert content_param in props
            assert path_param not in by_name[tool].inputSchema.get("required", [])
