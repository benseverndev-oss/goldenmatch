# Single-Kernel-Collapse — R1 Plan of Record (the go/no-go gate)

**Status:** R1 in progress • **Decision:** [../decisions/0016-single-kernel-collapse-spike.md](../decisions/0016-single-kernel-collapse-spike.md) • **Roadmap:** [single-kernel-collapse-roadmap.md](single-kernel-collapse-roadmap.md) • **Inventory:** [single-kernel-collapse-inventory.md](single-kernel-collapse-inventory.md)

R0 (the spike) proved the levenshtein tracer's equivalence template END-TO-END
in-env (Python native abi3 bit-identical; TS WASM 4dp on Node) and showed the
kernel measurably faster. It left the two **platform-reliability** kill-criterion
items unverified, and those are load-bearing. **R1 is the formal go/no-go gate:**
take the proven tracer and stand the equivalence gate up *in more places* — across
every binding and every platform — without flipping any default. If the four
kill-criterion items clear here, the collapse proceeds (R2+); otherwise it STOPS
and the parity-harness status quo stands.

R1 splits into two independent workstreams, each addressing one structural risk.

## The R1 rule — additive, no default flip

R1 changes **no default path**, deletes nothing, and flips no flag. Everything it
adds is *compare-only* infra (CI workflows, a generalized gate run in more places,
go/no-go reports). The native path stays default-OFF/gated (`GOLDENMATCH_NATIVE`);
the TS WASM path stays opt-in (`enableWasm()`). A default flips ON only in R2+,
and only after that algorithm's equivalence gate is green at 4dp/byte across every
binding in CI (the roadmap's parity-gate-before-flip rule). R1's job is to PROVE
the gates can be green, not to act on them.

## Workstream A — WASM across all four JS targets (kill-criterion 2)

**Goal.** Show the `score-core` WASM artifact loads + runs the equivalence gate on
**Node, browser, Cloudflare Workers, AND Deno** with no per-target hacks — the TS
edge-safety hard constraint. R0 verified Node only (vitest, in-env); the other
three targets are unverified and are kill-criterion (2).

**Verification design.** Run the SAME pure-TS-vs-WASM 4dp equivalence assertion
(factored from `tests/spike/kernel-equivalence.test.ts` into a runtime-agnostic
`tests/spike/kernel-equivalence-core.ts` + a frozen `fixtures/pure-ts-reference.json`)
under each runtime: Node (vitest), a headless browser (vitest browser-mode /
Playwright chromium), a Workers harness (`@cloudflare/vitest-pool-workers` / workerd),
and Deno (`deno test`). A per-target shim or polyfill needed anywhere is a RED flag
(it's exactly the "per-target hacks" the kill-criterion forbids).

**The universal loader (A1 decision): base64-INLINE (Option i)**, behind the
existing opt-in seam — `enableWasm({ universal: true })` resolves the artifact from
a generated `score_wasm_base64.js` module (no fetch/fs/`import.meta.url` asset
resolution), the only path edge-safe across Workers + Deno + every bundler. Cost:
base64 ~+33% over the raw `.wasm` (measured: 115,155 B → a 153,540-B string). The
default `enableWasm()` path is unchanged; default users load zero wasm bytes.
Trade-off note: [`../../docs/superpowers/notes/2026-06-14-wasm-universal-loader.md`](../../docs/superpowers/notes/2026-06-14-wasm-universal-loader.md).

**Per-target evidence.** A pass/fail row per runtime: artifact loaded? gate 4dp
green? any target-specific code path required? Plus the runtime/version used.

**Kill checkpoint.** If any target can't load the artifact without a per-target
hack, kill-criterion (2) FAILS → the TS half of the collapse STOPS (pure-TS stays
the permanent default + fallback). Node-only is not sufficient to flip the TS
default.

> Status: **SHIPPED as additive, workflow_dispatch-only infra** —
> [`.github/workflows/r1-kernel-js-targets.yml`](../../.github/workflows/r1-kernel-js-targets.yml)
> (jobs: node / deno / browser / workers; each writes a per-target PASS/FAIL to the
> step summary). In-env: node/deno/browser RAN-GREEN via the universal base64 loader
> with no per-target hack; the workers job surfaced a real constraint — **workerd bans
> runtime WASM codegen**, so Workers needs a BUILD-TIME CompiledWasm `.wasm` import
> (not the base64 path), green run pending the dispatched `workers` job (pins the
> pool/vitest versions). See ADR 0016's R1-A evidence section for the full table +
> the Workers finding. (Workstream B shipped earlier as `r1-kernel-wheels.yml`.)

## Workstream B — all-platform abi3 wheels + the #688 perf cliff (kill-criterion 3) — THIS CHANGE

**Goal.** Show the `goldenmatch-native` abi3 wheel can be (i) BUILT on every
platform the suite must support, (ii) imported + CORRECT (pure==kernel at 4dp) on
a clean interpreter, and (iii) PERFORMANT (the kernel is at least neutral vs pure
— no #688-class cliff) — *without* the recurring per-release firefighting that is
kill-criterion (3). This is the dominant no-go risk: root `CLAUDE.md` documents an
extensive #688 history (rayon `LockLatch` futex park, wheel/caller symbol skew,
`macos-13` runner queues, `ort`/openssl cross-container).

**Verification design.** A `workflow_dispatch` workflow,
[`.github/workflows/r1-kernel-wheels.yml`](../../.github/workflows/r1-kernel-wheels.yml),
with two job groups:

- **`wheels` matrix** over linux x86_64 (manylinux 2_28), linux aarch64, macOS
  arm64 (`macos-14`), macOS x86_64 (`macos-14` cross), and windows x64. Each leg
  builds the abi3 wheel with the same SHA-pinned `PyO3/maturin-action` + manifest
  as `publish-goldenmatch-native.yml` (`--release`, abi3-py311), then on a clean
  Python 3.11 installs the built wheel + the pure `goldenmatch` package and runs:
  - `scripts/check_kernel_equivalence.py --require-kernel` — pure==kernel at 4dp,
    but **FAIL (not skip)** if the just-built wheel isn't importable, since the
    whole point is exercising it;
  - `scripts/bench_kernel_levenshtein.py --require-kernel --assert-not-slower` —
    the kernel must not be slower than pure beyond a small tolerance.

  `fail-fast: false` so one platform's failure doesn't hide the others. A
  per-platform PASS/FAIL + wall-ratio line goes to the job summary. (The
  cross-built linux-aarch64 wheel isn't runnable on the x86 runner, so that leg is
  build-only — a successful aarch64 *build* is itself the kill-criterion-(3)
  signal that the manylinux aarch64 wheel is producible.)

- **`perf_cliff` job — THE #688 probe (highest signal).** Runs on
  `ubuntu-latest-xlarge` (the 8-core AMD EPYC shape #688 wedged on 100% — NOT
  `large-new-64GB`/16c, NOT plain `ubuntu-latest`; parameterized via the
  `cliff_runner` input for easy retargeting). Builds the kernel and runs the
  per-pair bench (`--assert-not-slower`) AND the #688 repro harness
  (`scripts/bench_issue_688.py`) on the kernel's default path, asserting the
  kernel does NOT regress into a multi-minute rayon futex park (the 60-min job cap
  turns a true wedge into an actionable failure, not a silent hang).

**Per-target evidence.** Per-platform: wheel built? gate 4dp green? perf
not-slower? + the wall ratio. From the cliff job: the per-pair kernel/pure ratio
and the #688 dedupe wall (pass = sub-minute, no park) on the wedge runner.

**Kill checkpoint.** If wheels can't be produced on all platforms without
firefighting, OR the kernel regresses into the #688 cliff on the EPYC shape,
kill-criterion (3) FAILS → the Python half of the collapse STOPS (the native path
stays default-OFF/gated). Boringly-reliable wheel production is the precondition
for ever flipping the Python default.

> The CLAUDE.md caveat is carried in the workflow as a comment: brand-new
> GitHub-hosted larger runners can take 30-60 min to provision and SOMETIMES STALL
> allocation entirely (jobs sit "Ready" / queue forever). A slow or queued
> `perf_cliff` job is EXPECTED — retarget via `cliff_runner` or re-dispatch; do
> NOT mistake a stall for a build failure.

## Cross-cutting — one generalized gate, run in more places

R1 deliberately does NOT write new gates per binding. It runs the SAME equivalence
gate (`check_kernel_equivalence.py`, scorer-name-parameterized; the TS
`kernel-equivalence.test.ts` arm) in more environments: Python on five wheel
platforms, TS on four JS runtimes. The spike added the two backward-compatible
flags this requires — `--require-kernel` (turn kernel-absence into a failure, so a
build leg can't pass by skipping) and `--assert-not-slower` (turn a perf cliff
into a non-zero exit). Default behavior is preserved: with neither flag the scripts
skip-on-absent and exit 0, so the spike's standalone-report use is intact.

The intended R1 output is a **`kernel-targets` go/no-go report** — one table,
per-target rows (Python: 5 platforms × {built, 4dp, not-slower}; TS: 4 runtimes ×
{loaded, 4dp}; plus the #688 cliff result) — that maps directly onto the ADR's
Go/No-Go evidence rows. When every target is green, R1 clears and the collapse may
proceed to R2 (collapse the scorers) under the existing reversible-flag rules.

## Expected outcome — honestly PARTIAL

The likely R1 outcome is **PARTIAL, not a clean all-green**. The realistic landing
spot is *kernel-default where the platform reliably supports it + a thin pure
fallback where it can't run* — e.g. the native kernel default-on for the platforms
whose wheels build + pass boringly, WASM default-on for the JS targets that load
cleanly, and the pure path retained as the permanent fallback for the rest. R1's
value is making that boundary EVIDENCE-BASED rather than assumed: the workflows
say which platforms/targets clear, so the R2 default-flip is scoped to exactly
those, and the parity-harness status quo stands for the remainder.
