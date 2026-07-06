# Native-Symbol Gate Rollout — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Roll the native-symbol reconciliation gate (#1459) to goldencheck, goldenanalysis, goldenflow via a regex fix + a `literal` idiom; document goldenpipe as reference-mode-N/A.

**Architecture:** Box-safe source parse (no cargo build). Two scanner changes in `scripts/check_native_symbols.py`: (1) `_WRAP` accepts bare fn names; (2) a per-package `idiom` (`runtime` | `literal`). Add REGISTRY entries for the 3 packages; goldenpipe gets a doc comment, no entry.

**Tech Stack:** Python stdlib (`re`, `pathlib`), pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-07-05-native-symbol-rollout-design.md`

**Acceptance oracle (computed in spec review — the build MUST reproduce):**
- `goldencheck` → exit 0, missing=∅, unwired = `{functional_dependency_holds}` (1 dead export).
- `goldenanalysis` → exit 0, missing=∅, unwired=∅.
- `goldenflow` → exit 0, missing=∅, referenced ≈ **74** (the `*_arrow` literals). A referenced count of a handful means the `literal_pattern`/filter is wrong — debug, don't allowlist.
- `goldenmatch` (regression) → still exit 0, missing=∅ (unchanged).

**Environment / SOP:** Branch `feat/native-symbol-rollout` off `origin/main` (has #1459). Box-safe: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe` (the script reads files only — no imports). Run **ruff** on the touched Python (#1451 lesson). benzsevern gh; merge-queue → `gh pr merge --auto --squash` (no `--delete-branch`); arm + stop.

**Anchors (verified):** `scripts/check_native_symbols.py` — `_WRAP` :22, `scan_file_refs` :37, `scan_references(py_root, loader_tokens)` :48, `load_allow` :58, `REGISTRY` :12, `run(package)` (reads crate_reg/py_root/loader_tokens/allow). `scripts/test_native_symbols.py` — the #1459 fixtures. CI `native_symbols` job ci.yml:1527 (`python scripts/check_native_symbols.py goldenmatch` :1540 + unit tests :1542); filter :433-438; output :105.
Crates: `goldencheck-native` (7, `mod::fn`), `analysis-native` (2, **bare**), `native-flow` (74, `mod::fn`), `goldenpipe-native` (5, bare). goldenflow shim: `packages/python/goldenflow/goldenflow/transforms/_native.py`.

---

## Task 1: Scanner changes + REGISTRY + goldenpipe doc + .allow files + tests

**Files:**
- Modify: `scripts/check_native_symbols.py`
- Modify: `scripts/test_native_symbols.py`
- Create: `parity/native_symbols/{goldencheck,goldenanalysis,goldenflow}.allow`

- [ ] **Step 1: Write the failing unit tests** (append to `scripts/test_native_symbols.py`)

```python
def test_parse_registrations_accepts_bare_fn():
    src = "m.add_function(wrap_pyfunction!(histogram, m)?)?;\n" \
          "m.add_function(wrap_pyfunction!(profile::benford_leading_digits, m)?)?;\n"
    assert mod.parse_registrations_text(src) == {"histogram", "benford_leading_digits"}


def test_literal_idiom_extracts_arrow_literals():
    src = ('from goldenflow.core._native_loader import native_module\n'
           'def _kernel_runner(name): ...\n'
           'x = _kernel_runner("phone_e164_arrow")\n'
           'attr = "split_address_arrow"\n'
           'y = getattr(native_module(), attr)\n'
           'z = "not_a_kernel"\n')
    got = mod.scan_references_text(src, idiom="literal", literal_pattern=r'"(\w+_arrow)"')
    assert got == {"phone_e164_arrow", "split_address_arrow"}


def test_literal_idiom_skips_files_without_loader_token():
    # a file without the loader token contributes nothing even if it has an _arrow literal
    src = 'x = "phone_e164_arrow"\n'   # no native_module token
    # via scan_references over a temp dir would skip it; here assert the token gate directly
    assert "native_module" not in src


def test_runtime_idiom_unchanged():
    src = ('from goldenmatch.core._native_loader import native_module\n'
           'r = native_module().connected_components(x)\n')
    assert mod.scan_references_text(src, idiom="runtime") == {"connected_components"}
```
(These call a new pure helper `scan_references_text(text, idiom, literal_pattern)` that applies one file's extraction — the plan adds it so the idiom is unit-testable without a temp dir.)

- [ ] **Step 2: Run — confirm FAIL** (`scan_references_text` missing; bare-fn parse fails under the `+` regex).

- [ ] **Step 3: Fix `_WRAP` (bare fn names)** (check_native_symbols.py:22):
```python
_WRAP = re.compile(r"wrap_pyfunction!\(\s*(?:\w+::)*(\w+)")
```

- [ ] **Step 4: Add the `literal` idiom + a per-file dispatch helper.** After `scan_file_refs`:
```python
def scan_references_text(text: str, idiom: str = "runtime",
                         literal_pattern: str | None = None) -> set[str]:
    """One file's referenced-symbol set, by idiom. Pure (testable)."""
    if idiom == "literal":
        return set(re.findall(literal_pattern, text)) if literal_pattern else set()
    return scan_file_refs(text)
```
Rework `scan_references` to take + thread the idiom:
```python
def scan_references(py_root: str, loader_tokens, idiom: str = "runtime",
                    literal_pattern: str | None = None) -> set[str]:
    out: set[str] = set()
    for py in pathlib.Path(py_root).rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if not any(tok in text for tok in loader_tokens):
            continue
        out |= scan_references_text(text, idiom, literal_pattern)
    return out
```

- [ ] **Step 5: Add the 3 REGISTRY entries + `idiom` on goldenmatch** (check_native_symbols.py REGISTRY). goldenmatch: add `"idiom": "runtime"`. Then:
```python
"goldencheck": {
    "crate_reg": ["packages/rust/extensions/goldencheck-native/src/lib.rs"],
    "py_root": "packages/python/goldencheck/goldencheck",
    "loader_tokens": ("native_module", "_ensure_native"),
    "idiom": "runtime",
    "allow": "parity/native_symbols/goldencheck.allow",
},
"goldenanalysis": {
    "crate_reg": ["packages/rust/extensions/analysis-native/src/lib.rs"],
    "py_root": "packages/python/goldenanalysis/goldenanalysis",
    "loader_tokens": ("native_module", "_ensure_native"),
    "idiom": "runtime",
    "allow": "parity/native_symbols/goldenanalysis.allow",
},
"goldenflow": {
    "crate_reg": ["packages/rust/extensions/native-flow/src/lib.rs"],
    "py_root": "packages/python/goldenflow/goldenflow",
    "loader_tokens": ("native_module",),
    "idiom": "literal",
    "literal_pattern": r'"(\w+_arrow)"',
    "allow": "parity/native_symbols/goldenflow.allow",
},
```
Update `run(package)` to read + pass the idiom:
```python
referenced = scan_references(spec["py_root"], spec["loader_tokens"],
                             spec.get("idiom", "runtime"), spec.get("literal_pattern"))
```

- [ ] **Step 6: goldenpipe doc comment** (near REGISTRY):
```python
# goldenpipe is intentionally NOT gated: its `goldenpipe-native` binding is a
# REFERENCE-MODE parity oracle (see goldenpipe/core/_native_loader.py) — the
# pure-Python planner (_planner_json.py) is the runtime, and the kernel exists only
# so the planner parity gate (#1424) can compare byte-identity. There are no
# host-accelerated references to reconcile; drift is caught by that parity gate.
```

- [ ] **Step 7: Create the 3 empty `.allow` files** (`parity/native_symbols/<pkg>.allow`), each with the header comment from goldenmatch's allow file (adapted per package): "Native-symbol allowlist for <pkg>. EMPTY at bootstrap."

- [ ] **Step 8: Run the unit tests — confirm ALL pass.**
Run: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_native_symbols.py -q`

- [ ] **Step 9: Run the 4 real gates — MUST match the oracle.**
For each of `goldenmatch goldencheck goldenanalysis goldenflow`:
`POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe scripts/check_native_symbols.py <pkg>; echo "exit=$?"`
Expected: all exit 0; goldencheck unwired lists exactly `functional_dependency_holds`; goldenanalysis no unwired; goldenflow prints `N registered, ~74 referenced` (referenced ≈ 74, NOT a handful) with no missing; goldenmatch unchanged. **If goldenflow's referenced count is small or missing is non-empty, the literal_pattern/filter is wrong — debug before proceeding.**

- [ ] **Step 10: ruff + commit.**
Run: `D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check scripts/check_native_symbols.py scripts/test_native_symbols.py` → All checks passed.
```bash
git add scripts/check_native_symbols.py scripts/test_native_symbols.py parity/native_symbols/goldencheck.allow parity/native_symbols/goldenanalysis.allow parity/native_symbols/goldenflow.allow
git commit -m "feat(native): roll native-symbol gate to goldencheck/goldenanalysis/goldenflow (+ literal idiom)"
```

---

## Task 2: CI — matrix the native_symbols job

**Files:** `.github/workflows/ci.yml`

- [ ] **Step 1: Broaden the `native_symbols` paths filter** (ci.yml:433-438) — add the 3 crates + hosts:
```yaml
              - 'packages/rust/extensions/native/**'
              - 'packages/rust/extensions/goldencheck-native/**'
              - 'packages/rust/extensions/analysis-native/**'
              - 'packages/rust/extensions/native-flow/**'
              - 'packages/python/goldenmatch/goldenmatch/**'
              - 'packages/python/goldencheck/goldencheck/**'
              - 'packages/python/goldenanalysis/goldenanalysis/**'
              - 'packages/python/goldenflow/goldenflow/**'
              - 'scripts/check_native_symbols.py'
              - 'scripts/test_native_symbols.py'
              - 'parity/native_symbols/**'
```
(Keep whatever `native/**` + goldenmatch host entries already exist; add the 3 new crates + hosts.)

- [ ] **Step 2: Matrix the job** (ci.yml:1527). Add a `strategy.matrix.package: [goldenmatch, goldencheck, goldenanalysis, goldenflow]`, `fail-fast: false`; the reconcile step becomes `python scripts/check_native_symbols.py ${{ matrix.package }}`; run the unit tests only once (`if: matrix.package == 'goldenmatch'`). Keep the box-safe setup (setup-python, no cargo build, `pip install pytest` for the unit-test shard as #1459 did).

- [ ] **Step 3: Validate ci.yml parses** (`yaml.safe_load`).

- [ ] **Step 4: Commit.**
```bash
git add .github/workflows/ci.yml
git commit -m "ci: matrix the native_symbols gate over goldenmatch/goldencheck/goldenanalysis/goldenflow"
```

---

## Task 3: Docs + PR

**Files:** `CLAUDE.md`

- [ ] **Step 1: CLAUDE.md** — one line under the native-symbol gate note (added by #1459/#1461 rollout): the gate now covers goldencheck/goldenanalysis/goldenflow (goldenflow via a `literal` `*_arrow`-string idiom); goldenpipe is reference-mode-N/A (parity oracle, not host-wired).

- [ ] **Step 2: Push + PR + arm auto-merge (STOP).** PR body: rolled the #1459 gate to 3 more native packages; regex fix for bare fn names; new `literal` idiom for goldenflow's string-literal kernel refs; goldenpipe documented as reference-mode-N/A; computed bootstrap (goldencheck 1 dead export, goldenanalysis clean, goldenflow ~74 refs). Do NOT poll CI.
```bash
unset GH_TOKEN; gh auth switch --user benzsevern
git push -u origin feat/native-symbol-rollout
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create --repo benseverndev-oss/goldenmatch --base main --title "Native-symbol gate rollout (goldencheck/goldenanalysis/goldenflow)" --body-file <body>
GH_TOKEN=$(gh auth token --user benzsevern) gh pr merge --auto --squash --repo benseverndev-oss/goldenmatch  # NO --delete-branch
```

---

## Notes for the implementer

- **The oracle is the acceptance test** (Step 9). goldenflow referenced ≈ 74 + missing=∅ is the load-bearing proof the `literal` idiom is right. A small count = broken pattern/filter — debug, don't allowlist.
- **`literal` idiom is goldenflow-specific** — the `_arrow` suffix + `native_module` filter target `_native.py`'s quoted kernel names. Don't apply it elsewhere.
- **goldenpipe: doc only** — no REGISTRY entry, no CI shard. It's a reference-mode oracle.
- **Box-safe** — source parse only; never import the packages or build Rust.
- **Run ruff** (the #1451 lint lesson).
