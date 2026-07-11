# GoldenCheck Polars eviction — P4b (the deps-flip: `polars` optional, major 2.0.0)

Date: 2026-07-11
Status: design (autonomous — /goal "remaining work is complete"; user chose Full P4 breaking flip, "implement + PR but do NOT publish the release without my OK"). Self-reviewed via spec-document-reviewer.
Base: the P4a branch tip (`feat/goldencheck-p4a-polars-free-readers`) — P4b builds on P4a's `read_columns`/`scan_file_columns`. Rebased onto `origin/main` once P4a merges.
Parent: goldencheck Polars-eviction. **P4b is the breaking capstone**: move `polars` from a base dependency to a `[polars]` optional extra so `pip install goldencheck` no longer pulls ~185MB of Polars. Parquet/Excel reading + `scan_columns`/`scan_file_columns` work polars-free; CSV + the full `scan_dataframe`/`scan_file` scan cleanly require `goldencheck[polars]`.

## Context

The groundwork is done: S2 made `import goldencheck` load zero Polars (lazy proxy) and shipped the polars-free `scan_columns` (mechanical + native regex/date profilers); P4a shipped the polars-free `read_columns` (Parquet via pyarrow, Excel via openpyxl; CSV needs Polars). P4b flips the dependency and makes the decline clean + CI-guaranteed. This is a **breaking major version** — existing `pip install goldencheck` users who scan CSVs will need `pip install goldencheck[polars]` after upgrading.

**Release note:** per the /goal constraint, this PR implements + lands the CODE (deps-flip, version, CI lane, docs); it does NOT cut/publish the PyPI 2.0.0 release. The golden-suite lockstep + PyPI publish are a separate, human-gated release step.

## Scope

### In scope
1. **Deps-flip** (`pyproject.toml`): move `"polars>=1.0"` out of base `dependencies` into a new `[project.optional-dependencies] polars = ["polars>=1.0"]`. Base deps keep `openpyxl` (Excel, already base) + everything else. `[parquet]` (pyarrow, P4a) + `[native]` (pyarrow+goldencheck-native) stay.
2. **Clean decline primarily in ONE place** (`_polars_lazy.py`): when the lazy proxy's first `import polars` fails, raise a helpful `ImportError` ("This GoldenCheck operation needs Polars; install `goldencheck[polars]`. Parquet/Excel reading and `scan_columns` work without it.") instead of the raw `ModuleNotFoundError`. Almost every polars-touching call site reaches Polars through `from goldencheck._polars_lazy import pl`, so this single change covers `scan_dataframe`, `scan_file`, baseline, drift, CLI scan, the R4-declined relation profilers. **EXCEPTIONS to fix (three proxy-bypassing direct imports, verified on-branch):** `engine/reader.py:67` (`_read_csv_columns`, P4a — keep its own CSV-specific message, fires first), and `core/kernels.py:125` + `core/kernels.py:251` (the FD / composite-key polars fallbacks on the native-absent path) — these do a function-local `import polars as pl` and would raise a raw `ModuleNotFoundError`. Route the two `kernels.py` sites through `from goldencheck._polars_lazy import pl` (module-level import is safe — runtime use inside the function, not a stringized annotation) so they get the helpful message too. (`cli/main.py:39` `except pl.exceptions.ComputeError` DOES fire the proxy at except-eval time — but note it can mask a real CLI error with the polars-install ImportError in a nopolars env; guard the polars-free CLI entrypoints so this handler isn't reached with polars absent, or accept it as a rare edge.)
3. **Version bump** `1.4.1` → `2.0.0` in `pyproject.toml` (+ any `__version__`/version-consistency surface — audit).
4. **nopolars-REQUIRED CI lane**: promote the S2.0 advisory `goldencheck_nopolars` job to a REQUIRED gate (or add a new required job) that installs `goldencheck[parquet,native]` (pyarrow + native kernel, NO polars), and asserts the polars-free surface WORKS + the polars-required surface DECLINES CLEANLY:
   - works: `import goldencheck`; `scan_columns({...})` (mechanical + native regex/date); `read_columns(parquet)`, `read_columns(xlsx)`; `scan_file_columns(parquet)`; `"polars" not in sys.modules`.
   - declines with the helpful `ImportError`: `read_columns(csv)`, `scan_file(csv)`, `scan_dataframe(...)`.
5. **P4 rollout docs** (the docs sweep for the whole eviction): `docs-site/goldencheck/native.mdx` + `overview.mdx` — the polars-optional story (install `goldencheck[polars]` for CSV + full scan; Parquet/Excel + `scan_columns` run without Polars); `README.md` (extras + the breaking-change note); `CHANGELOG.md` 2.0.0 section (breaking: polars → `[polars]`; polars-free Parquet/Excel + `scan_columns`/`scan_file_columns`; CSV needs `[polars]`). Move the `[Unreleased]` P4a/scan_columns entries under 2.0.0.

