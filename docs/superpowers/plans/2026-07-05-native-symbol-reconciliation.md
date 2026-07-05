# Native-Symbol Reconciliation — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A box-safe CI gate that fails when a goldenmatch host call-site references a native-kernel symbol the kernel doesn't export — the #688 silent-fallback class.

**Architecture:** Pure source parse (no cargo build, no import). Parse `wrap_pyfunction!` registrations from the native crate → `registered`; scan Python source for kernel references across module-binding aliases (`native_module()`/`_ensure_native()` and locals bound to them) → `referenced`. FAIL on `referenced − registered`; REPORT `registered − referenced`.

**Tech Stack:** Python stdlib (`re`, `pathlib`), pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-07-05-native-symbol-reconciliation-design.md`

**Acceptance oracle (computed in spec review — the build must reproduce this):**
`check_native_symbols.py goldenmatch` → **missing (FAIL) = ∅** (exit 0), **unwired (REPORT) = {`build_clusters_native`, `connected_components_arrow`, `score_block_pairs`}** (three genuinely-dead / Rust-internal-only exports). If the real-repo run shows any MISSING, the scanner idiom is wrong — debug before shipping.

**Environment / SOP:**
- Branch `feat/native-symbol-check` (worktree `D:\show_case\gg-local-llm`), off `origin/main`.
- Box-safe: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe`. The script imports nothing from goldenmatch — it only reads files — so no PYTHONPATH shadowing is needed, but run from the repo root so relative paths resolve.
- benzsevern gh; merge-queue repo → `gh pr merge --auto --squash` (no `--delete-branch`). Arm auto-merge + STOP.
- Verify against this worktree, never the stale `D:\show_case\goldenmatch`.

---

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `scripts/check_native_symbols.py` | The gate: parse registrations, scan references (alias-resolving), reconcile | **Create** |
| `scripts/test_native_symbols.py` | Box-safe unit tests for the pure core | **Create** |
| `parity/native_symbols/goldenmatch.allow` | Allowlist (empty at bootstrap) | **Create** |
| `.github/workflows/ci.yml` | `native_symbols` CI job + paths-filter | Modify |

**Anchors (verified):** registrations `packages/rust/extensions/native/src/lib.rs:24-67` (40, one multi-line at :54-57); host source root `packages/python/goldenmatch/goldenmatch/` (tests live outside it at `packages/python/goldenmatch/tests/`); alias-binding sites `core/autoconfig.py:359`, `core/autoconfig_planner.py:119-126`, `core/suggest/adapter.py:87`, `embeddings/inhouse/model.py:132`, `backends/datafusion_backend.py:235` (`native_mod = _ensure_native()`). Dead exports: `build_clusters_native`, `connected_components_arrow`.

---

## Task 1: The gate script + unit tests + real-repo validation

**Files:**
- Create: `scripts/check_native_symbols.py`
- Create: `scripts/test_native_symbols.py`
- Create: `parity/native_symbols/goldenmatch.allow`

- [ ] **Step 1: Write the failing unit tests** (`scripts/test_native_symbols.py`)

```python
"""Unit tests for the native-symbol reconciliation gate. Pure data — no build,
no goldenmatch import. Run: python -m pytest scripts/test_native_symbols.py -q"""
import importlib.util, pathlib
_spec = importlib.util.spec_from_file_location(
    "check_native_symbols", pathlib.Path(__file__).parent / "check_native_symbols.py")
mod = importlib.util.module_from_spec(_spec)
import sys as _sys; _sys.modules[_spec.name] = mod   # Py3.13: @dataclass needs the module in sys.modules
_spec.loader.exec_module(mod)


def test_parse_registrations_extracts_final_segment():
    src = """
    m.add_function(wrap_pyfunction!(cluster::connected_components, m)?)?;
    m.add_function(wrap_pyfunction!(
        hash::record_fingerprint, m)?)?;   // multi-line
    // a commented mention of wrap_pyfunction! with no path is ignored
    """
    assert mod.parse_registrations_text(src) == {"connected_components", "record_fingerprint"}


def test_scan_refs_direct_form():
    src = 'from goldenmatch.core._native_loader import native_module\nx = native_module().quantile(a)\n'
    assert mod.scan_file_refs(src) == {"quantile"}


def test_scan_refs_resolves_alias_binding():
    # THE load-bearing case: kernel bound to a local, then used via that local.
    src = (
        "from goldenmatch.core._native_loader import native_module\n"
        "_nm = native_module()\n"
        "if hasattr(_nm, 'autoconfig_decide_plan'):\n"
        "    r = _nm.autoconfig_decide_plan(x)\n"
    )
    assert mod.scan_file_refs(src) == {"autoconfig_decide_plan"}


def test_scan_refs_resolves_ensure_native_alias_and_getattr():
    src = (
        "from goldenmatch.core._native_loader import _ensure_native\n"
        "native_mod = _ensure_native()\n"
        "fn = getattr(native_mod, 'jaro_winkler_similarity', None)\n"
    )
    assert mod.scan_file_refs(src) == {"jaro_winkler_similarity"}


def test_scan_refs_ignores_unrelated_local_named_like_alias_when_not_bound():
    # `nm` is NOT bound to native_module here -> its attribute access is not a ref.
    src = ("from goldenmatch.core._native_loader import native_module\n"
           "nm = something_else()\n"
           "nm.frobnicate()\n"
           "y = native_module().real_symbol(z)\n")
    assert mod.scan_file_refs(src) == {"real_symbol"}


def test_reconcile_missing_fails_unwired_reports():
    registered = {"a", "b", "dead"}
    referenced = {"a", "b", "ghost"}
    res = mod.reconcile(registered, referenced, allow=set())
    assert res.missing == {"ghost"}
    assert res.unwired == {"dead"}


def test_allowlist_subtracts_from_missing():
    res = mod.reconcile({"a"}, {"a", "ghost"}, allow={"ghost"})
    assert res.missing == set()
```

