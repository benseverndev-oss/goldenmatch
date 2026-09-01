"""Which MCP tools are broken on a DEFAULT (polars-free) install?

The CLI sweep (``sweep_cli_polars_free.py``) found ELEVEN registered commands
that raised a bare ImportError on a default install, because polars has been an
OPTIONAL extra since v3.1.0. The MCP server is a second, larger surface -- 97
tools -- reached by a completely different entry point, and nothing had checked
it at all.

Argument synthesis is easier here than for the CLI: every tool carries an
``inputSchema``, so required properties and their types are declared rather than
inferred from a parameter name.

    python scripts/sweep_mcp_polars_free.py           # table
    python scripts/sweep_mcp_polars_free.py --json    # machine-readable

Verdicts match the CLI sweep exactly, and for the same reason:

  polars_bound   raised ImportError naming polars.
  ok             returned without raising.
  errored        raised something else. NOT a pass -- a tool that swaps an
                 ImportError for an AttributeError has not been fixed -- but
                 many are expected here, since synthesised arguments name
                 records and datasets that do not exist.
  needs_extra    raised an ImportError that NAMES an extra to install. A
                 documented capability limit, not a defect: GoldenFlow does
                 this on purpose when a transform config falls outside its
                 native columnar engine. Distinguished from `polars_bound` so
                 nobody "fixes" behaviour that is working as designed.
  unprobed       a required property this harness will not invent.

`mcp/*` is in the coverage `omit` list, so none of this surface appears in the
coverage numbers either. It was unmeasured twice over.
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

# Tools that reach outside the process: a database, a network, a spawned server.
NEVER_INVOKE = {
    "sync_database",
    "watch_database",
    "serve_api",
}

# Property names that should receive the scratch CSV rather than a bare string.
PATH_PROPS = {
    "file_path",
    "path",
    "file",
    "files",
    "input_path",
    "data_path",
    "target_file",
    "reference_file",
    "csv_path",
    "source_file",
    "file_a",
    "file_b",
}
CONFIG_PROPS = {"config_path", "config_file", "config"}


def _probe_source() -> str:
    return inspect.getsource(looks_like_polars_import_error) + textwrap.dedent(
        '''
        import json, sys, pathlib, tempfile
        class _Block:
            def find_spec(self, name, path=None, target=None):
                if name == "polars" or name.startswith("polars."):
                    raise ImportError("No module named 'polars'")
                return None
        sys.meta_path.insert(0, _Block())

        NEVER = set(json.loads(sys.argv[1]))
        PATH_PROPS = set(json.loads(sys.argv[2]))
        CONFIG_PROPS = set(json.loads(sys.argv[3]))

        d = pathlib.Path(tempfile.mkdtemp())
        csv = d / "a.csv"
        csv.write_text(
            "id,name,email,city\\n1,Ann Smith,a@x.com,Leeds\\n"
            "2,Ann Smyth,a@x.com,Leeds\\n3,Bo Jones,b@x.com,Hull\\n",
            encoding="utf-8")
        cfg = d / "cfg.yaml"
        cfg.write_text(
            "matchkeys:\\n  - name: k\\n    type: exact\\n    fields:\\n      - field: email\\n",
            encoding="utf-8")

        from goldenmatch.mcp.server import TOOLS, dispatch

        def synth(schema):
            """Fill REQUIRED properties only, from the declared schema."""
            props = (schema or {}).get("properties", {}) or {}
            required = list((schema or {}).get("required", []) or [])
            # Fill path-like properties even when the schema does not mark them
            # required. Several tools under-declare -- certify_recall and
            # pprl_link raised KeyError on a property listed as optional -- and
            # an under-filled call reports `errored` for a reason that is about
            # the harness rather than the code.
            for key in props:
                if key in PATH_PROPS or key in CONFIG_PROPS:
                    if key not in required:
                        required.append(key)
            args = {}
            for key in required:
                spec = props.get(key, {})
                typ = spec.get("type")
                if key in PATH_PROPS:
                    args[key] = str(csv)
                elif key in CONFIG_PROPS:
                    args[key] = str(cfg)
                elif "enum" in spec and spec["enum"]:
                    args[key] = spec["enum"][0]
                elif typ == "string":
                    args[key] = "probe"
                elif typ == "integer":
                    args[key] = 1
                elif typ == "number":
                    args[key] = 1.0
                elif typ == "boolean":
                    args[key] = False
                elif typ == "array":
                    args[key] = []
                elif typ == "object":
                    args[key] = {}
                else:
                    return None
            return args

        rows = []
        for tool in TOOLS:
            name = getattr(tool, "name", None)
            if name is None:
                continue
            if name in NEVER:
                rows.append({"tool": name, "verdict": "unprobed",
                             "why": "reaches an external system"})
                continue
            args = synth(getattr(tool, "inputSchema", None))
            if args is None:
                rows.append({"tool": name, "verdict": "unprobed",
                             "why": "required property this harness will not invent"})
                continue
            try:
                result = dispatch(name, args)
                # A RETURNED error counts. Several tools wrap their body in a
                # broad `except Exception` and return {"error": str(exc)}, so a
                # missing polars comes back as a payload rather than a raise --
                # `read_file` returned "Could not parse ...: No module named
                # 'polars'" and this sweep scored it `ok`. Checking only for
                # raised ImportErrors measured the error-handling style, not
                # the dependency.
                blob = json.dumps(result, default=str)
                if looks_like_polars_import_error(blob):
                    rows.append({
                        "tool": name,
                        "verdict": "polars_bound",
                        "why": "returned (did not raise) a polars ImportError",
                    })
                else:
                    rows.append({"tool": name, "verdict": "ok", "why": ""})
            except BaseException as exc:  # noqa: BLE001 - classify everything
                msg = str(exc)
                is_import = isinstance(exc, (ImportError, ModuleNotFoundError))
                # A DEFECT is the bare interpreter message. A deliberate,
                # actionable error that names the extra to install is a
                # documented capability limit, not a bug -- GoldenFlow raises
                # one on purpose when a transform config is not covered by its
                # native columnar engine. Collapsing the two would have had me
                # "fix" something working as designed.
                raw = is_import and looks_like_polars_import_error(msg)
                actionable = is_import and "pip install" in msg.lower()
                if raw:
                    verdict = "polars_bound"
                elif actionable:
                    verdict = "needs_extra"
                else:
                    verdict = "errored"
                rows.append({
                    "tool": name,
                    "verdict": verdict,
                    "why": (type(exc).__name__ + ": " + msg)[:140],
                })
        print("@@RESULT@@" + json.dumps(rows))
        '''
    )


def run_sweep() -> list[dict]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(PKG) + (os.pathsep + existing if existing else "")
    env["GOLDENMATCH_NATIVE"] = "0"
    env["GOLDENMATCH_FRAME"] = "arrow"
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "probe.py"
        script.write_text(_probe_source(), encoding="utf-8")
        workdir = Path(td) / "cwd"
        workdir.mkdir()
        home = Path(td) / "home"
        home.mkdir()
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                json.dumps(sorted(NEVER_INVOKE)),
                json.dumps(sorted(PATH_PROPS)),
                json.dumps(sorted(CONFIG_PROPS)),
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=1800,
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
    needs_extra = [r for r in rows if r["verdict"] == "needs_extra"]
    unprobed = [r for r in rows if r["verdict"] == "unprobed"]

    print(
        f"{len(rows)} MCP tools: {len(ok)} ok, {len(bound)} polars-bound, "
        f"{len(errored)} errored, {len(needs_extra)} needs-extra, {len(unprobed)} unprobed\n"
    )
    if bound:
        print("POLARS-BOUND (raw ImportError on a default install):")
        for r in sorted(bound, key=lambda r: r["tool"]):
            print(f"  - {r['tool']}")
        print()
    if needs_extra:
        print("NEEDS AN EXTRA (an ImportError naming a package to install):")
        for r in sorted(needs_extra, key=lambda r: r["tool"]):
            print(f"  - {r['tool']:30s} {r['why'][:70]}")
        print()
    if unprobed:
        print("UNPROBED (not a pass):")
        for r in sorted(unprobed, key=lambda r: r["tool"])[:20]:
            print(f"  - {r['tool']:34s} {r['why']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
