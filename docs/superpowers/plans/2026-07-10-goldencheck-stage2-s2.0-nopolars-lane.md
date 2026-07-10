# GoldenCheck Stage-2 S2.0 (nopolars scaffold + advisory lane) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the goldencheck "nopolars" proof — a locally-provable import-survival test + a polars-absent test module + an advisory CI lane that physically uninstalls polars — mirroring goldenflow's 4f lane. No product code.

**Architecture:** A subprocess `sys.meta_path` blocker proves `import goldencheck` survives polars being unimportable (runs in the required suite). A `tests/nopolars/` module (skipif-when-present) is the real polars-absent proof, run by an advisory `goldencheck_nopolars` CI job after `pip uninstall polars`. No non-Polars backend, no covered scan (that's S2.1).

**Tech Stack:** Python 3.13, pytest, GitHub Actions (`.github/workflows/ci.yml`).

**Spec:** `docs/superpowers/specs/2026-07-10-goldencheck-stage2-s2.0-nopolars-lane-design.md`

---

## Conventions (this plan runs in the `gc-s2` worktree, off fresh origin/main)

Branch `feat/goldencheck-stage2-nopolars-lane`, worktree `D:\show_case\gc-s2`, off fresh `origin/main`. NOT stacked.

**Test preamble** (run every test command from `/d/show_case/gc-s2`):
```bash
export PYTHONPATH="D:/show_case/gc-s2/packages/python/goldencheck"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe
$PY -c "import goldencheck; print(goldencheck.__file__)"   # MUST be under gc-s2
```
Run tests: `$PY -m pytest packages/python/goldencheck/tests/<path> -v`. Ruff (100-char): `$PY -m ruff check <paths>`.

**INVARIANT:** no product-code change (tests + CI + docs only). The full goldencheck suite + import gate stay green. `tests/nopolars/` is a NEW test dir (this is a deliberate new-file addition, per the spec — it is a separate lane, not part of the sharded suite). Commit per task; do NOT push.

**Reference (mirror these, adapting to goldencheck / no covered scan):**
- goldenflow's polars-absent test: `packages/python/goldenflow/tests/nopolars/test_polars_absent.py`.
- goldenflow's CI lane: `.github/workflows/ci.yml` — `goldenflow_nopolars` (changes output ~L76; filter block ~L662-673; job ~L2778-2810; `force_all` output ~L55; `ci-required` needs-list ~L3942 does NOT include it).
- The existing import gate to extend: `packages/python/goldencheck/tests/test_import_no_polars.py` (subprocess style).
- goldencheck exports (assert these survive): `scan_dataframe`, `scan_file`, `read_file`, `functional_dependencies`, `Finding`, `Severity`.

---

## Task 1: The two test surfaces (import-blocker + nopolars module)

**Files:**
- Modify: `packages/python/goldencheck/tests/test_import_no_polars.py`
- Create: `packages/python/goldencheck/tests/nopolars/__init__.py`
- Create: `packages/python/goldencheck/tests/nopolars/test_polars_absent.py`

- [ ] **Step 1: Add the import-blocker test** to `tests/test_import_no_polars.py` (append after the existing `test_import_goldencheck_does_not_load_polars`, reusing its `PYTHONPATH`/env pattern):
```python
def test_goldencheck_survives_polars_unimportable():
    # Simulate the P4 base-deps flip WITHOUT uninstalling: a meta_path finder makes
    # `polars` unimportable, then `import goldencheck` must still succeed (lazy proxy
    # defers `import polars`), and touching the proxy must raise a clean ModuleNotFoundError.
    code = (
        "import sys, importlib.abc\n"
        "class _Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'polars' or name.startswith('polars.'):\n"
        "            raise ModuleNotFoundError(f'No module named {name!r}')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Block())\n"
        "import goldencheck\n"
        "assert 'polars' not in sys.modules, sorted(m for m in sys.modules if m.startswith('polars'))\n"
        "assert hasattr(goldencheck, 'scan_dataframe')\n"
        "from goldencheck._polars_lazy import pl\n"
        "try:\n"
        "    pl.DataFrame\n"
        "    raise AssertionError('expected ModuleNotFoundError touching the lazy proxy')\n"
        "except ModuleNotFoundError:\n"
        "    pass\n"
    )
    pkg_dir = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ)
    env["PYTHONPATH"] = pkg_dir + os.pathsep + env.get("PYTHONPATH", "")
    env["POLARS_SKIP_CPU_CHECK"] = "1"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
```

- [ ] **Step 2: Run → PASS** (proves import-survival with polars installed-but-blocked):
```bash
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v
```
Expected: 2 passed. If the new test FAILS, the subprocess stderr (in the assert message) shows why — most likely a real eager `pl.` access at import (a genuine P4 bug to surface, NOT a test to weaken). Report it.

- [ ] **Step 3: Create `tests/nopolars/__init__.py`** — empty file (package marker, mirrors goldenflow).

- [ ] **Step 4: Create `tests/nopolars/test_polars_absent.py`:**
```python
"""GoldenCheck Stage-2 S2.0: goldencheck works with **polars genuinely uninstalled**.

This module imports polars NOWHERE. It is the living proof for the Polars-eviction end
state (P4, where `polars` moves to the `[polars]` extra). Every other polars-free test in
the suite still `import polars` somewhere, so none of them can run in a polars-absent
interpreter; this one can.

It is `skipif`'d OUT of the normal suite (where polars IS present), so it is inert there
and only executes in the dedicated `goldencheck_nopolars` CI lane (and any local run where
polars is absent).

NOTE (S2.0): goldencheck has no non-Polars `Column`/`Frame` backend yet (that arrives with
S2.1), so this lane asserts import-survival + a clean decline on the uncovered tail ONLY --
NOT a covered scan. The covered-scan assertions land when S2.1 ships the backend.
"""
from __future__ import annotations

import importlib.util
import sys

import pytest

_HAS_POLARS = importlib.util.find_spec("polars") is not None

pytestmark = pytest.mark.skipif(
    _HAS_POLARS,
    reason="polars-absent proof -- only runs where polars is NOT installed (the S2.0 lane)",
)


def test_import_goldencheck_without_polars() -> None:
    import goldencheck  # must not raise, must not import polars

    assert "polars" not in sys.modules
    # the public entry points survive a polars-absent import
    for name in ("scan_dataframe", "scan_file", "read_file",
                 "functional_dependencies", "Finding", "Severity"):
        assert hasattr(goldencheck, name), name


def test_uncovered_path_raises_clean_error_without_polars() -> None:
    # Touching the lazy proxy fires the deferred `import polars`, which is absent here.
    from goldencheck._polars_lazy import pl

    with pytest.raises(ModuleNotFoundError):
        _ = pl.DataFrame
```

- [ ] **Step 5: Verify locally** — the module collects, is SKIPPED (polars present), and imports polars nowhere:
```bash
$PY -m pytest packages/python/goldencheck/tests/nopolars -v          # expect: 2 skipped
grep -nE "import polars|from polars|_polars_lazy" packages/python/goldencheck/tests/nopolars/test_polars_absent.py
# (only the `_polars_lazy` proxy import inside the uncovered-path test is allowed; NO `import polars`)
```
Expected: 2 skipped; grep shows only the `from goldencheck._polars_lazy import pl` line, no bare `import polars`. Ruff clean on both test files.

- [ ] **Step 6: Commit.**
```bash
cd /d/show_case/gc-s2
git add packages/python/goldencheck/tests/test_import_no_polars.py packages/python/goldencheck/tests/nopolars/__init__.py packages/python/goldencheck/tests/nopolars/test_polars_absent.py
git commit -m "test(goldencheck): S2.0 nopolars proof -- import-blocker + polars-absent module"
```

---

## Task 2: The advisory CI lane + roadmap doc + final verification

**Files:** Modify `.github/workflows/ci.yml`

- [ ] **Step 1: Read the goldenflow_nopolars wiring in `.github/workflows/ci.yml`** so you match this file's exact conventions + action SHA pins:
  - The `changes` job `outputs:` map (find the `goldenflow_nopolars: ${{ steps.filter.outputs.goldenflow_nopolars }}` line and the `force_all:` output).
  - The `filter:` step `filters:` block (find the `goldenflow_nopolars:` path list).
  - The `goldenflow_nopolars:` job body.
  - Confirm the `ci-required` (or equivalent required-status aggregation) job's `needs:` list — you must NOT add `goldencheck_nopolars` to it (advisory).

- [ ] **Step 2: Add the `changes`-job output.** In the `changes` job `outputs:` map, add (next to the goldenflow one):
```yaml
      goldencheck_nopolars: ${{ steps.filter.outputs.goldencheck_nopolars }}
```

- [ ] **Step 3: Add the filter block.** In the `filter:` step's `filters:`, add:
```yaml
            goldencheck_nopolars:
              # Stage-2 S2.0 (Polars eviction) proof lane: installs goldencheck with polars
              # UNINSTALLED and runs the polars-absent tests (tests/nopolars). Proves `import
              # goldencheck` survives + the uncovered tail raises cleanly with polars gone, so
              # the P4 base-deps flip is de-risked. Advisory (not ci-required). No covered scan
              # yet (goldencheck has no non-Polars backend until S2.1).
              - 'packages/python/goldencheck/goldencheck/**'
              - 'packages/python/goldencheck/tests/nopolars/**'
              - 'packages/python/goldencheck/pyproject.toml'
```

- [ ] **Step 4: Add the job.** Place it near the goldenflow_nopolars job. Use the EXACT `actions/checkout` + `astral-sh/setup-uv` SHA pins the surrounding ci.yml already uses (copy them from goldenflow_nopolars):
```yaml
  goldencheck_nopolars:
    needs: changes
    if: needs.changes.outputs.goldencheck_nopolars == 'true' || needs.changes.outputs.force_all == 'true'
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@<same-sha-as-goldenflow_nopolars>
      - uses: astral-sh/setup-uv@<same-sha-as-goldenflow_nopolars>
        with:
          enable-cache: true
          cache-dependency-glob: |
            uv.lock
            **/pyproject.toml
      - run: uv sync --all-packages
      - name: Uninstall polars (simulate the P4 base-deps flip)
        # goldencheck has no non-Polars backend yet, so this lane proves import-survival +
        # clean decline with polars gone (no covered scan until S2.1). No pyarrow removal.
        run: uv pip uninstall polars polars-runtime-32 polars-runtime-64 || true
      - name: Confirm polars is gone
        run: uv run --no-sync python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('polars') is None else 'polars still present')"
      - name: Polars-absent proof (tests/nopolars)
        # --no-sync so uv doesn't reinstall polars; --noconftest defensive parity carry-over
        # (goldencheck has no tests/conftest.py today).
        run: uv run --no-sync python -m pytest packages/python/goldencheck/tests/nopolars --noconftest -v
