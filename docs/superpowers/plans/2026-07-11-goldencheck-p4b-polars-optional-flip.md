# GoldenCheck P4b (polars-optional deps-flip, 2.0.0) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Move `polars` from a base dependency to a `[polars]` optional extra (breaking, major 2.0.0). Parquet/Excel reading + `scan_columns`/`scan_file_columns` work polars-free; CSV + `scan_dataframe`/`scan_file` cleanly require `goldencheck[polars]`; a REQUIRED nopolars CI lane enforces it.

**Architecture:** All polars access already routes through the `_polars_lazy` proxy (+ 2 kernels.py exceptions to fix), so one wrapped-import change makes every polars-required path decline with a helpful `ImportError`. The flip is import-safe (annotations stringized, zero module-level `pl.`). Publishing 2.0.0 is NOT in this PR (human-gated).

**Tech Stack:** Python packaging (pyproject extras), pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-07-11-goldencheck-p4b-polars-optional-flip-design.md`

---

## PRE-FLIGHT (base must have P4a's read_columns)

This branch is cut from the P4a tip, so P4a's `read_columns`/`scan_file_columns` are present. Confirm:
```bash
cd /d/show_case/gc-p4b
grep -q "def read_columns" packages/python/goldencheck/goldencheck/engine/reader.py && grep -q "def scan_file_columns" packages/python/goldencheck/goldencheck/engine/scanner.py && echo "P4a present -- proceed" || echo "P4a MISSING -- wrong base, STOP"
```
**Rebase-later:** P4a (#1647) is in the merge queue. When it merges, rebase this branch onto fresh main (`git fetch origin main -q && git rebase --onto origin/main <P4a-tip> feat/goldencheck-p4b-deps-flip`) before the PR. Until then, develop atop P4a.

## Conventions (worktree `gc-p4b`, branch `feat/goldencheck-p4b-deps-flip`)

**Python test preamble** (from `/d/show_case/gc-p4b`):
```bash
export PYTHONPATH="D:/show_case/gc-p4b/packages/python/goldencheck"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENCHECK_NATIVE=auto
PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe
$PY -c "import goldencheck; print(goldencheck.__file__)"   # under gc-p4b
```
The dev venv HAS polars, so the full suite is unaffected by the flip; only the dedicated nopolars lane exercises the absent path. Ruff 100-char. Build native if needed (S2.2 steps) for the parity-covered tests.

**INVARIANTS:**
- `import goldencheck` loads zero polars (unchanged). With `[polars]` present, everything is byte-identical (no behavior change). Existing tests pass UNEDITED.
- Polars-absent: `import goldencheck` + `scan_columns` + `read_columns`(parquet/excel) + `scan_file_columns`(parquet/excel) WORK; CSV read + `scan_dataframe`/`scan_file` raise a helpful `ImportError` naming `goldencheck[polars]`.
- Commit per task; do NOT push. Do NOT publish a PyPI release.

---

## Task 1: lazy-proxy clean decline + route the 2 kernels.py bypasses

**Files:** Modify `goldencheck/_polars_lazy.py`, `goldencheck/core/kernels.py`; Test `tests/test_polars_decline.py` (new).

- [ ] **Step 1: Write a failing unit test** `tests/test_polars_decline.py` (proves the helpful message without uninstalling polars — a subprocess blocks polars then triggers `pl` access):
```python
import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_lazy_proxy_declines_with_helpful_message():
    code = textwrap.dedent("""
        import sys, importlib.abc
        class _B(importlib.abc.MetaPathFinder):
            def find_spec(self, n, path=None, target=None):
                if n == 'polars' or n.startswith('polars.'):
                    raise ModuleNotFoundError(n)
                return None
        sys.meta_path.insert(0, _B())
        from goldencheck._polars_lazy import pl
        try:
            pl.DataFrame  # triggers the deferred import
            raise SystemExit('expected ImportError')
        except ImportError as e:
            assert 'goldencheck[polars]' in str(e), str(e)
    """)
    pkg = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = pkg + os.pathsep + env.get("PYTHONPATH", "")
    env["POLARS_SKIP_CPU_CHECK"] = "1"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
