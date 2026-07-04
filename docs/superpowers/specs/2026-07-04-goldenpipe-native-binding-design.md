# goldenpipe-native — Python binding + parity gate (SP2) — design

**Program:** goldenpipe → Rust. SP1 (`goldenpipe-core`, PR #1418, merged) is the
pyo3-free planner kernel + golden-vector fixtures. **SP2 makes the core the Python
REFERENCE:** ship a `goldenpipe-native` abi3 wheel over the core + a reference-mode
loader, and gate the pure-Python planner against the core with a parity test. **SP3
(TS/WASM reroute — the actual Python↔TS drift-kill) is a separate spec.**

## Scope decisions (locked in brainstorming)

- **Parity-gate only, NOT reference-mode runtime.** The core is the ORACLE; the
  pure-Python planner stays the Python RUNTIME (unchanged). We do NOT route
  Resolver/Router/decisions through the core at runtime — the planner isn't
  compute-bound, so marshaling JSON to Rust and back per pipeline would be a pure
  tax for zero benefit. The anti-drift value is delivered by the **parity gate**
  (pure-Python provably conforms to the core), not by making Python call Rust.
  This is the honest reading of "Rust is the reference, source-of-truth not speed"
  for a no-perf-win component.
- **CI-primary, box best-effort.** The gate is authoritative in CI
  (`GOLDENPIPE_NATIVE=1` lane, wheel built there). Try building the wheel locally
  too — goldenpipe-native has NO heavy deps (pyo3 + the serde-only core; unlike the
  ort-blocked goldenmatch native) so it likely links on the box — but SP2 does not
  block on a local wheel build.

## Components

Mirror goldenflow's Wave 0a (`#1405`) exactly where it fits; goldenpipe is simpler
(JSON string boundary, not Arrow columnar).

### 1. `goldenpipe-native` crate — `packages/rust/extensions/goldenpipe-native/`

Standalone `[workspace]` (empty) so pyo3's `extension-module` feature isn't unified
with any other crate. `[lib] name = "_native", crate-type = ["cdylib"]` (produces
`_native.so`/`.pyd`; the `#[pymodule]` init is `PyInit__native`).

Deps: `pyo3 = { version = ">=0.28,<0.29", features = ["extension-module", "abi3-py311"] }`
+ `goldenpipe-core = { path = "../goldenpipe-core" }`. **No arrow** — the planner is
JSON-in/JSON-out, not columnar.

`src/lib.rs` — `#[pymodule] _native` exposing five thin string wrappers, each a
`#[pyfunction] fn(input: &str) -> String` that delegates to `goldenpipe_core::json::*`:
`resolve_json`, `apply_decision_json`, `evaluate_builtin_json`, `auto_config_json`,
`skip_if_falsy_json`, plus `__version__ = env!("CARGO_PKG_VERSION")`. It is a pure
marshaling shim: `&str` in → core fn → `String` out (release the GIL is unnecessary
— the work is sub-microsecond; keep it a plain call). The core is the single source
of truth; this crate adds zero logic.

`pyproject.toml` (maturin build backend, like native-flow's) so it publishes as the
separate `goldenpipe-native` abi3 wheel (`pip install goldenpipe[native]`).

### 2. `goldenpipe/core/_native_loader.py` (new)

Mirror `goldenflow/core/_native_loader.py`:
- Reachable two ways, tried in order: `goldenpipe._native` (in-tree build via
  `scripts/build_native.py`) → `goldenpipe_native._native` (the wheel).
- `GOLDENPIPE_NATIVE` env: `"0"` force pure; `"1"` require native (raise if absent —
  the CI parity lane); `"auto"`/unset → native available iff the floor symbol exists.
- Component = `"planner"`, floor symbol `resolve_json` (wheel-skew-safe `_has_symbol`
  probe). No `_FALLBACK_ONLY` (nothing wrong-spec here).
- Public API: `native_module()`, `native_available()`, `native_enabled("planner")`,
  and thin `resolve_json(...)`/etc. pass-throughs the parity test (and any future
  reference-mode consumer) call — so the wheel is reachable through one module.

Note the loader is SHIPPED but the runtime does NOT consume it yet (parity-gate-only
scope). It exists so (a) the parity test can reach the wheel and (b) SP3-era or a
future reference-mode flip has the seam ready. This matches the roadmap's
"native = required artifact, reachable under a gate" without paying the runtime tax.

### 3. `scripts/build_native.py`

Mirror goldenflow's: `maturin build`/`develop` the `goldenpipe-native` crate into
`goldenpipe/_native` for the in-tree path. Box-runnable if pyo3 links locally.

## The parity gate (the SP2 deliverable — makes the core the reference)

Reuse the SP1 golden vectors (`packages/rust/extensions/goldenpipe-core/tests/vectors/*.json`)
as the shared truth — their `expected` IS the core's output. Two legs:

- **Leg A — pure-Python == core (the anti-drift gate; box-safe, no wheel needed).**
  A test replays each vector `input` through the PURE-PYTHON planner and asserts the
  output equals the vector `expected`. This proves the Python planner conforms to the
  core and CANNOT drift from it (a future Python change that diverges fails here). It
  needs a small JSON-shaped adapter around the pure-Python planner:
  - resolve: build a throwaway registry/stage-info from the vector's `stages`, run
    `Resolver.resolve`, serialize the `ExecutionPlan` to the core's JSON shape
    (`{name, use, config, on_error}` per stage; `{ok|err}` envelope).
  - apply_decision / evaluate_builtin / auto_config / skip_if: call the pure-Python
    `Router.apply` / `decisions.*` / `_auto_config` / `not artifact`, serialize to the
    core's shape.
  This adapter lives in the TEST (or a tiny `goldenpipe/core/_planner_json.py` helper
  if cleaner) — it does NOT change the runtime planner. Runs in every CI lane +
  locally, no wheel.
- **Leg B — native wheel == core (validates the wheel/marshaling; CI-primary).**
  With the wheel built (`GOLDENPIPE_NATIVE=1`), replay each vector `input` through the
  native `_native.resolve_json(...)` etc. and assert == `expected`. Trivially true if
  the wheel is the core, but catches build/version/marshaling breakage. Skips (not
  fails) when the wheel is absent under `auto`; the `=1` CI lane makes it mandatory.

Leg A is the load-bearing anti-drift value and is always-on. Leg B is the wheel smoke.

## Loader unit tests

Mirror `test_native_loader.py`: `native_enabled("planner")` True when a fake `_native`
with `resolve_json` is present under `auto`; False under `"0"`; raises under `"1"` when
`_native is None`; `_has_symbol` probes the floor symbol.

## CI

- Add a `GOLDENPIPE_NATIVE=1` parity lane (mirror goldenflow's) that builds the wheel
  and runs Leg A + Leg B. Wire a `dorny/paths-filter` entry so a change to
  `goldenpipe-core/**` OR `goldenpipe-native/**` OR the goldenpipe Python planner
  re-triggers it — this is ALSO where goldenpipe-core finally gets its CI coverage
  (the SP1 gap: the core had no consumer to hang a lane on; goldenpipe-native is that
  consumer).
- Leg A runs in the ordinary goldenpipe pytest lane (no wheel) too, so drift is caught
  even on a doc-filtered run of the planner.

## Error handling / edge

- The wheel is optional: `_native = None` on any import failure → Leg B skips, runtime
  unaffected (pure-Python), exactly like goldenflow.
- Version skew: `_has_symbol` probes the actual module for `resolve_json` (not a
  version string), so a stale wheel missing a symbol degrades gracefully.
- The native wrappers never panic on bad JSON — the core's `*_json` already return a
  `{"err":{"kind":"parse",...}}` envelope; the pyo3 layer just passes the `String`
  through.

## Testing summary (box posture)

- Leg A parity (pure-Python == core fixtures): **box-safe**, always runs.
- Loader unit tests: **box-safe** (pure Python, fake `_native`).
- Wheel build + Leg B: **box best-effort** (goldenpipe-native has no heavy deps so it
  likely links on Windows/NTFS via maturin; if it doesn't, Leg B is CI-only). Do not
  block SP2 on the local wheel.
- Rust: `goldenpipe-native` `cargo build` (needs a Python lib to link the
  extension-module; `cargo build` alone may need `--no-default-features` juggling —
  prefer `maturin build` as the real check).

## Out of scope (SP2)

- Any runtime routing of the Python planner through the core (parity-gate-only). A
  future reference-mode flip is a separate, opt-in change if ever justified.
- SP3 (TS/WASM reroute + the Python↔TS drift-kill) — separate spec.
- Publishing the wheel to PyPI / release tags — SP2 lands the crate + build + CI gate;
  the publish workflow follows the existing `goldenflow-native` pattern in a later step.
- Touching Runner/registry/IO (host stays).
