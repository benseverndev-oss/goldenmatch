# Changelog

## 2.1.0 (2026-07-13)

### Owned auto-detect profile kernel

Zero-config's type-inference / profiling *decision* is now an owned
`goldenflow-core::profile` kernel, cross-surface byte-identical like the
identifier/name/text kernels before it.

- **`inferred_type` is owned on every surface.** The Polars columnar path, the
  Polars-free list/dict path, the `goldenflow-native` wheel, and
  `goldenflow-wasm` / the TS `inferType` all route the "what type is this
  column?" decision through the same `infer_type` kernel. The pure-Python
  `_infer_type` / `_infer_type_list` stay as byte-matched fallbacks.
- **Columnar `Column.profile()` is a Polars-free full profile.** One FFI call
  (`profile_column`) returns `inferred_type` plus null/unique/samples, no Polars
  import required.
- **Byte-identical**, proven by `tests/parity/profile_corpus.jsonl` (oracle =
  goldenflow-core), and **opt-out** via `GOLDENFLOW_NATIVE=0` (forces the
  pure-Python fallback).
- Bumps the base `goldenflow-native` floor to `>=0.27.0` so the profile kernel
  reaches users out of the box.
- Boundary rationale (including one accepted, corpus-unexercised TS
  sampling-order edge) in
  `docs/design/2026-07-06-goldenflow-owned-kernel-boundary.md`.

## 2.0.0 (2026-07-08)

**BREAKING — Polars is no longer a base dependency.** `pip install goldenflow` no longer
pulls Polars (dropping ~185 MB installed); it now runs **Polars-free by default** on
`goldenflow-native` (the ~5 MB abi3 kernel, now a base dependency). Every one of the 113
transforms, all file/DB readers, and zero-config run without Polars, byte-identical to the
Polars engine. Polars becomes an **optional bulk-vectorized backend** — `pip install
goldenflow[polars]`.

### Migration

- **If you use `transform_df(pl.DataFrame)`, `read_file`, `write_file`, or otherwise pass
  Polars objects:** `pip install goldenflow[polars]`. You already have Polars installed to
  construct a `pl.DataFrame`, so in practice this is only a dependency-declaration change.
- **New code should prefer `goldenflow.transform(data, config=...)`** — the Polars-free
  primary. It takes a `dict[str, list]` or a `.csv` / `.parquet` / `.xlsx` path and returns
  a `ColumnarResult` (`.columns` + `.manifest`, with an opt-in `.to_polars()` bridge).
- **`goldenflow-native` is now installed by default** (was the `[native]` extra). A bare
  install works out of the box, Polars-free. The `[native]` extra now only adds `pyarrow`
  (the zero-copy Series bridge for the Polars fused-numeric fast path); it still resolves
  for back-compat.
- **Parquet read** needs `goldenflow[parquet]` (pyarrow); **Excel** needs
  `goldenflow[excel]` (openpyxl); **CSV/DB** read Polars-free out of the box.
- **CSV zero-config** (`transform("<x>.csv", config=None)`) now profiles columns *as text*
  (so `"01234"` stays a zip, not `1234`) — an intentional, data-cleaning-correct divergence
  from `pl.read_csv`'s numeric coercion. Cleaned text values are unchanged.

### Details

- **Polars-free public `transform()`.** `goldenflow.transform(data, config=...)` accepts a
  `dict[str, list]` OR a file path and returns a `ColumnarResult` (`.columns` + `.manifest`,
  with an opt-in `.to_polars()` bridge) — with **Polars never imported** for a covered
  config. `transform_df(pl.DataFrame)` stays as the Polars-backend adapter (it needs a
  `pl.DataFrame`, which needs Polars).
- **Every transform on the columnar path (9/9 gaps closed).** A `scalar=` / `scalar_dtype`
  / `scalar_factory` registry mechanism plus new columnar op-shapes (multi-input
  `merge_name`, flag-only `initial_expand`, whole-column `category_auto_correct`, and a
  synthetic-coerce numeric-INPUT path for `round`/`clamp`/`abs_value`/`fill_zero`) bring the
  full transform surface onto the Polars-free path. Owned by `goldenflow-native>=0.26.0`
  (adds `format_f64` + the numeric-input `AsFloat` parser).
- **Polars-free file readers.** `transform("<x>.csv")` (stdlib csv), `"<x>.parquet"`
  (pyarrow `to_pydict`, byte-identical to `pl.read_parquet`), and `"<x>.xlsx"` (openpyxl)
  all read without Polars. `connectors.database.read_database_columns(conn, query)` reads
  any PEP-249 (DBAPI) connection into a dict, no connectorx/polars.
