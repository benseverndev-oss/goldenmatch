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

- **Kill-criterion (3) — all-platform abi3 wheels:** PROBED BY `r1-kernel-wheels.yml`
  `wheels` matrix (linux x86_64 manylinux 2_28 / linux aarch64 / macOS arm64 / macOS
  x86_64 cross / windows x64) — each leg builds the abi3 wheel (same SHA-pinned
  `maturin-action` + manifest as `publish-goldenmatch-native.yml`) and runs the
  equivalence gate `--require-kernel` + the bench `--assert-not-slower` on a clean
  Python 3.11. **Status: PENDING-RUN** (the workflow is committed but not yet
  dispatched; the parent runs it and collects the per-platform PASS/FAIL).
- **The #688 perf cliff:** PROBED BY the `perf_cliff` job on `ubuntu-latest-xlarge`
  (the 8-core AMD EPYC shape #688 wedged on; `cliff_runner`-parameterized) — runs the
  per-pair bench AND `scripts/bench_issue_688.py`, asserting the kernel does not
  regress into the rayon `LockLatch` futex park. **Status: PENDING-RUN.**

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
