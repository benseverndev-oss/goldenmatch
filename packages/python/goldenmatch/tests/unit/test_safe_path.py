"""Tests for goldenmatch.core._paths.safe_path."""


import pytest
from goldenmatch.core._paths import PathOutsideAllowedRootError, safe_path


def test_plain_path_resolves(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("a,b\n1,2")
    assert safe_path(str(f)) == f.resolve()


def test_rejects_null_byte():
    with pytest.raises(ValueError):
        safe_path("data\x00.csv")


def test_traversal_collapsed(tmp_path):
    p = safe_path(str(tmp_path / "sub" / ".." / "data.csv"))
    assert ".." not in p.parts


def test_containment_blocks_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    with pytest.raises(PathOutsideAllowedRootError):
        safe_path(str(outside), base_dir=root)


def test_containment_allows_inside(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "ok.csv"
    assert safe_path(str(inside), base_dir=root) == inside.resolve()


def test_env_root(tmp_path, monkeypatch):
    root = tmp_path / "jail"
    root.mkdir()
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(root))
    with pytest.raises(PathOutsideAllowedRootError):
        safe_path(str(tmp_path / "outside.csv"))
    assert safe_path(str(root / "in.csv")) == (root / "in.csv").resolve()


def test_no_root_no_containment(tmp_path, monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_ALLOWED_ROOT", raising=False)
    assert safe_path(str(tmp_path / "anything")) == (tmp_path / "anything").resolve()


def test_mcp_export_respects_root(tmp_path, monkeypatch):
    root = tmp_path / "jail"
    root.mkdir()
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(root))
    from goldenmatch.mcp import server as mcp_server
    result = mcp_server._tool_export_results(str(tmp_path / "escape.json"), "json")
    assert "error" in result


def test_mcp_compare_clusters_respects_root(tmp_path, monkeypatch):
    root = tmp_path / "jail"
    root.mkdir()
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(root))
    from goldenmatch.mcp import server as mcp_server
    # clusters_a_path escapes the jail — must get an error dict, not an open() call
    result = mcp_server._tool_compare_clusters(
        str(tmp_path / "escape_a.json"),
        str(root / "b.json"),
    )
    assert "error" in result
