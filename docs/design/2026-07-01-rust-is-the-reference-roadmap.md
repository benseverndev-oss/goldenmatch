# Rust is the reference — migration roadmap

Date: 2026-07-01
Status: direction decided (Rust-reference, Python = lossy fallback); sequencing proposed
Supersedes: the "keep the allow-list" decision in
`2026-07-01-native-gate-audit-and-goldenflow-parity-design.md` (that call was correct under
the *Python-reference* model this roadmap replaces).

## Decision

The Rust kernel is the **reference implementation** of each primitive. Python is the binding
+ orchestration layer that calls it. The pure-Python kernels are kept **only** as a
non-authoritative, explicitly-lossy **fallback** for platforms without a published wheel.
They are allowed to differ from Rust; Rust output is the spec.

This inverts the current model, where Python output is correctness and `_GATED_ON` means
"Rust proven to match Python."

## What inverts

1. **Authority.** Divergences resolve *in Rust's favor*, not held back. Where a kernel's
   output differs from today's Python, the resolution lives in Rust — either ship Rust's
   behavior as the spec (breaking output change) or fix the Rust kernel to the *desired*
   spec. "Rust-reference" does NOT mean "ship whatever Rust currently does" — it means the
   desired behavior is defined and enforced in Rust, and Python conforms or is lossy.
2. **The gate.** Under `auto` (default), native runs **wherever a kernel exists** — the
   `_GATED_ON` allow-list restriction is dropped from the `auto` path. `_GATED_ON` either
   disappears or inverts to a `_NO_KERNEL` / documented-lossy list. Target loader `auto`
   branch:
   ```python
   # auto (default): Rust is the reference — use native whenever a kernel exists;
   # pure-Python is a lossy fallback only when the wheel is absent.
   return _native is not None and _has_symbol(component)
   ```
   (`mode==0` stays "force fallback"; `mode==1` stays "require native, raise if missing".)
3. **Tests.** From "native must match Python" to "native is the oracle; the Python fallback
   is tested as acceptably-close / documented-lossy." Native becomes the primary assertion
   surface, not an `@native_only` skip.
4. **CI center of gravity.** The native wheel becomes a **required** build artifact; the
   native path is the **default** test lane. Today every native test is `@native_only` and
   *skips* without the wheel, so the default run validates Python — that must invert.
5. **Install.** Native present by default (already enforced by the `golden-suite`
   hard-native-dep work — that change was ahead of this decision and is aligned).

## Non-negotiable per-divergence step

Before native flips on by default for a component that currently diverges, make the
one-time **product decision**: is the Rust kernel's current behavior the desired spec?
- Yes → ship it (major version bump + migration note), make the Python fallback conform or
  mark it lossy.
- No → fix the Rust kernel to the desired spec first, then flip.

Known divergences requiring this decision:
- **goldenflow `phone_validate`** — Rust `is_valid` (strict metadata validation) vs Python
  `is_possible_number` (length-only). Which is the product's "valid"? Decide, then align.
- **goldenflow `phone_national`** — Rust national formatting diverges on ambiguous leading-1
  inputs. Decide whether Rust's formatting is the spec; if yes, ship + document.
- **goldenmatch FS native (`score_block_pairs_fs`)** — discrete FS comparison levels amplify
  float diffs and can move a pair across the link threshold. Decide whether native FS scoring
  becomes authoritative or stays an opt-in (determinism-sensitive; likely stays opt-in even
  under Rust-reference, as a documented exception).

## Sequencing (pilot first)

Ordered by readiness — most-ready first, so the risky product decisions come last.

### 1. goldenmatch (pilot)
Furthest along: `clustering, block_scoring, pairs, featurize, hashing` are already byte/bit
-exact, so flipping `auto` to "native wherever a symbol exists" changes nothing for them —
the fallback already matches. Work:
- Flip loader `auto` semantics (drop `_GATED_ON` restriction; use `_has_symbol`).
- `pprl_bloom`: byte-exact already; enable after the published-wheel check + one wall-clock
  bench (the only remaining precondition; correctness is not a blocker).
- FS native: product decision (recommend keep opt-in as a documented exception).
- Invert the parity tests to treat native as the oracle; make the native wheel a required CI
  build; run native as the default lane.
- Version bump only if any output actually changes (for the 5 exact components it does not;
  pprl_bloom is byte-exact; so likely a minor unless FS is flipped).

### 2. goldenanalysis, goldencheck (fast followers)
Both already have `_GATED_ON == full faithful surface`, all byte/set-exact. Same mechanical
flip (loader `auto` semantics + CI inversion + test re-orientation). No known divergences →
no product decisions, no breaking output change. Housekeeping to fold in: goldenanalysis
stale loader docstring; goldencheck `approximate_fd` symbol-probe honesty
(`fd_violation_rows`).

### 3. goldenflow (last — carries the product decisions)
Its wired transforms (`phone_e164`, `phone_country_code`) are already native-default. The two
remaining kernels each need: the product decision above, then the Rust fix (validate:
predicate swap `is_valid`→ desired; national: canonical-form spec), a fallback that conforms
-or-is-lossy, and a parity/oracle test. `phone_digits` stays pure Polars (no kernel needed).
Major version bump (outputs change).

## First concrete moves (this is what to start on)

1. **CI inversion (mechanical, highest leverage, no runtime behavior change):** make the
   native wheel a required build in each package's CI and run the native path as the default
   test lane, with a separate fallback lane. Shifts the center of gravity to Rust-as-oracle
   before any behavior flips.
2. **goldenmatch pilot loader flip:** change `auto` to `_native is not None and
   _has_symbol(component)`; land the pprl_bloom bench; make the FS opt-in exception explicit.
3. **Roll the flip through goldenanalysis + goldencheck** (near-zero risk).
4. **goldenflow product decisions + Rust work** (validate first, national second).
5. **Docs/versioning sweep:** relabel the Python path as non-authoritative lossy fallback
   across READMEs / CLAUDE.md / tuning docs; document per-primitive lossiness; major bumps
   where output changes. (Use the rollout-docs-sweep skill.)

## What does NOT change
- Python kernels are NOT deleted — they remain as the no-wheel fallback.
- `mode==0` (force fallback) and `mode==1` (require native) semantics stay.
- The `golden-suite` hard-native-dep install posture stays (it already assumes native is the
  default engine).
