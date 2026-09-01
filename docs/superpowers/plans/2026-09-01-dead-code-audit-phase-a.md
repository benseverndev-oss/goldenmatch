# Dead and Unused Surface — Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dead-code detector that cannot condemn dynamically-reached code, wire the CLI and MCP sweeps into the coverage union so the runtime signal is real, then delete module-level dead code and gate against regrowth.

**Architecture:** Liveness is computed, not inferred. The detector resolves the runtime registries (transforms, MCP tools, typer commands, entry points) into a live set by construction, then treats the remainder as candidates — and a candidate is only reported when BOTH a static signal (no importer, per the codemap graph) and a runtime signal (zero coverage across the pytest suite plus both sweeps) agree. Everything ships report-only first; deletions and the gate come after the report has run in CI.

**Tech Stack:** Python 3.12, pytest, `coverage`, `uv`, `ast`, the existing `docs/agent-codemap.json`, `ts-prune`, `cargo-machete`.

**Spec:** `docs/superpowers/specs/2026-09-01-dead-code-audit-design.md`

## Global Constraints

- Nothing is deleted on one signal: a candidate needs static non-reference AND zero coverage in the union.
- Published public API is out of scope for deletion. It is inventoried only.
- Untested is not unused. `mongo_backend`, `vertex_embedder`, `mongo`, `hubspot`, `bigquery` are live integrations that cannot run in CI and belong on the allowlist.
- Every allowlist entry carries a reason on the same line, matching `parity/native_symbols/*.allow`.
- Every detector test is verified by sabotage — revert the implementation, confirm the test fails — never by observing it pass.
- Commit messages carry no Claude attribution of any kind.
- Run Python via the repo venv: `D:/show_case/goldenmatch/.venv/Scripts/python.exe`, with `PYTHONPATH` set to the package dirs under `packages/python` (Windows `D:/` paths, not MSYS `/d/` paths).

## File Structure

| file | responsibility |
| --- | --- |
| `scripts/dead_code/__init__.py` | Package marker only |
| `scripts/dead_code/liveness.py` | Resolve registries into the live module set |
| `scripts/dead_code/static.py` | Module-level candidates from the codemap import graph |
| `scripts/dead_code/allowlist.py` | Parse `parity/dead_code.allow` |
| `scripts/dead_code/report.py` | Intersect signals; emit candidates and the public-export inventory |
| `parity/dead_code.allow` | Allowlist, one `module  # reason` per line |
| `scripts/test_dead_code_liveness.py` | Tests for registry resolution |
| `scripts/test_dead_code_report.py` | Tests for the intersection and the allowlist |
| `scripts/test_no_new_dead_code.py` | Regrowth ratchet (gating in Task 6) |
| `.github/workflows/ci.yml` | Sweep coverage + combine + report job |

---

### Task 1: Registry-resolved liveness

