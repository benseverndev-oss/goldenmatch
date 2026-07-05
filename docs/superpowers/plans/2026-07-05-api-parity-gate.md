# Cross-language API Parity Gate — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Govern the Python↔TypeScript operation surface (MCP tools + CLI commands) of goldenmatch with a reviewed `parity/goldenmatch.yaml` manifest and a CI drift gate that fails on undeclared divergence.

**Architecture:** Two language "emitters" introspect each package's real registries and print a JSON surface descriptor `{package, mcp_tools, cli_commands}`. A gate compares the manifest against the union of both descriptors, asserting the manifest *exactly partitions* it into `shared`/`python_only`/`ts_only`. The gate's core (`check_partition`) is a pure set-diff function unit-tested with synthetic data on the box; the Python emitter runs on the box; the TS emitter and full gate run in CI (the box OOMs TS builds).

**Tech Stack:** Python 3.11 (stdlib + PyYAML for manifest I/O), Node ESM (the goldenmatch-js package), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-07-04-api-parity-gate-design.md`

**Branch/worktree:** `feat/api-parity-gate` in `D:\show_case\gg-local-llm` (off `origin/main`).

---

## Constraints (read once, applies throughout)

- **Correct worktree only.** All code lives at `D:\show_case\gg-local-llm` (`origin/main`). Do NOT read `D:\show_case\goldenmatch` — it is a stale worktree on an old branch (spec §"Code verification note").
- **Python emitter + gate unit tests + manifest self-check = box-safe.** The main venv already has `goldenmatch[mcp]` (verified: `from goldenmatch.mcp.server import TOOLS` imports, `len(TOOLS)==69`). Run Python under:
  ```bash
  POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 PYTHONPATH="packages/python/goldenmatch" \
    /d/show_case/goldenmatch/.venv/Scripts/python.exe <cmd>
  ```
- **TS emitter = CI-ONLY.** vitest/tsup OOM-kill the box (exit 137). Write the TS files, verify-in-head, let CI validate. Do NOT run pnpm/node builds locally.
- **Manifest format is YAML** (comments record why gaps are intentional). The gate parses it with PyYAML (present in the box venv and pullable in CI). Keep the gate's *core logic* (`check_partition`, structural checks) operating on plain dicts/sets so the unit tests need no YAML and no package import.
- **Verified real symbols (origin/main):** Python MCP `goldenmatch.mcp.server.TOOLS` (`mcp/server.py:585`, 69 `Tool`s each with `.name`); Python CLI `typer.main.get_command(goldenmatch.cli.main.app).commands.keys()` → 32 resolved names incl. groups `pprl/memory/identity/config` and hyphenated leaves like `mcp-serve`; TS MCP `export const TOOLS` (`src/node/mcp/server.ts:369`, `.name`); TS CLI commander `program` (`src/cli.ts:122`, unexported + boots on import at `:992` — Task 1 fixes that).
- **GitHub:** `benzsevern` account (unset `GH_TOKEN` before `gh auth switch`). This repo uses a **merge queue** — `gh pr merge --auto --squash` WITHOUT `--delete-branch` (the flag errors under a merge queue). Arm auto-merge and STOP (no CI polling).

---

## File structure

| File | Action | Responsibility |
|------|--------|----------------|
| `packages/typescript/goldenmatch/src/cli.ts` | Modify | Export `program` + guard top-level `parseAsync` so importing doesn't boot the CLI (enables the TS emitter) |
| `scripts/emit_python_surface.py` | Create | Python emitter: registry map + introspect `TOOLS` and the Typer app → sorted JSON descriptor |
| `scripts/emit_ts_surface.mjs` | Create | Node ESM emitter: import `TOOLS` + `program` → sorted JSON descriptor (CI-only) |
| `scripts/check_api_parity.py` | Create | The gate: `check_partition` (pure) + structural checks + manifest/descriptor I/O + `--init` + CLI + env-vs-surface exit codes |
| `scripts/test_api_parity.py` | Create | Pure-data unit tests for the gate (every §5 row, structural errors, `--init`, env distinction) |
| `parity/goldenmatch.yaml` | Create | The reviewed manifest (bootstrapped from `--init`, then human-reviewed) |
| `.github/workflows/ci.yml` | Modify | `api_parity` job + `dorny/paths-filter` entry |

---

## Task 1: TS CLI enablement — export `program` + main-module guard

Make `src/cli.ts` importable without booting the CLI, so the TS emitter can read `program.commands`. CI-verified (box can't build TS).

**Files:**
- Modify: `packages/typescript/goldenmatch/src/cli.ts` (line 122 the `program` decl; line ~992 the top-level `parseAsync`).

- [ ] **Step 1: Read the current head + tail of the file** to confirm the exact text.

Run: read `packages/typescript/goldenmatch/src/cli.ts` around lines 1-10 (imports), 118-126 (`program` decl), 985-995 (the `parseAsync` call).

- [ ] **Step 2: Export the `program` const.**

Change `const program = new Command();` (line ~122) to:
```ts
export const program = new Command();
```

- [ ] **Step 3: Add the ESM `pathToFileURL` import if not already present.**

At the top of the file, ensure `node:url` provides `pathToFileURL`. If there is an existing `import ... from "node:url"`, add `pathToFileURL` to it; otherwise add:
```ts
import { pathToFileURL } from "node:url";
```
(Confirm the file isn't `--strict`-blocked on an unused import — it IS used in Step 4.)

- [ ] **Step 4: Guard the top-level parse so import doesn't boot the CLI.**

Wrap the existing top-level call (around line 992):
```ts
program.parseAsync(process.argv).catch((err: unknown) => { ... });
```
in a main-module check:
```ts
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  program.parseAsync(process.argv).catch((err: unknown) => { ... });
}
```
Keep the exact existing `.catch(...)` body. The `goldenmatch-js` bin still runs the CLI (it invokes this module as the entrypoint, so `argv[1]` === this file); an `import { program }` does not.

- [ ] **Step 5: Typecheck-in-head.** Confirm: `program` is now exported once; `pathToFileURL` imported once and used; the guard wraps the ONLY top-level `parseAsync`; no other top-level side effect remains (grep the file for other bare `program.` calls at column 0). Do NOT run tsc/pnpm (OOM). CI's `typescript` lane + the `api_parity` job validate it.

- [ ] **Step 6: Commit.**
```bash
git add packages/typescript/goldenmatch/src/cli.ts
git commit -m "feat(goldenmatch-js): export program + main-module guard cli.ts (enable parity emitter)"
```

---

## Task 2: Gate core — `check_partition` pure function (TDD, box-safe)

The heart of the gate: given a manifest surface + the two languages' name sets, return the list of §5-table failures. Pure — no I/O, no imports — so its tests are fast and box-safe.

**Files:**
- Create: `scripts/check_api_parity.py`
- Create/Test: `scripts/test_api_parity.py`

- [ ] **Step 1: Write the failing tests** (`scripts/test_api_parity.py`)

```python
"""Unit tests for the API-parity gate. Pure data — no package imports, no YAML,
box-safe. Run: python -m pytest scripts/test_api_parity.py -q"""
import importlib.util, pathlib
_spec = importlib.util.spec_from_file_location(
    "check_api_parity", pathlib.Path(__file__).parent / "check_api_parity.py")
