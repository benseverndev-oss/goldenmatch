# Native published-wheel drift advisory — design

**Status:** approved-in-scope (2026-07-05), pending spec review
**Context:** Project 1 (#1459, merged) reconciles host native-kernel references
against the *source* kernel (`lib.rs`). This is the one check Project 1 cannot do:
reconcile current-source host references against the **already-published**
`goldenmatch-native` PyPI wheel — the actual #688 republish-lag catcher. Related:
`project_688_stale_native_wheel`, `feedback_verify_perf_not_just_ship`,
`project_api_parity_gate`.

## 1. Problem (why this is not redundant with Project 1)

Project 1 proves the *source* is internally consistent: every host
`native_module().X` / `getattr(native, "X")` reference is a registered
`wrap_pyfunction!` export. It says nothing about what the **shipped wheel**
contains. #688 was exactly this gap: `build_exclude_set` landed in source (host +
kernel both updated, Project 1 would be green), but the published wheel `0.1.0`
predated it — so every `pip install goldenmatch[native]` user silently ran the
slow fallback for that symbol until the wheel was republished.

A build-from-source check (publish-time gate, or maturin-build-then-`dir()`) is
**largely redundant** for the republish-lag axis: a wheel built from source S
contains source S's registered symbols, and the host refs are read from that same
S — consistent by construction. The skew #688 is about is strictly between
**current-source host refs** and an **older published wheel**; only comparing those
two catches it. (A build+`dir()` check does have *one* marginal edge over Project 1
on a different axis — it sees registrations Project 1's `wrap_pyfunction!`-only
regex misses, e.g. `m.add_class::<ExcludeSet>` at `lib.rs:47` or a cfg-gated/
macro-generated registration. This advisory absorbs that benefit for free: it reads
the wheel via `dir()`, so it is strictly more robust than a text-parse on the
export-completeness axis, on top of being the only thing that catches the lag.)

## 2. Goal

An **advisory** CI job that:
1. reads the current-source host reference set (reusing Project 1's
   `scan_references`),
2. introspects the **published** `goldenmatch-native` wheel's actual Python-visible
   exports,
3. warns (does not hard-fail) when a host-referenced symbol is absent from the
   published wheel, with the remediation: **republish `goldenmatch-native`**.