**Files:**
- Create: `scripts/dead_code/__init__.py`
- Create: `scripts/dead_code/liveness.py`
- Test: `scripts/test_dead_code_liveness.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `live_modules() -> set[str]` — dotted module names reachable through a runtime registry. Used by `report.py` in Task 4.

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_dead_code_liveness.py
"""Registry-resolved liveness.

The codebase has 1,089 `getattr(` call sites, so a static reference scan will
condemn code that is only ever reached dynamically. These tests pin the
inversion: enumerate what the registries can dispatch to, and treat THAT as
live regardless of what references exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dead_code.liveness import live_modules  # noqa: E402


def test_a_registered_transform_makes_its_module_live():
    live = live_modules()
    assert "goldenflow.transforms.names" in live


def test_a_typer_command_makes_its_module_live():
    live = live_modules()
    assert any(m.startswith("goldenmatch.cli.") for m in live)


def test_the_mcp_surface_is_live():
    live = live_modules()
    assert "goldenmatch.mcp.server" in live


def test_liveness_is_not_trivially_everything():
    """A live set that contains every module would make the detector vacuous --
    it would never report a candidate and would look like a clean bill of
    health."""
    live = live_modules()
    assert 10 < len(live) < 2000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest scripts/test_dead_code_liveness.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'dead_code'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/dead_code/liveness.py
"""Modules reachable through a runtime registry.

Liveness here is COMPUTED, not inferred from references. The registries are
resolved and everything they can dispatch to is live by construction, because a
static reference scan cannot see dynamic dispatch and this codebase has 1,089
`getattr(` sites.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent


def _transform_modules() -> set[str]:
    """Modules defining a transform in goldenflow's registry (113 at runtime)."""
    from goldenflow.transforms import list_transforms

    out: set[str] = set()
    for info in list_transforms():
        fn = getattr(info, "func", None)
        mod = getattr(fn, "__module__", None)
        if mod:
            out.add(mod)
    return out


def _cli_modules() -> set[str]:
    """Modules backing a registered typer command (36 commands).

    Walks the command tree the same way scripts/sweep_cli_polars_free.py does,
    so the two agree on what "registered" means.
    """
    import click
    import typer
    from goldenmatch.cli.main import app

    grp = typer.main.get_command(app)
    ctx = click.Context(grp)
    out: set[str] = set()

    def walk(g) -> None:
        for name in sorted(g.commands):
            cmd = g.get_command(ctx, name)
            if isinstance(cmd, click.Group):
                walk(cmd)
                continue
            mod = getattr(getattr(cmd, "callback", None), "__module__", None)
            if mod:
                out.add(mod)

    walk(grp)
    return out


def _mcp_modules() -> set[str]:
    """The MCP surface.

    Deliberately COARSE: `dispatch(name, args)` routes internally, so a tool
    name does not resolve to a handler module from outside. Marking the whole
    `goldenmatch.mcp` package live is the safe direction of error -- it can
    hide dead code inside that package, but it cannot delete a live tool. The
    precise version belongs to phase A2, which does symbol-level work.
    """
    import goldenmatch.mcp.server  # noqa: F401  -- import proves it loads

    out: set[str] = set()
    pkg = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch" / "mcp"
    for f in pkg.rglob("*.py"):
        rel = f.relative_to(pkg.parent).with_suffix("")
        out.add("goldenmatch." + ".".join(rel.parts))
    return out


def _entry_point_modules() -> set[str]:
    """Modules named by a console script in any package pyproject."""
    out: set[str] = set()
    for pyproject in (REPO / "packages" / "python").glob("*/pyproject.toml"):
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        scripts = data.get("project", {}).get("scripts", {}) or {}
        for target in scripts.values():
            out.add(target.split(":", 1)[0])
    return out


def live_modules() -> set[str]:
    """Union of every registry-reachable module."""
    live: set[str] = set()
    for resolve in (
        _transform_modules,
        _cli_modules,
        _mcp_modules,
        _entry_point_modules,
    ):
        live |= resolve()
    return live
```

Also create `scripts/dead_code/__init__.py` containing only:

```python
"""Dead-code detection for the phase A audit."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest scripts/test_dead_code_liveness.py -q`
Expected: PASS, 4 passed

- [ ] **Step 5: Sabotage-check the tests bite**

Temporarily change `live_modules` to `return set()`. Re-run. Expected: all four tests FAIL. Restore. A test that passes against an empty live set is measuring nothing.

- [ ] **Step 6: Commit**

```bash
git add scripts/dead_code/__init__.py scripts/dead_code/liveness.py scripts/test_dead_code_liveness.py
git commit -m "feat(dead-code): resolve registries into a live module set"
```

---

### Task 2: Static candidates from the codemap

**Files:**
- Create: `scripts/dead_code/static.py`
- Test: `scripts/test_dead_code_static.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `unimported_modules() -> set[str]` — modules that no other module imports, per `docs/agent-codemap.json`.

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_dead_code_static.py
"""Static candidacy from the codemap import graph.

`docs/agent-codemap.json` records `defines` and `imports` per module across six
packages and is regenerated in CI, so it is the cheapest accurate source for
module-level reachability. It does NOT record symbol-level references, which is
why this phase stops at modules.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dead_code.static import unimported_modules  # noqa: E402


def test_a_widely_imported_module_is_not_a_candidate():
    assert "goldenmatch.core.frame" not in unimported_modules()


def test_package_roots_are_never_candidates():
    """A package __init__ is the import target, so it has no importer by
    construction and would otherwise be a permanent false positive."""
    cands = unimported_modules()
    assert "goldenmatch" not in cands
    assert "goldenflow" not in cands


def test_the_candidate_set_is_a_minority_of_modules():
    """If most modules look unimported the graph is being read wrong, and the
    report would drown its reviewer in false positives."""
    cands = unimported_modules()
    assert 0 < len(cands) < 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest scripts/test_dead_code_static.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'dead_code.static'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/dead_code/static.py
"""Module-level static candidacy, from the codemap import graph."""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
CODEMAP = REPO / "docs" / "agent-codemap.json"


def _codemap() -> dict:
    return json.loads(CODEMAP.read_text(encoding="utf-8"))


def all_modules() -> set[str]:
    out: set[str] = set()
    for pkg in _codemap()["packages"].values():
        for m in pkg["modules"]:
            out.add(m["module"])
    return out


def imported_modules() -> set[str]:
    """Every module named as an import by any other module."""
    out: set[str] = set()
    for pkg in _codemap()["packages"].values():
        for m in pkg["modules"]:
            for imp in m.get("imports", []) or []:
                out.add(imp)
    return out


def unimported_modules() -> set[str]:
    """Modules nothing imports.

    Package roots are excluded: a package __init__ is what other code imports
    BY name, so it never appears in an import list of its own package and would
    be a permanent false positive.
    """
    roots = set(_codemap()["packages"])
    return {
        m
        for m in all_modules() - imported_modules()
        if m not in roots
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest scripts/test_dead_code_static.py -q`
Expected: PASS, 3 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/dead_code/static.py scripts/test_dead_code_static.py
git commit -m "feat(dead-code): module-level static candidacy from the codemap graph"
```

---

### Task 3: Allowlist

**Files:**
- Create: `scripts/dead_code/allowlist.py`
- Create: `parity/dead_code.allow`
- Test: `scripts/test_dead_code_allowlist.py`

**Interfaces:**
- Consumes: `dead_code.static.all_modules` from Task 2.
- Produces: `load_allowlist() -> set[str]`, and `stale_entries() -> set[str]` for the rot guard.

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_dead_code_allowlist.py
"""The allowlist, and the guard that stops it rotting.

An entry naming a module that no longer exists can never match, so it quietly
shrinks the audit while looking like documentation. That is the same failure as
a coverage floor on a deleted module.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dead_code.allowlist import load_allowlist, stale_entries  # noqa: E402


def test_entries_parse_and_strip_reasons():
    entries = load_allowlist()
    assert all("#" not in e for e in entries)
    assert all(e == e.strip() for e in entries)


def test_known_external_integrations_are_allowlisted():
    """These sit at 0% coverage because they need external services, not
    because they are dead. Deleting them removes working integrations."""
    entries = load_allowlist()
    for mod in (
        "goldenmatch.identity.mongo_backend",
        "goldenmatch.core.vertex_embedder",
        "goldenmatch.connectors.bigquery",
        "goldenmatch.connectors.hubspot",
    ):
        assert mod in entries, f"{mod} must be allowlisted with a reason"


def test_no_stale_entries():
    assert stale_entries() == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest scripts/test_dead_code_allowlist.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'dead_code.allowlist'`

- [ ] **Step 3: Write minimal implementation**

Create `parity/dead_code.allow`:

```
# Modules the dead-code audit must never report. One `module  # reason` per
# line, matching parity/native_symbols/*.allow.
#
# An entry here is a claim that the module IS live and the detector cannot see
# it -- not that it is dead and we would rather not deal with it.

goldenmatch.identity.mongo_backend  # live MongoDB identity backend; 0% coverage because CI has no MongoDB
goldenmatch.connectors.mongo  # live MongoDB connector; same reason
goldenmatch.core.vertex_embedder  # live Vertex AI embedder; needs GCP credentials CI does not carry
goldenmatch.connectors.bigquery  # live BigQuery connector; needs GCP credentials
goldenmatch.connectors.hubspot  # live HubSpot connector; needs a HubSpot API key
```

Create `scripts/dead_code/allowlist.py`:

```python
"""The dead-code allowlist and its rot guard."""
from __future__ import annotations

from pathlib import Path

from dead_code.static import all_modules

REPO = Path(__file__).resolve().parent.parent.parent
ALLOW = REPO / "parity" / "dead_code.allow"


def load_allowlist() -> set[str]:
    """Allowlisted module names, reasons stripped."""
    if not ALLOW.exists():
        return set()
    out: set[str] = set()
    for line in ALLOW.read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            out.add(entry)
    return out


def stale_entries() -> set[str]:
    """Allowlisted modules that no longer exist.

    A stale entry can never match, so it silently shrinks the audit while
    reading as documentation.
    """
    return load_allowlist() - all_modules()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest scripts/test_dead_code_allowlist.py -q`
Expected: PASS, 3 passed

- [ ] **Step 5: Sabotage-check the rot guard**

Append `goldenmatch.core.no_such_module  # deliberately fake` to `parity/dead_code.allow`. Re-run. Expected: `test_no_stale_entries` FAILS. Remove the line.

- [ ] **Step 6: Commit**

```bash
git add scripts/dead_code/allowlist.py parity/dead_code.allow scripts/test_dead_code_allowlist.py
git commit -m "feat(dead-code): allowlist with reasons and a stale-entry guard"
```

---

### Task 4: The report

**Files:**
- Create: `scripts/dead_code/report.py`
- Test: `scripts/test_dead_code_report.py`

**Interfaces:**
- Consumes: `live_modules()` (Task 1), `unimported_modules()`/`all_modules()` (Task 2), `load_allowlist()` (Task 3).
- Produces: `candidates(coverage_xml: Path | None) -> list[dict]` with keys `module`, `static`, `runtime`; and `public_export_inventory() -> list[str]`. CLI entry `python -m dead_code.report`.

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_dead_code_report.py
"""The intersection, and the guarantees that make it safe to act on.

These tests drive a SYNTHETIC coverage.xml. An earlier draft asserted over
`candidates(None)`, which returns [] by design -- so every assertion passed over
an empty set and tested nothing. That is the precise defect this whole plan
exists to prevent, and it nearly shipped inside the plan itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from dead_code.allowlist import load_allowlist  # noqa: E402
from dead_code.liveness import live_modules  # noqa: E402
from dead_code.report import candidates  # noqa: E402
from dead_code.static import unimported_modules  # noqa: E402


def _pick_real_candidate() -> str:
    """A module that IS statically unimported, live-free and un-allowlisted.

    Chosen dynamically rather than hardcoded: pinning a specific module name
    would break the day someone imports it, and the test would then be
    'fixed' by weakening it.
    """
    pool = sorted(unimported_modules() - live_modules() - load_allowlist())
    if not pool:
        pytest.skip("no unimported module available to build a fixture from")
    return pool[0]


def _coverage_xml(tmp_path: Path, uncovered: list[str], covered: list[str]) -> Path:
    """Minimal coverage.xml in the shape report._uncovered_modules parses."""
    lines = ['<?xml version="1.0" ?>', "<coverage><packages><package><classes>"]
    for mod in uncovered:
        lines.append(f'<class filename="{mod.replace(".", "/")}.py">')
        lines.append('<lines><line number="1" hits="0"/></lines></class>')
    for mod in covered:
        lines.append(f'<class filename="{mod.replace(".", "/")}.py">')
        lines.append('<lines><line number="1" hits="3"/></lines></class>')
    lines.append("</classes></package></packages></coverage>")
    p = tmp_path / "coverage.xml"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def test_a_module_failing_both_signals_is_reported(tmp_path):
    """The fixture must actually produce a candidate -- otherwise every
    assertion below is vacuous."""
    target = _pick_real_candidate()
    xml = _coverage_xml(tmp_path, uncovered=[target], covered=[])
    assert {c["module"] for c in candidates(xml)} == {target}


def test_a_registry_live_module_is_never_reported(tmp_path):
    """The whole point of the inversion. Feed the report a live module with
    zero coverage: it must still not be a candidate."""
    live = live_modules()
    if not live:
        pytest.fail("liveness returned nothing -- the fixture cannot mean anything")
    victim = sorted(live)[0]
    xml = _coverage_xml(tmp_path, uncovered=[victim], covered=[])
    assert victim not in {c["module"] for c in candidates(xml)}


def test_an_allowlisted_module_is_never_reported(tmp_path):
    allowed = sorted(load_allowlist())
    xml = _coverage_xml(tmp_path, uncovered=allowed, covered=[])
    assert not {c["module"] for c in candidates(xml)} & set(allowed)


def test_a_covered_module_is_never_reported(tmp_path):
    """Runtime execution alone is enough to clear a module, whatever the
    static signal says."""
    target = _pick_real_candidate()
    xml = _coverage_xml(tmp_path, uncovered=[], covered=[target])
    assert target not in {c["module"] for c in candidates(xml)}


def test_every_candidate_carries_its_evidence(tmp_path):
    target = _pick_real_candidate()
    xml = _coverage_xml(tmp_path, uncovered=[target], covered=[])
    for c in candidates(xml):
        assert c["static"] is True
        assert c["runtime"] is True


def test_without_coverage_runtime_evidence_is_absent_not_assumed():
    """With no coverage.xml the runtime signal is unknown, so NOTHING is a
    candidate. An unknown treated as proof is how live code gets deleted."""
    assert candidates(None) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest scripts/test_dead_code_report.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'dead_code.report'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/dead_code/report.py
"""Intersect the dead-code signals and report candidates with their evidence.

A module is a candidate only when the static signal AND the runtime signal
agree, it is not registry-live, and it is not allowlisted. With no coverage
file the runtime signal is None -- unknown -- and no module is a candidate.
"""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from dead_code.allowlist import load_allowlist
from dead_code.liveness import live_modules
from dead_code.static import all_modules, unimported_modules


def _uncovered_modules(coverage_xml: Path) -> set[str]:
    """Modules with zero covered lines in the combined coverage report."""
    root = ET.parse(coverage_xml).getroot()
    out: set[str] = set()
    for cls in root.iter("class"):
        filename = cls.get("filename") or ""
        hits = sum(
            1 for line in cls.iter("line") if int(line.get("hits", "0")) > 0
        )
        if hits == 0:
            mod = filename.replace("/", ".").replace("\\", ".")
            if mod.endswith(".py"):
                mod = mod[:-3]
            if mod.endswith(".__init__"):
                mod = mod[: -len(".__init__")]
            out.add(mod)
    return out


def candidates(coverage_xml: Path | None) -> list[dict]:
    live = live_modules()
    allowed = load_allowlist()
    static = unimported_modules() - live - allowed

    if coverage_xml is None:
        # Runtime evidence unknown. Report nothing: one signal is not proof.
        return []

    uncovered = _uncovered_modules(coverage_xml)
    return [
        {"module": m, "static": True, "runtime": True}
        for m in sorted(static & uncovered)
    ]


def public_export_inventory() -> list[str]:
    """Modules that are unimported internally but MAY be public API.

    Reported only. Deleting published public API is out of scope for phase A:
    api_parity spans six packages, so a public symbol is a cross-surface
    contract rather than one deletion.
    """
    live = live_modules()
    return sorted(
        m
        for m in unimported_modules() - live
        if m.count(".") == 1  # top-level package submodule: most likely public
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage-xml", type=Path, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    found = candidates(args.coverage_xml)
    inventory = public_export_inventory()

    if args.json:
        print(json.dumps({"candidates": found, "public_inventory": inventory}, indent=2))
        return 0

    print(f"{len(all_modules())} modules known, {len(found)} candidates\n")
    for c in found:
        print(f"  {c['module']}  (static: no importer, runtime: 0 covered lines)")
    if args.coverage_xml is None:
        print("  no --coverage-xml given: runtime signal unknown, reporting nothing")
    print(f"\npublic-export inventory (reported only): {len(inventory)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest scripts/test_dead_code_report.py -q`
Expected: PASS, 6 passed

- [ ] **Step 5: Sabotage-check the liveness exclusion**

In `candidates`, temporarily change `static = unimported_modules() - live - allowed` to `static = unimported_modules() - allowed`. Re-run. Expected: `test_a_registry_live_module_is_never_reported` FAILS. Restore.

- [ ] **Step 6: Commit**

```bash
git add scripts/dead_code/report.py scripts/test_dead_code_report.py
git commit -m "feat(dead-code): intersect signals into an evidence-carrying report"
```

---

### Task 4b: TypeScript and Rust candidates

The spec's A1 covers three languages, and "module-level" means a different unit
in each. Tasks 1-4 handle Python only; this task adds the other two.

**Files:**
- Create: `scripts/dead_code/other_langs.py`
- Modify: `scripts/dead_code/report.py` (add the two sections to `main`)
- Test: `scripts/test_dead_code_other_langs.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-4.
- Produces: `unused_rust_deps() -> list[str]`, `unwired_rust_exports() -> list[str]`, `unused_ts_exports() -> list[str]`. All three return `[]` when their tool is unavailable rather than raising.

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_dead_code_other_langs.py
"""TypeScript and Rust candidacy.

Each returns [] when its tool is missing rather than raising, so a machine
without cargo-machete reports "nothing found" instead of failing the run -- but
the CI job installs the tools, so [] there means genuinely none.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dead_code.other_langs import (  # noqa: E402
    unused_rust_deps,
    unused_ts_exports,
    unwired_rust_exports,
)


def test_all_three_return_lists_of_strings():
    for fn in (unused_rust_deps, unwired_rust_exports, unused_ts_exports):
        out = fn()
        assert isinstance(out, list)
        assert all(isinstance(x, str) for x in out)


def test_a_missing_tool_is_empty_not_an_exception(monkeypatch):
    monkeypatch.setenv("PATH", "")
    assert unused_rust_deps() == []
    assert unused_ts_exports() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest scripts/test_dead_code_other_langs.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'dead_code.other_langs'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/dead_code/other_langs.py
"""TypeScript and Rust dead-surface candidates.

Rust symbol removal is bounded to exports that check_native_symbols already
flags as unwired: cargo-machete reasons about DEPENDENCIES, not functions, so
Rust internals stay out of scope for phase A.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent


def _run(cmd: list[str], cwd: Path | None = None) -> str | None:
    """Run a tool, returning None when it is absent or fails."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd or REPO, capture_output=True, text=True, timeout=600
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout


def unused_rust_deps() -> list[str]:
    """Crate dependencies nothing uses, per cargo-machete."""
    out = _run(["cargo", "machete", "--with-metadata"])
    if not out:
        return []
    found: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        # cargo-machete lists offenders as indented bare crate names under a
        # per-manifest heading.
        if line.startswith("-") or line.startswith("*"):
            found.append(line.lstrip("-* ").strip())
    return sorted(set(found))


def unwired_rust_exports() -> list[str]:
    """Kernel exports with no host reference, per check_native_symbols."""
    found: list[str] = []
    for pkg in ("goldenmatch", "goldenflow", "goldencheck", "infermap", "goldenanalysis"):
        out = _run(["python", "scripts/check_native_symbols.py", pkg])
        if not out:
            continue
        in_block = False
        for line in out.splitlines():
            if line.startswith("unwired"):
                in_block = True
                continue
            if in_block:
                if line.startswith("  - "):
                    found.append(f"{pkg}:{line[4:].strip()}")
                else:
                    in_block = False
    return sorted(set(found))


def unused_ts_exports() -> list[str]:
    """Exported TypeScript symbols with no importer, per ts-prune."""
    ts_root = REPO / "packages" / "typescript" / "goldenmatch"
    if not ts_root.exists():
        return []
    out = _run(["pnpm", "exec", "ts-prune"], cwd=ts_root)
    if not out:
        return []
    return sorted(
        line.strip()
        for line in out.splitlines()
        if line.strip() and "(used in module)" not in line
    )
```

- [ ] **Step 4: Add the sections to the report**

In `scripts/dead_code/report.py`, inside `main()`, immediately before `return 0`:

```python
    from dead_code.other_langs import (
        unused_rust_deps,
        unused_ts_exports,
        unwired_rust_exports,
    )

    for label, items in (
        ("unused rust deps", unused_rust_deps()),
        ("unwired rust exports", unwired_rust_exports()),
        ("unused ts exports", unused_ts_exports()),
    ):
        print(f"\n{label}: {len(items)}")
        for item in items[:40]:
            print(f"  - {item}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest scripts/test_dead_code_other_langs.py -q`
Expected: PASS, 2 passed

- [ ] **Step 6: Commit**

```bash
git add scripts/dead_code/other_langs.py scripts/test_dead_code_other_langs.py scripts/dead_code/report.py
git commit -m "feat(dead-code): TypeScript and Rust candidates alongside the Python report"
```

---

### Task 5: Sweep coverage in the union (A0 complete)

**Files:**
- Modify: `.github/workflows/ci.yml:2725-2736` (the two sweep steps in `goldenmatch_nopolars`)
- Modify: `.github/workflows/ci.yml:623-630` (the combine step)

**Interfaces:**
- Consumes: nothing.
- Produces: `coverage_sweep_mcp.dat` and `coverage_sweep_cli.dat` artifacts, merged into the combined `coverage.xml` that Task 4's `--coverage-xml` reads.

- [ ] **Step 1: Add coverage to the two sweep steps**

In `.github/workflows/ci.yml`, change the MCP sweep step's `run:` to:

```yaml
        run: |
          COVERAGE_FILE=coverage_sweep_mcp.dat uv run --no-sync python -m pytest \
            scripts/test_mcp_polars_free_sweep.py -q --noconftest \
            --cov=goldenmatch --cov-report=
```

and the CLI sweep step's `run:` to:

```yaml
        run: |
          COVERAGE_FILE=coverage_sweep_cli.dat uv run --no-sync python -m pytest \
            scripts/test_cli_polars_free_sweep.py -q --noconftest \
            --cov=goldenmatch --cov-report=
```

- [ ] **Step 2: Upload the two new coverage files**

Add to the same job, after the sweep steps:

```yaml
      - name: Upload sweep coverage
        uses: actions/upload-artifact@v4
        with:
          name: gm-cov-sweeps
          path: coverage_sweep_*.dat
          if-no-files-found: error
```

`if-no-files-found: error` is deliberate: a silently absent artifact would make the combine quietly weaker, which is exactly the failure this phase exists to prevent.

- [ ] **Step 3: Include them in the combine**

In the `python_goldenmatch_coverage` job, change the combine command to:

```yaml
          uv run coverage combine --rcfile=packages/python/goldenmatch/pyproject.toml \
            coverage_shard1.dat coverage_shard2.dat coverage_shard3.dat \
            coverage_heavy_1.dat coverage_heavy_2.dat coverage_heavy_3.dat \
            coverage_sweep_mcp.dat coverage_sweep_cli.dat
```

The download step in that job already uses `pattern: gm-cov-*`, so `gm-cov-sweeps` is collected without further change. Verify that is still true before relying on it.

- [ ] **Step 4: Verify the workflow still parses**

Run: `python -m pytest scripts/test_workflow_yaml.py -q`
Expected: PASS. A YAML failure here means the required gate never reports at all.

- [ ] **Step 5: Commit and push, then confirm in CI**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: fold the CLI and MCP sweeps into the coverage union"
```

After CI runs, download the combined `coverage.xml` and confirm coverage rose for at least one module that only the sweeps reach. Record the module name in the PR description as evidence the union is real, not nominal.

---

### Task 6: Report-only CI job, then the ratchet

**Files:**
- Create: `scripts/test_no_new_dead_code.py`
- Modify: `.github/workflows/ci.yml` (new `dead_code` job)

**Interfaces:**
- Consumes: `candidates()` from Task 4.
- Produces: a CI job that reports candidates; the ratchet is flipped to gating only after A1's deletions.

- [ ] **Step 1: Write the ratchet, reporting-only at first**

```python
# scripts/test_no_new_dead_code.py
"""No NEW dead module may appear.

KNOWN_DEAD is a floor to work DOWN, never a bucket to top up -- the same
contract as KNOWN_POLARS_BOUND in scripts/test_cli_polars_free_sweep.py. It is
populated once from the first CI report, emptied by the A1 deletions, and then
this test gates at zero.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from dead_code.report import candidates  # noqa: E402

COVERAGE_XML = Path("packages/python/goldenmatch/coverage.xml")

# Populated in Task 7 from the first CI report; emptied by the A1 deletions.
KNOWN_DEAD: set[str] = set()


@pytest.mark.skipif(
    not COVERAGE_XML.exists(),
    reason="needs the combined coverage.xml; runs in the dead_code CI job",
)
def test_no_new_dead_modules():
    found = {c["module"] for c in candidates(COVERAGE_XML)}

    new = found - KNOWN_DEAD
    assert not new, (
        f"NEW dead module(s): {sorted(new)}. Either delete them, or add an "
        f"entry to parity/dead_code.allow explaining why the detector cannot "
        f"see that they are live."
    )

    fixed = KNOWN_DEAD - found
    assert not fixed, (
        f"{sorted(fixed)} are no longer dead -- remove them from KNOWN_DEAD so "
        f"the ratchet keeps its value."
    )
```

- [ ] **Step 2: Run it locally to confirm it skips without coverage**

Run: `python -m pytest scripts/test_no_new_dead_code.py -q`
Expected: 1 skipped, with the reason shown.

- [ ] **Step 3: Add the CI job**

Add to `.github/workflows/ci.yml`, after `python_goldenmatch_coverage`:

```yaml
  dead_code:
    needs: [python_goldenmatch_coverage]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-packages
      - uses: actions/download-artifact@v4
        with:
          pattern: gm-cov-*
          merge-multiple: true
      - name: Combine coverage for the dead-code report
        run: |
          uv run coverage combine --rcfile=packages/python/goldenmatch/pyproject.toml \
            coverage_shard1.dat coverage_shard2.dat coverage_shard3.dat \
            coverage_heavy_1.dat coverage_heavy_2.dat coverage_heavy_3.dat \
            coverage_sweep_mcp.dat coverage_sweep_cli.dat
          uv run coverage xml --rcfile=packages/python/goldenmatch/pyproject.toml \
            -o packages/python/goldenmatch/coverage.xml
      - name: Detector self-tests
        run: uv run pytest scripts/test_dead_code_liveness.py scripts/test_dead_code_static.py scripts/test_dead_code_allowlist.py scripts/test_dead_code_report.py scripts/test_dead_code_other_langs.py -q
      - name: Dead-code report
        env:
          PYTHONPATH: scripts
        run: uv run python -m dead_code.report --coverage-xml packages/python/goldenmatch/coverage.xml
      - name: Ratchet
        run: uv run pytest scripts/test_no_new_dead_code.py -q
```

The detector self-tests run BEFORE the report in the same job: a detector whose own tests fail must not be believed, and running them here means the report can never be read as authoritative while its classifier is broken.

- [ ] **Step 4: Verify the workflow parses**

Run: `python -m pytest scripts/test_workflow_yaml.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/test_no_new_dead_code.py .github/workflows/ci.yml
git commit -m "ci: report-only dead-code job with the detector's own tests"
```

---

### Task 7: A1 triage and deletion

**Files:**
- Modify: `scripts/test_no_new_dead_code.py` (`KNOWN_DEAD`)
- Modify: `parity/dead_code.allow`
- Delete: whichever modules triage confirms are dead

**Interfaces:**
- Consumes: the CI report from Task 6.
- Produces: an empty `KNOWN_DEAD` and a populated allowlist.

- [ ] **Step 1: Capture the first report**

Read the `Dead-code report` step output from the `dead_code` job on the Task 6 pull request. Copy the candidate list verbatim into `KNOWN_DEAD` in `scripts/test_no_new_dead_code.py` and commit, so the ratchet starts from a recorded floor rather than an unexamined zero.

```bash
git add scripts/test_no_new_dead_code.py
git commit -m "chore(dead-code): record the measured floor in KNOWN_DEAD"
```

- [ ] **Step 2: Triage each candidate against one question**

For each module, answer: *can any user action reach this?* Check in order:

```bash
rg -n "<module basename>" packages docs --glob '!_archive/**' | head -20
git log --oneline -5 -- <path to module>
```

- reachable only through a registry the detector missed → add to `parity/dead_code.allow` with the reason, and open an issue to teach `liveness.py` about that registry
- reachable only with external credentials → allowlist with the reason
- reachable from nothing → delete

- [ ] **Step 3: Delete in per-package pull requests**

One PR per package, smallest first. Each PR removes the module, its tests, and any now-unused imports, and must leave the full suite and every existing gate green. Small PRs are the point: a wrong deletion reverts cleanly.

- [ ] **Step 4: Shrink KNOWN_DEAD with each PR**

Every deletion PR removes its modules from `KNOWN_DEAD` in the same commit, so the recorded floor and reality never diverge.

- [ ] **Step 5: Confirm the inventory was produced**

Run the report with `--json` and confirm `public_inventory` is non-empty. Attach it to the phase A wrap-up issue: it is the input a later public-API effort needs, and it is the only deliverable of this phase that is not otherwise visible.

---

### Task 8: Flip the ratchet (A3)

**Files:**
- Modify: `scripts/test_no_new_dead_code.py`

- [ ] **Step 1: Confirm KNOWN_DEAD is empty**

Run: `rg -n "KNOWN_DEAD" scripts/test_no_new_dead_code.py`
Expected: `KNOWN_DEAD: set[str] = set()`

- [ ] **Step 2: Replace the comment with the closed-ratchet contract**

```python
# EMPTY, and that is the point. Every module-level candidate is either deleted
# or allowlisted with a reason. Do not add an entry to make a build green: an
# entry here is a decision to keep code nothing can reach. Add to
# parity/dead_code.allow instead, and only when the module IS live and the
# detector cannot see it.
KNOWN_DEAD: set[str] = set()
```

- [ ] **Step 3: Verify the gate bites**

Create a throwaway module `packages/python/goldenmatch/goldenmatch/core/_sabotage_dead.py` containing `def unused(): return 1`, regenerate the codemap with `python scripts/agent_codemap.py --write`, and re-run the `dead_code` job locally against the last downloaded `coverage.xml`. Expected: `test_no_new_dead_modules` FAILS naming that module. Delete the file and regenerate the codemap.

- [ ] **Step 4: Commit**

```bash
git add scripts/test_no_new_dead_code.py
git commit -m "ci: close the dead-code ratchet at zero"
```

---

## Done when

- The `dead_code` job runs in CI, with the detector's own tests passing ahead of the report.
- The combined `coverage.xml` includes both sweep runs, evidenced by a named module whose coverage rose.
- Every module-level candidate is deleted or allowlisted with a reason.
- `KNOWN_DEAD` is empty and the ratchet gates.
- The public-export inventory is attached to the wrap-up issue.
- The full suite and every pre-existing gate are green.
