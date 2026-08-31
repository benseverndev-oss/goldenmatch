"""Which `goldenmatch` CLI commands are broken on a DEFAULT (polars-free) install?

polars has been an OPTIONAL extra since v3.1.0, so `pip install goldenmatch`
produces a polars-free environment. #2810 fixed `dedupe` for that install and
3.17.0 shipped a second leak on the same path, so "a registered command hard-
requires polars" is a repeated defect class here, not a hypothesis.

This sweeps every registered command rather than the handful someone thought to
check. It runs each one in a SUBPROCESS with polars blocked at the meta path --
an in-process hook would leak into everything after it.

    python scripts/sweep_cli_polars_free.py            # human-readable table
    python scripts/sweep_cli_polars_free.py --json     # machine-readable

Verdicts:

  polars_bound   invoking it raised ImportError naming polars. A user on a
                 default install gets a traceback, not an error message.
  ok             ran to completion, or failed for an unrelated reason, with
                 polars never imported.
  unprobed       needs arguments this harness cannot synthesise, or is a
                 long-running/interactive command (a server, a TUI, a watch
                 loop) that cannot be invoked non-interactively.

`unprobed` is NOT a pass. Static analysis alone misses indirect dependence:
`profile` has no polars reference in its own body and is still polars-bound,
because `load_file(...).collect()` round-trips through a LazyFrame. Only
invocation finds that class.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "packages" / "python" / "goldenmatch"

# Commands that never return on their own: servers, watch loops, TUIs, wizards.
# Excluded from invocation, not from concern -- several take a file argument and
# could carry the same defect. Reaching them needs a different probe.
NON_TERMINATING = {
    "serve",
    "serve-ui",
    "mcp-serve",
    "agent-serve",
    "watch",
    "schedule",
    "interactive",
    "tui",
    "setup",
    "init",
}

# Argument synthesis by parameter name. Anything unmatched makes the command
# `unprobed` rather than guessed at -- a wrong argument produces a usage error
# that looks like a pass.
CSV_NAMES = {
    "files",
    "file",
    "base_file",
    "input",
    "input_file",
    "path",
    "csv",
    "file_a",
    "new_records",
    "file_b",
    "target",
    "source",
}
CONFIG_NAMES = {"config", "config_path", "cfg"}

# Commands that CHANGE something -- a preset on disk, an identity store, a merge
# decision. They are never invoked here even when their arguments could be
# synthesised: a sweep that mutates the machine it runs on is not a sweep. Some
# are known polars-bound from a hand probe; those are recorded in
# scripts/test_cli_polars_free_sweep.py rather than re-derived by running them.
MUTATING = {
    "unmerge",
    "rollback",
    "config save",
    "config delete",
    "config load",
    "identity merge",
    "identity split",
    "identity migrate",
    "identity resolve",
    "memory add",
    "memory import",
    "sync",
    "certify-keys",
}


def _probe_source() -> str:
    return textwrap.dedent(
        '''
        import json, sys, tempfile, pathlib
        class _Block:
            def find_spec(self, name, path=None, target=None):
                if name == "polars" or name.startswith("polars."):
                    raise ImportError("No module named 'polars'")
                return None
        sys.meta_path.insert(0, _Block())

        import click, typer
        from typer.testing import CliRunner
        from goldenmatch.cli.main import app

        NON_TERMINATING = set(json.loads(sys.argv[1]))
        CSV_NAMES = set(json.loads(sys.argv[2]))
        CONFIG_NAMES = set(json.loads(sys.argv[3]))
        MUTATING = set(json.loads(sys.argv[4]))

        d = pathlib.Path(tempfile.mkdtemp())
        csv = d / "a.csv"
        csv.write_text(
            "id,name,email,city\\n1,Ann Smith,a@x.com,Leeds\\n"
            "2,Ann Smyth,a@x.com,Leeds\\n3,Bo Jones,b@x.com,Bristol\\n",
            encoding="utf-8")
        csv2 = d / "b.csv"
        csv2.write_text("id,name,email,city\\n4,Ann Smith,a@x.com,Leeds\\n", encoding="utf-8")
        cfg = d / "cfg.yaml"
        cfg.write_text(
            "matchkeys:\\n  - name: k\\n    type: exact\\n    fields:\\n      - field: email\\n",
            encoding="utf-8")

        grp = typer.main.get_command(app)
        ctx = click.Context(grp)

        leaves = []
        def walk(g, prefix=()):
            for name in sorted(g.commands):
                cmd = g.get_command(ctx, name)
                path = prefix + (name,)
                if isinstance(cmd, click.Group):
                    walk(cmd, path)
                else:
                    leaves.append((path, cmd))
        walk(grp)

        def synthesise(cmd):
            """Return argv tail, or None when a required param cannot be filled."""
            argv = []
            for p in cmd.params:
                if isinstance(p, click.Argument):
                    if not p.required:
                        continue
                    if p.name in CSV_NAMES:
                        argv.append(str(csv2 if p.name == "file_b" else csv))
                    else:
                        return None
                elif p.required:
                    flag = p.opts[0]
                    if p.name in CONFIG_NAMES:
                        argv += [flag, str(cfg)]
                    elif p.name in CSV_NAMES:
                        argv += [flag, str(csv2 if p.name == "file_b" else csv)]
                    elif p.name in ("fields", "field"):
                        argv += [flag, "name"]
                    else:
                        return None
            return argv

        runner = CliRunner()
        rows = []
        for path, cmd in leaves:
            name = " ".join(path)
            if path[-1] in NON_TERMINATING or path[0] in NON_TERMINATING:
                rows.append({"cmd": name, "verdict": "unprobed", "why": "non-terminating"})
                continue
            if name in MUTATING:
                rows.append({"cmd": name, "verdict": "unprobed", "why": "mutating -- never invoked"})
                continue
            tail = synthesise(cmd)
            if tail is None:
                rows.append({"cmd": name, "verdict": "unprobed", "why": "args not synthesisable"})
                continue
            res = runner.invoke(app, list(path) + tail)
            exc = res.exception
            bound = (
                isinstance(exc, (ImportError, ModuleNotFoundError))
                and "polars" in str(exc).lower()
            )
            rows.append({
                "cmd": name,
                "verdict": "polars_bound" if bound else "ok",
                "exit": res.exit_code,
                "why": (type(exc).__name__ + ": " + str(exc))[:120] if exc else "",
            })
        print("@@RESULT@@" + json.dumps(rows))
        '''
    )


def run_sweep() -> list[dict]:
    env = dict(os.environ)
    # PREPEND rather than overwrite: in CI the sibling packages (goldenphonetic
    # and friends) are already on PYTHONPATH, and clobbering it turns an import
    # error in a sibling into a fake "ok" verdict for every command.
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(PKG) + (os.pathsep + existing if existing else "")
    env["GOLDENMATCH_NATIVE"] = "0"
    env["GOLDENMATCH_FRAME"] = "arrow"
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "probe.py"
        script.write_text(_probe_source(), encoding="utf-8")
        # Run from a scratch CWD. Several commands write output next to where
        # they are invoked (`demo` and `dedupe` drop *_golden.csv), so a sweep
        # started at the repo root litters it with files that then get swept
        # into a commit by `git add -A`.
        workdir = Path(td) / "cwd"
        workdir.mkdir()
        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                json.dumps(sorted(NON_TERMINATING)),
                json.dumps(sorted(CSV_NAMES)),
                json.dumps(sorted(CONFIG_NAMES)),
                json.dumps(sorted(MUTATING)),
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=900,
            cwd=str(workdir),
        )
    marker = "@@RESULT@@"
    if marker not in proc.stdout:
        raise SystemExit(
            "probe produced no result:\n" + proc.stdout[-2000:] + "\n" + proc.stderr[-2000:]
        )
    return json.loads(proc.stdout.split(marker, 1)[1].splitlines()[0])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = run_sweep()
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    bound = [r for r in rows if r["verdict"] == "polars_bound"]
    ok = [r for r in rows if r["verdict"] == "ok"]
    unprobed = [r for r in rows if r["verdict"] == "unprobed"]

    print(
        f"{len(rows)} registered commands: {len(ok)} ok, "
        f"{len(bound)} polars-bound, {len(unprobed)} unprobed\n"
    )
    if bound:
        print("POLARS-BOUND (raw ImportError on a default install):")
        for r in sorted(bound, key=lambda r: r["cmd"]):
            print(f"  - {r['cmd']}")
        print()
    if unprobed:
        print("UNPROBED (not a pass -- see the module docstring):")
        for r in sorted(unprobed, key=lambda r: r["cmd"]):
            print(f"  - {r['cmd']:26s} {r['why']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
