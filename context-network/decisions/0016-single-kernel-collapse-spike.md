# 0016 — Single-Kernel-Collapse Feasibility Spike

**Status:** Spike • **Shipped:** 2026-06-14

## Context

The suite implements the same entity-resolution algorithms in (at least) three
places that are kept in sync by parity harnesses rather than by sharing code:

- the canonical Rust `score-core` crate (137 LOC) — already the source of truth
  for the Python `native` wheel, the DataFusion FFI UDFs, and the `score-wasm`
  TS backend, *by construction* (thin shims delegate into it);
- the pure-Python scorer `core/scorer.py` (1694 LOC), which dispatches to
  `rapidfuzz`;
- the **hand-rolled** pure-TS scorer `core/scorer.ts` (1042 LOC).

Across the suite this pattern repeats for fingerprint/hash, clustering/graph,
transforms, Fellegi-Sunter math, PPRL, featurization, and analysis aggregates
(full table in [../architecture/single-kernel-collapse-inventory.md](../architecture/single-kernel-collapse-inventory.md)).
Today these are held in lockstep by **31 parity harnesses** (`tests/parity/` on
both sides). That is real, recurring maintenance: every algorithm change must be
re-implemented and re-validated in N languages, and the hand-rolled TS scorers
have historically diverged from rapidfuzz (the `#879` Winkler-boost alignment).

The question this spike answers: **should the suite collapse the N duplicated
algorithm implementations toward one shared Rust kernel?** The deliverable is
EVIDENCE + a go/no-go, NOT a migration.

## Decision

Run a **careful, fully-additive spike** that changes no default path, deletes
nothing, and flips no flag. It produces:

1. an R0 duplication inventory (read-only);
2. a generalizable **`pure == kernel` equivalence gate** for ONE tracer scorer
   (levenshtein) — `scripts/check_kernel_equivalence.py` (Python) +
   `tests/spike/kernel-equivalence.test.ts` (TS, skip-guarded on the WASM
   artifact). It compares the existing pure implementation against the
   `score-core` kernel binding and asserts equal to 4 decimals;
3. a **wall-clock bench** (`scripts/bench_kernel_levenshtein.py`) — the
   measure-don't-assume audit lesson made executable;
4. this decision record + a staged roadmap + the kill criterion below.

The existing pure-Python and pure-TS scorers stay exactly as they are and remain
the default. Everything added is new files that COMPARE the implementations; none
is imported by a default path.

### THE KILL CRITERION (verbatim)

> STOP the collapse and keep the parity-harness status quo if ANY of: (1) the
> pure==kernel equivalence gate can't pass at 4dp/byte for the tracer scorer
> across Python+TS+SQL bindings; (2) WASM can't load reliably across all four JS
> targets (Node/browser/Workers/Deno) without per-target hacks; (3) all-platform
> abi3 wheels can't be produced without recurring per-release firefighting (the
> #688 class); (4) measured wall shows the kernel path isn't at least neutral vs
> pure on real workloads. GO = all four clear on the levenshtein tracer →
> proceed to the scorers with the proven template.

## Consequence

### Evidence gathered THIS spike (in-env, real measured)

- **Native kernel BUILT in-env** (`python scripts/build_native.py` → 1m32s,
  `goldenmatch._native` v0.1.5, abi3 for cpython-311-x86_64-linux-gnu).
- **Kill-criterion (1), Python binding: PASS.** `check_kernel_equivalence.py`
  over 2028 pairs (random + adversarial: empty, unicode BMP/astral/combining,
  CJK, transpositions, case, 500-char) — pure-Python `score_field` vs the
  `score-core` kernel:
  - `levenshtein`: **max abs diff 0.000e+00** (bit-identical) — PASS at 4dp.
  - `jaro_winkler`: max abs diff 5.55e-17 (float epsilon) — PASS at 4dp.
  - `token_sort`: max abs diff 0.000e+00 — PASS at 4dp.
  Note this is unsurprising and *desirable*: pure-Python levenshtein already IS
  `rapidfuzz.Levenshtein.normalized_similarity`, and the kernel wraps rapidfuzz —
  the gate confirms the binding/scale/encoding chain doesn't perturb it.
