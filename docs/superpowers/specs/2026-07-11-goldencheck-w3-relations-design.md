# GoldenCheck W3 — relations (approx_duplicate + age_validation) — design

Date: 2026-07-11
Status: wave design (Arrow fused-scan program; /goal "all Ws implemented"). Pending spec review.
Program: `...-arrow-fused-scan-engine-program-design.md` + `...-W-path-scoping.md`. **W3 — the HARD relations wave.**
Base: fresh `origin/main` (W0-land + CSV + W1 + W2 merged/enqueuing; kernels: benford/keys/composite/FD/approx-FD/fuzzy/regex/str_to_date/csv_infer/column_aggregate/numeric_stats/count_outside/sequence_analysis/date_freshness).

## Goal

Fused Arrow-native Rust kernels for the two **explicitly R4-declined "Polars-accelerator-only"** relation profilers — `approx_duplicate` (`duplicate_rows` + `near_duplicate_rows`) and `age_validation` (`cross_column`). Both use relational-engine ops with NO Frame/Column-seam equivalent (`concat_str`/`group_by`/`join`; `pl.col`/`pl.lit`/`.dt` date-expr trees), so they are Polars-bound at runtime today (`docs/.../2026-07-09-goldencheck-relation-ports-r4-decline.md`). W3 supplies fused kernels under the program's **OWNED CONTRACT** (not bit-identity), shadow-wired: parity vs the Polars path with registered divergence classes where Polars' string-cast formatting differs. Rust = source of truth; authoritative findings stay Polars until the Flip.

Scope is the TWO hard ones only (per W-path scoping). The other relations (null_correlation, numeric_cross, temporal, identity_safe_pk) are out of W3 — note their state but don't touch them.

## The key tractability insight (approx_duplicate)

`approx_duplicate` builds a per-row **signature string** (each column `cast(Utf8).fill_null("")`, string cols additionally normalized: `to_lowercase -> replace_all(r"[^0-9a-z]+"," ") -> strip_chars`), joins columns with `\x1f`, then `group_by(signature).len()` and reports:
- `duplicate_rows`: rows whose EXACT signature repeats (`ec >= 2`) — count + `n_unique` groups.
- `near_duplicate_rows`: rows whose NORMALIZED signature repeats but exact does not (`nc >= 2 & ec < 2`) — count + groups.

**The reported COUNTS depend only on WHICH rows collide (produce equal signatures), not on the literal signature bytes.** So a kernel that uses its OWN deterministic cast-to-string yields IDENTICAL counts to Polars **iff its value→string map induces the same equality partition** as Polars' `cast(Utf8)`. For the common dtypes (int, string, bool) this holds exactly. The divergence surface is narrow + registered: float formatting (`-0.0` vs `0.0`, `NaN`, scientific-notation collisions), and any dtype where Polars' Utf8 cast merges/splits values differently than the kernel. These become a registered **"cast-collision" divergence class**; on int/string/bool-only data the registry is empty.

## Kernel A: `duplicate_signatures` (approx_duplicate)

