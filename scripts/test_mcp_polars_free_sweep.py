"""No NEW MCP tool may hard-require polars.

The CLI ratchet (``test_cli_polars_free_sweep.py``) is closed at zero. This one
opens at NINE, because the MCP surface -- 97 tools, a completely separate entry
point from the CLI -- had never been checked at all. `mcp/*` is also in the
coverage ``omit`` list, so it was unmeasured twice over.

Same contract as the CLI ratchet, in both directions:

* a tool appearing that is not listed means someone added a polars-bound tool;
* a listed tool no longer appearing means someone FIXED one -- remove it.

The list is a floor to work down, never a bucket to top up. polars is an
OPTIONAL extra, so an entry here is a tool that raises a bare ImportError at an
agent calling it on a default install.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from sweep_mcp_polars_free import (  # noqa: E402
    NEVER_INVOKE,
    looks_like_polars_import_error,
    run_sweep,
)

# Measured 2026-08-31 by dispatching every tool with polars blocked.
KNOWN_POLARS_BOUND: set[str] = set()
# EMPTY. Opened at NINE on first measurement of this surface and closed the same
# day. As with the CLI ratchet, an entry added later is a decision to ship a
# tool that raises a bare ImportError at an agent on a default install.
#
# REOPENED at TWO on 2026-09-01 and closed again the same day. `read_file` and
# `write_csv` had been scored `ok` by a sweep that only recognised a RAISED
# ImportError. Both wrap their body in a broad `except Exception` and RETURN
# {"error": str(exc)}, so a missing polars came back as a payload -- read_file
# answered "Could not parse ...: No module named 'polars'" and counted as a pass.
# The detector now inspects the returned value too. The lesson is not about
# those two tools: a sweep that recognises one failure SHAPE reports a clean
# zero for every tool that fails in another shape.
#
# One of the original nine (`run_transforms`) turned out NOT to be a defect: the
# probe venv had goldenflow-native 0.1.1 against a >=0.27 floor, a wheel with 4
# symbols instead of 113, so every transform fell to the polars engine. Rebuilt
# from packages/rust/extensions/native-flow, it passes. Worth remembering before
# "fixing" a tool on this list: check the native wheel is current first.


@pytest.fixture(scope="module")
def sweep():
    return run_sweep()


def test_no_new_polars_bound_tool(sweep):
    found = {r["tool"] for r in sweep if r["verdict"] == "polars_bound"}

    new = found - KNOWN_POLARS_BOUND
    assert not new, (
        f"NEW polars-bound MCP tool(s): {sorted(new)}. polars is an optional "
        f"extra, so these raise a bare ImportError at an agent on a default "
        f"install. Route the frame through goldenmatch.core.frame."
    )

    fixed = KNOWN_POLARS_BOUND - found
    assert not fixed, (
        f"{sorted(fixed)} no longer require polars -- remove them from "
        f"KNOWN_POLARS_BOUND so the ratchet keeps its value."
    )


def test_the_sweep_actually_dispatched_tools(sweep):
    """Guard against a vacuous pass: if the MCP import or the schema-based
    argument synthesis broke, everything would come back `unprobed` and the
    assertion above would pass while testing nothing."""
    verdicts = [r["verdict"] for r in sweep]
    assert len(verdicts) > 80, f"only {len(verdicts)} tools enumerated"
    assert verdicts.count("ok") >= 40, (
        f"only {verdicts.count('ok')} tools dispatched cleanly -- the sweep is "
        f"not exercising the MCP surface"
    )


def test_external_system_tools_are_never_invoked(sweep):
    by_tool = {r["tool"]: r for r in sweep}
    for name in NEVER_INVOKE:
        if name in by_tool:
            assert by_tool[name]["verdict"] == "unprobed", (
                f"{name} reaches an external system but the sweep invoked it"
            )


def test_known_bad_list_carries_no_stale_entries(sweep):
    """A renamed tool would leave an entry that can never appear, quietly
    shrinking the ratchet -- the coverage-floor-on-a-deleted-module failure."""
    registered = {r["tool"] for r in sweep}
    missing = KNOWN_POLARS_BOUND - registered
    assert not missing, f"listed but no longer registered tools: {sorted(missing)}"


# -- the detector itself ----------------------------------------------------
#
# `run_sweep` is only as good as its classifier, and a narrowed classifier makes
# the ratchet above pass while measuring nothing -- which is exactly what
# happened before `read_file` and `write_csv` were found. So the predicate is
# tested directly, not just through the sweep that depends on it.


@pytest.mark.parametrize(
    "blob",
    [
        "No module named 'polars'",
        'no module named "polars"',
        "Could not parse /tmp/a.csv: No module named 'polars'",
        """{"error": "Could not parse a.csv: No module named 'polars'"}""",
    ],
)
def test_a_returned_polars_import_error_is_recognised(blob):
    assert looks_like_polars_import_error(blob)


@pytest.mark.parametrize(
    "blob",
    [
        "",
        "No module named 'pyarrow'",
        "polars is an optional extra",          # mentions polars, not an ImportError
        "install goldenflow[polars] for this",  # an actionable needs-extra message
    ],
)
def test_unrelated_text_is_not_flagged(blob):
    assert not looks_like_polars_import_error(blob)


def test_the_probe_uses_the_same_predicate_the_test_does():
    """The probe runs in a subprocess and cannot import this module, so it
    INLINES the predicate's source. If someone reimplements the check inside the
    probe instead, the two can drift and only the subprocess one matters."""
    import inspect

    import sweep_mcp_polars_free as sweep

    probe = sweep._probe_source()
    assert "def looks_like_polars_import_error" in probe
    assert inspect.getsource(sweep.looks_like_polars_import_error) in probe
