# Native-symbol ↔ host call-site reconciliation — design

**Status:** approved-in-scope (2026-07-05), pending spec review
**Context:** #688 cost a 240× regression because a host call-site reached a native
kernel symbol through a silent `AttributeError`/`getattr(..., None)` fallback that
resolved to the slow path. This gate makes a host call-site that depends on a
kernel symbol the kernel doesn't export a **hard CI failure**, instead of a silent
runtime slow-path. Related memory: `project_688_stale_native_wheel`,
`feedback_verify_perf_not_just_ship`, `project_api_parity_gate` (same gate shape).

This is the **static tier** (source ↔ source). The **shipped-wheel tier** (source
↔ published `.so`, the actual #688 republish-lag catcher) is a separate follow-on
(Project 3). This spec is the reference implementation on **goldenmatch**;
rolling to the other four native packages is a mechanical follow-on (§7).

## 1. Problem

goldenmatch's host reaches its Rust kernel (`goldenmatch._native`) through three
call-site forms — but crucially, through **many module-binding aliases**, not a
single `native` name. Production code does `_nm = native_module()` /
`nm = native_module()` / `mod = native_module()` / `native_mod = _ensure_native()`
and then calls `_nm.<symbol>(...)` / `hasattr(_nm, "<symbol>")` etc. The three
forms:
- **direct** — `<binding>.<symbol>(...)` (~23 sites)
- **guarded / silent-fallback** — `getattr(<binding>, "<symbol>", None)` then fall
  back to pure Python if `None` (~5 sites) — *the #688 shape*
- **capability probe** — `hasattr(<binding>, "<symbol>")` (~12 sites across aliases)

where `<binding>` is `native_module()`, `_ensure_native()`, or any local provably
bound to one of them (`native`, `_nm`, `nm`, `mod`, `native_mod`, ...).

The kernel's exported symbols are the `wrap_pyfunction!(module::<symbol>, m)`
registrations in `packages/rust/extensions/native/src/lib.rs` (**40** today;
`score.rs` only *defines* the `#[pyfunction]` shims that `lib.rs` registers — its
lone `wrap_pyfunction!` token is inside a comment, not a registration).

Nothing checks that every symbol the host *references* is one the kernel actually
*exports*. A typo, a rename on one side, or a `getattr` fallback for a symbol that
was never built all resolve to a silent slow/pure path — invisible until someone
profiles the wall (which is how #688 was found, the hard way).

## 2. Goal

A box-safe check `scripts/check_native_symbols.py goldenmatch` that reconciles the
two sets and **fails CI** when a host reference has no matching kernel export.

- **FAIL** if `REFERENCED ⊄ REGISTERED` — a host call-site depends on a symbol the
  source kernel does not export (drift / typo / rename / aspirational-never-built
  fallback). This is always a real defect at the source level.
- **REPORT (non-fatal)** `REGISTERED ∖ REFERENCED` — exported symbols no host
  references (dead export, or wasm/other-surface-only, or cross-package). Printed,
  not failed, so the count is visible and can be triaged, not silently accreting.

The bootstrap run may surface real findings on goldenmatch (a `getattr` fallback
for a symbol that isn't in `lib.rs`) — those are the payoff, triaged like the
API-parity gate's naming divergences were.

## 3. Design

### 3.1 Registered set (kernel exports)

Parse every `wrap_pyfunction!(<path>::<symbol>, m)` occurrence in the package's
native crate registration source (`packages/rust/extensions/native/src/lib.rs` —
one registration is multi-line, so match across newlines), extracting the final
`::`-segment as the exported symbol name. A `wrap_pyfunction!` token that is not a
call with a `::` path (e.g. inside a comment) is ignored. Text/regex parse — no
cargo build. This is the *source* truth; the shipped-wheel truth is Project 3.

### 3.2 Referenced set (host call-sites) — alias-resolving (LOAD-BEARING)

The scanner MUST resolve module-binding aliases, or it silently under-counts the
referenced set and the gate goes falsely green — missing exactly the #688-shape
`hasattr(_nm, "sym")` / `_nm.sym(...)` sites it exists to protect (verified: a
`native`-only scanner misses ~11 real references, including
`autoconfig_planner.py`'s `_nm.autoconfig_decide_plan`).

Per Python file under `packages/python/goldenmatch/goldenmatch/**/*.py` (tests
excluded), that imports `native_module`/`_ensure_native` from
`goldenmatch.core._native_loader`:
1. **Collect the alias set** for that file: `{"native_module()", "_ensure_native()"}`
   plus every local bound by `(\w+)\s*=\s*(?:native_module\(\)|_ensure_native\(\))`
   (e.g. `_nm`, `nm`, `mod`, `native_mod`, `native`).
2. **Extract referenced symbols** over that alias set, all three forms:
   - `(?:<alias>)\.(<symbol>)` — direct attribute call
   - `getattr\(\s*(?:<alias>)\s*,\s*["'](<symbol>)["']` — guarded/silent-fallback
   - `hasattr\(\s*(?:<alias>)\s*,\s*["'](<symbol>)["']` — capability probe

Union + dedupe across all files. Restricting to loader-importing files bounds
false positives from an unrelated local also named `mod`/`nm`.

**Tests are excluded** (structurally already — tests live at
`packages/python/goldenmatch/tests/`, outside the `goldenmatch/goldenmatch/**`
root — but exclude explicitly as belt-and-suspenders; a test `hasattr`-probe is not
a production dependency).

**Two reference idioms the static scanner cannot resolve** (documented so the
`unwired` triage doesn't mis-flag them as dead): `_native_loader.py`'s
`_COMPONENT_SYMBOLS` holds kernel symbol names as **bare string literals in a dict**
(telemetry probing), and `connectors/base.py` uses `getattr(mod, <computed_name>)`.
Both are invisible to any regex. Today every symbol they touch is also referenced
via a resolvable form, so no coverage is lost — but a symbol referenced *only* via
these would show as `unwired` (false-dead), not as `missing` (never false-red).

### 3.3 The check

```
registered = parse_registrations(crate_src)      # set[str]
referenced = scan_references(python_src)          # set[str]
missing    = referenced - registered              # FAIL rows
unwired    = registered - referenced              # REPORT rows
exit 1 if missing else 0   # unwired only prints
```

An **allowlist** (`# a sidecar `parity/native_symbols/<pkg>.allow` or inline
`# native-symbols: allow <symbol> <reason>`) covers the rare legitimate case: a
host reference the parser can't attribute to a build (e.g. a symbol provided by a
*different* loaded kernel, or a deliberately-aspirational fallback with a tracked
issue). Allowlisted symbols are removed from `missing` and the reason is printed.
Keep it empty at bootstrap unless a real cross-kernel case exists.

### 3.4 Per-package registry (built for rollout, goldenmatch first)

A `REGISTRY` dict mapping package → `{crate_src_globs, python_src_root,
reference_idiom}` — exactly the shape `emit_python_surface.py` uses. goldenmatch is
the only entry in this spec; the §7 rollout adds goldencheck / goldenanalysis
(same `native_module().X` idiom) and goldenflow / goldenpipe (whose host idiom
differs — direct=0 despite 74/5 exports — so their reference-scanner needs its own
idiom, discovered during rollout, NOT assumed here).

## 4. Testing

**Box-safe (pure stdlib, no build, no import):**
- `scripts/test_native_symbols.py` — synthetic fixtures exercising the pure core:
  `parse_registrations` on a `wrap_pyfunction!` snippet (incl. a multi-line
  registration and a commented-out token that must be ignored); `scan_references`
  on snippets of all three call-site forms **through an alias** — critically a
  `_nm = native_module()` then `_nm.x(...)` / `hasattr(_nm, "y")` and a
  `native_mod = _ensure_native()` then `native_mod.z` (the alias-resolution is the
  load-bearing behavior, so it gets a dedicated fixture); the check reporting
  `missing` (fail) vs `unwired` (report); allowlist subtraction; and that
  test-file references are excluded.
- A **goldenmatch smoke test** that runs the real check against the real repo and
  asserts it exits cleanly *after* any bootstrap findings are resolved/allowlisted
  (so the committed state is green and the gate has teeth — same red/green proof as
  the API-parity gate).

## 5. CI

A `native_symbols` job in `.github/workflows/ci.yml`, gated on a `dorny/paths-filter`
over `packages/rust/extensions/native/**`, `packages/python/goldenmatch/**`,
`scripts/check_native_symbols.py`, and `parity/native_symbols/**`. Runs
`python scripts/check_native_symbols.py goldenmatch`. **Box-safe → no cargo/maturin
build needed** (source parse only), so it is cheap and always-on. Editing the
workflow re-runs it. Must demonstrate FAIL on an injected bogus
`native_module().does_not_exist()` reference and PASS once removed.

## 6. Bootstrap

Measured ahead of the build (spec review, alias-resolving idiom):
- **`missing` (FAIL) set: EMPTY.** No hard failures — the bootstrap is clean, the
  gate goes green immediately once the scanner is correct. (This is the whole
  reason to get §3.2's alias resolution right: a naive scanner would show a green
  gate too, but for the wrong reason — by not seeing the references at all.)
- **`unwired` (REPORT) set: 2** — `build_clusters_native` and
  `connected_components_arrow`, both genuinely dead (superseded by the
  `build_clusters_arrow` / `_arrow` paths per `cluster.py` comments). Note them in
  the PR's unwired triage so they aren't re-investigated each run; leave them in
  REPORT (non-fatal) rather than allowlisting.

If a future `missing` row appears, triage: typo/rename → fix the call-site or
kernel; `getattr` fallback to a genuinely-unbuilt symbol → build it or allowlist
with a tracked reason; cross-kernel/dynamic reference → allowlist with reason.
Record the `unwired` count in the PR description (visibility, not a gate).

## 7. Rollout (follow-on, not this PR)

Add `REGISTRY` entries + bootstrapped state for goldencheck, goldenanalysis
(same idiom), then goldenflow, goldenpipe (discover their host idiom first —
direct=0 today means they bind the module differently; the scanner grows an
idiom per those packages). Each is a small PR mirroring this one.

## 8. Risks

- **Idiom coverage.** If a package reaches the kernel through a form the scanner
  doesn't recognize, its referenced set is under-counted → the check is falsely
  green (misses drift), NOT falsely red. goldenmatch's three forms are enumerated
  from the real source; other packages are explicitly deferred until their idiom
  is confirmed. A scanner that finds **zero** references for a package with a
  populated kernel is a red flag the rollout must catch (fail-loud on empty).
- **`native` local false positives.** Restricting the `native`-var forms to files
  importing `native_module` bounds this; the allowlist is the escape hatch.
- **Source ≠ shipped.** The static tier proves the *source* is consistent, not
  that the *published wheel* is — that is Project 3's job, and this spec says so
  plainly so no one over-reads the guarantee.