- **Polars-free zero-config.** `transform(data, config=None)` profiles + auto-selects over
  plain lists (`profiler_bridge.profile_columns`), Polars-free. For a **dict / Parquet /
  Excel** input it is byte-identical to `transform_df(..., config=None)`; for **CSV** it
  profiles columns *as text* (so `"01234"` stays a zip, not `1234`) — an intentional
  data-cleaning-correct divergence from `pl.read_csv`'s numeric coercion.
- **New extras.** `goldenflow[polars]` (the optional Polars bulk-vectorized backend +
  `transform_df` + `pl.read_*` I/O) and `goldenflow[parquet]` (pyarrow, for Polars-free
  Parquet read). `goldenflow[native]` floor bumped to `goldenflow-native>=0.26.0`.

## 1.17.0 (2026-07-07)

Polars-eviction Phases 2-3: a config's owned transforms now run entirely on the native/Arrow substrate (no Polars, no pyarrow) on both the whole-file CSV path and the in-memory path, byte-identical (data + manifest) to the Polars engine. Needs `goldenflow-native>=0.24.0`. Opt in with `GOLDENFLOW_ENGINE=columnar`; anything the columnar path does not cover declines to the Polars engine, so behavior is never wrong.

- **Native CSV pipeline.** `goldenflow transform data.csv` (and `transform_file`) runs read -> transform -> write in ONE Rust call for an owned-transform config: the CSV is parsed into Rust-owned Arrow string columns, the owned chain runs, and the CSV is written back, with parallel read/write across record-boundary chunks. No `pl.DataFrame` is constructed.
- **Native Arrow `Column` (pyarrow-free).** The in-memory columnar path holds columns as Rust-owned Arrow buffers ingested from Polars over the C-Data / PyCapsule interface (`__arrow_c_stream__`) -- pyarrow-free -- and runs the owned chain zero-copy.
- **Auto-routed string chains.** A run auto-routes between the total (never-null) and nullable (`Option`-returning URL/company/email) fused chains.
- **Numeric columnar execution.** A `string* parser f64*` config (the f64 parsers `currency_strip`/`percentage_normalize`/`comma_decimal`/`scientific_to_decimal`/`fraction_to_decimal` and the i64 parsers `to_integer`/`roman_to_int`/`ordinal_to_int`, plus the array ops `round`/`clamp`/`abs_value`/`fill_zero`) runs natively, with a Polars-matching f64 formatter so the output + manifest match Polars byte-for-byte. An f64 op promotes an Int64 column to Float64, exactly as Polars does.
- **Multi-output splits.** `split_name`/`split_name_reverse` (-> `first_name`+`last_name`) and `split_address` (-> `street`+`city`+`state`+`zip`) append the fixed-name output columns, exactly as Polars' dataframe-mode `with_columns`.
- **CSV empty-string fix.** The native CSV writer now quotes an empty-string value as `""` (distinct from a null empty field), matching Polars' `write_csv`, so any empty-string transform output round-trips correctly.
- `goldenflow[native]` floor bumped to `goldenflow-native>=0.24.0` (the wheel carrying the columnar symbols).

## 1.16.0 (2026-07-06)

Pillar-1 (evict Polars from the transform execution path): the fused columnar apply now covers a **third kernel shape** — the `Option`-returning URL / company / email families — so those chains fuse too. Needs `goldenflow-native>=0.14.0`.

- **Nullable (`Option<String>`) fused chains.** A run of owned URL (`url_normalize`/`url_strip_tracking`/`url_strip_www`/`url_canonical`/`url_extract_domain`), company (`company_normalize`/`company_strip_legal`/`company_extract_legal`), and email (`email_mask`/`email_extract_domain`) transforms now fuses into ONE native pass (`goldenflow_core::chain::apply_chain_nullable` → native `apply_chain_nullable_arrow`). A value a kernel can't parse becomes a null cell that passes through the rest of the run — exactly as the per-transform path does (each transform's `map_str_to_str` skips null input, nulls on `None`). Byte-identical output frame (nulls included) AND audit manifest; the per-kernel affected count matches Polars' `(before != after).sum()`, which counts a row only when both sides are non-null and differ (a non-null→null row isn't counted).
- **Mixed runs.** A nullable run may include the total/parameterized string kernels (`strip`/`lowercase`/`truncate`/…) alongside the `Option`-returning ones — e.g. `strip → lowercase → url_normalize → url_strip_www` fuses as a single pass. Symbol-aware: a pre-0.14.0 wheel keeps fusing the total + numeric families and only the nullable ops break a run (no regression).

## 1.15.0 (2026-07-06)

Pillar-1 (evict Polars from the transform execution path): the fused columnar apply now covers a **second dtype and the parameterized string ops**, so more real chains collapse into one native Arrow pass (byte-identical output, lower peak RSS). Needs `goldenflow-native>=0.13.0` (republished with the new kernel symbols).