- **Kill-criterion (4): PASS (in-env, conservative shape).** Per-pair median
  wall over 8000 name-shaped pairs (5 runs): pure rapidfuzz 2.80 ms (2.86M
  rec/s) vs kernel 1.94 ms (4.13M rec/s) → **kernel 1.44x faster** even on the
  per-pair boundary shape (the kernel's pessimal case; the shipped path batches
  NxN per block). "At least neutral" clears with margin.

- **Kill-criterion (1), TS binding: PASS (in-env).** The WASM artifact was BUILT
  in-env (`score-wasm/build_wasm.sh`, exit 0 — installed `wasm-bindgen-cli`
  0.2.125, emitted `score_wasm_bg.wasm` 115 KB) and the spike test
  `tests/spike/kernel-equivalence.test.ts` ran **un-skipped and GREEN**: pure-TS
  `levenshteinSimilarity` == the `score-wasm` kernel at 4dp over the adversarial
  corpus (identical / transposition / case / accented-BMP / CJK / 200-char). The
  existing `tests/parity/wasm-scorer.test.ts` also ran un-skipped (63 scorer +
  parity tests green). **As of #879 the pure-TS scorers were aligned to
  rapidfuzz**, which is why pure-TS ≈ WASM now holds.

### Evidence PENDING (needs CI / other env)
- **Kill-criterion (1), SQL binding:** the DataFusion UDFs + pg `kernels.rs`
  already link `score-core` (structural parity), but a runtime byte-equality
  gate for the SQL surface is not part of this spike — pending.
- **Kill-criterion (2): pending.** WASM-loads-on-all-four-JS-targets
  (Node/browser/Workers/Deno) is NOT tested here. The shared
  `goldenmatch-wasm-runtime` + the opt-in `enableWasm()` path exist and are
  CI-tested on Node; browser/Workers/Deno loading is unverified — a real risk
  item the roadmap flags as the TS-edge-safety hard constraint.
- **Kill-criterion (3): pending, and the known-hardest.** All-platform abi3 wheel
  production is the #688-class firefighting risk (rayon futex park; wheel/caller
  symbol skew; macos-13 runner queues; ort/openssl cross-container). The root
  CLAUDE.md documents an extensive history. This spike does NOT clear it — it is
  the dominant no-go risk and the reason the collapse must stay additive +
  reversible per step.

### Go/No-Go status after this spike