gate = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(gate)


def kinds(fails):
    return sorted(f.kind for f in fails)


def test_clean_partition_passes():
    m = {"shared": ["a", "b"], "python_only": ["p"], "ts_only": ["t"]}
    fails = gate.check_partition("mcp_tools", m, py={"a", "b", "p"}, ts={"a", "b", "t"})
    assert fails == []


def test_common_but_not_shared():
    m = {"shared": [], "python_only": [], "ts_only": []}
    fails = gate.check_partition("mcp_tools", m, py={"x"}, ts={"x"})
    assert kinds(fails) == ["unshared_common"]


def test_undeclared_python_only():
    m = {"shared": [], "python_only": [], "ts_only": []}
    fails = gate.check_partition("mcp_tools", m, py={"x"}, ts=set())
    assert kinds(fails) == ["undeclared_py_only"]


def test_undeclared_ts_only():
    m = {"shared": [], "python_only": [], "ts_only": []}
    fails = gate.check_partition("mcp_tools", m, py=set(), ts={"x"})
    assert kinds(fails) == ["undeclared_ts_only"]


def test_shared_missing_from_one_language():
    m = {"shared": ["x"], "python_only": [], "ts_only": []}
    fails = gate.check_partition("mcp_tools", m, py={"x"}, ts=set())
    assert kinds(fails) == ["shared_missing_ts"]