```
Run → FAIL (current proxy raises raw ModuleNotFoundError, no `goldencheck[polars]`).

- [ ] **Step 2: Wrap the import in `_polars_lazy.py`.** READ it; change `_LazyPolars.__getattr__` so the deferred `import polars` is wrapped:
```python
    def __getattr__(self, name):
        mod = self._mod
        if mod is None:
            try:
                import polars as _polars
            except ImportError as e:
                raise ImportError(
                    "This GoldenCheck operation needs Polars, which isn't installed. "
                    "Install it with `pip install goldencheck[polars]`. (Parquet/Excel "
                    "reading via read_columns() and the scan_columns() structural checks "
                    "work without Polars.)"
                ) from e
            self._mod = mod = _polars
        return getattr(mod, name)
```
(Keep the class/`__slots__`/`pl = _LazyPolars()` otherwise identical. Byte-identical when polars present.)

- [ ] **Step 3: Route the 2 `core/kernels.py` bypasses.** READ `core/kernels.py` around lines 125 + 251 — both do a function-local `import polars as pl`. Replace each with `from goldencheck._polars_lazy import pl` (so the native-absent FD / composite-key fallback declines with the helpful message instead of a raw ModuleNotFoundError). Confirm no behavior change when polars present (same `pl`).

- [ ] **Step 4: Run → PASS** + import gate + a quick kernels sanity:
```bash
$PY -m pytest packages/python/goldencheck/tests/test_polars_decline.py packages/python/goldencheck/tests/test_import_no_polars.py -v
$PY -m pytest packages/python/goldencheck/tests -k "kernels or native_parity" -v   # existing, UNEDITED
```
Ruff clean on the 3 files.

- [ ] **Step 5: Commit.**
```bash
cd /d/show_case/gc-p4b
git add packages/python/goldencheck/goldencheck/_polars_lazy.py packages/python/goldencheck/goldencheck/core/kernels.py packages/python/goldencheck/tests/test_polars_decline.py
git commit -m "feat(goldencheck): P4b helpful polars-absent decline (lazy proxy + kernels.py fallbacks)"
```

---

## Task 2: the deps-flip + version 2.0.0 + CHANGELOG

**Files:** Modify `packages/python/goldencheck/pyproject.toml`, `goldencheck/__init__.py`, `CHANGELOG.md`.

- [ ] **Step 1: Flip `pyproject.toml`.** Remove `"polars>=1.0"` from base `dependencies`; add to `[project.optional-dependencies]`:
```toml
polars = ["polars>=1.0"]
```
(Place near `parquet`. Leave all other deps/extras unchanged; add a comment in base deps: `# polars moved to the [polars] extra in 2.0.0`.) Set `version = "2.0.0"`.

- [ ] **Step 2: Bump `__version__`** in `goldencheck/__init__.py` (line ~4) to `"2.0.0"` — there is a REQUIRED `version_consistency` job (`scripts/check_version_consistency.py`) that checks pyproject == `__init__` == (per its rules) CHANGELOG.

- [ ] **Step 3: CHANGELOG 2.0.0 section.** READ `CHANGELOG.md`; convert the `[Unreleased]` section (which has the S2/scan_columns entries) into `## [2.0.0] - 2026-07-11` (use the date the plan runs; ASCII only), and PREPEND a breaking-change bullet:
```markdown
## [2.0.0] - 2026-07-11

### Changed (BREAKING)
- **`polars` is no longer a base dependency** — it moved to the `[polars]` optional
  extra. `pip install goldencheck` no longer pulls Polars (~185 MB). Parquet/Excel
  reading (`read_columns`) and the structural scan (`scan_columns`/`scan_file_columns`)
  run without Polars. **CSV reading and the full scan (`scan_dataframe`/`scan_file`)
  still require Polars** — install `goldencheck[polars]` for them (Polars' CSV dtype
  inference isn't reproducible, and the full scan is Polars-native). Upgrading users
  who scan CSVs or use `scan_file`/`scan_dataframe` must add `[polars]`.

### Added
- `read_columns(path)` / `scan_file_columns(path)` — polars-free Parquet (pyarrow,
  new `[parquet]` extra) + Excel (openpyxl) read + covered structural scan.
- (… the existing [Unreleased] scan_columns + native regex/date entries, folded in …)
```
Keep the existing `[Unreleased]` Added bullets (scan_columns, native components, denial-constraints, kernels) under 2.0.0's `### Added`. Verify no other version-bearing file (audit: `grep -rn "1.4.1" packages/python/goldencheck --include=*.toml --include=*.py --include=*.cfg | grep -v tests`).

