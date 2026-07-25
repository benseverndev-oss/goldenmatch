#!/usr/bin/env python3
"""Cross-language API parity gate. See docs/superpowers/specs/2026-07-04-api-parity-gate-design.md.

check_partition/check_structure are pure (dicts + sets); the CLI layer adds YAML + descriptor I/O.
"""
from __future__ import annotations

from typing import NamedTuple

SURFACES = ("mcp_tools", "cli_commands", "a2a_skills", "scorers", "transforms", "blocking_strategies", "scorer_kernels")

# ADVISORY (non-gating) SQL surfaces: the Postgres (pgrx) + DuckDB function
# inventories. These give VISIBILITY into whether the SQL surfaces track the
# Python reference — they are reported but NEVER affect the exit code, because
# the SQL surfaces delegate to the Python library (a scorer/transform can't drift)
# and their operation coverage is a roadmap concern, not a per-PR gate. Recorded
# in the manifest as `{functions: [...]}` inventory blocks and reconciled by
# check_sql_advisory(). check_structure() skips them so the gating partition
# checks don't treat them as unknown surfaces.
ADVISORY_SQL_SURFACES = ("postgres", "duckdb")


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
        # `scorer_kernels_deferred` is a classification MAP (scorer -> reason),
        # not a shared/python_only/ts_only partition surface; its shape is
        # validated by check_scorer_coverage, so skip the partition checks here.
        if surface == "scorer_kernels_deferred":
            continue
        # Advisory SQL inventory surfaces are `{functions: [...]}`, not
        # shared/python_only/ts_only partitions — checked by check_sql_advisory.
        if surface in ADVISORY_SQL_SURFACES:
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


def check_scorer_coverage(manifest: dict) -> list[ParityFailure]:
    """Coverage gate for the scorer surface (goldenmatch): every scorer in the
    ``scorers`` surface must be EITHER kernel-backed (present in
    ``scorer_kernels``) OR explicitly classified in ``scorer_kernels_deferred``
    with a non-empty reason.

    This is the floor that keeps the ``N of 19 kernel-backed`` metric honest: a
    NEW scorer, or a kernel that regresses back to a pure-language fallback,
    lands as ``uncovered`` and FAILS -- so a fallback-only scorer can no longer
    sit unaddressed the way ``5 of 19`` did (nothing forced per-scorer
    classification). Deferral is a conscious act with a rationale, not silence.

    Only runs when both surfaces are present (goldenmatch); other packages have
    no scorer surface and are unaffected.
    """
    f: list[ParityFailure] = []
    if "scorers" not in manifest or "scorer_kernels" not in manifest:
        return f

    def _union(surface: str) -> set[str]:
        body = manifest.get(surface, {}) or {}
        return {
            n
            for key in ("shared", "python_only", "ts_only")
            for n in body.get(key, [])
        }

    all_scorers = _union("scorers")
    kernel_backed = _union("scorer_kernels")
    deferred = manifest.get("scorer_kernels_deferred", {}) or {}
    if not isinstance(deferred, dict):
        return [
            ParityFailure(
                "scorer_kernels_deferred", "", "malformed_deferred",
                "scorer_kernels_deferred must be a mapping of scorer -> reason",
            )
        ]
    deferred_names = set(deferred)
    # 1. fallback-only AND unclassified -> kernelize it or defer it with a reason.
    for n in sorted(all_scorers - kernel_backed - deferred_names):
        f.append(ParityFailure(
            "scorer_kernels", n, "uncovered_scorer",
            f"'{n}' has no kernel and no deferral -> kernelize it (add to "
            f"scorer_kernels) or add it to scorer_kernels_deferred with a reason",
        ))
    # 2. deferred but now kernel-backed -> the annotation is stale.
    for n in sorted(deferred_names & kernel_backed):
        f.append(ParityFailure(
            "scorer_kernels_deferred", n, "stale_deferral",
            f"'{n}' is now kernel-backed -> remove it from scorer_kernels_deferred",
        ))
    # 3. deferred but not a real scorer -> typo or removed scorer.
    for n in sorted(deferred_names - all_scorers):
        f.append(ParityFailure(
            "scorer_kernels_deferred", n, "unknown_deferral",
            f"'{n}' is deferred but is not in the scorers surface -> remove it",
        ))
    # 4. every live deferral needs a non-empty reason.
    for n in sorted(deferred_names & all_scorers):
        reason = deferred.get(n)
        if not isinstance(reason, str) or not reason.strip():
            f.append(ParityFailure(
                "scorer_kernels_deferred", n, "missing_reason",
                f"'{n}' deferral needs a non-empty reason string",
            ))
    return f


