# GoldenFlow reference-mode completion — decision + spec

Date: 2026-07-02
Status: **DECIDED 2026-07-02 — phone_validate = `is_possible_number` (Option B, non-breaking): fix the Rust kernel to match Python.** Spec below is the plan of record.
Depends on: `docs/design/2026-07-01-rust-is-the-reference-roadmap.md`,
`docs/design/2026-07-01-native-gate-audit-and-goldenflow-parity-design.md`

## Goal

Finish the Rust-is-the-reference arc for goldenflow: every phone transform runs the
native kernel by default, with pure-Python as the lossy fallback. Unlike goldencheck /
goldenanalysis (mechanical `_has_symbol` flips), goldenflow needs **real Rust work + one
product decision**, because two of its four phone kernels are *complete-but-divergent*.

## Current state (main)

`_GATED_ON = {"phone"}` (family-level). Four native kernels in `native-flow/src/phone.rs`:

| transform | kernel | wired native? | status |
| --- | --- | --- | --- |
| `phone_e164` | `phone_e164_arrow` | yes | ✅ native-default, parity-tested (~20k corpus vs `phonenumbers`) |
| `phone_country_code` | `phone_country_code_arrow` | yes | ✅ native-default, parity-tested |
| `phone_validate` | `phone_valid_arrow` | **no** (pure-Python) | ❌ divergent predicate — **needs the decision below** |
| `phone_national` | `phone_national_arrow` | **no** (pure-Python) | ❌ no canonical gate for leading-1 ambiguity |
| `phone_digits` | — (pure Polars regex) | n/a | ✅ already fast; no kernel warranted |

So goldenflow is *already* native-default for its faithful surface. This spec closes the
two remaining transforms.

## THE DECISION — `phone_validate` validity semantic

The kernel and the Python reference compute **different things**:

- **Python (`phone.py:112`)**: `phonenumbers.is_possible_number(parsed)` — a **length-only**
  check. Permissive: "could this be a phone number by digit count for the region."
- **Rust (`phone.rs:102`)**: `phonenumber::is_valid(&n)` — **full metadata validation**
  (length + prefix + region assignment rules). Strict: "is this an actually-assignable
  number." Strictly stricter than `is_possible_number`.

Plus a secondary difference: the kernel returns **null** on parse failure; Python returns
**False**. (Reconcilable in the bridge either way; not the crux.)

Under reference mode the native result becomes the spec, so we must pick which semantic
`phone_validate` *should* mean. This is a product call, not a code detail:

- **Option A — `is_valid` (strict) is the spec.** A "valid phone" means a real, assignable
  number. Better for data-quality/ER (catches right-length-but-fake numbers). **Breaking**:
  numbers currently `True` that are possible-but-not-valid flip to `False`. Fix Python to
  call `is_valid`; wire the kernel as-is; parity test; document + version bump.
- **Option B — `is_possible_number` (permissive) is the spec.** Keep today's Python behavior
  as the reference. **Non-breaking** for existing users. The *kernel* is then "wrong" and
  must be fixed: change `phone.rs:102` to the crate's possible-length API to match; then wire
  + parity test.
- **Option C — expose both.** `phone_validate(strict=False)` (possible) vs `strict=True`
  (valid). Most flexible; largest surface; defers the default question.

Recommendation: **Option B** unless you specifically want stricter validation. Rationale:
(1) it's non-breaking (`is_possible_number` stays the default meaning of "validate"), (2) it
keeps goldenflow's `phone_validate` a *permissive* filter (callers who want strict validity
already have `phone_country_code` + downstream rules), (3) "possible" is the lower-surprise
default for a transform named `validate` on messy input. If you value strict validity as the
product stance, Option A is defensible and cleaner long-term — just breaking.

**DECISION (2026-07-02): Option B.** `phone_validate` stays `is_possible_number`; the Rust
kernel is fixed to match. Non-breaking → minor version bump, `GOLDENFLOW_NATIVE=0` escape hatch.

## Spec — implementation per transform (once the decision lands)

### 1. `phone_validate` — BLOCKED by the crate; stays pure-Python (RESOLVED 2026-07-02)
**Finding:** `phonenumber` 0.3.9 exposes **no public `is_possible_number`** — only `is_valid`
(lib.rs:56 re-exports `is_valid`/`is_valid_with`/`is_viable`/`Validation`; the
`validator::length(meta, ParseNumber, Type) -> Validation` fn that would compute it is private
and takes internal types, not a `PhoneNumber`). `PhoneNumber` has `is_valid` but no
`is_possible`.

Under the Option-B decision (`is_possible` is the spec), there is **no faithful native kernel
we can build without vendoring libphonenumber's length tables** (fragile, would drift). So the
reference-mode-correct outcome is: **`phone_validate` stays pure-Python** (native only where a
faithful kernel exists — same class as `phone_digits`). This requires **no code change** — it
is already pure-Python (`phone.py:114` `map_elements`, no `native_fn`).

Cleanup (optional, low priority): the existing `phone_valid_arrow` (is_valid) kernel is unwired
dead code that computes the wrong semantic for the spec. It is not reachable from Python
(`phone_validate` has no native wiring, so even `GOLDENFLOW_NATIVE=1` never calls it). Leave it,
or drop it from `native-flow` in a later cleanup; not load-bearing either way.

(If we ever want native validate, the path is either upgrading to a `phonenumber` release that
exposes `is_possible`, or re-deciding to Option A / adding a `strict=` param — a separate call.)

### 2. `phone_national` (second — needs a canonical gate)
- The divergence is leading-1 ambiguity with no cheap canonical form (unlike E.164's
  `^\+1[2-9]\d{9}$` at `_native.py:56`). Design a canonical-NANP national-format acceptance
  predicate — e.g. accept only `(NXX) NXX-XXXX`-shaped country-code-1 output; null the rest
  so tier-3 Python settles them (same shape as the E.164 path).
- Add `phone_national_native()` in `_native.py` with that null-out; wire into `phone_national`
  (`phone.py:76`).
- Full-corpus parity test. Higher risk than validate because the safe residual is narrower.

### 3. `phone_digits` — no change (pure Polars, already fast).

### Out of scope
- International (non-NANP) parity: blocked by the hardcoded `_DEFAULT_REGION = "US"`
  (`_native.py:22`) + the `+CC`-with-US-default national-prefix mis-strip. Threading a
  per-row default region is a larger, separate change.

## Version + gate posture
- Wiring `phone_validate` + `phone_national` native makes them native-default → an output
  change on those transforms for native-wheel users. **Minor bump** with a `GOLDENFLOW_NATIVE=0`
  escape-hatch note (matches how the FS flip shipped as goldenmatch 2.6.0), unless Option A is
  chosen for validate (then call out the breaking validity change explicitly).
- The loader docstring's "`=1` WILL change outputs" warning gets updated once both kernels are
  faithful (no remaining divergence under the `"phone"` gate).

## Sequencing
1. Decide the `phone_validate` semantic (A / B / C).
2. Ship `phone_validate` (Rust fix or Python realign per the decision) + wiring + parity test.
3. Ship `phone_national` (canonical gate) + wiring + parity test.
4. Update loader docstring; minor version bump; done → goldenflow is reference-mode complete.