def test_python_only_now_in_ts():
    m = {"shared": [], "python_only": ["x"], "ts_only": []}
    fails = gate.check_partition("mcp_tools", m, py={"x"}, ts={"x"})
    # x is in both -> should be shared; declared python_only but present in TS
    assert "py_only_in_ts" in kinds(fails)


def test_ts_only_now_in_python():
    m = {"shared": [], "python_only": [], "ts_only": ["x"]}
    fails = gate.check_partition("mcp_tools", m, py={"x"}, ts={"x"})
    assert "ts_only_in_py" in kinds(fails)


def test_phantom_manifest_entry():
    m = {"shared": ["ghost"], "python_only": [], "ts_only": []}
    fails = gate.check_partition("mcp_tools", m, py=set(), ts=set())
    assert kinds(fails) == ["phantom"]
```

- [ ] **Step 2: Run — verify it fails** (module doesn't exist yet)

Run: `POLARS_SKIP_CPU_CHECK=1 /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_api_parity.py -q`
Expected: collection/import error (no `check_api_parity.py`).

- [ ] **Step 3: Implement `check_partition`** (start `scripts/check_api_parity.py`)

```python
#!/usr/bin/env python3
"""Cross-language API parity gate. See docs/superpowers/specs/2026-07-04-api-parity-gate-design.md.

check_partition/check_structure are pure (dicts + sets); the CLI layer adds YAML + descriptor I/O.
"""
from __future__ import annotations

from typing import NamedTuple

SURFACES = ("mcp_tools", "cli_commands")


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
    for n in sorted(shared - py):                         # row 4a
        add(n, "shared_missing_py", f"'{n}' is declared shared but missing from Python")
    for n in sorted(shared - ts):                         # row 4b
        add(n, "shared_missing_ts", f"'{n}' is declared shared but missing from TS")
    for n in sorted(py_only & ts):                        # row 5
        add(n, "py_only_in_ts", f"'{n}' is marked python_only but now exists in TS -> move to {surface}.shared")
    for n in sorted(ts_only & py):                        # row 6
        add(n, "ts_only_in_py", f"'{n}' is marked ts_only but now exists in Python -> move to {surface}.shared")
    for n in sorted(declared - (py | ts)):                # row 7
        add(n, "phantom", f"'{n}' is in the manifest but no longer exists -> remove it")
    return f
```

- [ ] **Step 4: Run — verify pass.** Run the pytest command from Step 2. Expected: 8 passed.

- [ ] **Step 5: Commit.**
```bash
git add scripts/check_api_parity.py scripts/test_api_parity.py
git commit -m "feat(parity): check_partition gate core + unit tests"
```

---

## Task 3: Structural manifest checks (TDD, box-safe)

Catch a malformed manifest before diffing: a name in two partitions, an unsorted list, an unknown surface key.

**Files:** Modify `scripts/check_api_parity.py`, `scripts/test_api_parity.py`.

- [ ] **Step 1: Add failing tests** (append to `test_api_parity.py`)

```python
def test_structure_flags_duplicate_across_partitions():
    m = {"mcp_tools": {"shared": ["x"], "python_only": ["x"], "ts_only": []}}
    fails = gate.check_structure(m)
    assert any(f.kind == "not_disjoint" for f in fails)


