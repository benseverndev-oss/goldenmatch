"""No NEW CLI command may hard-require polars.

polars is an OPTIONAL extra (v3.1.0+), so `pip install goldenmatch` is a
polars-free environment. This has bitten repeatedly: #2810 fixed `dedupe` for
that install, 3.17.0 shipped a second leak on the same path, and a sweep of all
66 registered commands then found ten more.

The list below is a RATCHET, in both directions:

* a command appearing that is not listed means someone added a polars-bound
  command -- the failure names it;
* a listed command no longer appearing means someone FIXED one -- remove it
  from the list, and the failure says so.

It is deliberately not a "known failures" bucket that can be topped up. Adding
an entry is a decision to ship a command that raises a bare ImportError at a
user who followed the install instructions.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from sweep_cli_polars_free import MUTATING, run_sweep  # noqa: E402

# Confirmed by invocation on 2026-08-31 against a polars-blocked interpreter.
# Each raises ImportError: No module named 'polars' -- a traceback, not an
# error message -- on a default install.
KNOWN_POLARS_BOUND = {
    # Each is blocked behind a CORE module, not the command. Recorded with the
    # blocker so the next person starts where the work actually is.
    #
    # demo, lineage   -- both run through tui.engine.MatchEngine, which is a
    #                    polars LazyFrame pipeline end to end (df.lazy() /
    #                    .collect() around auto-fix, validation and
    #                    standardization). Porting the engine fixes both.
    # incremental     -- core/incremental.py leans on apply_standardization,
    #                    match_one and find_exact_matches; none of the three has
    #                    any frame-seam use.
    # pprl *          -- pprl/protocol.py and pprl/autoconfig.py have ZERO
    #                    to_frame calls, so this is a pprl-core port.
    "demo",
    "incremental",
    "lineage",
    "pprl auto-config",
    "pprl link",
}

# Ported and verified polars-free on 2026-08-31, each on BOTH lanes (polars
# blocked, and polars present so the classic lane is unregressed):
#   anomalies          read_files_arrow + the arrow csv writer
#   analyze-blocking   read_files_arrow; the analyzer already took arrow
#   profile            load_file(..., return_frame=True); also fixed
#                      format_profile_report, whose sample block called the
#                      polars-only head()/iter_rows() and broke `--verbose`
#   review             polars was imported ONLY to annotate a local
#   label              arrow ingest + seam select_dicts + arrow csv read/write;
#                      also fixed a pre-existing Rich MarkupError that crashed
#                      the command on any pair whose values differed

# Ported and verified polars-free on 2026-08-31, on BOTH lanes (arrow, and with
# polars present so the classic lane is unregressed):
#   anomalies          -- read_files_arrow + the arrow csv writer
#   analyze-blocking   -- read_files_arrow; analyze_blocking already took arrow
#   profile            -- load_file(..., return_frame=True); also fixed
#                         format_profile_report, whose sample block called the
#                         polars-only head()/iter_rows() and so broke
#                         `profile --verbose` on the arrow lane
#   review             -- polars was imported ONLY to annotate a local

# Also confirmed polars-bound by hand, but never invoked by the sweep because it
# MUTATES state (it removes a record from a cluster). Listed so the finding is
# not lost when the harness declines to run it.
KNOWN_POLARS_BOUND_MUTATING = {"unmerge"}


@pytest.fixture(scope="module")
def sweep():
    return run_sweep()


def test_no_new_polars_bound_command(sweep):
    found = {r["cmd"] for r in sweep if r["verdict"] == "polars_bound"}

    new = found - KNOWN_POLARS_BOUND
    assert not new, (
        f"NEW polars-bound command(s): {sorted(new)}. These raise a bare "
        f"ImportError on a default install (`pip install goldenmatch`), because "
        f"polars is an optional extra. Route the frame through "
        f"goldenmatch.core.frame instead of importing polars directly."
    )

    fixed = KNOWN_POLARS_BOUND - found
    assert not fixed, (
        f"{sorted(fixed)} no longer require polars -- remove them from "
        f"KNOWN_POLARS_BOUND so the ratchet keeps its value."
    )


def test_the_sweep_actually_ran_something(sweep):
    """Guard against a vacuous pass. If argument synthesis or the CLI import
    broke, every command would come back `unprobed` and the assertion above
    would pass while testing nothing."""
    verdicts = [r["verdict"] for r in sweep]
    assert len(verdicts) > 50, f"only {len(verdicts)} commands enumerated"
    assert verdicts.count("ok") >= 10, (
        f"only {verdicts.count('ok')} commands were successfully invoked -- the "
        f"sweep is not exercising the CLI"
    )


def test_mutating_commands_are_never_invoked(sweep):
    """A sweep that changes the machine it runs on is not a sweep."""
    by_cmd = {r["cmd"]: r for r in sweep}
    for name in MUTATING:
        if name in by_cmd:
            assert by_cmd[name]["verdict"] == "unprobed", (
                f"{name} is marked MUTATING but the sweep invoked it"
            )


def test_known_bad_list_carries_no_stale_entries():
    """Every listed command must still be a registered command. A rename would
    otherwise leave an entry that can never appear, quietly shrinking the
    ratchet -- the same failure mode as a coverage floor on a deleted module."""
    import click
    import typer
    from goldenmatch.cli.main import app

    grp = typer.main.get_command(app)
    ctx = click.Context(grp)
    registered = set()

    def walk(g, prefix=()):
        for name in sorted(g.commands):
            cmd = g.get_command(ctx, name)
            path = prefix + (name,)
            if isinstance(cmd, click.Group):
                walk(cmd, path)
            else:
                registered.add(" ".join(path))

    walk(grp)
    listed = KNOWN_POLARS_BOUND | KNOWN_POLARS_BOUND_MUTATING
    missing = listed - registered
    assert not missing, f"listed but no longer registered commands: {sorted(missing)}"
