"""Unit tests for the downstream-symbol gate.

The gate's whole value is that it fails on the break that actually happened, and
that it cannot pass by looking at nothing. Both are pinned here, against synthetic
consumer trees so the tests don't move when the real consumers do.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_downstream_symbols as mod  # noqa: E402


@pytest.fixture
def consumer(tmp_path, monkeypatch):
    """A fake consumer package rooted where the gate expects repo-relative paths."""
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    pkg = tmp_path / "fake-consumer"
    pkg.mkdir()

    def write(name: str, src: str) -> Path:
        p = pkg / name
        p.write_text(textwrap.dedent(src), encoding="utf-8")
        return p

    return write


def _refs(consumer_dir="fake-consumer"):
    return mod.collect_refs((consumer_dir,))


def test_detects_the_monkeypatch_shape_that_broke_main(consumer):
    """`monkeypatch.setattr(mod, "NAME", ...)` -- invisible to an import check."""
    consumer(
        "test_thing.py",
        """
        import goldenmatch.core.ann_blocker as _ab

        def fixture(monkeypatch):
            monkeypatch.setattr(_ab, "_HAS_FAISS", False)
        """,
    )
    refs = _refs()
    assert any(
        r.kind == "monkeypatch"
        and r.module == "goldenmatch.core.ann_blocker"
        and r.attr == "_HAS_FAISS"
        for r in refs
    ), refs
    problems = mod.unsatisfied(refs)
    assert len(problems) == 1
    assert "_HAS_FAISS" in problems[0][1]


def test_real_symbol_resolves(consumer):
    consumer(
        "uses.py",
        """
        from goldenmatch.core.ann_blocker import ANNBlocker
        """,
    )
    assert mod.unsatisfied(_refs()) == []


def test_missing_from_import_name_fails(consumer):
    consumer(
        "uses.py",
        """
        from goldenmatch.core.ann_blocker import NoSuchThing
        """,
    )
    problems = mod.unsatisfied(_refs())
    assert len(problems) == 1
    assert "NoSuchThing" in problems[0][1]


def test_missing_module_fails(consumer):
    consumer("uses.py", "import goldenmatch.core.not_a_real_module\n")
    problems = mod.unsatisfied(_refs())
    assert len(problems) == 1
    assert "does not import" in problems[0][1]


def test_submodule_from_import_is_not_a_false_positive(consumer):
    """`from goldenmatch import core` -- a module, not an attribute."""
    consumer("uses.py", "from goldenmatch import core\n")
    assert mod.unsatisfied(_refs()) == []


def test_monkeypatch_on_a_non_goldenmatch_module_is_ignored(consumer):
    consumer(
        "uses.py",
        """
        import os

        def f(monkeypatch):
            monkeypatch.setattr(os, "sep", "/")
        """,
    )
    assert _refs() == []


def test_unparseable_file_does_not_crash_the_scan(consumer):
    consumer("broken.py", "def (:\n")
    consumer("ok.py", "from goldenmatch.core.ann_blocker import ANNBlocker\n")
    assert len(_refs()) == 1


def test_empty_scan_is_reported_as_broken_not_clean(tmp_path, monkeypatch, capsys):
    """A scan that finds nothing must look BROKEN. This gate exists because a
    silently-empty check is indistinguishable from a passing one."""
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    (tmp_path / "fake-consumer").mkdir()
    rc = mod.main(["--consumer", "fake-consumer"])
    assert rc == 2
    assert "scan is broken" in capsys.readouterr().err


def test_absent_consumer_dirs_are_reported_as_broken(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    rc = mod.main(["--consumer", "does-not-exist"])
    assert rc == 2
    assert "no consumer packages found" in capsys.readouterr().err


def test_real_consumers_are_declared_and_present():
    """The configured consumer list must match reality, or the gate scans nothing."""
    missing = [c for c in mod.CONSUMERS if not (mod.ROOT / c).is_dir()]
    assert not missing, f"CONSUMERS names paths that do not exist: {missing}"


def test_the_repo_as_it_stands_passes():
    """End-to-end on the real tree -- the state the gate must hold."""
    refs = mod.collect_refs(mod.CONSUMERS)
    assert refs, "no goldenmatch references found in the real consumers"
    assert mod.unsatisfied(refs) == []
