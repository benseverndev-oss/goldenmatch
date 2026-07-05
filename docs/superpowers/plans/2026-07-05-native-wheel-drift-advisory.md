# Native Published-Wheel Drift Advisory — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An advisory CI job that warns when the published `goldenmatch-native` wheel no longer exports a native symbol the current host source depends on (the #688 republish-lag).

**Architecture:** Reuse Project 1's `scan_references` (host reference set) from `scripts/check_native_symbols.py` (on main). Introspect the *installed published* wheel via `dir(goldenmatch_native._native)`. `lagging = referenced − shipped − allow`; warn (exit 0) on lag, fail loud (exit 2) if the host scan is empty or the wheel can't be introspected. Not wired into per-PR CI (chicken-egg) — a weekly scheduled + dispatchable workflow.

**Tech Stack:** Python stdlib, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-07-05-native-wheel-drift-advisory-design.md`

**Reused signatures (verified, `scripts/check_native_symbols.py`):** `scan_references(py_root: str, loader_tokens) -> set[str]` (:48); `load_allow(path: str) -> set[str]` (:58); `REGISTRY["goldenmatch"]` = `{crate_reg, py_root: "packages/python/goldenmatch/goldenmatch", loader_tokens: ("native_module","_ensure_native"), allow: "parity/native_symbols/goldenmatch.allow"}`.

**Environment / SOP:**
- Branch `feat/native-wheel-drift` (worktree `D:\show_case\gg-local-llm`), off `origin/main` (has #1459).
- Unit tests box-safe (stub module — no real wheel/build): `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_native_wheel.py -q`. The real published-wheel introspection is CI-only.
- benzsevern gh; merge-queue → `gh pr merge --auto --squash` (no `--delete-branch`); arm auto-merge + STOP.

---

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `scripts/check_native_wheel.py` | The advisory: reuse scanner + introspect published wheel + reconcile | **Create** |
| `scripts/test_native_wheel.py` | Box-safe unit tests (stub module) | **Create** |
| `.github/workflows/native-wheel-drift.yml` | Weekly + dispatch advisory workflow | **Create** |

---

## Task 1: The advisory script + box-safe unit tests

**Files:** `scripts/check_native_wheel.py`, `scripts/test_native_wheel.py`

- [ ] **Step 1: Write the failing unit tests** (`scripts/test_native_wheel.py`)

```python
"""Unit tests for the native published-wheel drift advisory. Box-safe: uses a stub
module, no real wheel/build. Run: python -m pytest scripts/test_native_wheel.py -q"""
import importlib.util, pathlib, sys, types
_spec = importlib.util.spec_from_file_location(
    "check_native_wheel", pathlib.Path(__file__).parent / "check_native_wheel.py")
mod = importlib.util.module_from_spec(_spec); sys.modules[_spec.name] = mod
_spec.loader.exec_module(mod)


def test_public_callables_filters():
    stub = types.SimpleNamespace(
        connected_components=lambda: 0,
        ExcludeSet=type("ExcludeSet", (), {}),   # a class is a public callable export
        _private=lambda: 0,
        __version__="1",
        DATA=42,                                  # non-callable
    )
    assert mod._public_callables(stub) == {"connected_components", "ExcludeSet"}


def test_lag_computation():
    assert mod.lag({"a", "b"}, {"a"}, set()) == {"b"}
    assert mod.lag({"a", "b"}, {"a"}, {"b"}) == set()          # allow subtracts
    assert mod.lag({"a"}, {"a", "extra"}, set()) == set()      # wheel exporting more is fine


def test_run_fails_loud_on_zero_references(monkeypatch):
    monkeypatch.setattr(mod._ns, "scan_references", lambda *a, **k: set())
    assert mod.run("goldenmatch") == 2   # zero refs => fail loud, not falsely green


def test_run_fails_loud_when_wheel_cannot_be_introspected(monkeypatch):
    monkeypatch.setattr(mod._ns, "scan_references", lambda *a, **k: {"connected_components"})
    def boom(_name): raise ModuleNotFoundError("no goldenmatch_native")
    monkeypatch.setattr(mod, "wheel_exports", boom)
    assert mod.run("goldenmatch") == 2   # can't introspect => fail loud


