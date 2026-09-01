"""The native capability table is only worth having if it is checked.

`_CAPABILITIES` in engine/columnar.py names, per code path, the exact wheel
symbols that path needs. That replaced ~20 hand-rolled ``hasattr(nm, ...)``
guards whose strictness did not always match what the code went on to call.

The reason it earns a test file is the documented wheel-skew footgun (CLAUDE.md):
Python reaches new kernel symbols through these guards and falls back when they
are absent, so a published wheel missing one takes a slower path SILENTLY -- no
error, no warning, just worse. #688 lost real time to exactly that. A declared
capability that the installed wheel cannot satisfy should be a red test at the
moment it is declared, not a performance mystery months later.
"""

from __future__ import annotations

import inspect

import pytest
from goldenflow.core._native_loader import native_module
from goldenflow.engine import columnar
from goldenflow.engine.columnar import _CAPABILITIES, native_can


def _missing_symbols(nm, capability: str) -> list[str]:
    mod_syms, col_syms = _CAPABILITIES[capability]
    col_cls = getattr(nm, "Column", None)
    missing = [s for s in mod_syms if not hasattr(nm, s)]
    if col_syms:
        if col_cls is None:
            missing.append("Column")
        else:
            missing += [f"Column.{s}" for s in col_syms if not hasattr(col_cls, s)]
    return missing


def test_every_declared_capability_exists_on_this_wheel():
    nm = native_module()
    assert nm is not None, (
        "goldenflow-native is a BASE dependency (>=0.27), so the kernel must be "
        "importable. If this fails the environment is broken, not the code."
    )
    missing = {c: _missing_symbols(nm, c) for c in _CAPABILITIES if not native_can(nm, c)}
    assert not missing, (
        f"the installed goldenflow-native wheel cannot satisfy: {missing}. Either "
        f"the wheel is stale (rebuild/republish it -- see the wheel-skew note in "
        f"CLAUDE.md) or a capability was declared against a symbol that was never "
        f"shipped. Do NOT silence this by deleting the entry: the entry is what "
        f"stops the code taking a slow fallback without saying so."
    )


def test_an_unknown_capability_raises_rather_than_answering_false():
    """A typo answering False would disable a native path with no signal -- the
    exact silent-slow-fallback this table exists to prevent."""
    with pytest.raises(KeyError, match="unknown native capability"):
        native_can(native_module(), "colunms")


def test_no_kernel_answers_false_for_every_capability():
    for capability in _CAPABILITIES:
        assert not native_can(None, capability)


def test_capability_names_are_reachable_from_the_call_sites():
    """Guard against a table entry nothing consults -- a capability declared and
    then never asked about is dead weight that still passes the wheel check."""
    src = inspect.getsource(columnar)
    unused = [c for c in _CAPABILITIES if f'native_can(nm, "{c}")' not in src]
    assert not unused, f"declared but never queried: {unused}"


def test_the_hand_rolled_guards_do_not_come_back():
    """A ratchet, not a style rule. The consolidation's whole value is that ONE
    place decides what the wheel must expose; a fresh ``hasattr(nm, "new_symbol")``
    at a call site rebuilds the problem one line at a time.

    If you need a new symbol, add it to `_CAPABILITIES` and call `native_can`.
    """
    src = inspect.getsource(columnar)
    allowed = inspect.getsource(columnar.native_can)
    rest = src.replace(allowed, "")
    offenders = [
        line.strip()
        for line in rest.splitlines()
        if "hasattr(nm" in line and not line.strip().startswith("#")
    ]
    assert not offenders, (
        f"hand-rolled native guards outside native_can: {offenders}. Add the "
        f"symbol to _CAPABILITIES and use native_can(nm, ...) instead."
    )
