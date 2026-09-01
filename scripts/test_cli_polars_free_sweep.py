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

from sweep_cli_polars_free import NEVER_INVOKE, run_sweep  # noqa: E402

# Confirmed by invocation on 2026-08-31 against a polars-blocked interpreter.
# Each raises ImportError: No module named 'polars' -- a traceback, not an
# error message -- on a default install.
KNOWN_POLARS_BOUND: set[str] = set()
# EMPTY, and that is the point. This started at ELEVEN commands and the list is
# now closed: no registered command may require polars. The assertion below has
# stopped being "the debt has not grown" and become "there is no debt", which is
# the only version of this gate worth having.
#
# Do not add an entry to make a build green. polars is an OPTIONAL extra, so an
# entry here is a decision to ship a command that raises a bare ImportError at
# someone who followed the install instructions. Route the frame through
# goldenmatch.core.frame instead; every op these eleven needed already existed
# on the seam (derive_standardized_column, derive_matchkey, ensure_row_ids,
# select_dicts, semantic_dtype, concat_frames(relaxed=True)).

# Ported and verified polars-free on 2026-08-31, each on BOTH lanes (polars
# blocked, and polars present so the classic lane is unregressed):
#   anomalies          read_files_arrow + the arrow csv writer
#   analyze-blocking   read_files_arrow; the analyzer already took arrow
#   profile            load_file(..., return_frame=True); also fixed
#                      format_profile_report's polars-only head()/iter_rows()
#   review             polars was imported ONLY to annotate a local
#   label              arrow ingest + seam select_dicts + arrow csv io; also
#                      fixed a pre-existing MarkupError that crashed it entirely
#   demo, lineage      MatchEngine now DELEGATES to run_dedupe_df instead of
#                      keeping its own ~200-line copy of the pipeline
#   pprl link,
#   pprl auto-config   pprl/protocol.py + pprl/autoconfig.py read through the
#                      seam; the polars there was mostly type annotations plus
#                      three real operations

# Also EMPTY. `unmerge` and `rollback` live in cli/rollback.py and are never
# invoked by the sweep (they mutate state), so "zero polars-bound" would have
# been slightly false while they still imported polars. Both were ported by
# hand and verified on both lanes, so the claim now holds for every registered
# command -- probed or not.
KNOWN_POLARS_BOUND_MUTATING: set[str] = set()


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


def test_external_system_commands_are_never_invoked(sweep):
    """A sweep that touches a database is not a sweep.

    Locally-mutating commands ARE invoked now -- under a redirected $HOME, so
    their writes land in the scratch dir. Leaving them unprobed was safe but it
    was also twelve commands whose polars status was simply unknown, and
    unknown was being counted as fine.
    """
    by_cmd = {r["cmd"]: r for r in sweep}
    for name in NEVER_INVOKE:
        if name in by_cmd:
            assert by_cmd[name]["verdict"] == "unprobed", (
                f"{name} reaches an external system but the sweep invoked it"
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
