"""Which pure-Python fallbacks no test executes with native off."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from parity_coverage import unguarded_py_functions  # noqa: E402

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
    import pytest

    with pytest.raises(FileNotFoundError):
        unguarded_py_functions(tmp_path / "nope.xml")