- [ ] **Step 4: Verify version-consistency locally:**
```bash
$PY scripts/check_version_consistency.py 2>&1 | tail -5 || echo "(script path/behavior — inspect if it errors)"
$PY -c "import goldencheck; print(goldencheck.__version__)"   # 2.0.0
$PY -m pytest packages/python/goldencheck/tests -k "version or public_api" -v   # existing, adjust only if a test pins 1.4.1 (then it's an intentional version-file update, not a behavior change)
```
If a test asserts the old version string, update that literal (it's a version-file update, not a behavior regression) and note it. Ruff clean.

- [ ] **Step 5: Commit.**
```bash
git add packages/python/goldencheck/pyproject.toml packages/python/goldencheck/goldencheck/__init__.py packages/python/goldencheck/CHANGELOG.md
git commit -m "feat(goldencheck)!: P4b flip polars to [polars] extra + bump to 2.0.0 (BREAKING)"
```

---

## Task 3: nopolars-REQUIRED CI lane + expanded polars-absent assertions

**Files:** Modify `tests/nopolars/test_polars_absent.py`, `.github/workflows/ci.yml` (+ `.github/filters.yml` if needed).

- [ ] **Step 1: Expand `tests/nopolars/test_polars_absent.py`** (runs in the polars-uninstalled lane; pyarrow+openpyxl+native ARE installed there). Append tests that build tiny fixtures and assert the polars-free surface + declines:
```python
def test_read_columns_parquet_excel_polars_free(tmp_path) -> None:
    import pyarrow as pa, pyarrow.parquet as pq
    from openpyxl import Workbook
    from goldencheck import read_columns, scan_file_columns
    pqp = tmp_path / "f.parquet"
    pq.write_table(pa.table({"id": [1, 2, 3], "grade": ["A", "B", "A"]}), pqp)
    assert read_columns(pqp) == {"id": [1, 2, 3], "grade": ["A", "B", "A"]}
    assert isinstance(scan_file_columns(pqp), list)
    wb = Workbook(); ws = wb.active; ws.append(["a", "b"]); ws.append([1, "x"]); xp = tmp_path / "f.xlsx"; wb.save(xp)
    assert read_columns(xp)
    assert "polars" not in sys.modules


def test_csv_and_full_scan_decline_without_polars(tmp_path) -> None:
    import pytest
    from goldencheck import read_columns
    csv = tmp_path / "c.csv"; csv.write_text("a\n1\n", encoding="utf-8")
    with pytest.raises(ImportError, match=r"goldencheck\[polars\]"):
        read_columns(csv)
    # scan_dataframe touches pl at call time -> same decline
    import goldencheck
    with pytest.raises(ImportError, match=r"goldencheck\[polars\]"):
        goldencheck.scan_dataframe(object())  # any call that reaches pl. -> helpful ImportError
```
(Adjust the `scan_dataframe` decline case to whatever minimally reaches a `pl.` access — verify it raises the helpful ImportError, not a different TypeError first; if `scan_dataframe(object())` fails on arg-type before touching pl, use a path that reaches pl. e.g. read a CSV via scan_file. The point: a full-scan entry declines helpfully.)

- [ ] **Step 2: Inspect + update the `goldencheck_nopolars` CI job** (`.github/workflows/ci.yml`). READ it. Confirm: it `uv sync`s, builds native, uninstalls polars, runs `tests/nopolars`. Ensure **pyarrow survives the polars-uninstall** (uninstalling polars must NOT remove pyarrow; if `uv sync --all-packages` doesn't include pyarrow, add `uv pip install pyarrow openpyxl` before the pytest step). The lane must have pyarrow+openpyxl+native, NO polars.

- [ ] **Step 3: Promote the job to REQUIRED.** In `ci.yml`, add `goldencheck_nopolars` to the `ci-required` job's `needs:` list (the aggregate required gate — the reviewer identified it; find the `ci-required` job and its needs array) and flip any "Advisory" comment. Confirm `.github/filters.yml` scopes the job to `goldencheck/**` + `tests/nopolars/**` + `pyproject.toml` (it does per review) so unrelated PRs skip it (skipped==pass in ci-required).

- [ ] **Step 4: Validate YAML parses** (broken ci.yml = 0 jobs):
```bash
$PY -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml ok')"
```
Run the nopolars tests locally IF native is built + you simulate (they skipif when polars present, so locally they SKIP — that's expected; the lane runs them for real):
```bash
$PY -m pytest packages/python/goldencheck/tests/nopolars -v   # SKIP locally (polars present) -- expected
```

- [ ] **Step 5: Commit.**
```bash
git add packages/python/goldencheck/tests/nopolars/test_polars_absent.py .github/workflows/ci.yml .github/filters.yml
git commit -m "ci(goldencheck): P4b nopolars lane asserts polars-free read/scan + declines; promote to required"
```

---

## Task 4: P4 rollout docs (the polars-optional story) + final verification

**Files:** Modify `docs-site/goldencheck/native.mdx` + `overview.mdx`, `README.md` (goldencheck).

- [ ] **Step 1: `docs-site/goldencheck/overview.mdx` + `native.mdx`.** Add a short "Polars is optional (2.0.0)" section: `pip install goldencheck` runs `import goldencheck`, `scan_columns`, and Parquet/Excel `read_columns`/`scan_file_columns` WITHOUT Polars; **install `goldencheck[polars]` for CSV reading and the full `scan_file`/`scan_dataframe` scan** (Polars' CSV dtype inference isn't reproducible; the full scan is Polars-native). Do NOT add the eviction regex/date kernels to native.mdx's SPEEDUP table (they're byte-identical, not speedups) — describe them as "the polars-free structural checks" instead. Extras table: `[polars]` (CSV + full scan), `[parquet]` (polars-free Parquet), `[native]` (Rust kernels).

- [ ] **Step 2: `README.md`.** Update install/quickstart: base install is polars-free for Parquet/Excel structural scans; `goldencheck[polars]` for CSV + full scan. Add a brief "2.0.0 breaking change" note. Grep the README for any "pip install goldencheck" that implies CSV/full-scan works out of the box and qualify it.

- [ ] **Step 3: Doc-consistency gate** (there's a `docs_consistency` check):
```bash
$PY scripts/check_docs_consistency.py 2>&1 | tail -5 || echo "(inspect if it errors / different path)"
```
Fix anything it flags (changelog/version alignment).

- [ ] **Step 4: Final whole-batch verification.**
```bash
cd /d/show_case/gc-p4b && <preamble>
$PY -m pytest packages/python/goldencheck/tests/test_polars_decline.py packages/python/goldencheck/tests/test_import_no_polars.py -v
$PY -c "import goldencheck; print('version', goldencheck.__version__)"   # 2.0.0
$PY scripts/check_version_consistency.py && echo "version consistent"
$PY -m pytest packages/python/goldencheck/tests -k "reader or scanner or version or public_api or kernels" -v   # existing, UNEDITED (except version literals)
$PY -m ruff check packages/python/goldencheck/goldencheck packages/python/goldencheck/tests
$PY -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "ci.yml ok"
```
Report exact results; confirm version 2.0.0 consistent; existing tests green (only version-string literals updated, if any). Do NOT run the full suite (OOM).

- [ ] **Step 5: Commit.**
```bash
git add docs-site/goldencheck/ packages/python/goldencheck/README.md
git commit -m "docs(goldencheck): P4b polars-optional rollout docs (overview/native/README, 2.0.0)"
```

---

## Done criteria (P4b complete → Polars-eviction COMPLETE)
- [ ] `polars` moved to `[polars]` extra; base `pip install goldencheck` is polars-free; version `2.0.0` in pyproject + `__init__` + CHANGELOG (version-consistency gate green).
- [ ] Lazy proxy + the 2 kernels.py fallbacks raise a helpful `goldencheck[polars]` ImportError when polars absent; `import goldencheck` still loads zero polars.
- [ ] nopolars lane (now REQUIRED) proves: `scan_columns` + `read_columns`(parquet/excel) + `scan_file_columns` work polars-free; CSV + full scan decline helpfully.
- [ ] With `[polars]` present, byte-identical to before; existing tests unedited (only version literals).
- [ ] P4 rollout docs tell the polars-optional story (overview/native/README/CHANGELOG).
- [ ] NO PyPI publish (human-gated); goldenmatch `goldencheck[polars]` dep bump flagged for the release step, not done here.
