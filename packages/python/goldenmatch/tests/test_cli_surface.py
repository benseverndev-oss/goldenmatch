"""Tests for the CLI surface in `goldenmatch.cli.main`.

`cli/main.py` sat at 52.9% over 272 statements. It is the Typer app that wires
every command a user can type, plus several commands defined inline (`info`,
`score`, `init`, `interactive`, `analyze-blocking`, the `config` preset
sub-app), so half of the entry point to the whole tool was unexecuted.

Two rules this file follows deliberately:

* **Introspect click, never scrape `--help`.** Rich wraps, styles and
  truncates its output depending on terminal width, so asserting on rendered
  help text produces tests that fail on a narrow terminal and pass on a wide
  one. Every structural assertion here goes through
  `typer.main.get_command(app)` and the click objects underneath.
* **Isolate `$HOME`.** `PresetStore()` defaults to `~/.goldenmatch/presets`, so
  a test that skipped this would read and write the developer's real presets --
  and pass or fail depending on what happened to be in them.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import click
import pytest
import typer
from goldenmatch import __version__
from goldenmatch.cli import main as cli_main
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    """Point PresetStore at a tmp dir, so no test touches ~/.goldenmatch."""
    from goldenmatch.prefs.store import PresetStore

    store = PresetStore(tmp_path / "presets")
    monkeypatch.setattr(cli_main, "_get_store", lambda: store)
    return store


def _click_group() -> click.Group:
    cmd = typer.main.get_command(cli_main.app)
    assert isinstance(cmd, click.Group), "the top-level CLI must be a group"
    return cmd


# -- registration ------------------------------------------------------------


# Commands the README, docs site and PyPI page tell people to run. A typo in a
# registration, or a command module that stops importing, drops one of these
# silently -- the app still starts, that verb just no longer exists.
DOCUMENTED_COMMANDS = [
    "dedupe",
    "match",
    "evaluate",
    "info",
    "score",
    "profile",
    "config",
    "identity",
    "init",
    "demo",
    "explain",
    "serve",
    "mcp-serve",
]


@pytest.mark.parametrize("name", DOCUMENTED_COMMANDS)
def test_documented_command_is_registered(name):
    assert name in _click_group().commands, f"`goldenmatch {name}` is not registered"


def test_every_registered_command_resolves_and_is_callable():
    """Registration is lazy enough that a broken command can sit in the table
    until someone runs it. Resolving each one here is what makes that loud."""
    group = _click_group()
    ctx = click.Context(group)
    for name in group.commands:
        cmd = group.get_command(ctx, name)
        assert cmd is not None, f"{name} is listed but does not resolve"
        assert cmd.callback is not None or isinstance(cmd, click.Group), (
            f"{name} resolves but has no callback"
        )


def test_every_command_has_help_text():
    """Help is the only documentation many users read. Empty help is a bug."""
    group = _click_group()
    missing = [
        name
        for name, cmd in group.commands.items()
        if not (cmd.help or cmd.short_help)
    ]
    assert not missing, f"commands with no help text: {missing}"


def test_tui_is_registered_as_an_alias_of_interactive():
    """`interactive` (Python) and `tui` (TS) are the same operation; both CLIs
    answer to both names, and the parity manifest counts them as one
    capability. Registering the function twice is what the surface emitters
    see, so losing one name is a cross-language parity regression."""
    group = _click_group()
    assert "interactive" in group.commands
    assert "tui" in group.commands

    tui, interactive = group.commands["tui"], group.commands["interactive"]
    # NOT an identity check: Typer builds a fresh wrapper per registration, so
    # the two callbacks are distinct objects wrapping the same function. Compare
    # what actually establishes "same operation" -- the function they came from
    # and the parameters they expose.
    assert tui.callback.__qualname__ == interactive.callback.__qualname__
    assert tui.callback.__module__ == interactive.callback.__module__
    assert [p.name for p in tui.params] == [p.name for p in interactive.params]


def test_config_subcommands_are_registered():
    group = _click_group()
    config = group.commands["config"]
    assert isinstance(config, click.Group)
    for sub in ("save", "load", "list", "delete"):
        assert sub in config.commands, f"`goldenmatch config {sub}` is missing"


# -- info --------------------------------------------------------------------


def test_info_reports_the_installed_version(runner):
    result = runner.invoke(cli_main.app, ["info"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert __version__ in result.output


def test_info_lists_scorers_strategies_blocking_and_transforms(runner):
    """`info` is how a user discovers what values a config accepts, so it must
    enumerate the real registries rather than a hand-maintained copy."""
    from goldenmatch.config.schemas import VALID_SCORERS, VALID_STRATEGIES

    result = runner.invoke(cli_main.app, ["info"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    for label in ("Scorers:", "Strategies:", "Blocking:", "Transforms:"):
        assert label in result.output
    assert "jaro_winkler" in result.output
    # a sampling, not the whole set: the point is it reads the live registry
    for scorer in sorted(VALID_SCORERS)[:3]:
        assert scorer in result.output
    for strategy in sorted(VALID_STRATEGIES)[:2]:
        assert strategy in result.output


# -- score -------------------------------------------------------------------


def test_score_of_identical_strings_is_one(runner):
    result = runner.invoke(
        cli_main.app, ["score", "acme ltd", "acme ltd"], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    assert "1.0000" in result.output


def test_score_names_the_scorer_it_used(runner):
    result = runner.invoke(
        cli_main.app,
        ["score", "acme", "acme inc", "--scorer", "levenshtein"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert result.output.startswith("levenshtein:")


def test_score_defaults_to_jaro_winkler(runner):
    result = runner.invoke(
        cli_main.app, ["score", "acme", "acme inc"], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    assert result.output.startswith("jaro_winkler:")


def test_score_of_unrelated_strings_is_below_identical(runner):
    same = runner.invoke(cli_main.app, ["score", "acme", "acme"])
    diff = runner.invoke(cli_main.app, ["score", "acme", "zzzzzz"])
    assert float(same.output.split(":")[1]) > float(diff.output.split(":")[1])


# -- config presets ----------------------------------------------------------


def _write_config(path):
    path.write_text(
        "matchkeys:\n  - name: k\n    type: exact\n    fields:\n      - field: email\n",
        encoding="utf-8",
    )
    return path


def test_config_save_then_list_then_delete_round_trip(runner, isolated_store, tmp_path):
    cfg = _write_config(tmp_path / "goldenmatch.yaml")

    saved = runner.invoke(
        cli_main.app, ["config", "save", "mypreset", str(cfg)], catch_exceptions=False
    )
    assert saved.exit_code == 0, saved.output
    assert "mypreset" in isolated_store.list_presets()

    listed = runner.invoke(cli_main.app, ["config", "list"], catch_exceptions=False)
    assert listed.exit_code == 0
    assert "mypreset" in listed.output

    deleted = runner.invoke(
        cli_main.app, ["config", "delete", "mypreset"], catch_exceptions=False
    )
    assert deleted.exit_code == 0, deleted.output
    assert "mypreset" not in isolated_store.list_presets()


def test_config_load_writes_the_preset_to_the_requested_destination(
    runner, isolated_store, tmp_path
):
    cfg = _write_config(tmp_path / "src.yaml")
    isolated_store.save("p", cfg)
    dest = tmp_path / "out.yaml"

    result = runner.invoke(
        cli_main.app, ["config", "load", "p", "--dest", str(dest)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == cfg.read_text(encoding="utf-8")


def test_config_save_of_a_missing_file_exits_nonzero(runner, isolated_store, tmp_path):
    """A wrong path must fail loudly; silently saving nothing would leave the
    user with a preset name that loads an empty config later."""
    result = runner.invoke(
        cli_main.app, ["config", "save", "p", str(tmp_path / "nope.yaml")]
    )
    assert result.exit_code == 1
    assert isolated_store.list_presets() == []


def test_config_delete_of_a_missing_preset_exits_nonzero(runner, isolated_store):
    result = runner.invoke(cli_main.app, ["config", "delete", "ghost"])
    assert result.exit_code == 1


def test_config_list_is_quiet_and_successful_when_empty(runner, isolated_store):
    result = runner.invoke(cli_main.app, ["config", "list"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "No presets" in result.output


# -- bare invocation ---------------------------------------------------------


def test_bare_invocation_prints_the_banner_and_exits_zero(runner, tmp_path, monkeypatch):
    """`goldenmatch` with no arguments is the first thing a new user runs. The
    banner constructs a PresetStore and imports optional packages, so HOME is
    redirected here rather than reading the developer's own presets."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    result = runner.invoke(cli_main.app, [], catch_exceptions=False)
    assert result.exit_code == 0
    combined = result.output + (result.stderr or "")
    assert __version__ in combined


