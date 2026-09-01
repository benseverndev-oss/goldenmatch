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
import inspect
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from _polars_free_detect import looks_like_polars_import_error

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
    "data",
    "target",
    "against",
    "docs",
    "sample",
    "input_path",
    "file_b",
    "source",
}
CONFIG_NAMES = {"config", "config_path", "cfg"}

# Parameters that name an OUTPUT path. Given a path inside the scratch dir that
# does not exist yet, so a command writes there instead of refusing.
OUT_NAMES = {"out", "output", "output_path", "dest"}

# Scalar parameters, by name. Values are deliberately plausible-but-absent (an
# entity id that is not in any store, a preset that was never saved): the point
# is to REACH the command body, not to make it succeed. A command that then
# exits 1 saying "not found" has answered the only question this sweep asks --
# did it need polars to get that far.
STRING_DEFAULTS = {
    "entity_id": "1",
    "id_a": "1",
    "id_b": "2",
    "record_id": "1",
    "source_name": "probe_source",
    "source_pk_column": "id",
    "name": "probe_preset",
    "a": "acme ltd",
    "b": "acme limited",
    "cluster_id": "1",
    "keep": "1",
    "absorb": "2",
    "record_ids": "2",
    "run_id": "probe_run",
    "model": "probe_model",
    "decision": "merge",
    "dataset": "probe_dataset",
    "src": "memory.json",
}

# `identity migrate --dsn` wants a DATABASE connection string. Deliberately
# absent from STRING_DEFAULTS: a synthesised dsn either fails at parse (telling
# us nothing) or, worse, points at something real. It stays unprobed.

# Parameters wanting a structured file. Minimal-but-valid content, written into
# the scratch dir.
FIXTURE_NAMES = {
    "manifest": ("manifest.json", '{"nodes": {}, "sources": {}, "metadata": {}}'),
    "ontology": ("onto.ttl", "@prefix owl: <http://www.w3.org/2002/07/owl#> ."),
    "schema": ("schema.json", '{"fields": []}'),
    "sweep": ("sweep.json", "{}"),
}

# Commands that reach something this harness cannot contain: a database, a
# remote. Never invoked, at any isolation level.
NEVER_INVOKE = {
    "sync",
}

# Commands that mutate LOCAL state only -- presets, a memory store, an identity
# store, a clusters file. They ARE invoked, with $HOME redirected into the
# scratch directory so every write lands there. Leaving them unprobed was the
# safe default; it was also 13 commands whose polars status was simply unknown,
# and "unknown" was quietly counted as fine.
MUTATING_LOCAL = {
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
    "certify-keys",
}