- [ ] **Step 2: Run — confirm FAIL** (`check_native_symbols` not yet written)

Run: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_native_symbols.py -q`
Expected: collection/import error (module missing) or all fail.

- [ ] **Step 3: Implement `scripts/check_native_symbols.py`**

```python
#!/usr/bin/env python3
"""Reconcile goldenmatch host references to its native kernel against the kernel's
registered exports. Box-safe: pure source parse — no cargo build, no import.

FAIL (exit 1) if a host reference has no matching kernel export (the #688
silent-fallback class); REPORT (non-fatal) exports no host references.
Run from the repo root: python scripts/check_native_symbols.py goldenmatch"""
from __future__ import annotations
import re, sys, pathlib
from dataclasses import dataclass

REGISTRY = {
    "goldenmatch": {
        "crate_reg": ["packages/rust/extensions/native/src/lib.rs"],
        "py_root": "packages/python/goldenmatch/goldenmatch",
        "loader_tokens": ("native_module", "_ensure_native"),
        "allow": "parity/native_symbols/goldenmatch.allow",
    },
}

# wrap_pyfunction!( <optional module:: paths> <symbol> , m )   -- \s spans newlines
_WRAP = re.compile(r"wrap_pyfunction!\(\s*(?:\w+::)+(\w+)")
# The (?!\s*\.) lookahead is REQUIRED: without it, a method-call chain like
# `pairs = native_module().score_block_pairs_arrow(...)` binds `pairs` as a false
# alias, and list/dict methods on the return value (.add/.append/...) become
# false-MISSING symbols. Only a BARE module-alias binding counts.
_BIND = re.compile(r"(\w+)\s*=\s*(?:native_module\(\)|_ensure_native\(\))(?!\s*\.)")


def parse_registrations_text(text: str) -> set[str]:
    return set(_WRAP.findall(text))


def parse_registrations(paths) -> set[str]:
    out: set[str] = set()
    for p in paths:
        out |= parse_registrations_text(pathlib.Path(p).read_text(encoding="utf-8"))
    return out


def scan_file_refs(text: str) -> set[str]:
    aliases = {r"native_module\(\)", r"_ensure_native\(\)"}
    aliases |= {re.escape(name) for name in _BIND.findall(text)}
    alt = "|".join(sorted(aliases))
    syms: set[str] = set()
    syms |= set(re.findall(rf"(?:{alt})\.(\w+)", text))
    syms |= set(re.findall(rf"getattr\(\s*(?:{alt})\s*,\s*[\"'](\w+)[\"']", text))
    syms |= set(re.findall(rf"hasattr\(\s*(?:{alt})\s*,\s*[\"'](\w+)[\"']", text))
    return syms


def scan_references(py_root: str, loader_tokens) -> set[str]:
    out: set[str] = set()
    for py in pathlib.Path(py_root).rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if not any(tok in text for tok in loader_tokens):
            continue
        out |= scan_file_refs(text)
    return out


def load_allow(path: str) -> set[str]:
    p = pathlib.Path(path)
    if not p.exists():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


@dataclass
class Result:
    missing: set
    unwired: set


def reconcile(registered: set[str], referenced: set[str], allow: set[str]) -> Result:
    return Result(missing=referenced - registered - allow,
                  unwired=registered - referenced)


def run(package: str) -> int:
    spec = REGISTRY.get(package)
    if spec is None:
        sys.stderr.write(f"no native-symbol registry entry for '{package}'\n")
        return 2
    registered = parse_registrations(spec["crate_reg"])
    referenced = scan_references(spec["py_root"], spec["loader_tokens"])
    if not referenced:
        sys.stderr.write(f"FAIL: scanned zero kernel references for {package} — "
                         f"the reference idiom is wrong (falsely-green guard)\n")
        return 1
    res = reconcile(registered, referenced, load_allow(spec["allow"]))
    print(f"{package}: {len(registered)} registered, {len(referenced)} referenced")
    if res.unwired:
        print("unwired (exported, no host reference — informational):")
        for s in sorted(res.unwired):
            print(f"  - {s}")
    if res.missing:
        sys.stderr.write("MISSING (host references a symbol the kernel does not "
                         "export — a silent-fallback / drift bug):\n")
        for s in sorted(res.missing):
            sys.stderr.write(f"  - {s}\n")
        return 1
    print("native-symbol reconciliation OK")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_native_symbols.py <package>")
    raise SystemExit(run(sys.argv[1]))