### Explicitly NOT in scope
Publishing the PyPI 2.0.0 release (human-gated). The golden-suite meta-package floor bump (release step). Making the FULL scan (`scan_dataframe` → DatasetProfile, classification, denial, sampling, R4-declined relation profilers) polars-free — that path is inherently Polars and stays `[polars]`-required (clean decline). A text-mode CSV reader. Per-call-site error wrapping (the one-place lazy-proxy change covers it).

### Success criteria
- `pip install goldencheck` (base, no extras) → `import goldencheck` + `scan_columns` + `read_columns`(parquet/excel) + `scan_file_columns`(parquet/excel) all work; polars is NOT installed.
- CSV reading + `scan_dataframe`/`scan_file` raise the helpful `ImportError` naming `goldencheck[polars]` when polars absent; work unchanged when `[polars]` installed.
- The nopolars-required CI lane enforces the above on every PR.
- With `goldencheck[polars]` installed, everything is byte-identical to before (no behavior change for polars-present users). Existing tests pass unedited (the dev/CI env has polars).

## Design

### `_polars_lazy.py` clean decline
```python
class _LazyPolars:
    __slots__ = ("_mod",)
    def __init__(self) -> None:
        self._mod = None
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

pl = _LazyPolars()
```
Byte-identical for polars-present callers (the `try` succeeds, same object). The only change is a friendlier error when absent. NOTE: `_read_csv_columns` (P4a) already pre-checks `import polars` with its own message; keep it (its message is CSV-specific and fires before touching `pl`), OR let it fall through to the proxy message — decide at plan time (both are clean; the CSV-specific one is nicer).

### pyproject flip
```toml
dependencies = [
    # polars moved to the [polars] extra in 2.0.0 -- see [project.optional-dependencies]
    "typer>=0.12", "rich>=13.0", "pyyaml>=6.0", "pydantic>=2.7",
    "openpyxl>=3.1", "textual>=1.0", "goldencheck-types", "rapidfuzz>=3.0",
]
[project.optional-dependencies]
polars = ["polars>=1.0"]      # full scan (scan_dataframe/scan_file) + CSV reading
parquet = ["pyarrow>=14"]     # polars-free Parquet read (P4a)
# ... existing extras unchanged ...
```
`version = "2.0.0"`.

### CI lane
Base it on the existing `goldencheck_nopolars` job (S2.0, `.github/workflows/ci.yml`) — it already does `uv sync` + build native + uninstall polars + run `tests/nopolars`. P4b: (a) ensure it installs pyarrow (`[parquet]`/`[native]`) so Parquet read works polars-free; (b) add assertions for `read_columns`(parquet/excel) + `scan_file_columns` + the CSV/scan_file declines (extend `tests/nopolars/test_polars_absent.py`); (c) make the job REQUIRED (add to `ci-required` needs / the required-checks list) so the polars-optional guarantee is enforced. Keep `force_all`/merge-group behavior consistent with the repo's queue.

## Testing
- Extend `tests/nopolars/test_polars_absent.py` (runs in the polars-uninstalled lane): assert `read_columns(parquet)`/`read_columns(xlsx)`/`scan_file_columns(parquet)` work + `"polars" not in sys.modules`; assert `read_columns(csv)` + `scan_dataframe(...)`-adjacent entry raise `ImportError` mentioning `goldencheck[polars]`. (Build tiny parquet/xlsx fixtures in the test via pyarrow/openpyxl, which ARE installed in that lane.)
- A unit test (normal suite, polars present) for the lazy-proxy message: simulate `import polars` failing (monkeypatch/meta_path block) and assert the `ImportError` text mentions `goldencheck[polars]` — proving the decline message without uninstalling polars.
- Existing full suite passes UNEDITED with `[polars]` present (the dev/CI env installs it) — the flip is transparent when polars is there.
- Version-consistency: if a CI gate checks pyproject vs CHANGELOG vs `__version__`, keep 2.0.0 in lockstep.

## Risks / notes
- **Cross-package: goldenmatch depends on goldencheck + calls `goldencheck.cell_quality(pl.DataFrame)`.** goldenmatch has its own polars dep, so runtime is fine, but goldenmatch's dependency on goldencheck should become `goldencheck[polars]` to be explicit. FLAG for the release step (out of P4b's goldencheck scope; note in the PR so the suite-lockstep release covers it).
- **`openpyxl` stays base** (Excel polars-free needs it; it was already base) — good.
- **The dev/CI env has polars** (via `[polars]`/`dev` or the lock), so the existing suite is unaffected; only the dedicated nopolars-required lane exercises the absent path.
- **Breaking-change comms**: the CHANGELOG 2.0.0 + README must clearly state the `pip install goldencheck[polars]` migration for CSV/full-scan users. This is the whole user-facing surface of the eviction.
- **No PyPI publish** in this PR (human-gated per /goal).

## Non-goals (YAGNI)
Publishing 2.0.0; suite floor bump; evicting the full scan path; text-mode CSV; per-call-site decline wrappers; changing any polars-present behavior.
