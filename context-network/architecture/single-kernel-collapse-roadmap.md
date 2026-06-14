# Single-Kernel-Collapse — Roadmap (R0–R5)

**Status:** Spike • **Decision:** [../decisions/0016-single-kernel-collapse-spike.md](../decisions/0016-single-kernel-collapse-spike.md) • **Inventory:** [single-kernel-collapse-inventory.md](single-kernel-collapse-inventory.md)

Staged plan for collapsing the suite's N duplicated algorithm implementations
toward one shared Rust `*-core` kernel. **Additive until R5; one reversible flag
per step; parity-gate-before-flip; measure-first.** Any kill-criterion failure at
its stage STOPS the collapse and keeps the parity-harness status quo.

## Stages

### R0 — Duplication inventory (THIS SPIKE, done)
Read-only census of every algorithm in ≥2 implementations, tagged
`kernelizable-hot` vs `orchestration-glue` and ranked ROI×inverse-risk. Output:
`single-kernel-collapse-inventory.md`. Zero risk. **Done.**

### R1 — Universal default-on bindings = THE GO/NO-GO GATE (next)
Take the proven tracer (levenshtein) and stand up the equivalence gate across
**all** bindings as a required CI gate: Python (`--require-kernel`), TS WASM
(`wasm_score` lane, un-skipped), and the SQL/FFI surface. Verify WASM loads on
**all four JS targets** (Node/browser/Workers/Deno) and that all-platform abi3
wheels build without per-release firefighting. **This stage IS the go/no-go**: if
the four kill-criterion items all clear here, proceed; otherwise STOP. No default
flips — bindings stay opt-in (`GOLDENMATCH_NATIVE`, `enableWasm()`).

### R2 — Collapse the scorers
With the template proven, retire the duplicated scorer math: pure-Python and
pure-TS scorers delegate to `score-core` (via the existing native/WASM bindings)
behind one reversible flag each, parity-gated. The hand-rolled TS scorers are the
biggest win (they have historically drifted from rapidfuzz). Pure fallback stays.

### R3 — Transforms / fingerprint / Fellegi-Sunter / PPRL
The next ROI tier (inventory ranks 2, 4, 5, 6). Fingerprint (`fingerprint-core`,
byte-exact, already gated ON) is the safe lead; Fellegi-Sunter (1998+819 LOC, no
core crate yet) is the largest LOC win but needs an `fs-core` crate and carries
float sensitivity — sequence it after a `transform-core` is shaken out.

### R4 — Clustering / graph hot loops
Collapse the `graph-core` primitives (connected-components, union-find, MST
split) under the per-language policy layers (the policy — oversized-split,
confidence scoring — stays per language; only the hot loop collapses). Already
gated ON for `clustering`/`pairs`; this stage makes the pure copies thin.

### R5 — Decommission + ast-grep gate
Remove the now-dead duplicated math and add an `ast-grep` (or equivalent) CI gate
forbidding algorithm math outside `*-core` (e.g. a hand-rolled levenshtein DP loop
in `scorer.ts`). This is the only DELETING stage; it lands only after every prior
stage's flag has been default-on and stable for a full release cycle.

## The two hard constraints

1. **TS edge-safety.** `src/core/**` must stay edge-safe (no `node:*`), and WASM
   must load on Node, browser, Cloudflare Workers, AND Deno. The shared
   `goldenmatch-wasm-runtime` + opt-in `enableWasm()` exist and are CI-tested on
   Node only today. **Cross-target WASM loading is kill-criterion (2) and is
   UNVERIFIED** — the collapse cannot flip the TS default until all four targets
   load without per-target hacks. Pure-TS stays the permanent fallback.
2. **Python wheel reliability.** All-platform abi3 wheels must build without the
   recurring #688-class firefighting (rayon futex park; wheel/caller symbol skew;
   `macos-13` runner queues; `ort`/openssl cross-container). This is
   kill-criterion (3), the dominant no-go risk, and the reason the native path
   ships default-OFF/gated today. The collapse cannot flip the Python default
   until wheel production is boringly reliable.

## The four rules (apply at every stage)

- **Additive until R5.** No default path changes, nothing deletes, until the
  final decommission — and only after a full stable release cycle on default-on.
- **One reversible flag per step.** Each collapse hides behind a single flag
  (`GOLDENMATCH_NATIVE`, `enableWasm()`, or a new per-stage gate) that restores
  the pure path byte-for-byte. No flag-day big-bang.
- **Parity-gate-before-flip.** A default flips ON only after the equivalence gate
  for that algorithm passes at 4dp/byte across every binding in CI.
- **Measure-first.** Wall-clock the real (batched) workload before designing or
  flipping — per the performance-audit lesson (static counts mislead; cProfile
  cumtime ≠ wall; compare 5-run median wall on real shapes).

## Go/No-Go evidence

Each kill-criterion item mapped to what THIS spike gathered vs what is pending.

| # | Kill-criterion item | Evidence | Status |
|---|---------------------|----------|--------|
| 1a | pure==kernel 4dp — **Python** binding | `check_kernel_equivalence.py`: levenshtein max diff 0.0, jaro_winkler 5.5e-17, token_sort 0.0 over 2028 pairs (built `_native` v0.1.5 in-env) | **GATHERED — PASS** |
| 1b | pure==kernel 4dp — **TS/WASM** binding | WASM artifact BUILT in-env (`build_wasm.sh`, exit 0, `score_wasm_bg.wasm` 115 KB); `tests/spike/kernel-equivalence.test.ts` ran **un-skipped GREEN** (pure-TS == WASM at 4dp); existing `wasm-scorer.test.ts` also green un-skipped (63 tests) | **GATHERED — PASS (Node)** |
| 1c | pure==kernel 4dp — **SQL/FFI** binding | DataFusion UDFs + pg `kernels.rs` link `score-core` (structural parity by construction); no runtime byte-equality gate built this spike | **PENDING** |
| 2 | WASM loads on **all four JS targets** (Node/browser/Workers/Deno) without per-target hacks | WASM loaded + ran in **Node** in-env (vitest); browser/Workers/Deno unverified | **PARTIAL — Node PASS, 3 targets PENDING (hard constraint)** |
| 3 | all-platform **abi3 wheels** without #688-class firefighting | not tested this spike; extensive #688/wheel-skew/ort history in root CLAUDE.md | **PENDING (dominant no-go risk)** |
| 4 | measured wall — kernel **at least neutral** vs pure on real workloads | `bench_kernel_levenshtein.py`: kernel **1.44x faster** (4.13M vs 2.86M rec/s) on per-pair shape (kernel's pessimal case; shipped path batches NxN) | **GATHERED — PASS** |

**Summary:** items (1a Python), (1b TS/WASM-on-Node) and (4) PASS in-env with
margin; (1c SQL byte-gate), (2 browser/Workers/Deno), (3 all-platform wheels)
pending. The two structural risks — cross-JS-target WASM and all-platform wheels
— remain unverified and load-bearing. **The tracer template is proven end-to-end
(Python + TS, equivalent AND faster); the platform-reliability gates are not.
Proceed to R1 to clear them in CI before any default flips.**
