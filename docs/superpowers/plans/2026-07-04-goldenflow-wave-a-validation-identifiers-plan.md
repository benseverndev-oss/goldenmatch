# GoldenFlow Wave A — validation/identifier families (SWIFT, ABA, IMEI) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add three new owned checksummed/structural identifier families — **SWIFT/BIC**, **ABA routing number**, **IMEI** — across every surface (native, WASM/TS, pure-Python fallback), byte-identical to the Rust oracle, reusing the Wave 0b/0c machinery.

**Architecture:** Same as 0b/0c. Kernels in `goldenflow-core/src/identifiers/<family>.rs`; Arrow shims in `native-flow`; wasm-bindgen exports in `goldenflow-wasm`; Python transforms + fallback in `transforms/identifiers.py`; TS transforms + fallback in `src/core/transforms/identifiers.ts`; loader `_COMPONENT_SYMBOLS` entries; corpus rows in the shared `identifiers_corpus.jsonl` (Python) byte-copied to TS. The existing `native_flow`, `wasm_flow`, `rust` (goldenflow-core clippy), and `python_goldenflow*` CI lanes already cover all of this — no CI changes needed beyond the corpus staying in sync (already guarded).

**Depends on:** Wave 0 (merged). Branch off `origin/main`.

**Environment (Windows box):** Rust `cargo test`/`cargo-clippy clippy` (from the goldenflow-core dir) are the local gate. Python: `ruff`/`py_compile` + ONE targeted parity pytest with `POLARS_SKIP_CPU_CHECK=1` via `.venv/Scripts/python.exe`; kill python after. TS: no local typecheck/vitest (OOM) — CI gate. Do NOT build the native wheel (CI validates native). Prefer `x.is_multiple_of(n)` over `x % n == 0` (clippy).

**Reference:** the merged Wave 0b plan (`docs/superpowers/plans/2026-07-02-goldenflow-identifier-kernels-plan-0b.md`) is the canonical per-family pattern; the merged 0c plan is the TS/wasm pattern. Read the existing `luhn.rs`/`iban.rs` + `identifiers.ts` cc/iban functions as live templates.

---

## Family specs (the kernels are the oracle — lock a rule, make all surfaces match)