**(1) Python PASS + TS/WASM PASS, SQL pending • (2) partial — WASM loads in Node
in-env, browser/Workers/Deno pending • (3) pending (hardest) • (4) PASS.** The
levenshtein tracer's equivalence gate clears on BOTH the Python (native abi3) and
TS (WASM) bindings in-env, and the kernel is measurably faster — three of four
items have real positive evidence on the tracer. The two genuinely unverified
risks are (2) cross-JS-target WASM loading (only Node tested) and (3) all-platform
abi3 wheel reliability (the #688 class) — both load-bearing, both must be cleared
in CI before any flag flips. **Provisional verdict: proceed to R1 (universal
default-on bindings as the formal go/no-go gate) — do NOT yet collapse any
scorer.** The tracer template is proven end-to-end (Python+TS, equivalent +
faster); the platform-reliability gates are not.

### R1 evidence — PROBED, pending run (2026-06-14)

R1 stands the equivalence gate up *in more places* without flipping a default (see
[../architecture/single-kernel-collapse-R1-plan.md](../architecture/single-kernel-collapse-R1-plan.md)).
Workstream B adds a `workflow_dispatch` CI workflow,
[`.github/workflows/r1-kernel-wheels.yml`](../../.github/workflows/r1-kernel-wheels.yml),
that directly probes the two pending platform-reliability items:

- **Kill-criterion (3) — all-platform abi3 wheels:** **RESULT — PASS (GO-with-residual).**
  Run [#27509949013](https://github.com/benseverndev-oss/goldenmatch/actions/runs/27509949013)
  + the harness-fixed re-run [#27514052267](https://github.com/benseverndev-oss/goldenmatch/actions/runs/27514052267)
  (PR #971, which moved aarch64 to the native `ubuntu-24.04-arm` runner and marked
  macOS-x86_64 build-only): the abi3 wheel **builds on 5/5** platforms, and runtime
  `pure==kernel` at 4dp + bench `--assert-not-slower` **passed on 4/5** — linux-x86_64,
  **linux-aarch64 (native arm)**, macOS-arm64, windows-x64. macOS-x86_64 is build-only
  (no Intel-mac runner in this org; the x86 mac slice is sunsetting) — a documented
  residual, not a defect. No per-release firefighting was needed to produce any wheel.
- **The #688 perf cliff:** **RESULT — INFRA-BLOCKED (accepted 2026-06-14).** The
  `perf_cliff` job on `ubuntu-latest-xlarge` failed to allocate a runner on **both**
  attempts (queued 66 min, then 34 min, runner never assigned) — exactly the
  larger-runner stall the root CLAUDE.md documents. The EPYC-specific rayon-`LockLatch`
  futex scenario therefore **cannot be reproduced in this org's CI**. Per the maintainer
  decision, this is ACCEPTED as an infra gap (not a code finding): mitigated by the
  already-shipped `GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS` fix (#692) and by the kernel
  measuring **not-slower on every platform we could run** (4/5). If a self-hosted 8-core
  EPYC ever becomes available, re-dispatch with `cliff_runner` set to it.

**R1-B verdict: GO-with-residual.** Kill-criterion (3) is cleared for the four
mainstream arches; the macOS-x86_64 runtime gap and the un-reproducible #688 EPYC cliff
are documented *infra* residuals, not code blockers. The other R1 gate before any
default flip was **Workstream A** — cross-JS-target WASM, kill-criterion (2) — now
**PASS (GREEN)** (see the Workstream-A section + the R1 overall verdict below).

### R1 evidence — Workstream A: cross-JS-target WASM (kill-criterion 2), PROBED 2026-06-14

Workstream A adds a second `workflow_dispatch` CI workflow,
[`.github/workflows/r1-kernel-js-targets.yml`](../../.github/workflows/r1-kernel-js-targets.yml),
that builds the `score-wasm` kernel once and runs the SAME pure-TS-vs-kernel 4dp
equivalence assertion (the spike's `tests/spike/kernel-equivalence*` arm, factored
into a runtime-agnostic `kernel-equivalence-core.ts` + a frozen
`fixtures/pure-ts-reference.json`) under all four JS runtimes — node (vitest),
deno (`deno test`), browser (vitest browser-mode / Playwright chromium), and
workers (`@cloudflare/vitest-pool-workers` / workerd). It is ADDITIVE + dispatch-only;
flips no default (the TS WASM path stays opt-in via `enableWasm()`; pure-TS stays the
default + fallback), deletes nothing, and builds the same crate the `wasm_score` lane
builds.

- **Universal loader (the A1 decision): base64-INLINE (Option i).** Implemented behind
  the existing opt-in seam as `enableWasm({ universal: true })` — resolves the artifact
  from a generated `score_wasm_base64.js` module (emitted by `build_wasm.sh`, gitignored
  like the `.wasm`), decoded via `decodeWasmBase64` in `goldenmatch-wasm-runtime` (`atob`
  where present, else Node `Buffer`). No fetch/fs/`import.meta.url`-relative asset
  resolution — the only path that loads edge-safe in Workers + Deno + every bundler. Cost:
  base64 is ~+33% over the raw `.wasm` (115,155 B → a 153,540-B string). The DEFAULT
  `enableWasm()` path (URL/fs/fetch) is unchanged; default users load zero wasm bytes.
  Trade-off note: [`docs/superpowers/notes/2026-06-14-wasm-universal-loader.md`](../../docs/superpowers/notes/2026-06-14-wasm-universal-loader.md).

- **Per-target status — kill-criterion (2):**

  | target | runtime | how it loads the kernel | status |
  |---|---|---|---|
  | node | vitest (Node 22) | bytes via fs (default loader) | **PASS** (spike; re-run in-env GREEN) |
  | deno | `deno test` (Deno 2.8) | universal base64 + `atob` | **PASS (RAN-GREEN in-env)** — 1200 comparisons, max abs diff 4.8e-7 |
  | browser | vitest browser-mode, chromium (Playwright) | universal base64 + `atob` | **PASS (RAN-GREEN in-env)** — real chromium; fault-injection confirmed it compares |
  | workers | vitest-pool-workers (workerd) | build-time CompiledWasm `.wasm` import | **PASS (RAN-GREEN in CI)** — workerd, real run; see finding |

  All four legs green in CI run
  [#27518182208](https://github.com/benseverndev-oss/goldenmatch/actions/runs/27518182208)
  (workflow_dispatch on `main` @ `40fbace2`): node / deno / browser / **workers** each
  build the `score-wasm` kernel and reproduce the frozen pure-TS reference at 4dp.

- **Workers FINDING (load-bearing for the design).** The pool RUNS in real workerd and
  surfaced a genuine Workers constraint: **workerd BANS runtime WASM codegen** — both
  `WebAssembly.instantiate(bytes)` AND the synchronous `new WebAssembly.Module(bytes)`
  constructor throw *"Wasm code generation disallowed by embedder"*. So the base64-bytes
  universal path that clears node/browser/deno does NOT work on Workers; the Workers-legal
  path is a BUILD-TIME CompiledWasm `.wasm` import (the pool compiles the module at deploy
  time). That is the one supported Workers mechanism — NOT a per-target *hack*: same
  wasm-bindgen glue, same `runEquivalence` comparator, only the module hand-off differs.
  HARNESS NOTE: the first dispatch ([#27515943709](https://github.com/benseverndev-oss/goldenmatch/actions/runs/27515943709))
  red'd because the static `.wasm?module` import tripped the HOST vite `import-analysis`
  before the pool resolved it (0 tests collected); fixed in #977 by importing a plain
  `.wasm` + `assetsInclude: ["**/*.wasm"]` (`server.deps.inline` only covers node_modules).
  SIGNAL for the eventual TS default-flip scope — a Workers consumer must ship the kernel as
  a build-time CompiledWasm module, not the `{ universal: true }` base64 path.

**R1-A verdict: PASS (GREEN).** All four JS targets (node / deno / browser / workers)
load the `score-core` kernel and reproduce pure-TS at 4dp in CI. node/deno/browser clear
via the ONE universal base64 loader with no per-target hack; **workers clears via the
build-time CompiledWasm module** — a documented, supported per-target *load mechanism*
(workerd's codegen ban makes it mandatory), not a per-target hack. Kill-criterion (2) is
cleared, with that Workers load-form nuance recorded as the single R1-A residual.

### R1 OVERALL VERDICT (2026-06-15): GO to R2, two documented residuals

With **R1-A: PASS (GREEN)** (this section) and **R1-B: GO-with-residual** (the wheels
section above), all four kill-criteria now have real positive evidence on the levenshtein
tracer: (1) pure==kernel 4dp across Python + TS/WASM (node/deno/browser/workers) — PASS;
(2) cross-JS-target WASM — PASS (GREEN); (3) all-platform abi3 wheels — PASS for the four
mainstream arches; (4) measured wall not-slower — PASS (kernel 1.44× faster + not-slower on
every platform run). **R1 is GO.** The two carried residuals are both *infra/mechanism*, not
code blockers: (a) macOS-x86_64 wheel is build-only (no Intel-mac runner in this org;
sunsetting arch); (b) a Workers kernel consumer must use the build-time CompiledWasm module
form, not the base64 universal loader. Per the reversibility commitment, R2 (collapse the
first scorer behind a reversible default-flip flag) may proceed — still additive, parity-gated,
one reversible flag per step.

### R2 (2026-06-15): executed first slice + a value RECALIBRATION

Executing R2 on the levenshtein tracer surfaced a material recalibration of the whole
collapse, and two of the four "pending" items turned out to be already-resolved or
must-hold. Recorded here so R3+ is scoped to reality, not the original framing.

**The recalibration (the reward is narrower than "delete N reimplementations"):**
- **Python is already collapsed.** The default polars-direct path
  (`_fuzzy_score_matrix` → `_native_field_matrix`) already prefers the `score-core`
  kernel over rapidfuzz for jaro_winkler / levenshtein / token_sort / exact /
  soundex_match when the wheel is importable; parity is already gated
  (`test_native_field_matrix_parity.py`). R2 on Python is governance, not a risky flip.
- **TS cannot flip default.** WASM stays opt-in for edge-safety, so the pure-TS scorer
  MUST remain the default + fallback. R2 doesn't reduce TS maintenance.
- **The pure paths are load-bearing fallbacks** (Python no-wheel installs; TS always),
  so **R5 "decommission" cannot delete the default-path scorers.** Realistic end-state:
  *kernel = governed canonical fast path; pure = fallback; the parity harnesses stay as
  kernel-vs-pure equivalence gates* (not cross-language reimplementation gates).
  **R5 (deletion) is retired.**

**R2 first slice — SHIPPED (#980):** brought field scoring under the reversible
`GOLDENMATCH_NATIVE` gate. `_native_field_matrix` previously checked only
`native is not None`, so `GOLDENMATCH_NATIVE=0` did NOT force the pure path — a latent
reversibility gap. Now it consults `native_enabled("field_scoring")` (`=0` forces pure,
`=1` requires native, `auto` uses native iff signed off), and `field_scoring` is in
`_GATED_ON` so the default is unchanged. Output is byte-identical (proven); only WHICH
path runs is now reversible + telemetered. Verified 138/138 scorer + 11/11 field-matrix
parity (incl. 3 new gate tests).

**Kill-criterion (1) SQL surface — was NOT pending; it is GATED (correction to the R1
verdict above).** `tests/test_datafusion_ffi_udf.py::test_ffi_string_scorers_match_rapidfuzz`
already stands up a real DataFusion `SessionContext`, registers the FFI scalar UDFs, runs
`SELECT jaro_winkler/token_sort/levenshtein(a,b)` and asserts each == rapidfuzz (the pure
lib) at 1e-6 (token_sort `/100` accounted). It runs LIVE in CI (`ci.yml` installs
`datafusion>=53,<54`, builds + installs the `goldenmatch_datafusion_udf` wheel, hard-imports
it). So **all three bindings — Python, TS/WASM, SQL — have a live runtime `==pure` gate**;
kill-criterion (1) is fully cleared, not partial.

**`pprl_bloom` — investigated for the R2 governance set, deliberately HELD default-off.**
Parity battery green (26 tests) and the kernel is **7.08× faster, byte-identical output**
(measure-first). BUT `native/src/bloom.rs` fans out with an **unconditional
`prepared.par_iter()`** — no `GOLDENMATCH_NATIVE_RAYON_MIN_*` threshold guard, unlike the
#692 fix in `score.rs`. That is the exact **#688 rayon-`LockLatch` futex-park class**, and
it reproduces only on the 8-core EPYC `ubuntu-latest-xlarge` runner that **R1-B confirmed
won't provision** — so it cannot be cleared in this org's CI. Flipping `pprl_bloom` into
`_GATED_ON` would ship an unvalidatable #688-class risk; it stays default-off (`=1` opt-in)
until the kernel gets the #692-style calling-thread-below-threshold guard (a kernel change +
wheel republish, NOT a cheap follow-up).

**R2 verdict:** the concrete win (governed reversibility on field scoring + the confirmation
that all three bindings are gated) is banked; the grand R3–R5 program is reframed to
"govern any remaining ungoverned kernel defaults; keep parity gated" with **deletion (R5)
retired** and `pprl_bloom`'s flip blocked on the unguarded-rayon fix.

Two backward-compatible script flags back the gate: `--require-kernel`
(kernel-absence → exit 1) and `--assert-not-slower` (perf cliff → exit 1); with
neither flag the scripts keep the spike's skip-on-absent / exit-0 default. NO
default path or Rust source was touched; the workflow is workflow_dispatch-only.
Kill-criterion (2) (cross-JS-target WASM) is scoped to R1 Workstream A, harness
pending.

### The reversibility commitment

Every step stays additive until R5; one reversible flag per step; the parity
gate must pass before any default flips; measure-first on real workloads. If any
kill-criterion item fails at its stage, the collapse STOPS and the parity-harness
status quo stands.
