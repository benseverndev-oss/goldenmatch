#!/usr/bin/env python3
"""Unified benchmark dispatcher — local twin of the consolidated `bench.yml`.

Reads `.github/benchmarks/registry.yml` (the single source of truth) and either
lists the catalog, resolves a suite's run-plan for CI, or runs a suite locally.
One code path for "what does suite X need + how is it invoked", shared by the
GitHub workflow and a developer at their terminal.

    python scripts/bench.py --list
    python scripts/bench.py issue-688                 # run with the suite defaults
    python scripts/bench.py lsh-recall -- --threshold 0.5 --num-perms 128
    python scripts/bench.py perceptual --dry-run      # print the command, don't run
    python scripts/bench.py --resolve lsh-recall      # emit the CI run-plan

Per-suite CLI flags live in each bench script's own argparse; everything after
`--` (or any unrecognized trailing args) is forwarded to the script verbatim.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / ".github" / "benchmarks" / "registry.yml"

# Field -> (default, type). Unknown fields are rejected so a typo fails loudly
# instead of silently no-op'ing.
_FIELDS: dict[str, Any] = {
    "desc": None,
    "script": None,
    "workdir": ".",
    "native": False,
    "install": "uv",
    "extras": [],
    "with": [],
    "pip": [],
    "runner": "ubuntu-latest",
    "env": {},
    "args": "",
    "artifact": "",
    "summary": "",
}
_REQUIRED = ("desc", "script")
_VALID_INSTALL = ("uv", "pip")


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, dict[str, Any]]:
    """Parse + validate the registry, returning normalized suite entries."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top level must be a mapping of suite -> spec")
    out: dict[str, dict[str, Any]] = {}
    for name, spec in raw.items():
        out[name] = _normalize(name, spec)
    return out


def _normalize(name: str, spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValueError(f"suite '{name}': spec must be a mapping")
    unknown = set(spec) - set(_FIELDS)
    if unknown:
        raise ValueError(f"suite '{name}': unknown field(s) {sorted(unknown)}")
    for req in _REQUIRED:
        if not spec.get(req):
            raise ValueError(f"suite '{name}': missing required field '{req}'")
    entry = {k: spec.get(k, default) for k, default in _FIELDS.items()}
    if entry["install"] not in _VALID_INSTALL:
        raise ValueError(
            f"suite '{name}': install must be one of {_VALID_INSTALL}, "
            f"got '{entry['install']}'"
        )
    for listf in ("extras", "with", "pip"):
        if not isinstance(entry[listf], list):
            raise ValueError(f"suite '{name}': '{listf}' must be a list")
    if not isinstance(entry["env"], dict):
        raise ValueError(f"suite '{name}': 'env' must be a mapping")
    return entry


def resolve(name: str, reg: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if name not in reg:
        raise SystemExit(
            f"unknown suite '{name}'. Known: {', '.join(sorted(reg))}"
        )
    return reg[name]


def build_command(entry: dict[str, Any], extra_args: list[str]) -> list[str]:
    """Construct the local run command for a resolved suite entry."""
    args = extra_args or shlex.split(entry["args"])
    if entry["install"] == "uv":
        cmd = ["uv", "run"]
        for dep in entry["with"]:
            cmd += ["--with", dep]
        cmd += ["python", entry["script"], *args]
    else:
        cmd = ["python", entry["script"], *args]
    return cmd


def cmd_list(reg: dict[str, dict[str, Any]]) -> int:
    width = max((len(n) for n in reg), default=4)
    print(f"{'SUITE'.ljust(width)}  NATIVE  RUNNER                  DESC")
    for name in sorted(reg):
        e = reg[name]
        nat = "yes" if e["native"] else " - "
        print(f"{name.ljust(width)}   {nat}    {e['runner'].ljust(22)}  {e['desc']}")
    return 0


def cmd_resolve(name: str, reg: dict[str, dict[str, Any]]) -> int:
    """Emit the CI run-plan as `key=value` lines. Strings stay raw; lists/maps
    are JSON so the workflow can re-parse them. Appends to $GITHUB_OUTPUT when
    set (GitHub Actions), and always echoes to stdout for visibility."""
    e = resolve(name, reg)
    lines = [
        f"script={e['script']}",
        f"workdir={e['workdir']}",
        f"native={'true' if e['native'] else 'false'}",
        f"install={e['install']}",
        f"runner={e['runner']}",
        f"extras={json.dumps(e['extras'])}",
        f"with={json.dumps(e['with'])}",
        f"pip={json.dumps(e['pip'])}",
        f"env={json.dumps(e['env'])}",
        f"args={e['args']}",
        f"artifact={e['artifact']}",
        f"summary={e['summary']}",
    ]
    blob = "\n".join(lines) + "\n"
    sys.stdout.write(blob)
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(blob)
    return 0


def cmd_run(
    name: str, reg: dict[str, dict[str, Any]], extra_args: list[str], dry: bool
) -> int:
    e = resolve(name, reg)
    cmd = build_command(e, extra_args)
    workdir = REPO_ROOT / e["workdir"]
    env = {**os.environ, **{k: str(v) for k, v in e["env"].items()}}
    printable = f"(cd {e['workdir']} && {shlex.join(cmd)})"
    if dry:
        print(printable)
        return 0
    print(f"+ {printable}", file=sys.stderr)
    return subprocess.run(cmd, cwd=workdir, env=env, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    # allow_abbrev=False so a forwarded bench flag (e.g. --list-something) can't
    # be mistaken for a dispatcher flag; parse_known_args sends everything the
    # dispatcher doesn't own into `extra` for forwarding to the bench script.
    p = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    p.add_argument("suite", nargs="?", help="suite name to run")
    p.add_argument("--list", action="store_true", help="list the registry")
    p.add_argument("--resolve", metavar="SUITE", help="emit a suite's CI run-plan")
    p.add_argument("--dry-run", action="store_true", help="print the command, don't run")
    ns, extra = p.parse_known_args(argv)

    reg = load_registry()
    if ns.list:
        return cmd_list(reg)
    if ns.resolve:
        return cmd_resolve(ns.resolve, reg)
    if not ns.suite:
        p.error("a suite name, --list, or --resolve is required")
    # A literal "--" separator (if present) just delimits forwarded args; drop it.
    extra = [a for a in extra if a != "--"]
    return cmd_run(ns.suite, reg, extra, ns.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