def test_run_warns_but_exits_zero_on_lag(monkeypatch, capsys):
    monkeypatch.setattr(mod._ns, "scan_references", lambda *a, **k: {"old_sym", "new_sym"})
    monkeypatch.setattr(mod, "wheel_exports", lambda _n: {"old_sym"})   # wheel lacks new_sym
    monkeypatch.setattr(mod._ns, "load_allow", lambda _p: set())
    rc = mod.run("goldenmatch")
    out = capsys.readouterr().out
    assert rc == 0                       # advisory: warn, don't fail
    assert "new_sym" in out and "republish" in out.lower()


def test_run_clean_when_wheel_covers_all(monkeypatch, capsys):
    monkeypatch.setattr(mod._ns, "scan_references", lambda *a, **k: {"a", "b"})
    monkeypatch.setattr(mod, "wheel_exports", lambda _n: {"a", "b", "c"})
    monkeypatch.setattr(mod._ns, "load_allow", lambda _p: set())
    assert mod.run("goldenmatch") == 0
    assert "up to date" in capsys.readouterr().out
```

- [ ] **Step 2: Run — confirm FAIL** (module missing).

Run: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_native_wheel.py -q`

- [ ] **Step 3: Implement `scripts/check_native_wheel.py`**

```python
#!/usr/bin/env python3
"""Advisory: does the PUBLISHED goldenmatch-native wheel still export every native
symbol the current host source depends on? Warns (does not gate) on republish lag —
the #688 class. Reuses Project 1's host-reference scanner.
Run (needs the published wheel installed): python scripts/check_native_wheel.py goldenmatch"""
from __future__ import annotations
import importlib, importlib.util, pathlib, sys

# Reuse Project 1's scanner (it's a script, not a package) by path-import.
_p1 = importlib.util.spec_from_file_location(
    "check_native_symbols", pathlib.Path(__file__).parent / "check_native_symbols.py")
_ns = importlib.util.module_from_spec(_p1); sys.modules[_p1.name] = _ns
_p1.loader.exec_module(_ns)

# Which installed wheel module to introspect, per package.
_WHEEL_MODULE = {"goldenmatch": "goldenmatch_native._native"}


def _public_callables(module) -> set[str]:
    """Python-visible callable exports (the runtime-registered names). Keeps classes
    like ExcludeSet; drops dunders/private and non-callables."""
    return {n for n in dir(module)
            if not n.startswith("_") and callable(getattr(module, n))}


def wheel_exports(module_name: str) -> set[str]:
    return _public_callables(importlib.import_module(module_name))


def lag(referenced: set[str], shipped: set[str], allow: set[str]) -> set[str]:
    return referenced - shipped - allow


def run(package: str, module_name: str | None = None) -> int:
    spec = _ns.REGISTRY.get(package)
    if spec is None:
        sys.stderr.write(f"no registry entry for '{package}'\n")
        return 2
    referenced = _ns.scan_references(spec["py_root"], spec["loader_tokens"])
    if not referenced:  # falsely-green guard (mirrors check_native_symbols)
        sys.stderr.write(f"FAIL: scanned zero host references for {package} — "
                         f"the reference idiom is wrong\n")
        return 2
    module_name = module_name or _WHEEL_MODULE.get(package)
    if module_name is None:
        sys.stderr.write(f"no wheel module known for '{package}'\n")
        return 2
    try:
        shipped = wheel_exports(module_name)
    except Exception as e:  # noqa: BLE001 - can't introspect => fail loud, never falsely green
        sys.stderr.write(f"FAIL: could not import/introspect published wheel "
                         f"'{module_name}': {e!r}\n")
        return 2
    lagging = lag(referenced, shipped, _ns.load_allow(spec["allow"]))
    if lagging:
        print(f"::warning::published {module_name} lags current source — "
              f"republish goldenmatch-native")
        for s in sorted(lagging):
            print(f"::warning::  host references '{s}' but the published wheel "
                  f"does not export it")
        return 0  # ADVISORY: warn, do not fail the job
    print(f"{package}: published wheel exports all {len(referenced)} "
          f"host-referenced symbols — up to date")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv:
        raise SystemExit("usage: check_native_wheel.py <package> [module_name]")
    raise SystemExit(run(argv[0], argv[1] if len(argv) > 1 else None))
```