def test_structure_flags_unsorted():
    m = {"mcp_tools": {"shared": ["b", "a"], "python_only": [], "ts_only": []}}
    fails = gate.check_structure(m)
    assert any(f.kind == "unsorted" for f in fails)


def test_structure_flags_unknown_surface():
    m = {"a2a_skills": {"shared": [], "python_only": [], "ts_only": []}}
    fails = gate.check_structure(m)
    assert any(f.kind == "unknown_surface" for f in fails)


def test_structure_clean():
    m = {"mcp_tools": {"shared": ["a", "b"], "python_only": [], "ts_only": []}}
    assert gate.check_structure(m) == []
```

- [ ] **Step 2: Run — verify fail** (`check_structure` undefined).

- [ ] **Step 3: Implement `check_structure`** (add to `check_api_parity.py`)

```python
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
```

- [ ] **Step 4: Run — verify pass** (12 passed). **Step 5: Commit** (`test(parity): structural manifest checks`).

---

## Task 4: Gate CLI, YAML/descriptor I/O, `--init`, exit codes (TDD where box-safe)

Wire the pure functions into a runnable gate: load the manifest (YAML) + both descriptors (JSON), run structural + partition checks across both surfaces, and print actionable output. Add `--init`. Define exit codes: `0` clean, `1` drift/structural failure, `3` environment gap (an emitter couldn't provision an extra).

**Files:** Modify `scripts/check_api_parity.py`, `scripts/test_api_parity.py`.

- [ ] **Step 1: Add failing tests for `init_manifest` + `run_checks`** (pure, no I/O)

```python
def test_init_manifest_partitions():
    py = {"package": "gm", "mcp_tools": ["a", "p"], "cli_commands": ["x"]}
    ts = {"package": "gm", "mcp_tools": ["a", "t"], "cli_commands": ["x"]}
    m = gate.init_manifest(py, ts)
    assert m["mcp_tools"] == {"shared": ["a"], "python_only": ["p"], "ts_only": ["t"]}
    assert m["cli_commands"] == {"shared": ["x"], "python_only": [], "ts_only": []}
    # the generated manifest passes its own gate
    assert gate.run_checks(m, py, ts) == []


def test_run_checks_reports_across_surfaces():
    m = {"package": "gm",
         "mcp_tools": {"shared": [], "python_only": [], "ts_only": []},
         "cli_commands": {"shared": [], "python_only": [], "ts_only": []}}
    py = {"package": "gm", "mcp_tools": ["a"], "cli_commands": []}
    ts = {"package": "gm", "mcp_tools": [], "cli_commands": ["b"]}
    fails = gate.run_checks(m, py, ts)
    assert {f.kind for f in fails} == {"undeclared_py_only", "undeclared_ts_only"}
```

- [ ] **Step 2: Run — verify fail. Step 3: Implement `init_manifest` + `run_checks`.**

```python
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
```

- [ ] **Step 4: Run — verify pass (14 passed).**

- [ ] **Step 5: Add the I/O + CLI layer** (bottom of `check_api_parity.py`) — not unit-tested (I/O), exercised by the emitter/CI tasks.

```python
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
        # missing manifest during a normal run: print the bootstrap for capture, fail
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
```

- [ ] **Step 6: Commit.**
```bash
git add scripts/check_api_parity.py scripts/test_api_parity.py
git commit -m "feat(parity): gate CLI, YAML/descriptor I/O, --init, exit codes"
```

---

## Task 5: Python emitter + smoke test (box-safe)

**Files:**
- Create: `scripts/emit_python_surface.py`
- Test: append to `scripts/test_api_parity.py` (a box-safe smoke test that imports goldenmatch).

- [ ] **Step 1: Implement the emitter** (`scripts/emit_python_surface.py`)

```python
#!/usr/bin/env python3
"""Emit goldenmatch's real Python operation surface as JSON: {package, mcp_tools, cli_commands}.
Runtime introspection of the actual registries. Needs the surface-bearing extras installed
(goldenmatch[mcp]); a missing extra exits 3 (environment gap), distinct from a code breakage (2)."""
from __future__ import annotations
import json, sys