```
Note the `_WRAP` regex requires at least one `module::` prefix (`(?:\w+::)+`), so a bare commented `wrap_pyfunction!` token with no path never matches — covered by the unit test.

- [ ] **Step 4: Create the empty allowlist** `parity/native_symbols/goldenmatch.allow`:
```
# Native-symbol allowlist for goldenmatch. One symbol per line; `# reason` inline.
# A host reference whose symbol the static scanner can't attribute to a build
# (cross-kernel, or a deliberately-aspirational fallback with a tracked issue).
# EMPTY at bootstrap — the computed FAIL set is empty.
```

- [ ] **Step 5: Run the unit tests — confirm PASS**

Run: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_native_symbols.py -q`
Expected: all pass.

- [ ] **Step 6: Run the real gate against goldenmatch — MUST match the oracle**

Run: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe scripts/check_native_symbols.py goldenmatch; echo "exit=$?"`
Expected: `exit=0`; `unwired` lists exactly `build_clusters_native`, `connected_components_arrow`, and `score_block_pairs` (three dead / Rust-internal-only exports) and nothing else; no MISSING. **If missing is non-empty, the scanner idiom is wrong — debug (likely an alias binding or regex edge) before proceeding.**

- [ ] **Step 7: Commit**

```bash
git add scripts/check_native_symbols.py scripts/test_native_symbols.py parity/native_symbols/goldenmatch.allow
git commit -m "feat(native): static native-symbol reconciliation gate (goldenmatch)"
```

---

## Task 2: CI job

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add a `native_symbols` paths-filter entry** to the `changes` job's `dorny/paths-filter` (mirror the existing `api_parity` filter): watch `packages/rust/extensions/native/**`, `packages/python/goldenmatch/goldenmatch/**`, `scripts/check_native_symbols.py`, `parity/native_symbols/**`, and `.github/workflows/ci.yml`. Wire the output.

- [ ] **Step 2: Add the job** (box-safe — no cargo/maturin build; just Python stdlib):
```yaml
  native_symbols:
    needs: changes
    if: needs.changes.outputs.native_symbols == 'true'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.13" }
      - name: Reconcile native symbols (goldenmatch)
        run: python scripts/check_native_symbols.py goldenmatch
      - name: Gate unit tests
        run: python -m pytest scripts/test_native_symbols.py -q
```
(No pip install needed — the script + tests are stdlib-only. Confirm the repo's CI Python-setup convention and match it.)

- [ ] **Step 3: Demonstrate teeth (RED then GREEN).** Temporarily append a bogus reference to a goldenmatch source file already importing the loader (e.g. `# native_module().does_not_exist_xyz()` — but a comment won't scan; use a real-looking line inside a function guarded so it never runs, or just trust the unit test's `missing` coverage). Simplest: rely on the unit test `test_reconcile_missing_fails_unwired_reports` for the FAIL-path proof, and note in the PR that an injected `native_module().nonexistent` locally exits 1. Do NOT commit the injected line.

- [ ] **Step 4: Commit**
```bash
git add .github/workflows/ci.yml
git commit -m "ci: run native-symbol reconciliation gate for goldenmatch"
```

---

## Task 3: PR

- [ ] **Step 1: Push + PR + arm auto-merge (STOP)**
```bash
unset GH_TOKEN; gh auth switch --user benzsevern
git push -u origin feat/native-symbol-check
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create --repo benseverndev-oss/goldenmatch --base main \
  --title "Native-symbol reconciliation gate (goldenmatch, static tier)" --body-file <body>
GH_TOKEN=$(gh auth token --user benzsevern) gh pr merge --auto --squash --repo benseverndev-oss/goldenmatch  # NO --delete-branch
```
PR body: what it catches (#688 silent-fallback class), box-safe source parse, the computed clean bootstrap (FAIL ∅, unwired = the 2 dead exports `build_clusters_native`/`connected_components_arrow`), and that the per-package rollout + shipped-wheel tier are follow-ons. Do NOT poll CI.

---

## Notes for the implementer

- **The oracle is the acceptance test.** Step 6 must reproduce FAIL=∅, unwired={`build_clusters_native`, `connected_components_arrow`}. A mismatch means the alias resolution or regex is off — fix it, don't adjust the allowlist to force green.
- **Alias resolution is load-bearing** — the whole gate is worthless if it silently under-scans. The `_nm = native_module()` / `native_mod = _ensure_native()` fixtures guard it.
- **Box-safe** — the script reads files only; it never imports goldenmatch or builds Rust. Keep it that way (the shipped-wheel tier that *does* introspect the built module is a separate project).
- **Two known-unresolvable idioms** (`_COMPONENT_SYMBOLS` string dict in `_native_loader.py`; `getattr(mod, <computed>)` in `connectors/base.py`) — don't try to parse them; they only ever cause false-*unwired* (never false-missing), and today touch symbols referenced elsewhere anyway.
