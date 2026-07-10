import base64
import os
import time
from pathlib import Path

import pytest
from goldenmatch.mcp import _ingest


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_base64_roundtrip_writes_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(tmp_path))
    raw = b"a,b\n1,2\n"
    path = _ingest.resolve_input_source(
        file_path=None, file_content=_b64(raw), filename="d.csv"
    )
    assert Path(path).read_bytes() == raw
    assert Path(path).suffix == ".csv"
    # Under the allowed root so safe_path will accept it.
    assert Path(path).resolve().is_relative_to(tmp_path.resolve())


def test_newline_wrapped_base64_decodes(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(tmp_path))
    raw = b"x" * 200
    wrapped = "\n".join(
        _b64(raw)[i : i + 76] for i in range(0, len(_b64(raw)), 76)
    )
    path = _ingest.resolve_input_source(
        file_path=None, file_content=wrapped, filename="d.csv"
    )
    assert Path(path).read_bytes() == raw


def test_text_encoding_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(tmp_path))
    path = _ingest.resolve_input_source(
        file_path=None, file_content="a,b\n1,2\n", filename="d.csv", encoding="text"
    )
    assert Path(path).read_text() == "a,b\n1,2\n"


def test_uploads_dir_falls_back_to_tempdir_when_root_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_ALLOWED_ROOT", raising=False)
    monkeypatch.setattr(_ingest.tempfile, "gettempdir", lambda: str(tmp_path))
    d = _ingest._uploads_dir()
    assert Path(d).resolve().is_relative_to(tmp_path.resolve())


def test_passthrough_file_path_unchanged():
    assert _ingest.resolve_input_source(
        file_path="/data/x.csv", file_content=None
    ) == "/data/x.csv"


def test_neither_source_raises():
    with pytest.raises(ValueError, match="file_path or file_content"):
        _ingest.resolve_input_source(file_path=None, file_content=None)


def test_oversized_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(tmp_path))
    monkeypatch.setenv("GOLDENMATCH_MCP_MAX_UPLOAD_BYTES", "8")
    with pytest.raises(ValueError, match="exceeds"):
        _ingest.resolve_input_source(
            file_path=None, file_content=_b64(b"x" * 100), filename="d.csv"
        )


def test_size_cap_measures_decoded_not_encoded_bytes(tmp_path, monkeypatch):
    # Pin the guarantee that the cap is on DECODED bytes: 6 raw bytes
    # base64-encode to 8 chars. With cap=7, decoded (6) is under and must
    # succeed; a buggy check on the encoded length (8) would wrongly reject.
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(tmp_path))
    monkeypatch.setenv("GOLDENMATCH_MCP_MAX_UPLOAD_BYTES", "7")
    raw = b"x" * 6
    assert len(_b64(raw)) == 8  # encoded length exceeds the cap
    path = _ingest.resolve_input_source(
        file_path=None, file_content=_b64(raw), filename="d.csv"
    )
    assert Path(path).read_bytes() == raw


def test_invalid_base64_hints_text(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="base64"):
        _ingest.resolve_input_source(
            file_path=None, file_content="not*valid*b64*!!", filename="d.csv"
        )


def test_bad_encoding_value(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="encoding"):
        _ingest.resolve_input_source(
            file_path=None, file_content="x", filename="d.csv", encoding="hex"
        )


@pytest.mark.parametrize(
    "raw,expected_suffix",
    [("../../etc/passwd", ""), ("a b.csv", ".csv"), ("weirdé.parquet", ".parquet")],
)
def test_safe_filename_traversal_and_ext(raw, expected_suffix):
    safe = _ingest._safe_filename(raw)
    assert "/" not in safe and "\\" not in safe and ".." not in safe
    if expected_suffix:
        assert safe.endswith(expected_suffix)


def test_reaper_deletes_aged_keeps_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_MCP_UPLOAD_TTL", "1")
    old = tmp_path / "old.csv"
    old.write_text("x")
    new = tmp_path / "new.csv"
    new.write_text("y")
    past = time.time() - 10
    os.utime(old, (past, past))
    _ingest._reap(tmp_path, ttl=1)
    assert not old.exists()
    assert new.exists()


def test_resolve_ingest_args_rewrites_path(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(tmp_path))
    args = {"file_content": _b64(b"a,b\n1,2\n"), "filename": "d.csv"}
    err = _ingest.resolve_ingest_args("analyze_data", args)
    assert err is None
    assert "file_path" in args and Path(args["file_path"]).exists()


def test_resolve_ingest_args_two_sided(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(tmp_path))
    args = {
        "file_a_content": _b64(b"a\n1\n"),
        "file_b_content": _b64(b"b\n2\n"),
        "fields": ["a"],
    }
    err = _ingest.resolve_ingest_args("schema_match", args)
    assert err is None
    assert Path(args["file_a"]).exists() and Path(args["file_b"]).exists()


def test_resolve_ingest_args_unknown_tool_noop():
    args = {"foo": 1}
    assert _ingest.resolve_ingest_args("get_stats", args) is None
    assert args == {"foo": 1}


def test_resolve_ingest_args_optional_side_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(tmp_path))
    # compare_strategies: only the dataset side supplied, ground_truth omitted
    args = {"file_content": _b64(b"a\n1\n"), "filename": "d.csv"}
    err = _ingest.resolve_ingest_args("agent_compare_strategies", args)
    assert err is None
    assert Path(args["file_path"]).exists()
    assert "ground_truth" not in args  # untouched when neither path nor content


def test_resolve_ingest_args_bad_content_returns_error(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(tmp_path))
    args = {"file_content": "not*b64*!!"}
    err = _ingest.resolve_ingest_args("analyze_data", args)
    assert err is not None and "error" in err
