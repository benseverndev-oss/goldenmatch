"""Which pure-Python fallbacks no test executes with native off."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import parity_coverage as pc  # noqa: E402
from parity_coverage import (  # noqa: E402
    modules_without_coverage_data,
    unguarded_py_functions,
)

XML = """<?xml version="1.0" ?>
<coverage><packages><package><classes>
<class filename="packages/python/goldenflow/goldenflow/transforms/email.py">
<lines>
<line number="25" hits="0"/>
<line number="26" hits="0"/>
<line number="40" hits="3"/>
</lines>
</class>
</classes></package></packages></coverage>
"""


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "coverage.xml"
    p.write_text(body, encoding="utf-8")
    return p


SPANS = {
    "packages/python/goldenflow/goldenflow/transforms/email.py": [
        ("_never_ran_py", 25, 26),  # both lines hits=0
        ("_did_run_py", 39, 41),  # covers line 40, hits=3
    ]
}


def test_a_bom_prefixed_module_is_scanned_for_py_functions(tmp_path, monkeypatch):
    """core/autoconfig_planner.py and core/execution_plan.py in the real
    goldenmatch tree both carry a UTF-8 BOM (`﻿`). A plain
    `encoding="utf-8"` decode left the BOM character in the source string,
    `ast.parse` raised a SyntaxError, and `_py_function_spans` silently
    `continue`d past the module -- any `_py` function in it invisible to
    `unguarded_py_functions` on every platform, Linux CI included.
    `encoding="utf-8-sig"` strips it before parsing."""
    (tmp_path / "bommed.py").write_bytes(
        b"def fallback_py():\n    return 1\n".decode("ascii").encode("utf-8-sig")
    )
    monkeypatch.setattr(pc, "PACKAGES", (tmp_path,))
    spans = pc._py_function_spans()
    matches = [k for k in spans if k.endswith("bommed.py")]
    assert matches, f"BOM-prefixed module was skipped entirely: {sorted(spans)}"
    names = {fn for fn, _, _ in spans[matches[0]]}
    assert "fallback_py" in names, names


def test_an_unexecuted_function_is_reported_and_an_executed_one_is_not(tmp_path):
    """The unit is the FUNCTION, not the module: a module with SOME executed
    lines still has _py functions that never ran, and both must be classified
    correctly from the same file."""
    out = unguarded_py_functions(_write(tmp_path, XML), spans=SPANS)
    names = {i.split("::")[-1] for i in out}
    assert "_never_ran_py" in names, out
    assert "_did_run_py" not in names, out


def test_a_lineless_class_is_not_reported(tmp_path):
    body = XML.replace(
        '<line number="25" hits="0"/>\n<line number="26" hits="0"/>\n<line number="40" hits="3"/>',
        "",
    )
    assert unguarded_py_functions(_write(tmp_path, body), spans=SPANS) == []


def test_a_missing_file_raises_rather_than_reporting_clean(tmp_path):
    # match= witnesses OUR deliberate guard's message, not merely "some
    # FileNotFoundError happened" -- ET.parse() on a missing path raises the
    # same exception TYPE from its own open() call, so a bare `pytest.raises
    # (FileNotFoundError)` cannot tell whether our guard exists at all. See
    # fix-round-1 sabotage: replacing the guard body with `pass` still passed
    # this test before `match=` was added.
    with pytest.raises(FileNotFoundError, match="coverage report missing"):
        unguarded_py_functions(tmp_path / "nope.xml")


def test_a_module_absent_from_coverage_is_a_gap_not_silently_clean(tmp_path):
    """A module in `spans` with no matching <class> anywhere in the XML (a
    whole package excluded from that run's --source, say) must be visible as
    a gap. Silently dropping it from `unguarded_py_functions` -- which it
    must, since it has no evidence to report -- would read as "fewer
    unguarded functions" for a reason having nothing to do with coverage."""
    spans = {
        "packages/python/goldenflow/goldenflow/transforms/never_measured.py": [
            ("_absent_py", 1, 2),
        ]
    }
    gaps = modules_without_coverage_data(_write(tmp_path, XML), spans=spans)
    assert len(gaps) == 1
    assert "never_measured.py" in gaps[0]
    # and the findings list stays silent about it -- that's the whole risk
    assert unguarded_py_functions(_write(tmp_path, XML), spans=spans) == []


def test_an_ambiguous_filename_match_raises_rather_than_guessing(tmp_path):
    """Two modules in the coverage XML share a basename that a short `spans`
    key suffix-matches equally. Picking the first candidate would silently
    attribute one module's coverage to the other's functions -- a wrong
    attribution is unfalsifiable, so this must raise instead."""
    body = """<?xml version="1.0" ?>
<coverage><packages><package><classes>
<class filename="packages/python/goldenflow/goldenflow/transforms/scoring.py">
<lines><line number="1" hits="1"/></lines>
</class>
<class filename="packages/python/goldenmatch/goldenmatch/core/scoring.py">
<lines><line number="1" hits="1"/></lines>
</class>
</classes></package></packages></coverage>
"""
    spans = {"scoring.py": [("_ambiguous_py", 1, 1)]}
    with pytest.raises(ValueError, match="ambiguous"):
        unguarded_py_functions(_write(tmp_path, body), spans=spans)


def test_max_no_data_below_the_limit_exits_0(tmp_path, monkeypatch):
    """The CLI's --max-no-data enforces the documented no-data floor: a gap
    count AT OR BELOW the limit is fine and main() exits clean."""
    monkeypatch.setattr(pc, "unguarded_py_functions", lambda *a, **k: [])
    monkeypatch.setattr(pc, "modules_without_coverage_data", lambda *a, **k: ["a.py", "b.py"])
    monkeypatch.setattr(pc, "_py_function_spans", lambda: {})
    xml = _write(tmp_path, XML)
    rc = pc.main(["--native-off-xml", str(xml), "--max-no-data", "2"])
    assert rc == 0


def test_max_no_data_above_the_limit_exits_1_and_names_count_and_limit(
    tmp_path, monkeypatch, capsys
):
    """A gap count exceeding the limit means the RUN was incomplete, not that
    more code is unguarded -- that must fail loudly, and the message must
    name both the actual count and the configured limit so a reader doesn't
    have to go dig through the printed module list to find them."""
    monkeypatch.setattr(pc, "unguarded_py_functions", lambda *a, **k: [])
    monkeypatch.setattr(
        pc, "modules_without_coverage_data", lambda *a, **k: ["a.py", "b.py", "c.py"]
    )
    monkeypatch.setattr(pc, "_py_function_spans", lambda: {})
    xml = _write(tmp_path, XML)
    rc = pc.main(["--native-off-xml", str(xml), "--max-no-data", "2"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL: 3 module(s) had no coverage data, exceeding --max-no-data 2" in out


def test_max_no_data_unset_never_fails_regardless_of_gap_count(tmp_path, monkeypatch):
    """Existing callers (no --max-no-data) must be unaffected: even a large
    gap count is report-only when no limit was requested."""
    monkeypatch.setattr(pc, "unguarded_py_functions", lambda *a, **k: [])
    monkeypatch.setattr(pc, "modules_without_coverage_data", lambda *a, **k: ["a.py"] * 50)
    monkeypatch.setattr(pc, "_py_function_spans", lambda: {})
    xml = _write(tmp_path, XML)
    rc = pc.main(["--native-off-xml", str(xml)])
    assert rc == 0