### SWIFT/BIC — `swift.rs`: `swift_validate(&str)->bool`, `swift_format(&str)->Option<String>`
- Normalize: uppercase + strip spaces (NOT `-`/`.` — BICs don't use them; but strip spaces only, or reuse `strip_sep`? Use uppercase + remove ASCII spaces only, to avoid stripping a `-` that would make an invalid BIC pass. Decide: strip spaces only).
- Structure: length 8 OR 11. `[0:4]` letters A-Z (institution). `[4:6]` letters A-Z (ISO-3166 country — structural A-Z, not a country-set lookup). `[6:8]` alphanumeric A-Z/0-9 (location). If length 11, `[8:11]` alphanumeric (branch). No checksum (BIC has none).
- `swift_format` → the normalized uppercase value if valid, else None.
- Vectors: valid `DEUTDEFF`, `DEUTDEFF500`, `NEDSZAJJXXX`, lowercase `deutdeff`→valid (normalized); invalid `DEUTDEFF5` (len 9), `DEUT1EFF` (digit in institution), `12345678` (digits in bank/country).

### ABA routing — `aba.rs`: `aba_validate(&str)->bool`
- Normalize: `strip_sep` (space/-/.); must be exactly 9 ASCII digits.
- Checksum: `3*(d0+d3+d6) + 7*(d1+d4+d7) + 1*(d2+d5+d8)`, valid iff `sum.is_multiple_of(10)`.
- Vectors: valid `011000015`, `021000021`, `122105155`; invalid `011000016` (bad check), `12345` (length), `01100001a` (non-digit).

### IMEI — `imei.rs`: `imei_validate(&str)->bool`
- Normalize: `strip_sep`; exactly 15 ASCII digits; Luhn check.
- **Reuse Luhn:** change `luhn.rs`'s `fn luhn_ok` to `pub(crate) fn luhn_ok` and call `super::luhn::luhn_ok` from `imei.rs` (DRY — one Luhn impl). The Python/TS fallbacks similarly reuse their existing `_luhn_ok`/luhn helper.
- Vectors: valid `490154203237518`, `356938035643809`; invalid `490154203237519` (bad Luhn), `12345` (length).

Component/loader keys: `swift` → `("swift_validate_arrow",)`, `aba` → `("aba_validate_arrow",)`, `imei` → `("imei_validate_arrow",)`. Transforms (all `auto_apply=False`): `swift_validate`, `swift_format`, `aba_validate`, `imei_validate`.

---

## Task 1: SWIFT/BIC — full stack (native side)
**Files:** `goldenflow-core/src/identifiers/{mod.rs,swift.rs}`, `native-flow/src/identifiers.rs`+`lib.rs`, `goldenflow-wasm/src/lib.rs`, `transforms/_native.py`, `transforms/identifiers.py`, `core/_native_loader.py`, `scripts/gen_identifiers_corpus.py`, corpus, `tests/transforms/test_identifiers*.py`.
- [ ] Kernel TDD (red): write `swift.rs` test module with the vectors → `cargo test identifiers::swift` fails. Add `pub mod swift;` to mod.rs.
- [ ] Implement `swift_validate`/`swift_format`; `cargo test` green + `cargo-clippy clippy -- -D warnings` (from goldenflow-core dir) + `cargo fmt --check`.
- [ ] Arrow shims `swift_validate_arrow` (map_str_to_bool) + `swift_format_arrow` (map_str_to_str) in native-flow + register in lib.rs; `cargo build --release` (native-flow).
- [ ] wasm exports `swift_validate`/`swift_format` in `goldenflow-wasm/src/lib.rs`; `cargo build --target wasm32-unknown-unknown --release`.
- [ ] Python: `_native.py` `_swift_kernel_runner` + native helpers; `identifiers.py` `swift_validate`/`swift_format` transforms (`auto_apply=False`, priority 50) + byte-identical pure-Python fallback. `_native_loader.py` add `"swift"` to `_COMPONENT_SYMBOLS`.
- [ ] Corpus: extend `gen_identifiers_corpus.py` (`_CASES` + fn maps) with swift rows; regenerate + `--check`. Extend `test_identifiers_parity.py` `_TRANSFORMS`/`_NATIVE_FLOOR_SYMBOL`. Append swift unit cases to `test_identifiers.py`. Run the fallback parity file once (`-k "not native"`) → green.
- [ ] Commit `feat(goldenflow): owned SWIFT/BIC identifier kernel (structural, cross-surface native side)`.

## Task 2: ABA routing — full stack (native side)
- [ ] Same pattern as Task 1 for `aba.rs` (`aba_validate` only): kernel TDD, shim `aba_validate_arrow`, wasm export `aba_validate`, Python transform+fallback, loader `"aba"`, corpus rows, tests. clippy/fmt/cargo test green; fallback parity green. Commit `feat(goldenflow): owned ABA routing kernel (checksum)`.

## Task 3: IMEI — full stack (native side), reusing Luhn
- [ ] Make `luhn.rs::luhn_ok` `pub(crate)`. Implement `imei.rs::imei_validate` calling `super::luhn::luhn_ok`. Same pattern: kernel TDD, shim `imei_validate_arrow`, wasm export `imei_validate`, Python transform+fallback (reuse the existing `_luhn_ok` py helper), loader `"imei"`, corpus rows, tests. clippy/fmt/cargo test green; fallback parity green. Commit `feat(goldenflow): owned IMEI kernel (Luhn, reuses luhn_ok)`.

## Task 4: TS transforms (all three families) + corpus sync
**Files:** `packages/typescript/goldenflow/src/core/transforms/identifiers.ts`, `packages/typescript/goldenflow/tests/parity/identifiers_corpus.jsonl`.
- [ ] Add pure-TS `swiftValidateTs/swiftFormatTs/abaValidateTs/imeiValidateTs` (byte-identical to the Python fallbacks) + the wasm-dispatch wrappers `swiftValidate/swiftFormat/abaValidate/imeiValidate` (mirror the cc/iban dispatch idiom via `getFlowWasmBackend()`), register them, and add the exports (`*Ts`) that the parity test imports. Extend the `FlowWasmBackend` interface in `src/core/wasm/backend.ts` + the loader glue mapping with the 4 new methods.
- [ ] Byte-copy the updated `packages/python/goldenflow/tests/parity/identifiers_corpus.jsonl` → the TS copy (`cmp` IDENTICAL). Extend `tests/parity/identifiers.parity.test.ts`'s `PURE_TS_FN`/`_TRANSFORMS` maps with the 4 new transforms.
- [ ] Commit `feat(goldenflow-js): SWIFT/ABA/IMEI pure-TS transforms + wasm dispatch + corpus sync`.

## Task 5: docs + version bump + PR
- [ ] Version bump: goldenflow py 1.4.0→1.5.0 (pyproject + `__init__` + server.json, all 3), npm 0.4.0→0.5.0, goldenflow-native 0.2.0→0.3.0 (Cargo.toml + pyproject + `__init__` fallback + Cargo.lock). CHANGELOG entries. (Version-consistency CI gate requires the 3 goldenflow spots match.)
- [ ] Docs: goldenflow CLAUDE.md + README transform list/count (86→90), Identifiers section (+ swift/aba/imei), docs-site if it enumerates; context-network updates.log entry. Get authoritative count via the registry command. (Follow `.claude/doc-surfaces.md`.)
- [ ] `pnpm install --lockfile-only` if any package.json changed (it shouldn't this wave — no new deps — but run it if it did).
- [ ] Push, open PR `feat(goldenflow): Wave A — SWIFT/ABA/IMEI identifier families (cross-surface)`, arm `--auto --squash`.

---

## Guardrails
- Kernels are the oracle; Python + TS fallbacks must match byte-for-byte (corpus parity enforces). Run `cargo-clippy clippy` FROM the goldenflow-core dir (not native-flow — it skips the path-dep).
- Sequential Tasks 1-3 (shared files: mod.rs, identifiers.py, _native_loader.py, corpus). Task 4 is TS-only. No CI changes needed (lanes exist; corpus sync-check + goldenflow-core clippy already wired).
- `auto_apply=False` on all four transforms. No new deps → no lockfile churn expected.
