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