```
Do NOT include rust-toolchain / rust-cache / build_native steps (S2.0 has no covered scan). Do NOT add this job to any `ci-required`/required-status `needs:` list.

- [ ] **Step 5: Validate the YAML parses + still emits all jobs** (the `feedback_ci_yaml_startup_failure` trap — a malformed ci.yml yields 0 jobs and silently blocks the PR):
```bash
cd /d/show_case/gc-s2
PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe
$PY -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/ci.yml',encoding='utf-8')); j=d['jobs']; assert 'goldencheck_nopolars' in j, 'job missing'; assert 'goldenflow_nopolars' in j, 'sibling job vanished'; assert d['jobs']['changes']['outputs'].get('goldencheck_nopolars'), 'changes output missing'; print('ci.yml OK -', len(j), 'jobs')"
```
Expected: prints `ci.yml OK - <N> jobs` with N unchanged-plus-one vs origin/main. If it raises, the YAML is malformed — fix before committing.

- [ ] **Step 6: Full suite still green** (the test files are the only python change; confirm no collection breakage):
```bash
cd /d/show_case/gc-s2 && <preamble>
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py packages/python/goldencheck/tests/nopolars -v
$PY -m pytest packages/python/goldencheck/tests -q
```
Expected: import gate 2 passed; nopolars 2 skipped; full suite green (same pass count as fresh-main baseline + the 1 new import-blocker test).

- [ ] **Step 7: Commit.**
```bash
git add .github/workflows/ci.yml
git commit -m "ci(goldencheck): S2.0 advisory goldencheck_nopolars lane (polars-absent proof)"
```

---

## Done criteria (S2.0 complete)
- [ ] `tests/test_import_no_polars.py` gains the import-blocker test (2 passed in the required suite).
- [ ] `tests/nopolars/` exists (import + clean-decline assertions, no covered scan), skipped locally, imports polars nowhere.
- [ ] `ci.yml` has the `goldencheck_nopolars` changes-output + filter block + advisory job; the YAML parses + all jobs still emit; it is NOT in `ci-required`.
- [ ] Full suite green; `import goldencheck` loads zero Polars.
- [ ] The Stage-2 roadmap doc is committed (the spec itself records S2.0→S2.1→S2.2→reader→P4).
- [ ] No scope creep: no non-Polars backend, no covered scan, no native build in the lane, no deps-flip. S2.0 is the nopolars proof only; S2.1 (the covered backend) is next.
