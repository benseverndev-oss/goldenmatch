#!/usr/bin/env python3
"""Cross-language API parity gate. See docs/superpowers/specs/2026-07-04-api-parity-gate-design.md.

check_partition/check_structure are pure (dicts + sets); the CLI layer adds YAML + descriptor I/O.
"""
from __future__ import annotations

from typing import NamedTuple

SURFACES = ("mcp_tools", "cli_commands", "a2a_skills", "scorers", "transforms")


class ParityFailure(NamedTuple):
    surface: str
    name: str
    kind: str
    message: str


def check_partition(surface: str, manifest_surface: dict, py: set[str], ts: set[str]) -> list[ParityFailure]:
    """Assert the manifest exactly partitions py|ts. Returns [] when clean."""
    shared = set(manifest_surface.get("shared", []))
    py_only = set(manifest_surface.get("python_only", []))
    ts_only = set(manifest_surface.get("ts_only", []))
    declared = shared | py_only | ts_only
    both, only_py, only_ts = py & ts, py - ts, ts - py
    f: list[ParityFailure] = []

    def add(name, kind, msg):
        f.append(ParityFailure(surface, name, kind, msg))

    for n in sorted(both - shared):                       # row 1
        add(n, "unshared_common", f"'{n}' exists in both -> add to {surface}.shared")
    for n in sorted(only_py - py_only - shared):          # row 2
        add(n, "undeclared_py_only", f"'{n}' is Python-only and undeclared -> add to {surface}.python_only or port it to TS")
    for n in sorted(only_ts - ts_only - shared):          # row 3
        add(n, "undeclared_ts_only", f"'{n}' is TS-only and undeclared -> add to {surface}.ts_only or add it to Python")
    for n in sorted((shared & (py | ts)) - py):           # row 4a (shared, present in TS, gone from Python; absent-from-both is a phantom, row 7)
        add(n, "shared_missing_py", f"'{n}' is declared shared but missing from Python")
    for n in sorted((shared & (py | ts)) - ts):           # row 4b (shared, present in Python, gone from TS)
        add(n, "shared_missing_ts", f"'{n}' is declared shared but missing from TS")
    for n in sorted(py_only & ts):                        # row 5
        add(n, "py_only_in_ts", f"'{n}' is marked python_only but now exists in TS -> move to {surface}.shared")
    for n in sorted(ts_only & py):                        # row 6
        add(n, "ts_only_in_py", f"'{n}' is marked ts_only but now exists in Python -> move to {surface}.shared")
    for n in sorted(declared - (py | ts)):                # row 7
        add(n, "phantom", f"'{n}' is in the manifest but no longer exists -> remove it")
    return f


def check_structure(manifest: dict) -> list[ParityFailure]:
    f: list[ParityFailure] = []
    for surface, body in manifest.items():
        if surface == "package":
            continue
        if surface not in SURFACES:
            f.append(ParityFailure(surface, "", "unknown_surface", f"unknown surface '{surface}' (allowed: {', '.join(SURFACES)})"))
            continue
        lists = {k: list(body.get(k, [])) for k in ("shared", "python_only", "ts_only")}
        for k, v in lists.items():
            if v != sorted(v):
                f.append(ParityFailure(surface, "", "unsorted", f"{surface}.{k} is not sorted"))
        seen: dict[str, str] = {}
        for k, v in lists.items():
            for n in v:
                if n in seen:
                    f.append(ParityFailure(surface, n, "not_disjoint", f"'{n}' appears in both {surface}.{seen[n]} and {surface}.{k}"))
                seen[n] = k
    return f


def init_manifest(py_desc: dict, ts_desc: dict) -> dict:
    out = {"package": py_desc.get("package", ts_desc.get("package", ""))}
    for s in SURFACES:
        py, ts = set(py_desc.get(s, [])), set(ts_desc.get(s, []))
        if not py and not ts:
            continue
        out[s] = {"shared": sorted(py & ts), "python_only": sorted(py - ts), "ts_only": sorted(ts - py)}
    return out


def run_checks(manifest: dict, py_desc: dict, ts_desc: dict) -> list[ParityFailure]:
    fails = check_structure(manifest)
    if fails:  # a malformed manifest short-circuits before diffing
        return fails
    for s in SURFACES:
        if s not in manifest:
            continue
        fails += check_partition(s, manifest[s], set(py_desc.get(s, [])), set(ts_desc.get(s, [])))
    return fails


def _load_yaml(path):
    import yaml  # PyYAML; provisioned in CI + present in the box venv
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _dump_yaml(manifest) -> str:
    import yaml
    return yaml.safe_dump(manifest, sort_keys=False, default_flow_style=False, allow_unicode=True)


def _run_emitter(cmd: list[str]) -> dict:
    """Run an emitter subprocess; return its parsed JSON descriptor.
    Exit code 3 from an emitter = environment gap (missing extra) -> re-raise as SystemExit(3)."""
    import json, subprocess, sys
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 3:
        sys.stderr.write(proc.stderr)
        raise SystemExit(3)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"emitter failed ({' '.join(cmd)}): exit {proc.returncode}")
    return json.loads(proc.stdout)


def main(argv=None):
    import argparse, pathlib, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("--init", action="store_true", help="write a bootstrap manifest from both descriptors")
    ap.add_argument("--py-cmd", default=None, help="override python emitter argv (space-joined)")
    ap.add_argument("--ts-cmd", default=None, help="override ts emitter argv (space-joined)")
    args = ap.parse_args(argv)
    root = pathlib.Path(__file__).resolve().parent.parent
    py_cmd = (args.py_cmd.split() if args.py_cmd else
              [sys.executable, str(root / "scripts" / "emit_python_surface.py"), args.package])
    ts_cmd = (args.ts_cmd.split() if args.ts_cmd else
              ["node", str(root / "scripts" / "emit_ts_surface.mjs"), args.package])
    py_desc = _run_emitter(py_cmd)
    ts_desc = _run_emitter(ts_cmd)
    manifest_path = root / "parity" / f"{args.package}.yaml"

    if args.init or not manifest_path.exists():
        boot = init_manifest(py_desc, ts_desc)
        text = _dump_yaml(boot)
        if args.init:
            manifest_path.parent.mkdir(exist_ok=True)
            manifest_path.write_text(text, encoding="utf-8")
            print(f"wrote bootstrap manifest -> {manifest_path} (REVIEW the python_only/ts_only lists)")
            return 0
        sys.stderr.write(f"no manifest at {manifest_path}. Bootstrap (review + commit):\n\n{text}\n")
        return 1

    manifest = _load_yaml(manifest_path)
    fails = run_checks(manifest, py_desc, ts_desc)
    if not fails:
        print(f"parity OK: {args.package} manifest exactly partitions the real MCP + CLI surface")
        return 0
    for fl in fails:
        print(f"  [{fl.surface}] {fl.kind}: {fl.message}")
    print(f"\nparity FAILED: {len(fails)} issue(s). Reconcile parity/{args.package}.yaml.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
