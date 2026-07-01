# Native acceleration gate audit + goldenflow parity path

Date: 2026-07-01
Status: audit complete; one flip candidate pending a bench; goldenflow path scoped

## Context

Goal: the suite should default to the native (Rust) path — Polars-shaped, where the
compiled engine is the default and pure-Python is the fallback, not an opt-in accelerator.
This audits each package's `_native_loader.py` gate (`_GATED_ON`) against the actual native
symbol surface and the parity-test coverage, to (a) flip the default to native wherever
parity already holds, and (b) scope exactly what goldenflow needs before its remaining
kernels can join.

## Headline finding

**The flip is already in place wherever parity holds.** Every loader defaults to
`GOLDENx_NATIVE=auto`, and `auto` runs native for any component in `_GATED_ON` when the
wheel is importable. In all four packages `_GATED_ON` already equals the full set of
components whose native kernel has a strong (mostly byte-/bit-exact) parity test. There is
no component that is proven-faithful-but-gated-off except one (`goldenmatch.pprl_bloom`,
below), and that one carries a documented measure-first precondition.

So "make native the default" is, across the suite, **already true by construction** — not a
change waiting to be made. The `_GATED_ON` allow-list is the mechanism that produces that
outcome safely.

### Per-package result

| Package | `_GATED_ON` | Native-faithful surface | Verdict |
| --- | --- | --- | --- |
| goldenmatch | clustering, block_scoring, pairs, featurize, hashing | same 5 + `pprl_bloom` | 5 already gated; `pprl_bloom` = FLIP-CANDIDATE (precondition below) |
| goldencheck | benford, composite_keys, functional_dependencies, fuzzy_values, approximate_fd | same 5 | already at max; no flip |
| goldenanalysis | histogram, quantile | same 2 | already at max; no flip |
| goldenflow | phone (family) | phone_e164 + phone_country_code (wired) | already at max for wired transforms; 2 kernels divergent (below) |

## Why we keep the allow-list (do NOT convert to a deny-list)

The Polars model has no allow-list because Polars has **one** engine — the Rust path *is*
the reference, so there is no pure-Python output to diverge from. GoldenSuite's kernels are
accelerators over a validated Python reference, so "prove parity, then enable" is the
correct discipline, and it is already delivering native-by-default for everything faithful.

Converting `auto` to a deny-list (native on for all components, exceptions listed) would
have shipped, by default and silently:
- goldenflow `phone_valid_arrow` (wrong validity predicate — see below),
- goldenflow `phone_national_arrow` (leading-1 ambiguity divergence),
- goldenmatch `score_block_pairs_fs` (discrete-FS-level boundary nondeterminism that can
  move a pair across the link threshold).

The allow-list is precisely what stops those from becoming default behavior. Recommendation:
**keep the allow-list**; it already produces the native-first outcome for the faithful
surface, and it is the guardrail that keeps it honest as new kernels land.

## The one flip candidate: `goldenmatch.pprl_bloom`

`bloom_clk_batch` has **byte-for-byte hex parity** with the pure-Python reference
(`tests/test_native_bloom_parity.py`, 7 transform shapes x 8 unicode-spanning values, plus
gate on/off equivalence and invalid-size raise). Because the CLK bits are byte-identical,
gating it on/off can never change a downstream dice/jaccard score — correctness is not a
blocker.

It is deliberately **not** yet in `_GATED_ON` (`core/_native_loader.py:65-74`). The
remaining preconditions, per the loader note and the measure-first rule (the #688 scar —
verify the wall moved, not just that parity holds):
1. Confirm `bloom_clk_batch` ships in the **published** `goldenmatch-native` wheel (the
   wheel/caller symbol-skew footgun), not just the in-tree build.
2. One wall-clock A/B bench showing an actual lift on a realistic PPRL workload.

Then adding `"pprl_bloom"` to `_GATED_ON` is a one-line change. Until (1)+(2), a blind flip
would repeat the #688 pattern (shipping a "perf" change without confirming the wall).

## GoldenFlow parity path (the explicit ask)

Current gate is **family-level**: `_GATED_ON = {"phone"}`
(`goldenflow/core/_native_loader.py:53`). The two *wired* phone transforms —
`phone_e164` and `phone_country_code` — are therefore **already native-by-default under
`auto`**, with strong parity vs `phonenumbers` over a ~20k mixed corpus
(`tests/transforms/test_native_parity.py`). The E.164 path is parity-safe because the bridge
nulls ambiguous outputs via the canonical acceptance regex `^\+1[2-9]\d{9}$`
(`transforms/_native.py:56`) and lets tier-3 Python settle the residual.

The native crate `goldenflow-native` (`packages/rust/extensions/native-flow`) exposes four
kernels. Two are live+faithful (above). The other two **exist but are never reached under
`auto`** because each is *complete-but-divergent* — not a stub, not untested-but-faithful, a
real semantic difference:

### 1. `phone_validate` -> `phone_valid_arrow` — KNOWN DIVERGENCE (predicate mismatch)
- Kernel uses `phonenumber::is_valid(&n)` (`native-flow/src/phone.rs:102`); Python reference
  uses `phonenumbers.is_possible_number(parsed)` (`transforms/phone.py:112`). `is_valid`
  (full metadata length+prefix validation) is strictly stricter than `is_possible_number`
  (length-only) — they return different booleans for possible-but-not-valid numbers.
- Parse-failure handling differs: kernel returns null (`phone.rs:97-100`), Python returns
  `False` (`phone.py:110-111`).

**Work to flip on:**
1. Change the kernel predicate to a possible-number check (the `phonenumber` crate's
   metadata/possible-length API) so it matches `is_possible_number`.
2. Decide the null-vs-False contract: map kernel-null -> `False` in the bridge, or keep null
   and let tier-3 Python return `False`.
3. Add `phone_valid_native()` to `transforms/_native.py`; wire it into `phone_validate`
   (`phone.py:105`) the way `phone_country_code` is wired.
4. Add a full-corpus parity test mirroring `test_phone_country_code_parity_with_native`.
5. No new `_GATED_ON` entry — it rides the existing `"phone"` key.

### 2. `phone_national` -> `phone_national_arrow` — KNOWN DIVERGENCE (no cheap canonical gate)
- National formatting diverges from `phonenumbers` on ambiguous leading-1 inputs, and unlike
  E.164 there is no cheap canonical-form predicate to null the divergent rows
  (`transforms/phone.py:85-88`, verbatim comment).

**Work to flip on:**
1. Design a canonical-NANP acceptance predicate for national format (the analog of
   `_CANONICAL_NANP`) that rejects the ambiguous-leading-1 outputs — e.g. accept only
   `(NXX) NXX-XXXX`-shaped country-code-1 output.
2. Add `phone_national_native()` with that null-out; wire into `phone_national`
   (`phone.py:76`).
3. Add a full-corpus parity test. Higher risk than the validate fix because the safe residual
   is narrower.

### Not in scope
- `phone_digits` is pure Polars regex (`phone.py:99`) — already fast in Rust via Polars; no
  kernel warranted.
- International parity (beyond NANP) is blocked by the hardcoded `_DEFAULT_REGION = "US"`
  (`transforms/_native.py:22`) + the `+CC`-with-US-default national-prefix mis-strip
  (`phone.rs`; `_native_loader.py:44-49`). Making native authoritative on international rows
  means threading the correct per-row default region into `parse_gated` — a larger change,
  out of scope for wiring the existing NANP kernels.

**Bottom line for goldenflow:** its native-faithful surface is already native-by-default. The
two remaining kernels are not blocked by the gate — the gate for `"phone"` is already open —
they are blocked by real Rust semantic fixes + parity tests. Do the validate fix first
(cheaper, clean gate), national second.

## Housekeeping (low-risk, independent of the above)

1. **goldenanalysis** `core/_native_loader.py` module docstring is stale — it says
   "`_GATED_ON` is empty until Phase 4 / always False today", but the frozenset is
   `{"histogram","quantile"}` and both run native under `auto`. Doc-only fix.
2. **goldencheck** `_COMPONENT_SYMBOLS`: `approximate_fd` probes only
   `discover_approximate_fds`, but the path also calls `fd_violation_rows`. A published wheel
   with the first symbol but not the second clears the gate then hits `AttributeError` (caught
   -> safe Python fallback, but the capability probe is dishonest). Make `_COMPONENT_SYMBOLS`
   map to a symbol *list* and require all present.
3. **goldenmatch** three exposed symbols with no production gate: `build_clusters_native`
   (unwired prototype, superseded by `build_clusters_arrow`), `score_block_pairs` (Vec kernel,
   retained as the arrow kernel's parity oracle), `connected_components_arrow` (no Python
   caller located). Cleanup pass: confirm/remove/wire; none affect the default-on decision.

## Decision

> SUPERSEDED (2026-07-01) by `2026-07-01-rust-is-the-reference-roadmap.md`. The
> "keep the allow-list" conclusion below was correct under the *Python-reference* model. The
> direction has since changed to **Rust-is-the-reference** (Python = lossy fallback), under
> which the allow-list is transitional and the target is native-default. The audit's factual
> findings (per-component parity, symbol surface, goldenflow divergences) stand and feed the
> roadmap; only the "keep the allow-list" call is reversed.

- ~~Keep the allow-list gate in all four packages~~ → under Rust-reference, `_GATED_ON` is
  transitional; the target `auto` path uses native wherever a kernel exists.
- The parity facts still hold: the 5+5+2 gated components are byte/bit-exact; `pprl_bloom` is
  byte-exact (needs published-wheel check + bench); goldenflow's two kernels are the real
  divergences carrying a product decision.
- goldenflow's two remaining kernels need the Rust fixes above before native becomes their
  default; the `"phone"` gate is already open for them.