def _normalize_sql(name: str) -> str:
    """Strip the goldenmatch namespace prefix from a SQL function name so it can
    be compared, heuristically, against a Python operation name.
    `goldenmatch_identity_resolve` / `gm_resolve` -> `identity_resolve` / `resolve`."""
    for pfx in ("goldenmatch_", "gm_"):
        if name.startswith(pfx):
            return name[len(pfx):]
    return name


def _py_operation_surface(py_desc: dict, manifest: dict) -> set[str]:
    """The Python capability surface to measure SQL coverage against: MCP tools +
    CLI commands. Prefers the live descriptor (CI, where goldenmatch[mcp] is
    installed); falls back to the manifest's own gated lists when the descriptor
    is absent (e.g. the box, where the emitter env-gapped)."""
    ops: set[str] = set()
    for surface in ("mcp_tools", "cli_commands"):
        live = py_desc.get(surface) if py_desc else None
        if live:
            ops |= set(live)
        else:
            body = manifest.get(surface) or {}
            ops |= {n for key in ("shared", "python_only") for n in body.get(key, [])}
    return ops


def check_sql_advisory(manifest: dict, sql_desc: dict, py_ops: set[str]) -> list[str]:
    """ADVISORY (non-gating) reconciliation of the SQL surfaces. Returns human-
    readable report lines; the caller prints them but NEVER fails on them.

    Two kinds of visibility, per SQL dialect:
      1. DRIFT (robust): the emitted function set vs the manifest `functions`
         inventory — functions added in source but not recorded, and recorded but
         no longer in source. This is the maintenance signal for the inventory.
      2. COVERAGE (heuristic): how many Python operations (MCP tools + CLI) have a
         namespace-normalized match among the SQL functions — i.e. which Python
         capabilities have NO SQL entrypoint. Labeled heuristic because SQL names
         are hand-chosen and the prefix-normalized match is approximate."""
    lines: list[str] = []
    for surface in ADVISORY_SQL_SURFACES:
        emitted = set(sql_desc.get(surface, []) or [])
        if not emitted and surface not in manifest:
            continue  # dialect not present for this package
        declared = set((manifest.get(surface) or {}).get("functions", []))
        added = sorted(emitted - declared)
        removed = sorted(declared - emitted)
        lines.append(f"[{surface}] {len(emitted)} function(s) in source, {len(declared)} in manifest")
        for n in added:
            lines.append(f"  drift: '{n}' exists in source but is NOT in parity manifest -> add to {surface}.functions")
        for n in removed:
            lines.append(f"  drift: '{n}' is in the manifest but no longer in source -> remove from {surface}.functions")

        # Heuristic coverage: Python ops with no normalized SQL match.
        normalized = {_normalize_sql(n) for n in emitted}
        covered = {op for op in py_ops if op in normalized or any(
            nz == op or nz.startswith(op + "_") or op.startswith(nz + "_") for nz in normalized)}
        uncovered = sorted(py_ops - covered)
        if py_ops:
            lines.append(f"  coverage (heuristic): {len(covered)}/{len(py_ops)} Python ops have a {surface} entrypoint")
    return lines


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
    fails += check_scorer_coverage(manifest)
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

    # ADVISORY (non-gating) SQL surface report. Runs regardless of the gating
    # result and NEVER changes the exit code. The SQL emitter is a static source
    # parse (no toolchain), so it is best-effort here — a parse hiccup must not
    # break the gating gate.
    sql_cmd = ([sys.executable, str(root / "scripts" / "emit_sql_surface.py"), args.package])
    try:
        import json as _json
        import subprocess as _sp
        _proc = _sp.run(sql_cmd, capture_output=True, text=True)
        sql_desc = _json.loads(_proc.stdout) if _proc.returncode == 0 and _proc.stdout.strip() else {}
    except Exception as e:  # noqa: BLE001 — advisory, must never gate
        sql_desc = {}
        sys.stderr.write(f"(advisory) SQL surface emitter unavailable: {e}\n")
    advisory = check_sql_advisory(manifest, sql_desc, _py_operation_surface(py_desc, manifest))
    if advisory:
        print("\nadvisory: SQL surfaces (visibility only — does NOT affect the gate)")
        for line in advisory:
            print(f"  {line}")

    if not fails:
        print(f"parity OK: {args.package} manifest exactly partitions the real MCP + CLI surface")
        return 0
    for fl in fails:
        print(f"  [{fl.surface}] {fl.kind}: {fl.message}")
    print(f"\nparity FAILED: {len(fails)} issue(s). Reconcile parity/{args.package}.yaml.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