`duplicate_signatures(columns: Vec<&dyn Array>) -> DupStats { exact_dup_rows, exact_dup_groups, near_dup_rows, near_dup_groups }` in `goldencheck-core`:
- Per row `i`: build `exact_sig` = join over columns of `cast_utf8(col[i]) or "" if null` with `\x1f`; build `norm_sig` = same but string columns get `normalize(s)` = `to_lowercase -> replace_all(r"[^0-9a-z]+"," ") -> trim` (reuse the `regex` kernel's replace + a lowercase; MATCH Polars `str.to_lowercase`/`str.strip_chars` — ASCII-focused, but verify Unicode lowercase parity or register). Non-string columns identical in both sigs (cast_utf8).
- Two `HashMap<sig, count>` passes (exact, norm). Then: `exact_dup_rows` = sum of counts where `ec>=2`; `exact_dup_groups` = number of distinct sigs with `ec>=2`; `near_dup_rows` = rows where `nc>=2 AND ec<2` (needs per-row `ec` — keep a per-row exact-count lookup); `near_dup_groups` = distinct norm sigs among those near rows (matches `near_dups["__norm__"].n_unique()`).
- **`cast_utf8` per Arrow dtype** must be DETERMINISTIC + induce Polars' equality partition: int/uint -> decimal; bool -> `"true"`/`"false"` (VERIFY Polars bool->Utf8 casing); float -> a documented form (register the float cast-collision divergence class — `-0.0`/`NaN`/repr); Date32 -> ISO `YYYY-MM-DD` (VERIFY Polars date->Utf8); Utf8 -> identity; null -> `""` (the `fill_null("")`). The formatting need only be SELF-consistent + partition-equal to Polars for parity — pin each dtype + register the residual.
- Native shim takes the pyarrow table's arrays; `_COMPONENT_SYMBOLS["duplicate_signatures"]`.

## Kernel B: `age_mismatch` (age_validation)

`age_mismatch(actual: &dyn Array, dob_epoch_days: &dyn Array, ref_epoch_days: i64) -> AgeStats { mismatch_count, sample_indices: Vec<usize> }`:
- `actual` = the age column cast to Float64 (the cast stays Python — Polars `cast(pl.Float64)`); `dob_epoch_days` = the DOB column ALREADY parsed to Date32 (parse stays Python via the existing `str_to_date` kernel / Polars `str.to_date`); `ref_epoch_days` = `(reference_date - 1970-01-01).days` (offset-free, like W2 B2).
- Per row: `expected = (ref_epoch_days - dob_days) as f64 / 365.25`; `non_null = actual and dob both present`; `mismatch = non_null AND |actual - expected| > 2.0`. `mismatch_count` = count; `sample_indices` = first-5 mismatch row indices (the caller maps them to `col_series.filter(mask).head(5)` values — the profiler samples the ORIGINAL age column, so the kernel returns INDICES and the caller gathers; VERIFY the mask ordering matches `filter(mismatch_mask).head(5)`).
- The reference-date selection (max non-dob date col <= today), the age/dob column DISCOVERY (name gates), and the DOB parse all stay Python. The kernel does the per-row arithmetic + mismatch scan ONLY. `365.25` exact; `> 2.0` strict.
- Native shim; `_COMPONENT_SYMBOLS["age_mismatch"]`.

## Wiring (shadow — mirrors W1/W2)
- `approx_duplicate.py`: after the Polars group_by/join computes the four numbers, when `native_enabled("duplicate_signatures")`, ALSO call the kernel on `df`'s Arrow arrays (`df.to_arrow()` columns) in SHADOW; discard. Emitted findings STAY Polars.
- `age_validation.py`: after the Polars `df.select(...)` mismatch compute, when `native_enabled("age_mismatch")`, ALSO call the kernel (on the Arrow `actual`/`dob` arrays + `ref_epoch_days`) in SHADOW; discard. Findings STAY Polars.
- Shadow test per profiler asserts kernel == Polars counts on a corpus (int/string/bool data => exact; float/edge => within registered divergence).

## Parity / contract
- `duplicate_signatures`: the four counts EXACT on int/string/bool data; register a **"cast-collision" divergence class** for float/edge (`-0.0`/`NaN`/float-repr) + any Unicode-lowercase residual. Parity harness fixtures: pure-string dup/near-dup, int dup, mixed, all-unique, empty; float fixtures under the registered class.
- `age_mismatch`: `mismatch_count` EXACT; `sample_indices` map to the same rows as `filter(mask).head(5)` (order-preserved). Register empty (integer-exact) — the /365.25 float compare uses `> 2.0` with the same f64 arithmetic Polars does (VERIFY total_days()/365.25 matches; if float-edge rows near the 2.0 boundary flip, register).
- Full-scan authoritative findings UNCHANGED (shadow); `import goldencheck` zero polars; existing relation tests UNEDITED.

## Testing
- Rust: `duplicate_signatures` — exact dups, near dups (case/space/punct), both, none, single-col, mixed-dtype, all-null; `age_mismatch` — matching ages, off-by>2, nulls, boundary 2.0, empty.
- Parity harness: both kernels vs the Polars profilers on random + adversarial frames; register the cast-collision class for duplicate float fixtures.
- Python: existing `approx_duplicate`/`age_validation` tests UNEDITED green (shadow, findings unchanged); shadow test asserts kernel==Polars counts.
- `import goldencheck` zero polars; cargo/clippy/wasm clean; all prior native symbols intact.

## Risks
- **Polars `cast(Utf8)` formatting parity** — the crux. Mitigated by the count-invariance insight (only the equality partition matters) + registered cast-collision class. Must pin each dtype's cast + VERIFY the partition matches (esp. bool casing, date ISO form, float).
- **normalize() Unicode** — Polars `str.to_lowercase` is Unicode-aware; the regex `[^0-9a-z]+` is ASCII. Match Polars' exact normalize (Unicode lowercase then ASCII-class replace) or register the residual.
- **age_mismatch sample ordering** — the profiler samples `col_series.filter(mismatch_mask).head(5)` (original age values, mask order); the kernel returns indices — the caller (shadow) must gather identically. Since it's shadow + discarded, only the parity test must match; pin it there.
- **multi-array FFI** — `duplicate_signatures` takes N arrays; the shim marshals a list of pyarrow arrays (like a table). Confirm the arrow=59 FFI handles a Vec of ArrayData (W0-land marshals single arrays — this is the first multi-array kernel; may need per-column calls returning per-row sig hashes, then group in Rust across a passed table, or accept `Vec<PyArrowType<ArrayData>>`).

## Non-goals
- No true fuzzy/edit-distance dup detection (explicitly a heavier follow-up in the profiler docstring). No other relations (null_correlation/numeric_cross/temporal/identity_safe_pk). No changing user-visible output (shadow). No polars-free wiring (Flip). No reference-date/column-discovery in Rust (stays Python).