- **Numeric (f64) fused chains.** A maximal run of owned `round` / `clamp` / `abs_value` / `fill_zero` transforms on a `Float64` column now fuses into ONE native pass (`goldenflow_core::chain::apply_chain_f64` → native `apply_chain_f64_arrow`), instead of N per-transform Arrow round-trips + N column rebuilds. Each kernel dispatches to the SAME `numeric::*` core fn the per-transform path uses, so a fused run is byte-identical — output frame AND audit manifest (the per-kernel affected count matches Polars' `(before != after).sum()`, which excludes null-`before` rows, so `fill_zero`'s null→0.0 fills but isn't counted).
- **Parameterized string ops fuse.** `truncate` / `pad_left` / `pad_right` (and, on the numeric side, `round` / `clamp`) now join a fusable run via the superset native symbol `apply_chain_ops_arrow` / `apply_chain_f64_arrow` (`(name, params)` tuples). Symbol-aware: an older 0.12.0 wheel keeps fusing the no-arg families and only these break a run — no regression.
- **Mixed-dtype chains.** The engine recomputes the fusable set as a run advances, so a parser that changes the column dtype mid-chain (e.g. `currency_strip`: str→f64) lets the string head and the numeric tail each fuse in their own dtype.
- Fixed a sample-replay bug for parameterized `expr`-mode ops (the audit's before/after replay int-cast `pad_left`'s pad char; now passes `expr` params raw, exactly like the per-transform path).

## Unreleased

## 1.14.0 (2026-07-06)

**Fused columnar apply (Pillar-1, default-on).** A column's run of owned no-arg total string→string transforms (25 kernels: text/email/name) now fuses into ONE native pass over the Arrow buffer instead of crossing the Python/Polars/Arrow boundary once per transform. Output and the audit trail are byte-identical to the per-transform path; the win is lower memory — measured **~22% lower peak RSS at 5M rows** (fusion avoids materializing one intermediate column per transform), plus a modest wall speedup. On by default when the native kernel is available (needs `goldenflow-native >= 0.12.0`); opt out with `GOLDENFLOW_FUSED_APPLY=0`; pre-0.12.0 wheels and pure-Python installs fall back gracefully. See ADR 0034.

Owned-kernel depth (Waves 2-6): 21 NEW transforms, each an owned Rust kernel in `goldenflow-core` fanned out byte-for-byte to all six surfaces (native wheel, WASM/TS, pure-Python fallback, DuckDB extension, Postgres). Registry count 92 → 113; goldenflow-duckdb UDFs 74 → 98.

- **Phonetic (W2):** `soundex`, `double_metaphone_primary`, `double_metaphone_alt` — blocking/match keys for entity resolution.
- **Email/URL dedup depth (W3):** `email_canonical` (googlemail→gmail alias), `email_mask` (PII), `url_strip_tracking` (utm_*/gclid/fbclid), `url_strip_www`, `url_canonical` (composite dedup key).
- **Company (W4):** `company_normalize`, `company_strip_legal`, `company_extract_legal` — legal-suffix-aware org dedup.
- **Within-category breadth (W5):** `isin_validate`, `cusip_validate`, `npi_validate`, `luhn_validate`, `cc_brand` (identifiers); `name_initials`, `strip_middle` (name); `roman_to_int`, `ordinal_to_int`, `fraction_to_decimal` (numeric).
- **Boundary (W6):** `docs/design/2026-07-06-goldenflow-owned-kernel-boundary.md` + `tests/transforms/test_owned_kernel_boundary.py` — every registered transform is classified as owned (corpus/pinned) or a documented structural hole (dates non-portable, data-dependent categorical, `phone_validate` spec-fallback); a new transform can't merge unclassified.

## 1.13.0 (2026-07-05)

Wave D (category_auto_correct): the fuzzy, data-dependent categorical-autocorrect transform is now backed by an owned Rust kernel in `goldenflow-core::autocorrect` (native + WASM/TS + pure-Python fallback), Rust is the reference implementation. Migration, not addition (count unchanged).

- `goldenflow-core::autocorrect` owns the WHOLE correction-map algorithm: `fuzz_ratio` (the rapidfuzz `fuzz.ratio` Indel/LCS similarity) + `build_canonical_map` (frequency analysis → canonical determination → fuzzy matching → correction map, order-deterministic). The host computes `value_counts` and applies the returned map.
- **Cross-surface fix:** the TypeScript surface previously used a *different* fuzzy ratio (Levenshtein-based) than Python (rapidfuzz Indel), and did not strip whitespace on apply — so TS and Python produced different corrections. Both now conform to the single Rust kernel, so the corrected column is byte-identical across native, WASM/TS, and pure-Python. The Python fallback (rapidfuzz `_build_canonical_map`) remains the byte-exact reference the kernel replicates. `rapidfuzz` stays a Python dependency for that fallback.

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
