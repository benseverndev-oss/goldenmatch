"""Unit tests for the mcp-union guard.

The guard exists to prove the sweep coverage was actually combined in, not
merely that `goldenmatch/mcp/*` files are LISTED -- `source = goldenmatch`
makes coverage.py enumerate every file under the package whether or not it
ever ran, so a present-but-unexecuted mcp class proves nothing. These tests
pin all three states the guard must tell apart.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import assert_union_reached_mcp as mod  # noqa: E402


def _xml(tmp_path: Path, classes: str) -> Path:
    p = tmp_path / "coverage.xml"
    p.write_text(
        textwrap.dedent(
            f"""\
            <?xml version="1.0" ?>
            <coverage><packages><package><classes>
            {classes}
            </classes></package></packages></coverage>
            """
        ),
        encoding="utf-8",
    )
    return p


def test_no_mcp_entries_at_all_fails(tmp_path, capsys):
    xml = _xml(
        tmp_path,
        '<class filename="goldenmatch/core/pipeline.py">'
        '<lines><line number="1" hits="3"/></lines></class>',
    )
    rc = mod.main(str(xml))
    assert rc == 1
    assert "no goldenmatch/mcp/ module" in capsys.readouterr().err


def test_mcp_entries_present_but_unexecuted_fails(tmp_path, capsys):
    """The exact false-positive the guard was rewritten to close: presence
    alone (line-rate 0, as coverage.py emits for every enumerated-but-unrun
    file under `source`) must NOT be read as proof the sweep landed."""
    xml = _xml(
        tmp_path,
        '<class filename="goldenmatch/mcp/server.py">'
        '<lines><line number="1" hits="0"/><line number="2" hits="0"/></lines></class>',
    )
    rc = mod.main(str(xml))
    assert rc == 1
    assert "NONE have an executed line" in capsys.readouterr().err


def test_mcp_entry_with_an_executed_line_passes(tmp_path, capsys):
    xml = _xml(
        tmp_path,
        '<class filename="goldenmatch/mcp/server.py">'
        '<lines><line number="1" hits="0"/><line number="2" hits="7"/></lines></class>',
    )
    rc = mod.main(str(xml))
    assert rc == 0
    assert "FAIL" not in capsys.readouterr().out


def test_real_ci_filename_shape_is_recognized(tmp_path):
    """The doubled repo-root-relative shape CI actually emits, not just the
    package-relative shape it's easy to hand-write in a fixture."""
    xml = _xml(
        tmp_path,
        '<class filename="packages/python/goldenmatch/goldenmatch/mcp/server.py">'
        '<lines><line number="1" hits="4"/></lines></class>',
    )
    assert mod.main(str(xml)) == 0
