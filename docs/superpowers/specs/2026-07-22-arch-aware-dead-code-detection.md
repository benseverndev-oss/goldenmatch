# Architecture-aware dead-code detection

**Status:** draft (prototype landed for goldenmatch-Python module layer)
**Date:** 2026-07-22

## Problem

Off-the-shelf dead-code tools (`vulture`, `ts-prune`, Rust `dead_code`) are wrong for this
repo. In a **Rust + Arrow-native source-of-truth, fused-compute** codebase, *"never runs by
default"* is not *"dead."* The patterns that make the thesis work look exactly like dead code
to a naive scanner:

| Pattern | Why it looks dead | Why it is load-bearing |
|---|---|---|
| Pure-Python/TS **fallbacks** (`_native_loader` → `GOLDENMATCH_*_NATIVE=0`) | native is default-on, so the fallback branch has 0 coverage in the normal lane | it is the *lossy reference fallback* the reference-mode roadmap depends on; and the only path in the `nopolars` lane |
| **Parity oracles** (`GOLDENMATCH_FS_EM_BLOCK_SLIM=0` = "full-width parity oracle") | never runs by default | it is the correctness ground-truth the kernel is diffed against |
| **Opt-in kernels/features** (default-OFF `GOLDENMATCH_*` flags, ~163 of them) | unreached unless the flag is set | shipped capability, gated on by users |
| **FFI/ABI exports** (`wrap_pyfunction!`, `#[wasm_bindgen]`, cabi, `#[pg_extern]`) | no in-language caller | called by name/ABI across the language boundary — invisible to same-language analysis |
| **Public API** (`__all__`, `package.json` exports) | no internal caller | PyPI/npm consumers |

The inverse failure — **phantom-live** code — matters just as much: a "primary" path that
*looks* live but silently never executes because an exception-swallowing fallback always fires
(the #688 rayon park; the goldengraph `ModuleNotFoundError: polars` that RED-committed for
weeks). That is dead code masquerading as the hot path.

## Definition of dead (thesis-consistent)

> Code is **dead** iff it is unreachable from every declared surface **and** every FFI/ABI
> export, is **not** the pure-language reference/fallback for a kernel, is **not** a parity
> oracle, is **not** gated behind a declared opt-in flag, and is **not** in the
> out-of-band-tested `omit` set. Everything else — even at 0% default coverage — is
> **dormant-but-load-bearing**.

Reachability, not coverage, is the primary signal. Coverage in CI runs only the native-ON +
polars-present lanes (shards 1-3 + heavy); the fallback (`GOLDENMATCH_NATIVE=0`), `nopolars`,
and arrow-frame lanes carry no `--cov`, so a coverage-first verdict would delete precisely the
reference/fallback contract. Coverage is used only as *corroboration* and for the phantom-live
check.

## Prior art in this repo (reuse, don't reinvent)

The repo already encodes reachability in a CI-enforced drift-gate ecosystem (18 `check_*` +
5 `gen_*`, most in `ci-required`). The dead-code gate is modeled on it:

- **`docs/agent-codemap.json`** (`agent_codemap.py`, `--check`/`--write`, gated): a pure-AST
  import graph over the Python source — 404 goldenmatch modules with per-module `imports`.
  The AST walk captures function-level *lazy* imports too, so it is a **sound module-level
  reachability substrate**.
- **`parity/*.yaml`** (`check_api_parity.py`, `api_parity` gate): machine-readable names for
  MCP tools / CLI commands / A2A skills / scorers / transforms / blocking strategies /
  `scorer_kernels`. Includes **`scorer_kernels_deferred`** — a flat `name → reason` map
  validated by `check_scorer_coverage` (uncovered / stale / phantom / missing-reason). This is
  the exact template for `dead_code_deferred`.
- **`docs/agent-manifest.json`** (`gen_config_matrix.py --manifest`): config models, CLI, MCP,
  `env_vars`, and the `rust_crates` roster.
- **config-matrix** (`scan_env_vars` regex, "scanned from source, complete"): the ~163
  `GOLDENMATCH_*` flags — every opt-in/oracle entry point, by name.
- **`goldenmatch/core/_native_loader.py`**: `_COMPONENT_SYMBOLS` (component → native symbol)
  + grep-stable `native_enabled("<comp>")` / `native_module()` call sites + `_FALLBACK_ONLY`.
  Enumerates the native-fallback branches by call site (fallback *function names* are
  inconsistent — `_py` / `_python` / inline — so do NOT match on name).
