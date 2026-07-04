# GoldenFlow Wave B — i18n names (transliterate + script) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Two new owned i18n-name kernels — **`name_transliterate`** (deterministic Unicode→ASCII fold via an explicit curated map) and **`name_script`** (dominant-script detection via Unicode ranges) — across every surface (native, WASM/TS, pure-Python fallback), byte-identical to the Rust oracle.

**Architecture:** Same cross-surface pattern as Waves 0b/0c/A. New `goldenflow-core/src/names.rs` (NOT under identifiers/ — it's a name-normalization family, but it REUSES the existing `identifiers_corpus.jsonl` + parity harness, which is generic over transform name → no CI changes). Kernels → native-flow Arrow shims → goldenflow-wasm exports → Python transforms+fallback (`transforms/names.py`) → TS transforms+fallback (`src/core/transforms/names.ts` if it exists, else identifiers.ts) → loader `_COMPONENT_SYMBOLS` → corpus rows.

**Depends on:** Wave A (this branch is stacked on `feat/goldenflow-wave-a`). Rebase onto `origin/main` before opening the PR (after A merges), via `git rebase --onto origin/main <A-tip-sha>`.

**Environment (Windows box):** Rust `cargo test` + `cargo-clippy clippy` from the goldenflow-core dir. Python `ruff`/`py_compile` + ONE targeted parity pytest (`POLARS_SKIP_CPU_CHECK=1`, `.venv/Scripts/python.exe`); kill python after. TS = CI gate (no local typecheck). No native wheel build.

---

## THE byte-parity rule for names (critical)
Unicode-DB-dependent operations (NFD/NFKC) can differ across Rust/Python/JS runtime Unicode versions → cross-surface byte divergence. So:
- **`name_transliterate` uses an EXPLICIT curated char→ASCII map**, NOT `unicode-normalization`/NFD/`unicodedata`. The SAME literal map is written in Rust, Python, and TS (the Rust kernel is the oracle; corpus enforces the other two match). Bounded to common European name diacritics + ligatures; documented coverage. This guarantees parity with zero runtime-Unicode dependency.
- **`name_script` uses explicit Unicode codepoint RANGES** (stable, not version-dependent for the major scripts). Same ranges in all three surfaces.

## Kernel specs (goldenflow-core/src/names.rs; `pub mod names;` in lib.rs)

### `name_transliterate(&str) -> String`
- For each char: if ASCII (`is_ascii()`), pass through unchanged. Else look up an explicit map; if mapped, emit the ASCII replacement (may be multi-char, e.g. `ß`→`ss`); if unmapped, DROP the char (emit nothing). Always returns a String (never None). Empty/whitespace preserved as-is for ASCII.
- **Explicit map (the oracle — lock exactly, replicate byte-identical in Py+TS):** lowercase + uppercase of: a-acute/grave/circ/diaer/tilde/ring → a/A; e/i/o/u variants similarly; n-tilde→n/N; c-cedilla→c/C; y-acute/diaer→y/Y; ligatures ß→ss, æ→ae Æ→AE, œ→oe Œ→OE, ø→o Ø→O, å→a Å→A, đ→d Đ→D, ł→l Ł→L, þ→th Þ→Th, ð→d Ð→D; plus common ones (š/ž/č/ć/ř/ě → s/z/c/c/r/e, upper too). Keep the table CURATED + documented ("common Latin-script diacritics; unmapped non-ASCII is dropped"). The kernel is the oracle — pick the table, lock it, make Py/TS match.
- Vectors (TDD): `"José"`→`"Jose"`, `"Müller"`→`"Muller"`, `"Straße"`→`"Strasse"`, `"Łódź"`→`"Lodz"`, `"Renée"`→`"Renee"`, `"Æsir"`→`"AEsir"`, ASCII `"Smith"`→`"Smith"`, `""`→`""`, an unmapped char (e.g. an emoji or CJK) → dropped.

### `name_script(&str) -> String`
- Count chars by script via ranges; return the dominant NON-Common script label, or `"Common"` if only ASCII/punct/digits, or `"Unknown"` if empty. Labels: `"Latin"`, `"Cyrillic"`, `"Greek"`, `"Han"`, `"Hiragana"`, `"Katakana"`, `"Hangul"`, `"Arabic"`, `"Hebrew"`, `"Devanagari"`, `"Common"`, `"Unknown"`. Tie-break: highest count wins; on exact tie, the earliest label in a fixed priority order (document it). Ranges (lock these): Latin `A-Za-z` + Latin-1 Supplement/Extended-A (`À-ɏ`); Cyrillic `Ѐ-ӿ`; Greek `Ͱ-Ͽ`; Han `一-鿿`; Hiragana `぀-ゟ`; Katakana `゠-ヿ`; Hangul `가-힣`; Arabic `؀-ۿ`; Hebrew `֐-׿`; Devanagari `ऀ-ॿ`. Digits/space/punct = Common (not counted as a script).
- Vectors: `"Smith"`→`"Latin"`, `"José"`→`"Latin"`, `"Иван"`→`"Cyrillic"`, `"Ολγα"`→`"Greek"`, `"张伟"`→`"Han"`, `"田中"`→`"Han"`, `"محمد"`→`"Arabic"`, `"123"`→`"Common"`, `""`→`"Unknown"`.

Component/loader keys: `name_transliterate` → `("name_transliterate_arrow",)`, `name_script` → `("name_script_arrow",)`. Transforms `auto_apply=False`, `mode="series"`, `input_types=["name","string"]`.

---

## Task 1: `name_transliterate` — full stack
**Files:** `goldenflow-core/src/{names.rs,lib.rs}`, `native-flow/src/{identifiers.rs or a new names.rs,lib.rs}`, `goldenflow-wasm/src/lib.rs`, `transforms/_native.py`, `transforms/names.py`, `core/_native_loader.py`, `scripts/gen_identifiers_corpus.py`, corpus, tests.
- [ ] Kernel TDD (red) in `names.rs` with the vectors; add `pub mod names;`. Implement the explicit map + logic; `cargo test`, `cargo-clippy clippy -- -D warnings` (from goldenflow-core dir), `cargo fmt --check` green.
- [ ] Arrow shim `name_transliterate_arrow` (map_str_to_str — but transliterate always returns String, so wrap `|s| Some(names::name_transliterate(s))`) in native-flow (add a `names` section in identifiers.rs or a new module; register in lib.rs). `cargo build --release`.
- [ ] wasm export `name_transliterate` in goldenflow-wasm (returns String). `cargo build --target wasm32-unknown-unknown --release`.
- [ ] Python: `_native.py` runner + `name_transliterate_native`; `transforms/names.py` add `name_transliterate` transform + byte-identical pure-Python fallback (the SAME explicit map as Rust — a Python dict). `_native_loader.py` add `"name_transliterate"`.
- [ ] Corpus: extend `gen_identifiers_corpus.py` (cases + maps) with name_transliterate rows; regenerate + `--check`. Extend `test_identifiers_parity.py` maps. Add unit cases (new `tests/transforms/test_names_i18n.py` or append to an existing names test). Run the fallback parity pytest once → green.
- [ ] Commit `feat(goldenflow): owned name_transliterate kernel (explicit ASCII-fold map, cross-surface)`.

## Task 2: `name_script` — full stack
- [ ] Same pattern for `name_script` (returns a label String): kernel TDD with the range table + vectors, shim `name_script_arrow`, wasm export, Python transform+fallback (same ranges), loader `"name_script"`, corpus rows, parity + unit tests. All green. Commit `feat(goldenflow): owned name_script detection kernel (Unicode ranges)`.

## Task 3: TS transforms + corpus sync
- [ ] Add pure-TS `nameTransliterateTs`/`nameScriptTs` (byte-identical: the SAME explicit map + SAME ranges) + wasm-dispatch wrappers + register (auto_apply:false) + `*Ts` exports, in the TS names/identifiers transforms module. Extend `FlowWasmBackend` + loader glue with `nameTransliterate`/`nameScript`. Byte-copy the corpus to TS (`cmp` IDENTICAL). Extend the parity test maps. Commit `feat(goldenflow-js): name_transliterate/name_script pure-TS + wasm dispatch + corpus sync`.

## Task 4: docs + version + rebase + PR
- [ ] Version bump: goldenflow py 1.5.0→1.6.0 (3 spots), npm 0.5.0→0.6.0, goldenflow-native 0.3.0→0.4.0 (Cargo.toml+pyproject+__init__ fallback+Cargo.lock). CHANGELOG. Docs: count 90→92, add the 2 name-i18n transforms to CLAUDE.md/README/docs-site; context-network updates entry.
- [ ] **Rebase onto origin/main** once Wave A (#1417) has merged: `git fetch origin && git rebase --onto origin/main <A-branch-tip-sha> feat/goldenflow-wave-b` (A's tip = the `chore+docs ... Wave A version bumps` commit). Resolve any version/CHANGELOG conflicts (A bumped to 1.5.0; B bumps to 1.6.0 — keep B's on top of A's landed 1.5.0; the CHANGELOG gets both sections). Re-verify version-consistency after rebase.
- [ ] Push, open PR `feat(goldenflow): Wave B — i18n name kernels (transliterate + script)`, arm `--auto --squash`.

## Guardrails
- **No NFD/unicodedata/unicode-normalization** — explicit maps/ranges only, for guaranteed cross-runtime byte parity. The kernel is the oracle; corpus enforces Py+TS match.
- Reuse the existing identifiers corpus/harness (generic over transform name) → NO CI changes.
- Sequential Tasks 1-2 (shared files). `auto_apply=False`. Clippy from the goldenflow-core dir.
- Corpus rows use only COMMON, stable characters (the documented coverage) — don't add exotic codepoints whose behavior could vary.