# Per-package registry map. Each surface -> a callable returning a list[str] of names.
# Extend this dict to add packages (the follow-up); goldenmatch is the reference.
def _goldenmatch_mcp() -> list[str]:
    from goldenmatch.mcp.server import TOOLS      # needs goldenmatch[mcp]
    return [t.name for t in TOOLS]

def _goldenmatch_cli() -> list[str]:
    # NOTE: deliberately uses typer.main.get_command(app).commands.keys() rather than the spec's
    # §3.1 app.registered_commands/registered_groups. get_command resolves the real CLI names
    # (hyphenation like `mcp-serve`, and commands whose .name is None derive from the function
    # name) and includes groups — the authoritative surface a user actually types. Verified.
    from typer.main import get_command
    from goldenmatch.cli.main import app
    names = list(get_command(app).commands.keys())  # resolved leaves + groups (mcp-serve, pprl, ...)
    if len(names) != len(set(names)):
        raise SystemExit("CLI leaf/group name collision in goldenmatch — surface is ambiguous")
    return names

REGISTRY = {
    "goldenmatch": {
        "mcp_tools": (_goldenmatch_mcp, "mcp"),      # (emitter, extra-name for the env-gap message)
        "cli_commands": (_goldenmatch_cli, None),
    },
}

def emit(package: str) -> dict:
    spec = REGISTRY.get(package)
    if spec is None:
        raise SystemExit(f"no parity registry entry for '{package}'")
    out = {"package": package}
    for surface, (fn, extra) in spec.items():
        try:
            out[surface] = sorted(fn())
        except ModuleNotFoundError as e:
            # a surface-bearing OPTIONAL extra is absent -> environment gap, not drift
            sys.stderr.write(f"environment not provisioned for {package}.{surface}: "
                             f"install {package}[{extra}] (missing module: {e.name})\n")
            raise SystemExit(3)
    return out

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: emit_python_surface.py <package>")
    print(json.dumps(emit(sys.argv[1]), sort_keys=True))
```

- [ ] **Step 2: Add a box-safe smoke test** (append to `test_api_parity.py`)

```python
import os, subprocess, sys, json, pathlib