- **`parity/native_symbols/*.allow`** (`check_native_symbols.py`, `native_symbols` gate): the
  pre-built — currently **empty** — slot for FFI-root allowlists.

## Roots and allow-list (where each comes from)

**Reachability roots** (a symbol/module reached from any of these is live):

| Root | Machine source today |
|---|---|
| MCP tools / CLI / A2A / scorers / transforms / blocking | `parity/goldenmatch.yaml` (names) + declaring modules |
| Config models, env-flag-gated paths (~163) | `agent-manifest.json` + config-matrix |
| Public API | `goldenmatch/__init__.py::__all__` |
| Rust crate roster | `agent-manifest.json::rust_crates` |
| Rust→Python / WASM / C-ABI / pgrx exports | **grep-only today** → populate `parity/native_symbols/*.allow` |
| REST / web routes | **grep-only today** (path/decorator dispatch) → small new manifest |
| Plugin entry points | `plugins/registry.py::_GROUPS` + external `pyproject` entry_points |
| Tests / benches | pytest/vitest/cargo-test collection |

**Dormant-but-load-bearing allow-list** (unreached-by-default but NOT dead):

- native fallbacks — from `_COMPONENT_SYMBOLS` + `native_enabled(...)` call sites.
- env-gated paths — from `scan_env_vars("GOLDENMATCH_", ...)`.
- parity oracles / opt-in / kill-switch — **the one gap**: classification is prose only today
  (greppable vocabulary: `parity oracle`, `byte-identical`, `default OFF`, `kill-switch`, "the
  #662 kill-switch pattern"). Formalized via the `dead_code_deferred` map below.

## The gate: `scripts/check_dead_code.py`

Modeled on `check_api_parity.py` / `check_native_symbols.py` (AST-based, allowlist-driven,
`--check`/`--write`, per-package matrix job).

1. **Substrate**: reachability graph from `agent-codemap.json`.
2. **Roots**: union of the table above (start with the machine-enumerable ones; populate the
   FFI/route gaps).
3. **Dormant allow-list**: native-fallback call sites + env-gated paths + the
   `dead_code_deferred` map.
4. **Verdict**: unreached from all roots **AND** not dormant **AND** not in the coverage-`omit`
   set = genuinely-dead suspect.
5. **Discipline**: every suspect is either **deleted** or added to
   **`dead_code_deferred:`** in `parity/<pkg>.yaml` — a flat `symbol/module → reason` map with a
   prefixed vocabulary (`oracle --` / `fallback --` / `surface --` / `dead --`), validated by
   four rules mirroring `check_scorer_coverage`: *uncovered* (a suspect neither reachable nor
   deferred → fail), *stale* (deferred but now reachable), *phantom* (deferred but not a real
   symbol), *missing-reason*.
6. **Companion phantom-live check**: assert the native/primary branch actually has coverage in
   its own lane (native lane), so a silent-always-fallback is caught as phantom-dead.

## Phased rollout

- **Phase 1 (this PR): module-level orphans, goldenmatch-Python.** Robust and low-false-positive
  — a module either is reached over the import graph or is not. Naturally immune to the
  fallback/oracle trap (fallback *modules* are still imported; only a *branch* is dormant).
  Prototype: `scripts/check_dead_code.py`.
- **Phase 2: symbol-level** (unused top-level `def`/`class`) with the `dead_code_deferred`
  discipline + repo-wide identifier/string-literal reference scan to keep FP low.
- **Phase 3: TS** via `knip` (needs a `knip.json` with all `package.json` `exports`/`bin` +
  the three vitest configs + `tests/parity/**` + `examples/*.ts` as entries) and **Rust** via
  whole-graph `cargo-udeps` per standalone crate (per-crate `dead_code` over-reports because
  `-core` libs are consumed cross-crate and `-native`/`-wasm` pub fns are reached only through
  export macros).
- **Phase 4: populate `parity/native_symbols/*.allow`** from the FFI greps and add a REST/web
  route manifest — closes the two grep-only root gaps — then promote the gate into `ci-required`.

## Non-goals / limits

- The prototype does not do symbol-level analysis (Phase 2) — it reports whole-module orphans.
- It trusts `agent-codemap.json` as the import graph; entry surfaces reached only via string
  dispatch (MCP/A2A/CLI/route handlers) are added as explicit roots.
- Coverage is not consulted in Phase 1 (reachability is sufficient and more robust); it enters
  in the phantom-live companion check (Phase 2+).
