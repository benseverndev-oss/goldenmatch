# 0031 — GoldenFlow: owned-kernel core split, reference-mode loader, checksummed identifiers, cross-surface WASM

**Status:** Accepted • **Shipped:** Wave 0 (0a + 0b + 0c) — goldenflow 1.4.0, npm 0.4.0, goldenflow-native 0.2.0

## Context

GoldenFlow's native acceleration shipped in 2026-06-07 (ADR
[0006](0006-goldenflow-native-nanp-gating.md)) as a phone-only kernel living in a
single `native-flow` PyO3 crate, gated by a hand-maintained `_GATED_ON` allowlist
under `GOLDENFLOW_NATIVE=auto`. Two things had since diverged from the rest of the
suite:

- **Crate shape.** goldenmatch had already moved to the suite-standard
  `-core` (pyo3-free kernels) / `-native` (thin PyO3 shim) / `-wasm`
  (wasm-bindgen edge binding) split, with the pure kernel as the single source of
  truth reused across Python, SQL, and TS. GoldenFlow still had only a monolithic
  phone-only `native-flow`.
- **Loader gate.** goldenmatch had adopted reference-mode
  (`docs/design/2026-07-01-rust-is-the-reference-roadmap.md`): Rust is the
  reference implementation and pure-Python the lossy fallback, with `auto`
  running native wherever a kernel symbol exists (`_has_symbol` +
  `_COMPONENT_SYMBOLS`) except a small `_FALLBACK_ONLY` set. GoldenFlow still used
  the older per-component `_GATED_ON` allowlist.

Separately, the Identifiers transform group was thin (only `ssn_format` /
`ssn_mask` / `ein_format`) relative to the real-world demand for checksummed
identifier validation/formatting (payment cards, IBAN, ISBN, EAN/UPC, EU VAT).

## Decision

Bring GoldenFlow to the suite-standard shape and grow the identifier surface,
staying additive and measure-first (opt-in, and only where output is proven
byte-identical).

- **Crate split.** New pyo3-free `goldenflow-core` crate OWNS the kernels (phone +
  identifiers). `native-flow` becomes a thin PyO3 shim over goldenflow-core. New
  `goldenflow-wasm` (wasm-bindgen cdylib) surfaces the identifier kernels to the
  edge.
- **Reference-mode loader.** `goldenflow/core/_native_loader.py` adopts the suite
  `_has_symbol` + `_COMPONENT_SYMBOLS` + `_FALLBACK_ONLY` pattern.
  `GOLDENFLOW_NATIVE`: `auto` (default) = native wherever a component's kernel
  symbol exists, `0` = force pure-Python, `1` = require native. `_GATED_ON` is
  retained only as documentation of the byte-exact phone sign-off; it no longer
  governs `auto`. `phone_validate` is `_FALLBACK_ONLY` (its only native symbol
  implements `is_valid`, not the product-chosen `is_possible` spec); `phone_digits`
  is pure Polars.
- **10 checksummed-identifier transforms** (all `auto_apply=False`, owned Rust
  kernels, native-first): `cc_validate`/`cc_format`/`cc_mask` (Luhn payment card),
  `iban_validate`/`iban_format` (ISO 7064 mod-97), `isbn_validate`/`isbn_normalize`
  (10/13 checksum + 10->13), `ean_validate` (EAN/UPC GTIN mod-10),
  `vat_validate`/`vat_format` (EU VAT: structural for all 27 member-state prefixes,
  checksum bounded to DE + IT this wave — a deliberate, documented bound that may
  grow without changing the contract).
- **Cross-surface WASM/TS.** The TS package keeps pure-TS as the default and
  permanent fallback; opt-in `enableWasm()` (async, returns `false`/stays pure-TS
  on failure) routes the identifier transforms through the goldenflow-wasm kernel
  — the TS analog of `pip install goldenflow[native]`.
- **One cross-surface byte-parity contract.** `goldenflow-core` is the oracle. The
  corpus `tests/parity/identifiers_corpus.jsonl` is committed in two byte-identical
  copies (Python + TS); `scripts/gen_identifiers_corpus.py --check` is the CI drift
  guard; `test_identifiers_parity.py` (Python) and `identifiers.parity.test.ts`
  (TS) assert native / WASM-TS / pure-Python all agree.
- **CI.** A `wasm_flow` lane (paths-filter on `goldenflow-wasm/**`) builds the wasm
  artifact into the TS package's `src/core/wasm/artifacts/`, runs a corpus
  sync-check (Python vs TS copies must be byte-identical), then runs the identifier
  parity test with the WASM leg active. `goldenflow-core` is clippy-linted in the
  required `rust` job. The phone `native_flow` / `native_flow_wheel` lanes are
  unchanged.

## Consequence

- The transform registry grows 76 -> **86** (Identifiers 3 -> 13). The new
  checksummed identifiers are opt-in (`auto_apply=False`) — zero-config does not
  fire them; request them explicitly in a config.
- GoldenFlow now matches the suite kernel shape: one pyo3-free `-core` reused by
  `-native` (Python) and `-wasm` (TS), consistent with goldenmatch's
  score/analysis/suggest folds.
- Native/WASM are additive and parity-safe: turning any kernel on or off changes
  only speed, never the cleaned values, enforced by the shared oracle corpus.
- Versions moved in lockstep: goldenflow 1.4.0, npm 0.4.0, goldenflow-native 0.2.0.

See also [0006](0006-goldenflow-native-nanp-gating.md) (the original phone kernel +
NANP gating) and [architecture/goldenflow-native-kernel.md](../architecture/goldenflow-native-kernel.md).
