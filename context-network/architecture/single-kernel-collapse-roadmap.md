# Single-Kernel-Collapse — Roadmap (R0–R5)

**Status:** R2–R4 substantially LANDED, **R5 remaining** (reconciled 2026-08-04) • **Decision:** [../decisions/0016-single-kernel-collapse-spike.md](../decisions/0016-single-kernel-collapse-spike.md) • **Inventory:** [single-kernel-collapse-inventory.md](single-kernel-collapse-inventory.md)

> **Reconciliation note (2026-08-04):** far ahead of the per-stage text below. The own-string
> primitives shipped and rapidfuzz was fully evicted suite-wide (#2159, #2218), as were
> scipy/jellyfish/faiss/diptest/dateutil; the shared kernels were realized as their own published
> products — **goldenfuzz**, **goldenphonetic**, **goldenhnsw**. R2 (scorers) and much of R3/R4 are
> done; only **R5** (decommission dead duplicated math + the ast-grep gate forbidding algorithm math
> outside `*-core`) genuinely remains. The stage text below is retained as the original plan of record.

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

> **R5 status (2026-08-07): the GATE has landed; the DELETION half is mostly a
> no-op by design.** Two findings from doing the work:
>
> 1. **"Delete the duplicated math" cannot mean deleting the pure ports.** Hard
>    constraint 1 above makes pure-TS the *permanent* fallback (cross-target WASM
>    is still unverified), and the architecture frame classifies pure-Python /
>    pure-TS as conformance-tested **fallbacks**, not dead code. So `scorer.ts`'s
>    `levenshtein`/`jaro`/… are deliberate and STAY. The only genuinely dead math
>    is a *second* copy of a primitive that already has a sanctioned home.
> 2. **The gate is the durable deliverable.** `ts-no-duplicate-kernel-math`
>    (`.ast-grep/rules/`) flags a hand-rolled kernel-backed primitive declared
>    outside the sanctioned fallback modules, which are allow-listed. It ships
>    **warning**-severity (per the repo's gradual-landing convention) because its
>    current findings ARE the consolidation backlog: goldencheck
>    `fuzzy-values.ts`, goldenflow `phonetic.ts`, infermap `string-distance.ts`
>    (×3). Promote to **error** once those are consolidated — that is the
>    remaining R5 work, and it is cross-package (each needs a dependency
>    decision), not a mechanical delete.
>
> **R5 consolidation — infermap DONE (2026-08-07), 5 findings → 2.** infermap's
> vendored `jaroSimilarity`/`jaroWinklerSimilarity`/`levenshteinDistance` now
> alias goldenmatch's. This was the cheap one: infermap already depended on
> goldenmatch, and its *Python* `fuzzy_name` already reuses
> `goldenmatch-score-core::jaro_winkler_similarity`, so the TS fork was the only
> surface still running its own math — a Python↔TS parity gap, not just
> duplication.
>
> It also validated the thesis empirically. The fork carried **all three**
> pre-#879 bugs goldenmatch had already fixed: unfloored `t/2` transposition
> (`jaroWinkler("saturday","sunday")` 0.7475 → 0.7775, the exact pair #879
> cites), the `>= 0.7` vs strict `> 0.7` Winkler threshold, and UTF-16 code-unit
> instead of codepoint iteration (57 jaro/JW + 62 levenshtein disagreements in a
> 75.7K-pair sweep; zero on ASCII-only, so the blast radius was non-BMP input
> plus the transposition class). A second copy does not stay in sync — it
> silently misses the fixes the first one ships.
>
> **The mechanism, for the two remaining packages.** `core/scorer.ts` is 1794
> lines and transitively pulls the reference-data tables and the WASM registry,
> so "just import goldenmatch/core" is a disproportionate payload for three edit
> -distance functions. The primitives were extracted into a **zero-import leaf**
> `goldenmatch/src/core/stringDistance.ts`, published as the
> `goldenmatch/core/string-distance` subpath; `core/scorer.ts` re-exports them,
> so no existing import or public API changed. infermap keeps goldenmatch as a
> **devDependency** and tsup inlines the leaf (`noExternal`) — the established
> `goldenmatch-wasm-runtime` pattern — so no published runtime dep is added.
> **Gotcha:** tsup's `dts.resolve` will not follow a subpath specifier, so a bare
> `export … from` leaks an unresolvable `from 'goldenmatch/core/string-distance'`
> into the published `.d.ts`. Re-export as type-annotated `const` aliases
> instead; that emits self-contained declarations.
>
> **The remaining two findings are NOT the same problem — audited 2026-08-07.**
> An earlier draft of this note framed goldencheck `fuzzy-values.ts` and
> goldenflow `phonetic.ts` as needing "a dependency decision (import goldenmatch
> vs. a new shared TS primitives package)". **That framing was wrong.** Neither
> is a rogue copy of goldenmatch's math; both are their OWN package's sanctioned
> parity-gated pure-TS fallback — the same category as `goldenmatch`
> `core/scorer.ts`, which this rule already ignores:
>
> * goldencheck `fuzzy-values.ts` mirrors `goldencheck-core::fuzzy`, and the
>   profiler already routes to `getGoldencheckWasmBackend()?.nearDuplicateClusters`
>   when the wasm backend is enabled, falling back to the local `levRatio`.
> * goldenflow `phonetic.ts` says so in its own header: "Pure-TS reference for
>   goldenflow-core's `phonetic` kernels; MUST reproduce the Rust/Python bytes."
>
> So the correct action for the TS layer is to **allow-list both** (they are the
> permanent edge-safe fallback surface, per hard constraint 1) and promote the
> rule to `error` — not to consolidate them onto goldenmatch.
>
> **Two rule defects the audit found.** (1) In goldenflow the rule flags
> `function soundex(values: readonly ColumnValue[])`, which is a one-line
> `values.map(...)` wrapper; the actual algorithm is `soundexTs`, which the name
> regex does NOT match. So the rule is matching series-wrapper *names* rather
> than algorithm bodies. (2) More generally the regex cannot tell "second copy of
> someone else's kernel" from "this package's own parity-gated fallback" — the
> distinction that actually matters. Both argue for allow-listing by module and
> keeping the rule as a *reintroduction* guard, which is what it is good at.
>
> ## The real duplication is one layer DOWN, in Rust
>
> The suite already owns both algorithms as standalone published kernels —
> **`goldenfuzz-core`** (levenshtein / jaro-winkler / token ratios, Myers
> bit-parallel) and **`goldenphonetic-core`** (soundex / metaphone / nysiis,
> jellyfish-compatible). But the sibling cores do not all use them:
>
> | Consumer | Uses the shared kernel? | |
> |---|---|---|
> | `score-core` | **yes** — `goldenfuzz-core` path dep | the model case |
> | `goldencheck-core` | no — private `fn levenshtein` in `fuzzy.rs` | redundant |
> | `goldenflow-core` | no — own `phonetic::soundex` | **divergent** |
>
> * **goldencheck's levenshtein is redundant but semantically equivalent** —
>   classic two-row DP vs goldenfuzz's Myers bit-parallel, and its `similarity`
>   is the same `1 - dist/max(len)` formula as
>   `levenshtein_normalized_similarity`. Same answers, slower algorithm. Low
>   urgency; the win would be speed + one owner.
> * **goldenflow's soundex genuinely DIVERGES from goldenphonetic's**, and
>   nothing guards it. Measured 2026-08-07 by running both crates on one corpus
>   (2 of 16 cases differ):
>
>   | input | `goldenphonetic-core` | `goldenflow-core` | cause |
>   |---|---|---|---|
>   | `T-t` | `T300` | `T000` | non-letter: goldenphonetic resets the run, goldenflow filters it out |
>   | `Ünal` | `U540` | `N400` | non-ASCII: goldenphonetic NFKD-folds `Ü`→`U`, goldenflow drops it |
>
>   Both are defensible readings of "Soundex", but they are two different
>   functions with one name, and a record standardized by goldenflow will not
>   phonetically match the same record encoded by goldenphonetic. That is the
>   exact failure class R5 exists to prevent, and it is invisible to the TS-only
>   ast-grep rule.
>
> **Neither `goldenfuzz-core` nor `goldenphonetic-core` has a `-wasm` crate**
> (only `-py`), so there is no TS path to them today; a TS consolidation onto
> them would mean a new wasm crate + loader + parity fixture + CI lane each.
> That cost is not justified by the TS findings above (which are legitimate
> fallbacks) — but it may be justified by the Rust divergence, which should be
> resolved in Rust first. Recommended order: (1) allow-list the two TS fallbacks
> and promote the rule to `error`; (2) decide the goldenflow-vs-goldenphonetic
> soundex semantics and make one of them the owner; (3) treat goldencheck's
> levenshtein redundancy as an opportunistic perf/ownership cleanup.
>
> Also done: the one *intra*-package duplicate was removed — goldenmatch
> `core/indicators.ts` carried its own Levenshtein DP used solely by
> `tokenSortRatio`; it now calls the exported `levenshteinSimilarity` from
> `core/scorer.ts` (same package, no new dep). That copy was UTF-16 code-unit
> based while the scorer's is codepoint-aware (`Array.from`), so the dedup also
> fixes a latent non-BMP divergence from the Python/Rust reference.
>
> Note the preconditions in the stage text are still only partly met (kill-criteria
> 1c/2/3 below remain PENDING), which is exactly why nothing default-flipped and
> nothing in the fallback surface was deleted here.

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
