"""Tests for the 3 host-helper MCP tools (reverse-parity with the TS surface).

`server_info` / `read_file` / `write_csv` are not ER capabilities -- they let an
agent stage an input and collect an output without a second toolchain. The
security-relevant behavior (path containment) is pinned in BOTH modes, because
`safe_path` only enforces containment when `GOLDENMATCH_ALLOWED_ROOT` is set.
"""

from __future__ import annotations

import pytest
from goldenmatch.core._paths import _ENV_ROOT
from goldenmatch.mcp.server import (
    _BASE_TOOLS,
    TOOLS,
    _tool_read_file,
    _tool_server_info,
    _tool_write_csv,
)


@pytest.fixture
def csv_file(tmp_path):
    p = tmp_path / "in.csv"
    p.write_text("name,city\nAlice,Boston\nBob,Denver\nCarol,Austin\n", encoding="utf-8")
    return p


def test_all_three_are_registered():
    assert {"server_info", "read_file", "write_csv"} <= {t.name for t in _BASE_TOOLS}


class TestServerInfo:
    def test_tool_count_is_derived_not_a_literal(self):
        # A hardcoded count silently drifts from the real surface the moment a
        # tool is added; TS derives it from TOOLS.length and so do we.
        assert _tool_server_info()["tool_count"] == len(TOOLS)

    def test_reports_the_package_version(self):
        from goldenmatch import __version__

        info = _tool_server_info()
        assert info["version"] == __version__
        assert info["name"] == "goldenmatch"


class TestReadFile:
    def test_wire_shape(self, csv_file):
        out = _tool_read_file(str(csv_file), None)
        assert out["total"] == 3
        assert out["returned"] == 3
        assert out["rows"][0] == {"name": "Alice", "city": "Boston"}

    def test_limit_truncates_rows_but_total_is_the_real_count(self, csv_file):
        out = _tool_read_file(str(csv_file), 1)
        assert out["total"] == 3        # the file still has 3
        assert out["returned"] == 1     # we returned 1
        assert len(out["rows"]) == 1

    def test_zero_limit_returns_no_rows(self, csv_file):
        out = _tool_read_file(str(csv_file), 0)
        assert out["returned"] == 0 and out["rows"] == []

    def test_non_numeric_limit_is_a_tool_error(self, csv_file):
        assert "error" in _tool_read_file(str(csv_file), "abc")

    def test_missing_file_is_a_tool_error_not_a_raise(self, tmp_path):
        assert "error" in _tool_read_file(str(tmp_path / "absent.csv"), None)


class TestWriteCsv:
    def test_round_trips_through_read_file(self, tmp_path):
        out_path = tmp_path / "out.csv"
        rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
        res = _tool_write_csv(str(out_path), rows)
        assert res["written"] == 2
        assert out_path.read_text(encoding="utf-8") == "a,b\n1,x\n2,y\n"
        # the two helpers compose: what write_csv wrote, read_file reads back
        back = _tool_read_file(str(out_path), None)
        assert back["total"] == 2

    def test_empty_rows_writes_a_file_rather_than_raising(self, tmp_path):
        # A zero-result export should still produce the file the caller asked for.
        out_path = tmp_path / "empty.csv"
        assert _tool_write_csv(str(out_path), [])["written"] == 0
        assert out_path.exists()

    @pytest.mark.parametrize("bad", ["not-a-list", 42, None, [1, 2, 3], [{"ok": 1}, "nope"]])
    def test_non_object_rows_are_refused(self, tmp_path, bad):
        # Guards against a scalar/string being coerced into a surprise file write.
        assert "error" in _tool_write_csv(str(tmp_path / "x.csv"), bad)


class TestPathContainment:
    """`safe_path` enforces containment ONLY when GOLDENMATCH_ALLOWED_ROOT is set.

    Both modes are pinned so the real contract can't silently change, and so the
    docs claim stays honest.
    """

    def test_with_root_set_traversal_is_blocked_for_read_and_write(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv(_ENV_ROOT, str(tmp_path))
        inside = tmp_path / "in.csv"
        inside.write_text("a\n1\n", encoding="utf-8")
        assert "error" not in _tool_read_file(str(inside), None)

        outside = tmp_path.parent / "escape.csv"
        assert "error" in _tool_read_file(str(outside), None)
        assert "error" in _tool_write_csv(str(outside), [{"a": 1}])
        assert not outside.exists()  # the write was refused, not merely reported

    def test_without_root_set_containment_is_not_enforced(self, tmp_path, monkeypatch):
        # DOCUMENTED default: same reach as the pre-existing upload_dataset /
        # export_results tools, which use this identical guard. Asserted so the
        # module docstring cannot quietly become a false security claim.
        monkeypatch.delenv(_ENV_ROOT, raising=False)
        outside = tmp_path / "sibling.csv"
        res = _tool_write_csv(str(outside), [{"a": 1}])
        assert res.get("written") == 1 and outside.exists()

    def test_nul_byte_in_path_is_rejected_in_both_modes(self, tmp_path, monkeypatch):
        monkeypatch.delenv(_ENV_ROOT, raising=False)
        assert "error" in _tool_read_file("in\x00.csv", None)
        monkeypatch.setenv(_ENV_ROOT, str(tmp_path))
        assert "error" in _tool_read_file("in\x00.csv", None)
