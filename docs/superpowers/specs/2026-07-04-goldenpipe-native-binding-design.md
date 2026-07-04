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
as the shared truth — their `expected` IS the core's output. Two legs, both replaying
those vectors.

### `goldenpipe/core/_planner_json.py` (new, SHIPPED helper) — the JSON face of the pure-Python planner

Leg A is NOT "just serialize" — mapping the object/exception-based Python planner to
the core's JSON shape needs real, specified glue. Put it in ONE helper with an explicit
contract (five `*_json(input_str) -> output_str` fns that CALL the real
Resolver/Router/decisions internally, so the gate tests the ACTUAL planner, not a
re-implementation):

- **`resolve_json`** — parse `{config, stages}`. Build a throwaway `StageRegistry` from
  the vector's `StageInfo` objects, keying **by `StageInfo.key`** — `registry.register()`
  keys by `info.name`, so for the `key != info.name` vectors the helper writes
  `registry._stages[info.key] = _stub_stage(info)` directly (a stub Stage whose `.info`
  carries the metadata). Call `Resolver.resolve(config, registry)`; on success serialize
  `ExecutionPlan.stages -> [{name, use, config, on_error}]` as `{"ok": {...}}`. On
  `WiringError` -> `{"err": {"kind":"wiring", stage, missing, available}}`; on the
  unknown-`use` `KeyError` -> `{"err": {"kind":"unknown_stage", "use": <the first
  config stage whose use ∉ registry>}}` (deterministic — Resolver fails on the first
  such stage).
  - **In-scope additive change to `resolver.py`:** `WiringError` currently carries only
    a message. Add structured attributes `.stage`, `.missing`, `.available` (the message
    is unchanged) so the helper reads them directly instead of string-parsing. Additive,
    behavior-preserving, and it's the faithful way to map the wiring vector.
- **`apply_decision_json`** — parse `{decision, remaining}`. Reconstruct `PlannedStage`s
  from `remaining`, a `Decision`, a stub registry providing the `insert` names (Router
  calls `registry.get(name)` per insert), and a `PipeContext`. Call `Router.apply`,
  then serialize `{remaining: [{name,use,config,on_error}], router_note}` where
  `router_note` is `ctx.reasoning.get("_router")` (Router writes it there; absent -> the
  field is omitted, matching the core's `skip_serializing_if`).
- **`evaluate_builtin_json`** — parse `{name, ctx}`. Dispatch a name table
  `{"severity_gate": severity_gate, "pii_router": pii_router, "row_count_gate":
  row_count_gate}`; **unknown name -> `None`** (the core's `_ => None`). Call the
  `decisions.py` fn with a `PipeContext` built from `ctx.artifacts`/`ctx.metadata`;
  serialize the returned `Decision` (or JSON `null`).
- **`auto_config_json`** — parse `{available, identity_opts}`. `Pipeline._auto_config`
  is an instance method reading `self._registry.list_all()` + `self._identity_opts`, so
  construct a `Pipeline` with a stub registry whose `list_all()` returns `available` and
  set `_identity_opts` (empty `{}` -> not-given, matching both sides), call
  `_auto_config`, serialize the `PipelineConfig` to the core's shape.
- **`skip_if_falsy_json`** — parse a bare JSON value; return `str(bool(not <value>))`
  lowercased... i.e. Python `not artifact` -> the same truthy set the core pins.

Leg A (below) asserts each helper fn == the vector `expected`. Because the helper calls
the real Resolver/Router/decisions, a future runtime-planner change that diverges from
the core fails the gate. The registry-construction / dispatch / envelope glue is helper
concern, not planner logic — ordering, validation, and routing stay in the real modules.

- **Leg A — pure-Python == core (the anti-drift gate; box-safe, no wheel needed).**
  Replay each vector `input` through the matching `_planner_json.*` fn and assert the
  parsed output equals the vector `expected`. Runs in every CI lane + locally, no wheel.
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
- Touching Runner/registry/IO (host stays). The ONLY runtime file SP2 modifies is
  `engine/resolver.py`, and only ADDITIVELY: `WiringError` gains `.stage`/`.missing`/
  `.available` attributes (message unchanged, no behavior change) so the parity helper
  can read a structured error. The pure-Python planner runtime path is otherwise
  untouched (parity-gate-only).
