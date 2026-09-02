"""Which pure-Python fallbacks no test executes with native off."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

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
        ("_never_ran_py", 25, 26),   # both lines hits=0
        ("_did_run_py", 39, 41),     # covers line 40, hits=3
    ]
}


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