def test_banner_survives_a_missing_optional_package(monkeypatch, tmp_path, capsys):
    """The banner reports polars/rapidfuzz versions. Those are OPTIONAL -- since
    v3.1.0 a default install has no polars -- so an ImportError there must be
    reported in the table, not raised at a user who just typed the bare
    command."""
    import builtins

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    real_import = builtins.__import__

    def _no_polars(name, *args, **kwargs):
        if name == "polars" or name.startswith("polars."):
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_polars)
    cli_main._print_banner()  # must not raise

    out = capsys.readouterr()
    assert __version__ in (out.out + out.err)


def test_callback_analytics_failure_never_breaks_a_command(monkeypatch, runner):
    """Analytics is opt-in and explicitly 'never load-bearing'. If it raises,
    the command must still run -- otherwise a telemetry bug becomes a CLI
    outage."""
    import goldenmatch.core.analytics as analytics

    def _boom(*a, **k):
        raise RuntimeError("analytics backend down")

    monkeypatch.setattr(analytics, "capture", _boom)
    result = runner.invoke(cli_main.app, ["info"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert __version__ in result.output


# -- polars-free reachability of registered commands -------------------------
#
# Since v3.1.0 polars is an OPTIONAL extra, so `pip install goldenmatch` gives a
# polars-free install. #2810 fixed `dedupe` for that install and 3.17.0 shipped
# a second leak on the same path, so "a registered command hard-requires polars"
# is a known, repeated defect class here rather than a hypothetical.
#
# These run in a SUBPROCESS with polars blocked at the meta path: an import hook
# installed in-process would leak into every later test in the session.


def _polars_free(body: str) -> subprocess.CompletedProcess:
    prelude = (
        "import sys\n"
        "class _Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'polars' or name.startswith('polars.'):\n"
        "            raise ImportError('polars blocked')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Block())\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parent.parent)
    env["GOLDENMATCH_NATIVE"] = "0"
    env["GOLDENMATCH_FRAME"] = "arrow"
    return subprocess.run(
        [sys.executable, "-c", prelude + textwrap.dedent(body)],
        capture_output=True, text=True, env=env, timeout=300,
    )


def _cli_probe(argv: list[str], tmp_path) -> subprocess.CompletedProcess:
    csv = tmp_path / "c.csv"
    csv.write_text(
        "id,name,city\n1,Ann Smith,Leeds\n2,Ann Smyth,Leeds\n3,Bo Jones,Bristol\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        "matchkeys:\n  - name: k\n    type: exact\n    fields:\n      - field: name\n",
        encoding="utf-8",
    )
    resolved = [a.replace("<CSV>", str(csv)).replace("<CFG>", str(cfg)) for a in argv]
    return _polars_free(f"""
        from typer.testing import CliRunner
        from goldenmatch.cli.main import app
        res = CliRunner().invoke(app, {resolved!r})
        if res.exit_code != 0:
            raise SystemExit("FAILED: " + type(res.exception).__name__ + ": " + str(res.exception))
        print("OK")
    """)


def test_info_works_without_polars(tmp_path):
    """The cheapest possible smoke: a command that needs no data at all must
    not drag polars in through an import at module scope."""
    proc = _cli_probe(["info"], tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr[-1500:]


def test_score_works_without_polars(tmp_path):
    proc = _cli_probe(["score", "acme", "acme ltd"], tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr[-1500:]


def test_profile_works_without_polars(tmp_path):
    """Was a strict xfail; `profile` is now ported (load_file(return_frame=True),
    plus format_profile_report's sample block, which called the polars-only
    head()/iter_rows() and broke `--verbose` on the arrow lane)."""
    proc = _cli_probe(["profile", "<CSV>"], tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr[-1500:]


def test_analyze_blocking_works_without_polars(tmp_path):
    """Was a strict xfail; `analyze-blocking` now ingests via read_files_arrow.
    analyze_blocking itself already read through the frame seam."""
    proc = _cli_probe(["analyze-blocking", "<CSV>", "--config", "<CFG>"], tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr[-1500:]