- [ ] **Step 4: Run unit tests — confirm ALL pass.**

- [ ] **Step 5: Commit**
```bash
git add scripts/check_native_wheel.py scripts/test_native_wheel.py
git commit -m "feat(native): published-wheel drift advisory (reuses Project 1 scanner)"
```

---

## Task 2: The scheduled advisory workflow

**Files:** `.github/workflows/native-wheel-drift.yml`

- [ ] **Step 1: Create the workflow.** Reuse the pinned `actions/checkout` + `actions/setup-python` SHAs already used in `.github/workflows/ci.yml` (grep them; do not invent versions).

```yaml
name: Native wheel drift advisory
# Advisory only (NOT a per-PR gate): warns when the PUBLISHED goldenmatch-native
# wheel lags current-source host references (the #688 republish-lag). Remediation:
# republish goldenmatch-native. A source PR is never blocked by this.
on:
  schedule:
    - cron: "0 9 * * 1"   # Mondays 09:00 UTC
  workflow_dispatch:
jobs:
  drift:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@<pinned-v6-sha>
      - uses: actions/setup-python@<pinned-sha>
        with:
          python-version: "3.13"
      - name: Install the PUBLISHED goldenmatch-native wheel
        run: pip install goldenmatch-native
      - name: Check published-wheel drift (goldenmatch)
        run: python scripts/check_native_wheel.py goldenmatch
```
Notes:
- No `pip install pytest`/`pyyaml` needed — the script + its reused scanner are stdlib-only, and it reads goldenmatch *source* (checked out) rather than importing goldenmatch.
- If `pip install goldenmatch-native` can't resolve an importable wheel on the runner, `wheel_exports` raises → the script exits 2 → the job fails LOUD (correct: a silent skip would be falsely reassuring).

- [ ] **Step 2: Validate the YAML parses** (`yaml.safe_load`).

- [ ] **Step 3: Commit**
```bash
git add .github/workflows/native-wheel-drift.yml
git commit -m "ci: weekly native published-wheel drift advisory"
```

---

## Task 3: Docs note + PR

- [ ] **Step 1: Add a one-line note** to the goldenmatch-native section of the root `CLAUDE.md` (near the #688 / republish lessons): the drift advisory exists; when it warns, republish `goldenmatch-native`. Reinforces `feedback_verify_perf_not_just_ship`.

- [ ] **Step 2: Push + PR + arm auto-merge (STOP).** PR body: the one non-redundant #688 catcher (published-wheel vs current-source host refs), advisory-not-gate (chicken-egg + graceful degradation), reuses Project 1's scanner, fail-loud on empty-refs / can't-introspect, weekly + dispatch. Note the per-package + other-native-wheels rollout is a follow-on (the `_WHEEL_MODULE`/REGISTRY structure is ready).
```bash
unset GH_TOKEN; gh auth switch --user benzsevern
git push -u origin feat/native-wheel-drift
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create --repo benseverndev-oss/goldenmatch --base main --title "Native published-wheel drift advisory" --body-file <body>
GH_TOKEN=$(gh auth token --user benzsevern) gh pr merge --auto --squash --repo benseverndev-oss/goldenmatch  # NO --delete-branch
```

---

## Notes for the implementer

- **Reuse, don't reimplement** — `scan_references`/`load_allow`/`REGISTRY` come from `check_native_symbols.py` by path-import. The host-reference definition must stay single-sourced with Project 1.
- **Fail loud, never falsely green** — empty host refs AND wheel-introspection failure both exit 2. Only a real "wheel covers everything" or "lag warned" exits 0.
- **Advisory posture** — lag is `::warning::` + exit 0; it must not block. The chicken-egg (a PR adding a symbol can't pre-republish the wheel) is why.
- **Box-safe tests use a stub module** — no real wheel needed locally; the real introspection is CI-only.