**Advisory, not a gate — deliberately.** A hard gate would be a chicken-and-egg:
the PR that adds symbol X + its host dependency cannot also republish a wheel
containing X (the wheel is built from that PR's not-yet-merged code), so a blocking
check would make it unmergeable. And a lagging wheel *degrades gracefully* (slow
path, correct output), so blocking CI over it is disproportionate. The advisory
surfaces the lag as an annotation; a human republishes.

## 3. Design

### 3.1 Reuse Project 1's scanner

Import `scan_references`, `REGISTRY`, and `load_allow` from
`scripts/check_native_symbols.py` (by path, as its own tests do). The host
reference set is computed identically — one source of truth for "what the host
depends on."

### 3.2 Wheel introspection — Python-visible exports, not `nm`

The published wheel ships a compiled abi3 module (`goldenmatch_native._native`).
Its Python-visible export names are the ones registered at runtime via
`m.add_function(...)` — **not** recoverable by `nm`/symbol-dump on the `.so` (that
yields mangled Rust/pyo3 wrapper symbols). The reliable way: `pip install
goldenmatch-native`, then `import goldenmatch_native._native as m` and read
`{name for name in dir(m) if not name.startswith("_") and callable(getattr(m, name))}`.
The CI job installs the **published** wheel (from PyPI, not a local build) so the
introspected set reflects what real users get.

### 3.3 The check

```
referenced = scan_references(REGISTRY[pkg]["py_root"], REGISTRY[pkg]["loader_tokens"])  # reused, Project 1
if not referenced:                                        # falsely-green guard (see below)
    fail_loud("scanned zero host references — the reference idiom is wrong"); exit 2
shipped    = wheel_exports("goldenmatch_native._native")  # dir() of the installed wheel
lagging    = referenced - shipped - allow
```
**The zero-referenced guard is load-bearing** (mirrors Project 1's
`check_native_symbols.py:88-91`): if `scan_references` returns `∅` (a broken host
scanner), then `lagging = ∅ − shipped − allow = ∅` → the job prints "up to date" and
goes green while checking nothing. That is the exact "falsely reassuring" failure
§7 warns about, arriving via the host side. Fail loud (distinct non-zero exit) when
`referenced` is empty, same posture as the "couldn't introspect the wheel" guard.
`scan_references` takes **two** args (`py_root`, `loader_tokens`) — both from
`REGISTRY[pkg]`; pass both.
- `lagging` non-empty → print an actionable warning listing each symbol and
  "the published goldenmatch-native (vX.Y.Z) lacks these; republish it". Exit 0
  (advisory) — or exit non-zero only under an explicit `--strict` used by a future
  release checklist, never on the scheduled run.
- Also print, informationally, `shipped - referenced` is NOT interesting here (the
  wheel may export more than the host uses — fine); only the `referenced - shipped`
  direction is the lag.

A `--module` flag selects the wheel module (default `goldenmatch_native._native`)
so the same script serves the other native wheels later. A per-package `allow`
(reuse `parity/native_symbols/<pkg>.allow`) covers a symbol deliberately allowed to
lag (e.g. one gated behind a not-yet-released feature).

### 3.4 Scope: goldenmatch first

Reference implementation on `goldenmatch` / `goldenmatch-native`, structured (the
`--module` + REGISTRY) so the other native wheels (`goldencheck-native`,
`goldenpipe-native`, `native-flow`, `analysis-native`) are a mechanical follow-on.

## 4. Testing

**Box-safe unit tests** (`scripts/test_native_wheel.py`): the reconcile logic and
`wheel_exports` are tested against a **stub module object** (a synthetic object with
attributes standing in for a compiled `_native`), so no real wheel/build is needed:
- `wheel_exports` filters to public callables (ignores `_private`, non-callables;
  keeps a class like `ExcludeSet` since it's a public callable export).
- the lag computation (`referenced - shipped - allow`) reports the right set.
- an empty lag prints "up to date" and exits 0.
- **the zero-referenced guard** fires (distinct non-zero exit) when the host scan
  yields `∅` — the load-bearing anti-falsely-green test.
- allow subtraction works.

The real published-wheel introspection is CI-only (needs the installed wheel).

## 5. CI

A new **scheduled + dispatchable** workflow `native-wheel-drift.yml`:
- `on: schedule` (weekly) + `workflow_dispatch`.
- Steps: checkout; `setup-python`; `pip install goldenmatch-native` (the published
  wheel); `python scripts/check_native_wheel.py goldenmatch`.
- Advisory posture: the job surfaces `::warning::` annotations and stays green (the
  script exits 0 on lag by default). A weekly cadence is enough — republish lag is
  a release-hygiene issue, not a per-PR one.

(Not wired into per-PR `ci.yml` — a source PR must never be blocked by the state of
the *previously* published wheel.)

## 6. Rollout / docs

- Single PR, branch `feat/native-wheel-drift` off `origin/main` (has #1459's
  script). Script + unit tests + the scheduled workflow.
- benzsevern gh; merge-queue → `gh pr merge --auto --squash` (no `--delete-branch`);
  arm auto-merge, stop.
- A one-line note in the native-kernel section of the relevant CLAUDE.md /
  docs that the advisory exists and its remediation is "republish goldenmatch-native"
  (reinforces the existing #688 lesson).

## 7. Risks

- **Published wheel unavailable / platform mismatch.** `pip install
  goldenmatch-native` on the linux CI runner must resolve an abi3 wheel importable
  by the runner's Python (abi3 ⇒ 3.11+; the wheel targets that). If the install or
  import fails, the job must fail LOUD (a silent skip would make the advisory
  falsely reassuring) — distinct exit for "couldn't introspect the wheel" vs
  "wheel is up to date".
- **Version drift is expected between releases.** The advisory will legitimately
  warn right after a source PR adds a symbol and before the next
  `goldenmatch-native` release — that is the intended signal, not noise. The
  warning names the fix (republish). It should not be escalated to a hard failure.
- **Reused scanner coupling.** If Project 1's `scan_references` signature changes,
  this script breaks — acceptable (they are the same subsystem and should evolve
  together); a shared unit-test import guards it.