def _probe_source() -> str:
    # The probe runs in a subprocess, so it cannot import the shared module --
    # inline the predicate's SOURCE, exactly as sweep_mcp_polars_free.py does,
    # so both sweeps and their tests apply one definition.
    return inspect.getsource(looks_like_polars_import_error) + textwrap.dedent(
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
        NEVER_INVOKE = set(json.loads(sys.argv[4]))
        OUT_NAMES = set(json.loads(sys.argv[5]))
        STRING_DEFAULTS = json.loads(sys.argv[6])
        FIXTURE_NAMES = json.loads(sys.argv[7])

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

        fixtures = {}
        for pname, (fname, body) in FIXTURE_NAMES.items():
            fp = d / fname
            fp.write_text(body, encoding="utf-8")
            fixtures[pname] = str(fp)

        _out_seq = [0]

        def out_path():
            _out_seq[0] += 1
            return str(d / f"out_{_out_seq[0]}.csv")

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

        def value_for(pname):
            """A plausible value for a parameter NAME, or None if we have none.

            Name-based rather than type-based on purpose: click reports `str`
            for a preset name, an entity id and an output path alike, so the
            type tells us nothing about what would reach the command body.
            """
            if pname in CONFIG_NAMES:
                return str(cfg)
            if pname in CSV_NAMES:
                return str(csv2 if pname == "file_b" else csv)
            if pname in OUT_NAMES:
                return out_path()
            if pname in FIXTURE_NAMES:
                return fixtures[pname]
            if pname in STRING_DEFAULTS:
                return STRING_DEFAULTS[pname]
            if pname in ("fields", "field"):
                return "name"
            return None

        def synthesise(cmd):
            """Return argv tail, or None when a required param cannot be filled."""
            argv = []
            for p in cmd.params:
                if isinstance(p, click.Argument):
                    if not p.required:
                        continue
                    v = value_for(p.name)
                    if v is None:
                        return None
                    argv.append(v)
                elif p.required:
                    v = value_for(p.name)
                    if v is None:
                        return None
                    argv += [p.opts[0], v]
            return argv

        runner = CliRunner()
        rows = []
        for path, cmd in leaves:
            name = " ".join(path)
            if path[-1] in NON_TERMINATING or path[0] in NON_TERMINATING:
                rows.append({"cmd": name, "verdict": "unprobed", "why": "non-terminating"})
                continue
            if name in NEVER_INVOKE:
                rows.append({"cmd": name, "verdict": "unprobed",
                             "why": "reaches an external system -- never invoked"})
                continue
            tail = synthesise(cmd)
            if tail is None:
                rows.append({"cmd": name, "verdict": "unprobed", "why": "args not synthesisable"})
                continue
            res = runner.invoke(app, list(path) + tail)
            exc = res.exception
            # The RAISED case, plus the caught-and-printed one. A command that
            # catches its own ImportError and prints "No module named 'polars'"
            # is exactly as broken for the user as one that lets it propagate,
            # but only the first was being counted -- which is how the MCP sweep
            # reported a clean zero while `read_file` was polars-bound.
            bound = looks_like_polars_import_error(str(exc) if exc else "") or (
                looks_like_polars_import_error(res.output or "")
            )
            if bound:
                verdict = "polars_bound"
            elif res.exit_code == 0:
                verdict = "ok"
            else:
                # NOT "ok". Lumping these in with success hid a real regression:
                # after the MatchEngine port `demo` swapped its ImportError for an
                # AttributeError and the sweep still called it fine, because it
                # only ever looked for polars.
                verdict = "errored"
            rows.append({
                "cmd": name,
                "verdict": verdict,
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
        # $HOME into the scratch dir: several commands write presets, memory or
        # identity state under ~/.goldenmatch. Redirecting it is what makes the
        # locally-mutating commands safe to actually RUN rather than skip.
        fake_home = Path(td) / "home"
        fake_home.mkdir()
        env["HOME"] = str(fake_home)
        env["USERPROFILE"] = str(fake_home)
        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                json.dumps(sorted(NON_TERMINATING)),
                json.dumps(sorted(CSV_NAMES)),
                json.dumps(sorted(CONFIG_NAMES)),
                json.dumps(sorted(NEVER_INVOKE)),
                json.dumps(sorted(OUT_NAMES)),
                json.dumps(STRING_DEFAULTS),
                json.dumps(FIXTURE_NAMES),
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
    errored = [r for r in rows if r["verdict"] == "errored"]
    unprobed = [r for r in rows if r["verdict"] == "unprobed"]

    print(
        f"{len(rows)} registered commands: {len(ok)} ok, "
        f"{len(bound)} polars-bound, {len(errored)} errored, {len(unprobed)} unprobed\n"
    )
    if bound:
        print("POLARS-BOUND (raw ImportError on a default install):")
        for r in sorted(bound, key=lambda r: r["cmd"]):
            print(f"  - {r['cmd']}")
        print()
    if errored:
        print("ERRORED for a non-polars reason (not a pass):")
        for r in sorted(errored, key=lambda r: r["cmd"]):
            print(f"  - {r['cmd']:26s} {r['why'][:60]}")
        print()
    if unprobed:
        print("UNPROBED (not a pass -- see the module docstring):")
        for r in sorted(unprobed, key=lambda r: r["cmd"]):
            print(f"  - {r['cmd']:26s} {r['why']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
