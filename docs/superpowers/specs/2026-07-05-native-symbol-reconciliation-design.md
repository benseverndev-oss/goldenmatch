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
call-site forms:
- **direct** — `native_module().<symbol>(...)` (30 sites)
- **guarded / silent-fallback** — `getattr(native, "<symbol>", None)` then
  fall back to pure Python if `None` (5 sites) — *the #688 shape*
- **capability probe** — `hasattr(native_module(), "<symbol>")` (14 sites)

The kernel's exported symbols are the `wrap_pyfunction!(module::<symbol>, m)`
registrations in `packages/rust/extensions/native/src/*.rs` (41 today).

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

Parse every `wrap_pyfunction!(<path>::<symbol>, ...)` occurrence across the
package's native crate source (`packages/rust/extensions/native/src/**/*.rs` —
`lib.rs` plus submodules like `score.rs`), extracting the final `::`-segment as the
exported symbol name. Text/regex parse — no cargo build. This is the *source*
truth; the shipped-wheel truth is Project 3.

### 3.2 Referenced set (host call-sites)

Scan the package's Python source (`packages/python/goldenmatch/goldenmatch/**/*.py`,
excluding tests) for all three forms, unioned + deduped:
- `native_module()\.(<symbol>)`
- `getattr\(\s*(?:native|native_module\(\))\s*,\s*["'](<symbol>)["']`
- `hasattr\(\s*(?:native|native_module\(\))\s*,\s*["'](<symbol>)["']`

The `native` local is the conventional binding of `native_module()`
(`native = native_module()` / `from ..._native_loader import native_module`).
To bound false positives, only files that import `native_module` from
`goldenmatch.core._native_loader` are scanned for the `native`-var forms; the
`native_module().X` form is unambiguous everywhere.

**Tests are excluded** from the referenced set (a test may `hasattr`-probe a
symbol precisely to skip when absent — that is not a production dependency).

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
  `parse_registrations` on a snippet of `wrap_pyfunction!` lines; `scan_references`
  on snippets of all three call-site forms (incl. a `getattr(native, "x", None)`
  and a `hasattr`); the check reporting `missing` (fail) vs `unwired` (report);
  allowlist subtraction; and that test-file references are excluded.
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

Run the check locally against goldenmatch; triage each `missing` row:
- a real typo/rename → fix the call-site or the kernel;
- a `getattr` fallback for a genuinely-unbuilt symbol → decide (build it, or
  allowlist with a tracked reason);
- a cross-kernel/legitimate-dynamic reference → allowlist with reason.
Commit until the check is green. Record the `unwired` count in the PR description
(visibility, not a gate).

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