def test_python_emitter_goldenmatch_smoke():
    """Runs the real emitter against goldenmatch. Box-safe (needs goldenmatch[mcp] in the venv)."""
    root = pathlib.Path(__file__).resolve().parent.parent
    env = {**os.environ, "POLARS_SKIP_CPU_CHECK": "1", "GOLDENMATCH_NATIVE": "0",
           "PYTHONPATH": str(root / "packages" / "python" / "goldenmatch")}
    proc = subprocess.run([sys.executable, str(root / "scripts" / "emit_python_surface.py"), "goldenmatch"],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    desc = json.loads(proc.stdout)
    assert desc["package"] == "goldenmatch"
    assert desc["mcp_tools"] == sorted(desc["mcp_tools"]) and desc["mcp_tools"]
    assert desc["cli_commands"] == sorted(desc["cli_commands"]) and desc["cli_commands"]
    # MCP count equals the MEASURED len(TOOLS) — never a hardcoded number
    from goldenmatch.mcp.server import TOOLS
    assert len(desc["mcp_tools"]) == len(TOOLS)
    # known real names present
    assert "find_duplicates" in desc["mcp_tools"]
    assert "mcp-serve" in desc["cli_commands"] and "identity" in desc["cli_commands"]
```

- [ ] **Step 3: Run — verify pass.**

Run: `POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 PYTHONPATH="packages/python/goldenmatch" /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_api_parity.py -q`
Expected: all pass (15 unit + the smoke). If the smoke's `from goldenmatch.mcp.server import TOOLS` raises ModuleNotFoundError, the venv lacks `[mcp]` — install it into the venv (`uv pip install -e "packages/python/goldenmatch[mcp]"`) and retry.

- [ ] **Step 4: Commit.**
```bash
git add scripts/emit_python_surface.py scripts/test_api_parity.py
git commit -m "feat(parity): python surface emitter + box smoke test"
```

---

## Task 6: TypeScript emitter (CI-only, verify-in-head)

**Files:**
- Create: `scripts/emit_ts_surface.mjs`

- [ ] **Step 1: Implement the emitter.** It imports the built goldenmatch-js registries. Because it runs only in CI where the package is built, import from the built entry (`dist`) via the package's export map, or load `src` via `tsx`. Prefer importing the compiled `dist` the `typescript` lane produces.

```js
#!/usr/bin/env node
// Emit goldenmatch's real TS operation surface as JSON: {package, mcp_tools, cli_commands}.
// CI-only (the box OOMs TS builds). Imports the built package registries — no server boot
// (Task 1 made src/cli.ts import-safe; node/mcp/server.ts's TOOLS is a module-level export).
import path from "node:path";
import { pathToFileURL } from "node:url";

const REGISTRY = {
  goldenmatch: {
    // paths are relative to the goldenmatch-js package dist; resolved below
    mcpFrom: "dist/node/mcp/server.js",  // export const TOOLS
    cliFrom: "dist/cli.js",              // export const program (Task 1)
  },
};

async function emit(pkg) {
  const spec = REGISTRY[pkg];
  if (!spec) throw new Error(`no parity registry entry for '${pkg}'`);
  const base = path.resolve(process.cwd(), "packages/typescript", pkg);
  const mcpMod = await import(pathToFileURL(path.join(base, spec.mcpFrom)).href);
  const cliMod = await import(pathToFileURL(path.join(base, spec.cliFrom)).href);
  const mcp_tools = [...mcpMod.TOOLS].map((t) => t.name).sort();
  const cli_commands = cliMod.program.commands.map((c) => c.name()).sort();
  return { package: pkg, mcp_tools, cli_commands };
}

const pkg = process.argv[2];
if (!pkg) { console.error("usage: emit_ts_surface.mjs <package>"); process.exit(2); }
emit(pkg).then((d) => console.log(JSON.stringify(d, Object.keys(d).sort()))).catch((e) => {
  console.error(e?.stack || String(e)); process.exit(2);
});
```

- [ ] **Step 2: Verify-in-head.** Confirm against Task-1 output + verified symbols: `dist/node/mcp/server.js` exports `TOOLS` (from `src/node/mcp/server.ts:369`); `dist/cli.js` exports `program` (Task 1) and no longer boots on import (Task 1 guard). Names come out as `.name` (MCP `Tool`) / `.name()` (commander). `JSON.stringify(d, Object.keys(d).sort())` — note: the 2nd arg to stringify is a *replacer allowlist*; here the keys are `package/mcp_tools/cli_commands`, all wanted, so it's fine, but simpler is `JSON.stringify(d)` with the object built in sorted key order. Use `JSON.stringify({ package: pkg, cli_commands, mcp_tools })` with keys in the same order the Python emitter's `sort_keys=True` produces (`cli_commands, mcp_tools, package`) OR rely on the gate comparing by key, not string — the gate parses JSON so key order is irrelevant. Keep `JSON.stringify(d)`; drop the replacer to avoid confusion.

Apply that simplification: replace the final line's `JSON.stringify(d, Object.keys(d).sort())` with `JSON.stringify(d)`.

- [ ] **Step 3: Commit.**
```bash
git add scripts/emit_ts_surface.mjs
git commit -m "feat(parity): typescript surface emitter (CI-only)"
```

---

## Task 7: CI `api_parity` job

**Files:**
- Modify: `.github/workflows/ci.yml` (add a `changes` filter output + the job). Editing this file forces every job to re-run.

- [ ] **Step 1: Read the existing patterns.** Read the `changes` job's `dorny/paths-filter` block and one representative TS-building job (e.g. `goldenpipe_wasm` or `typescript`) to copy the Python(uv)+Node(pnpm) setup + action SHAs.

- [ ] **Step 2: Add the filter AND wire it to a `changes` job output.** Two edits in the `changes` job — BOTH are required, or `needs.changes.outputs.api_parity` is empty and the job is silently skipped at PR time (which would break Task 8).

  (a) In the `dorny/paths-filter` step's `with.filters`, add:
```yaml
            api_parity:
              - 'packages/python/goldenmatch/**'
              - 'packages/typescript/goldenmatch/**'
              - 'parity/goldenmatch.yaml'
              - 'scripts/emit_python_surface.py'
              - 'scripts/emit_ts_surface.mjs'
              - 'scripts/check_api_parity.py'
```
  (b) In the `changes` job's `outputs:` map (near `ci.yml:102`, alongside the other `<area>: ${{ steps.filter.outputs.<area> }}` lines), add:
```yaml
      api_parity: ${{ steps.filter.outputs.api_parity }}
```
(Read the existing `outputs:` block first and match its exact key style; the filter `id` may be `filter` or `changes` — use whatever the sibling lines use.)

- [ ] **Step 3: Add the job** (mirror the setup of an existing python+node job; pin the same action SHAs already used in the file):
```yaml
  api_parity:
    needs: changes
    if: needs.changes.outputs.api_parity == 'true' || needs.changes.outputs.force_all == 'true'
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@<sha-from-file>
      - uses: astral-sh/setup-uv@<sha-from-file>
      - uses: pnpm/action-setup@<sha-from-file>
      - uses: actions/setup-node@<sha-from-file>
        with: { node-version: 22, cache: pnpm }
      - name: Sync workspace + install the goldenmatch[mcp] extra
        # Match the repo's uv convention (uv sync into .venv, then uv run). `uv sync` does NOT
        # install optional extras, so add [mcp] explicitly. pyyaml is already a core goldenmatch
        # dep, so it comes in via the sync — no need to add it.
        run: |
          uv sync --all-packages
          uv pip install -e "packages/python/goldenmatch[mcp]"
      - name: Gate unit tests (pure data)
        run: uv run pytest scripts/test_api_parity.py -q
        env: { POLARS_SKIP_CPU_CHECK: "1", GOLDENMATCH_NATIVE: "0", PYTHONPATH: "packages/python/goldenmatch" }
      - name: Build goldenmatch-js (TS emitter imports dist)
        run: pnpm install --frozen-lockfile && pnpm --filter goldenmatch build
      - name: Parity gate (both emitters + manifest)
        run: uv run python scripts/check_api_parity.py goldenmatch
        env: { POLARS_SKIP_CPU_CHECK: "1", GOLDENMATCH_NATIVE: "0", PYTHONPATH: "packages/python/goldenmatch" }
```
(Read a sibling Python job first — e.g. `ci.yml:~858` — and match its exact `uv sync` / `uv run` / node-setup invocation and action SHAs. Essential requirements: `goldenmatch[mcp]` importable in the uv env, `goldenmatch-js` built to `dist/`, both emitters + the gate run. The gate shells out to `node scripts/emit_ts_surface.mjs` itself, so `node` must be on PATH from the `setup-node` step.)

- [ ] **Step 4: Validate YAML.** Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('OK')"`.

- [ ] **Step 5: Commit.**
```bash
git add .github/workflows/ci.yml
git commit -m "ci(parity): api_parity job (goldenmatch MCP+CLI drift gate)"
```

---

## Task 8: Bootstrap + review the goldenmatch manifest, prove FAIL→PASS

The manifest needs both descriptors; the Python one is box-local but the TS one is CI-only, so bootstrap from the **first CI run** (the gate prints the `--init` YAML when the manifest is absent — Task 4 Step 5).

**Files:**
- Create: `parity/goldenmatch.yaml`

- [ ] **Step 1: Push the branch so CI runs.** With Tasks 1-7 committed and no `parity/goldenmatch.yaml` yet, push. The `api_parity` job runs the gate, finds no manifest, and **prints the bootstrap YAML to stderr + exits 1** (red, expected). (Follow the auth/PR steps in Task 9 to open the PR first, or push and read the job log.)

- [ ] **Step 2: Capture the printed bootstrap** from the CI job log (the full `shared`/`python_only`/`ts_only` partition for MCP tools + CLI commands).

- [ ] **Step 3: Human-review the gaps.** For each `python_only` and `ts_only` entry, decide: intentional (edge-safe scoping — keep, add a `# reason` comment) or an accidental drift/bug (note it for a follow-up ticket, but it still goes in `*_only` now so the gate can go green — the manifest records reality, and the review is what makes the gap *known*). Write the reviewed result to `parity/goldenmatch.yaml` with comments.

- [ ] **Step 4: Prove the gate has teeth (FAIL case).** Temporarily delete one real name from the manifest's `shared` (e.g. remove `find_duplicates` from `mcp_tools.shared`) and confirm CI's `api_parity` goes RED with **`unshared_common`** on that name (it's still in both PY and TS, just no longer declared shared — row 1). Then restore it.

- [ ] **Step 5: Confirm PASS.** With the reviewed manifest committed, CI `api_parity` is green: "parity OK: goldenmatch manifest exactly partitions the real MCP + CLI surface".

- [ ] **Step 6: Commit.**
```bash
git add parity/goldenmatch.yaml
git commit -m "feat(parity): reviewed goldenmatch parity manifest (MCP + CLI)"
```

---

## Task 9: PR + arm auto-merge

**Files:** none (git + gh).

- [ ] **Step 1: Auth + push** (benzsevern; merge-queue repo).
```bash
cd /d/show_case/gg-local-llm && unset GH_TOKEN && gh auth switch --user benzsevern
export GH_TOKEN=$(gh auth token --user benzsevern)
git push -u origin feat/api-parity-gate
```

- [ ] **Step 2: Open the PR** (`--base main`), body summarizing: the manifest + gate mechanism, scope (goldenmatch, MCP+CLI), the deferred A2A/subcommands/other-packages follow-ups, and that the TS `src/cli.ts` got a one-line export+guard to enable the emitter.

- [ ] **Step 3: Arm auto-merge and STOP** (merge queue → NO `--delete-branch`):
```bash
gh pr merge <N> --repo benseverndev-oss/goldenmatch --auto --squash
```
Do NOT poll CI (`feedback_dont_poll_ci_arm_automerge`).

---

## Graduation checklist

- [ ] `src/cli.ts` exports `program` + main-guards `parseAsync` (Task 1).
- [ ] `check_partition` + `check_structure` + `init_manifest` + `run_checks` implemented; `scripts/test_api_parity.py` green on the box (every §5 row + structural + `--init` + emitter smoke, MCP count asserted MEASURED).
- [ ] `emit_python_surface.py` (box) + `emit_ts_surface.mjs` (CI) emit matching-shape sorted JSON.
- [ ] `parity/goldenmatch.yaml` bootstrapped, human-reviewed (every `python_only`/`ts_only` justified), committed.
- [ ] `api_parity` CI job wired + path-filtered; provisions `goldenmatch[mcp]`+`pyyaml`, builds goldenmatch-js; demonstrated RED on an injected drift and GREEN once declared.
- [ ] A2A skills, CLI subcommands, and the other five packages captured as follow-ups (spec §9) — not attempted here.
