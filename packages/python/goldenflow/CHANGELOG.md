# Changelog

## 1.12.0 (2026-07-04)

Wave D (text, part 2): the 5 Unicode-heavy text transforms are now backed by owned Rust kernels in `goldenflow-core::text` (native + WASM/TS + pure-Python fallback), Rust is the reference implementation. This completes the text family migration (count unchanged).

- Migrated: `lowercase`, `uppercase` (Rust std casing), `title_case` (ASCII title-case), `normalize_unicode` (NFKD-decompose + strip-combining), `fix_mojibake` (Latin-1↔UTF-8 round-trip).
- **Behavior change (reference-mode, resolved in Rust's favor):** `normalize_unicode` now uses an EXPLICIT generated decompose table (`scripts/gen_normalize_unicode_map.py`, over U+00C0–U+017F + U+1E00–U+1EFF) replicated byte-identically to all three surfaces, instead of each runtime's `unicodedata`/`String.normalize` — so the surfaces agree regardless of their bundled Unicode DB. Precomposed chars outside those ranges (e.g. Greek Extended) now pass through unchanged (previously decomposed); the ASCII fast-path is retained. `title_case` is bounded to ASCII title-casing (was polars Unicode `to_titlecase`); `lowercase`/`uppercase` casing agrees with the old behavior on Latin/ASCII (exotic Greek final-sigma / Turkish dotted-I casing is the documented boundary). Outputs are unchanged for well-formed Latin/ASCII inputs.

## 1.11.0 (2026-07-04)

Wave D (text, part 1): the 13 mechanical/ASCII-bound text transforms are now backed by owned Rust kernels in `goldenflow-core::text` (native + WASM/TS + pure-Python fallback), Rust is the reference implementation. This wave migrated existing transforms to the owned-kernel pattern; it did not add new transforms (count unchanged).

- Migrated: `strip`, `collapse_whitespace`, `normalize_quotes`, `normalize_line_endings`, `remove_html_tags`, `remove_urls`, `remove_digits`, `remove_punctuation`, `remove_emojis`, `extract_numbers` (scalar), plus the parameterized `truncate`, `pad_left`, `pad_right`. (`strip`/`collapse_whitespace` reuse the kernels added in the SQL de-bridge; the rest are new owned kernels.)
- **Behavior change (reference-mode, resolved in Rust's favor):** the HTML-tag / URL / number-extraction scans and char-class filters are hand-rolled with no regex engine for byte-identical output across Rust/Python/JS. `remove_digits`/`extract_numbers` are bounded to ASCII digits (`0-9`, not the old Unicode-aware `\d`); `remove_emojis` uses the explicit emoji codepoint ranges. `char::is_whitespace` is byte-equal to the old polars `\s`, so `strip`/`collapse_whitespace`/`remove_urls`/`remove_punctuation` whitespace handling is exact. The 10 non-parameterized transforms moved from vectorized Polars to native-first dispatch (the pure-Python fallback runs per-row when the native wheel is absent). Outputs are unchanged for well-formed inputs.
- The 5 Unicode-heavy text transforms (`lowercase`, `uppercase`, `title_case`, `normalize_unicode`, `fix_mojibake`) are intentionally deferred to a follow-up (text part 2) where the cross-runtime Unicode-casing/normalization parity is handled explicitly.

## 1.10.0 (2026-07-04)

Wave D (address-simple): the eight US-address transforms are now backed by owned Rust kernels in `goldenflow-core` (native + WASM/TS + pure-Python fallback), Rust is the reference implementation. This wave migrated existing transforms to the owned-kernel pattern; it did not add new transforms (count unchanged).

- Migrated: `address_standardize`, `address_expand`, `state_abbreviate`, `state_expand`, `zip_normalize`, `country_standardize`, `unit_normalize` (scalar), and `split_address` (multi-output: one column in, `street`/`city`/`state`/`zip` out — four Arrow arrays natively, a 1->4 marshaling shape). The scalar seven fit the string->scalar parity corpus; `split_address` is covered by a pinned-vector test.
- **Behavior change (reference-mode, resolved in Rust's favor):** the street-suffix, unit-prefix, and `split_address` parsing are now hand-rolled with ASCII word-boundary semantics (no regex engine) for byte-identical output across Rust/Python/JS. `address_standardize`/`address_expand`/`state_abbreviate`/`state_expand`/`zip_normalize` moved from vectorized Polars expressions to native-first dispatch (the pure-Python fallback runs per-row when the native wheel is absent). Outputs are unchanged for well-formed US-address inputs.

## 1.9.0 (2026-07-04)

Wave D (names-remainder): the eight remaining `name` transforms are now backed by owned Rust kernels in `goldenflow-core` (native + WASM/TS + pure-Python fallback), Rust is the reference implementation. This wave migrated existing transforms to the owned-kernel pattern; it did not add new transforms (count unchanged).

- Migrated: `strip_titles`, `strip_suffixes`, `name_proper`, `nickname_standardize`, `initial_expand` (its middle-initial flag predicate `has_initial`), `split_name`, `split_name_reverse`, `merge_name`.
- New kernel shapes: `split_name`/`split_name_reverse` are multi-output (one column in, `first_name`+`last_name` out — a pair of Arrow arrays natively); `merge_name` is multi-input (`first_name`+`last_name` in, `full_name` out). These don't fit the string->scalar parity corpus and are covered by pinned-vector tests instead.
- **Behavior change (reference-mode, resolved in Rust's favor):** `name_proper`'s title-casing is bounded to ASCII semantics; `strip_titles`/`strip_suffixes` moved from a vectorized Polars regex to native-first dispatch (the pure-Python fallback runs per-row when the native wheel is absent). Outputs are unchanged for well-formed ASCII inputs.

## 1.8.0 (2026-07-04)

Wave D (sweep, part 1): the url, numeric, and categorical transform families are now backed by owned Rust kernels in `goldenflow-core` (native + WASM/TS + pure-Python fallback), Rust is the reference implementation.

- **Behavior change (reference-mode, resolved in Rust's favor):** numeric `round` now uses round-half-away-from-zero (e.g. 2.5 -> 3), replacing Python's round-half-to-even (banker's rounding). Numeric parsers (`currency_strip`/`percentage_normalize`/`to_integer`/`comma_decimal`/`scientific_to_decimal`) return null on unparseable input (value-parity with the kernel). TS numeric parsers now return null on parse failure (was pass-through).
- url/categorical outputs unchanged for well-formed inputs.

## 1.7.0 (2026-07-04)

Wave D1: the email transform family (`email_lowercase`, `email_normalize`, `email_extract_domain`, `email_validate`) is now backed by owned Rust kernels in `goldenflow-core`, cross-surface (native + WASM/TS + pure-Python fallback), byte-parity to the Rust oracle. This wave migrated existing transforms to the owned-kernel pattern; it did not add new transforms. `email_lowercase`/`email_extract_domain` moved from a vectorized Polars expression to native-first dispatch (the pure-Python fallback runs per-row when the native wheel is absent). Outputs are unchanged for well-formed inputs.

## 1.6.0 (2026-07-04)

Wave B of the i18n-name-kernel program: two new owned name kernels for cross-script identity matching, cross-surface (native + WASM/TS + pure-Python), byte-parity to the Rust oracle. No breaking changes; existing transform outputs are unchanged.

### Added

- `name_transliterate`: deterministic Unicode-to-ASCII fold for names via an explicit curated character map (NOT NFD/Unicode decomposition), chosen for guaranteed byte-for-byte parity across the native, WASM/TS, and pure-Python surfaces.
- `name_script`: dominant-script detection for a name string via Unicode code point ranges (e.g. Latin, Cyrillic, Han, Arabic).
- Both transforms are `auto_apply=False` (request them explicitly in a config), native-first with pure-Python fallbacks proven byte-identical to the `goldenflow-core` Rust oracle, and available on the TypeScript/WASM surface via `enableWasm()`.

## 1.5.0 (2026-07-04)

Wave A of the identifier-kernel program: three new owned checksummed/structural identifier families (SWIFT/BIC, ABA routing, IMEI), cross-surface (native + WASM/TS + pure-Python fallback), byte-parity to the Rust oracle. No breaking changes; existing transform outputs are unchanged.

### Added

- `swift_validate` / `swift_format`: validate and format SWIFT/BIC bank identifier codes (8 or 11 characters, structural bank/country/location/branch code checks).
- `aba_validate`: validate US ABA routing numbers via the standard weighted checksum.
- `imei_validate`: validate mobile-device IMEI numbers via the Luhn checksum.
- All four transforms are `auto_apply=False` (request them explicitly in a config), native-first with pure-Python fallbacks proven byte-identical to the `goldenflow-core` Rust oracle, and available on the TypeScript/WASM surface via `enableWasm()`.

## 1.4.0 (2026-07-03)

Wave 0 of the identifier-kernel + WASM cross-surface program: owned Rust kernels for GoldenFlow's transform engine, a reference-mode native loader, 10 new checksummed-identifier transforms, a byte-parity harness, and an opt-in WASM/TS acceleration surface. No breaking changes; existing transform outputs are unchanged.

### Added

- `goldenflow-core`: a new pyo3-free Rust crate owning GoldenFlow's kernel logic (`native-flow` is now a thin marshaling shim over it — Arrow in/out + GIL release, computation lives in the core).
- Reference-mode native loader: `GOLDENFLOW_NATIVE` gating now follows the same reference-implementation posture as goldenmatch-native — native is authoritative wherever a kernel symbol exists and is proven parity-safe, pure Python is the documented fallback.
- 10 new checksummed-identifier transforms covering payment card, IBAN, ISBN, EAN, and EU VAT numbers: validate, format, normalize, and mask variants per family.
- Byte-parity harness validating the new identifier kernels against their pure-Python reference implementations across a randomized corpus.
- Cross-surface WASM/TS acceleration surface: `enableWasm()` in the TypeScript package opts into the shared `goldenmatch-wasm-runtime` plumbing for the new identifier kernels. Pure-TS stays the default and fallback; the `.wasm` artifact is built in CI, never committed.

## 1.3.0 (2026-06-24)

New pure-scalar canonicalizers for clean-room match keys, an opt-in Arrow-native date/phone acceleration runtime, and expanded carceral domain coverage. No breaking changes; existing transform outputs are unchanged.

### Added

- `goldenflow.canonicalize(value, kind)` — pure, scalar, stdlib-only field canonicalizers for `email`, `phone`, `name`, and `postal`. Total, idempotent, and locale-independent (ASCII-only case folding) so they reproduce byte-for-byte in a browser JS/TS port. Built for PPRL / clean-room two-party CLK linkage, where server-Python and browser-JS must agree on the exact canonical string before hashing. Exported from the package root. (#1183, closes #1128)
- Optional `goldenflow[native]` extra: a Rust/PyO3 acceleration runtime (`goldenflow-native`) with vectorized Arrow-native date and phone kernels. Off by default at runtime, NANP-gated, and output-preserving; the pure-Python path stays the default fallback. (#796)
- `carceral` domain: state-prison aliases for PA and CA. (#1010)

### Changed

- Vectorized fast paths for the `date_iso8601` and phone transforms, dispatched to the native kernel when `goldenflow[native]` is installed and enabled. Outputs are unchanged from the pure-Python path. (#796)

### Fixed

- `carceral` domain: corrected the Texas (TX) facility prefix. (#1010)

## 1.2.0 (2026-06-01)

New `carceral` domain pack plus a native-Polars (expr-mode) perf migration of the
transform library. The expr migration is output-preserving: existing test
assertions are unchanged; only the internal call shape changed.

### Added

- `carceral` domain pack (U.S. prisons / jails / detention): `carceral_org_strip`,
  `carceral_abbreviate`, `carceral_name_normalize`, and `latlng_pack`, plus the
  `CARCERAL_OPERATOR_ORGS`, `CARCERAL_BOP_ABBREVIATIONS`, and
  `CARCERAL_STATE_COMPLEX_ALIASES` constants. Registered as the `carceral` domain.

### Performance

- Native-Polars (expr-mode) rewrites of the currency, percentage, integer,
  truncate, pad, html, url, emoji, line-ending, number-extract, address, and state
  transforms. ASCII fast path for `normalize_unicode`; vectorized `date_iso8601`
  for numeric and 4-digit-string year columns. Outputs are unchanged.

### Changed

- Repository and project URLs rebranded from `benzsevern` to `benseverndev-oss`.

## 1.1.6 (2026-05-13)

Bug-fix release. Resolves a panic on large datasets surfaced by the
scale audit work.

### Fixed

- `category_auto_correct` panic at 1M+ rows under memory pressure (PR
  #174 / #175). The old path called `series.to_list()` + Python
  `Counter`, materialising one `PyString` per input row. At ~2 GB
  measured peak RSS following a `goldenmatch.auto_configure` sample
  run, the PyString allocations inside Polars' `to_list` path returned
  NULL and pyo3 0.28.2 mapped that to `PanicException("PyObject pointer
  is null")` rather than `MemoryError`. The function only ever needed
  `n_unique` distinct values, so the rewrite uses
  `series.value_counts()` instead -- stays in Rust, no per-row Python
  allocation, fixes the crash on the goldenmatch scale-audit Round 2
  1M synthetic fixture.

## 1.1.5 (2026-05-11)

Maintenance release. No transform / API behaviour changes.

### Fixed

- `goldenflow/__init__.py` `__version__` was `1.1.1`, lagging behind the
  PyPI `1.1.2` release. Both now report `1.1.5`.

### Infrastructure

- New `.github/workflows/publish-goldenflow.yml` mirrors the per-package
  PyPI publish workflows used by goldenmatch / goldencheck / goldenpipe /
  infermap. Fires on `release: published` for `goldenflow-v*` tags;
  `workflow_dispatch` with `ref` input for retro-publish. Brings goldenflow
  into the same release pipeline the rest of the suite uses.
- Companion MCP Registry sync (`.github/workflows/publish-mcp.yml`,
  added in monorepo PR #165) flips
  `registry.modelcontextprotocol.io/v0/servers?search=io.github.benseverndev-oss/goldenflow`
  from 1.1.4 → 1.1.5 automatically after this release lands on PyPI.

## 1.1.0 (2026-04-03)

### New Transforms (33 new, 43 → 76 total)

- **Email** (new module): `email_lowercase`, `email_normalize`, `email_extract_domain`, `email_validate`
- **Identifiers** (new module): `ssn_format`, `ssn_mask`, `ein_format`
- **URL** (new module): `url_normalize`, `url_extract_domain`
- **Text**: `remove_html_tags`, `remove_urls`, `remove_digits`, `remove_emojis`, `fix_mojibake`, `normalize_line_endings`, `extract_numbers`, `pad_left`, `pad_right`
- **Phone**: `phone_country_code`
- **Name**: `nickname_standardize`, `merge_name`
- **Address**: `country_standardize`, `unit_normalize`
- **Date**: `datetime_iso8601`, `extract_year`, `extract_month`, `extract_day`, `extract_quarter`, `extract_day_of_week`, `date_shift`, `date_validate`
- **Numeric**: `to_integer`, `abs_value`, `fill_zero`, `comma_decimal`, `scientific_to_decimal`

### Fixed GoldenCheck Integration

- `FINDING_TRANSFORM_MAP` now uses real GoldenCheck check names (was using invented labels that matched nothing)
- Profiler bridge uses GoldenCheck's semantic type system (`person_name` → `name`, `currency` → `numeric`, etc.)
- Safer LLM fallback with logged warnings instead of bare `except Exception`

### Bug Fixes

- `comma_decimal` no longer corrupts US-format decimals
- `email_normalize` preserves invalid emails instead of silently dropping to None
- `merge_name` handles missing `last_name` column gracefully
- Fixed `NameError` on `os` when `use_llm=True` in profiler bridge

### Stats

- 234 tests passing (up from 158)
- 11 transform categories (up from 8)

## 1.0.0 (2026-03-29)

First stable release. GoldenFlow is production-ready with 170 tests passing and DQBench Transform Score of 100/100.

### Features
- **43+ built-in transforms** across 8 categories: text, phone, name, address, date, categorical, numeric, auto-correct
- **5 domain packs**: people/HR, healthcare, finance, e-commerce, real estate
- **Zero-config mode**: auto-detects and fixes common data issues
- **Config-driven mode**: YAML config with `goldenflow learn` to generate from data
- **Schema mapping**: auto-map columns between source and target files
- **Streaming/batch processing**: handle large files without memory issues
- **Cloud connectors**: S3 and GCS read/write
- **MCP server**: 10 tools for AI agent integration (stdio + HTTP)
- **REST API**: FastAPI-based `goldenflow serve`
- **TUI**: Textual-based interactive mode
- **Watch mode**: auto-transform on file changes
- **Scheduling**: cron-like repeating transforms
- **Audit trail**: JSON manifest for every transformation run
- **History tracking**: with actual duration measurement
- **GoldenCheck integration**: map findings to recommended transforms via `select_from_findings`

### Golden Suite Pipeline
```
GoldenCheck (scan) -> GoldenFlow (transform) -> GoldenMatch (dedupe)
```

## 0.1.0 (2026-03-15)

Initial release.
