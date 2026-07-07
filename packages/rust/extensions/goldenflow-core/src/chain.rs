//! Fused apply — Pillar-1 of the Rust cutover: run a WHOLE chain of owned kernels
//! over a column in one pass, instead of crossing the boundary once per transform.
//! The `Kernel` enums + the arrow-free [`apply_chain_str`] are always compiled (the
//! WASM / pure-TS surfaces fuse without arrow); the Arrow-columnar executors below
//! are `#[cfg(feature = "arrow")]` (native-flow enables it).
//!
//! The host (`engine/transformer.py`) currently applies N transforms to a column
//! as N × (Series→Arrow export + kernel + Arrow→Series import + `with_columns` +
//! a full-column affected-count scan). [`apply_chain`] collapses a maximal run of
//! owned, string→string, no-arg kernels into ONE export/import: each row is
//! threaded `kN(...k2(k1(x)))` through two reused scratch buffers (no per-row heap
//! churn beyond those), and the per-kernel affected-row counts are returned so the
//! host can still emit a per-transform audit record (byte-identical manifest).
//!
//! Parity is by construction: each `Kernel` dispatches to the SAME owned kernel the
//! per-transform path uses (the `*_into` streaming variants where they exist, else
//! the scalar `fn(&str)->String`), so `apply_chain(x, [k1, k2])` is byte-identical
//! to `k2(k1(x))` applied sequentially. Proven in the tests below + the host-side
//! parity test.
//!
//! Three fusable shapes now live here, each with its own executor + arrow symbol:
//!   1. [`apply_chain`] — TOTAL string→string kernels (`Kernel`), incl. the
//!      parameterized `truncate`/`pad`. The fast path (two reused scratch buffers).
//!   2. [`apply_chain_f64`] — f64→f64 numeric kernels (`NumericKernel`:
//!      round/clamp/abs_value/fill_zero) on a `Float64Array`.
//!   3. [`apply_chain_nullable`] — `Option`-returning string kernels
//!      (`NullableKernel`: the URL/company/email families), which may MIX with the
//!      total kernels in one run; a value a kernel can't parse becomes a null cell
//!      that passes through the rest of the run.
//! Still on the per-transform path: `*_validate` (bool), multi-arity split/merge,
//! and the residual-tier phone/date families — see the boundary note in the design
//! doc / ADR 0034.

// Arrow imports back the columnar executors (native-flow, `--features arrow`).
// The `Kernel`/`NumericKernel`/`NullableKernel` enums + `apply_chain_str` below are
// arrow-FREE, so the WASM / pure surfaces get the fusable chain without pulling arrow.
#[cfg(feature = "arrow")]
use arrow_array::builder::Float64Builder;
#[cfg(feature = "arrow")]
use arrow_array::{Array, Float64Array, GenericStringArray, OffsetSizeTrait};
#[cfg(feature = "arrow")]
use arrow_buffer::{Buffer, OffsetBuffer, ScalarBuffer};

// `email`/`names`/`text` back the arrow-free `Kernel` (via `apply_into`); the
// `numeric`/`company`/`url` families are only reached by the arrow-gated
// NumericKernel/NullableKernel executors.
#[cfg(feature = "arrow")]
use crate::{company, numeric, url};
use crate::{email, names, phonetic, text};

/// One owned, no-arg, total string→string kernel eligible for the fused chain.
/// The name mapping (`from_name`) is the single source of truth the host's
/// `FUSABLE` table mirrors; the chain-coverage guard test asserts they agree.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kernel {
    Strip,
    Lowercase,
    Uppercase,
    TitleCase,
    FixMojibake,
    CollapseWhitespace,
    NormalizeQuotes,
    NormalizeLineEndings,
    NormalizeUnicode,
    RemoveHtmlTags,
    RemoveUrls,
    RemoveDigits,
    RemovePunctuation,
    RemoveEmojis,
    ExtractNumbers,
    // email family (total string->string)
    EmailLowercase,
    EmailNormalize,
    EmailCanonical,
    // name family (total string->string normalizers)
    NameTransliterate,
    StripTitles,
    StripSuffixes,
    NameProper,
    NicknameStandardize,
    NameInitials,
    StripMiddle,
    // phonetic family (total string->string keys)
    Soundex,
    DoubleMetaphonePrimary,
    DoubleMetaphoneAlt,
    // parameterized string->string (carry their args; total, never null)
    Truncate(usize),
    PadLeft(usize, char),
    PadRight(usize, char),
}

impl Kernel {
    /// Resolve a registered transform name to its chain kernel, or `None` if the
    /// transform is not fusable (Option-returning, parameterized, multi-arity, or
    /// simply not yet mapped). Host and kernel MUST agree on this table — the
    /// coverage guard test enforces it.
    pub fn from_name(name: &str) -> Option<Kernel> {
        Some(match name {
            "strip" => Kernel::Strip,
            "lowercase" => Kernel::Lowercase,
            "uppercase" => Kernel::Uppercase,
            "title_case" => Kernel::TitleCase,
            "fix_mojibake" => Kernel::FixMojibake,
            "collapse_whitespace" => Kernel::CollapseWhitespace,
            "normalize_quotes" => Kernel::NormalizeQuotes,
            "normalize_line_endings" => Kernel::NormalizeLineEndings,
            "normalize_unicode" => Kernel::NormalizeUnicode,
            "remove_html_tags" => Kernel::RemoveHtmlTags,
            "remove_urls" => Kernel::RemoveUrls,
            "remove_digits" => Kernel::RemoveDigits,
            "remove_punctuation" => Kernel::RemovePunctuation,
            "remove_emojis" => Kernel::RemoveEmojis,
            "extract_numbers" => Kernel::ExtractNumbers,
            "email_lowercase" => Kernel::EmailLowercase,
            "email_normalize" => Kernel::EmailNormalize,
            "email_canonical" => Kernel::EmailCanonical,
            "name_transliterate" => Kernel::NameTransliterate,
            "strip_titles" => Kernel::StripTitles,
            "strip_suffixes" => Kernel::StripSuffixes,
            "name_proper" => Kernel::NameProper,
            "nickname_standardize" => Kernel::NicknameStandardize,
            "name_initials" => Kernel::NameInitials,
            "strip_middle" => Kernel::StripMiddle,
            "soundex" => Kernel::Soundex,
            "double_metaphone_primary" => Kernel::DoubleMetaphonePrimary,
            "double_metaphone_alt" => Kernel::DoubleMetaphoneAlt,
            _ => return None,
        })
    }

    /// Resolve a transform name + its (string) params to a chain kernel — the
    /// superset of [`from_name`](Self::from_name) that also handles the
    /// parameterized string ops. Defaults + negative-clamping mirror the
    /// native-flow arrow shims exactly (`truncate` n=255; `pad_left` width=10
    /// pad='0'; `pad_right` width=10 pad=' '; a negative width/n clamps to 0).
    pub fn from_op(name: &str, params: &[&str]) -> Option<Kernel> {
        let usize_arg = |i: usize, default: usize| {
            params
                .get(i)
                .and_then(|p| p.parse::<i64>().ok())
                .map(|n| if n < 0 { 0 } else { n as usize })
                .unwrap_or(default)
        };
        let char_arg = |i: usize, default: char| {
            params
                .get(i)
                .and_then(|p| p.chars().next())
                .unwrap_or(default)
        };
        match name {
            "truncate" => Some(Kernel::Truncate(usize_arg(0, 255))),
            "pad_left" => Some(Kernel::PadLeft(usize_arg(0, 10), char_arg(1, '0'))),
            "pad_right" => Some(Kernel::PadRight(usize_arg(0, 10), char_arg(1, ' '))),
            _ => Kernel::from_name(name),
        }
    }

    /// Parameterized fusable names — need `apply_chain_ops_arrow` (not the older
    /// no-arg `apply_chain_arrow`). Kept separate so a pre-0.13.0 wheel still fuses
    /// the no-arg families and only these break a run.
    pub const PARAM_NAMES: &'static [&'static str] = &["truncate", "pad_left", "pad_right"];

    /// Every fusable kernel name, for the coverage guard.
    pub const ALL_NAMES: &'static [&'static str] = &[
        "strip",
        "lowercase",
        "uppercase",
        "title_case",
        "fix_mojibake",
        "collapse_whitespace",
        "normalize_quotes",
        "normalize_line_endings",
        "normalize_unicode",
        "remove_html_tags",
        "remove_urls",
        "remove_digits",
        "remove_punctuation",
        "remove_emojis",
        "extract_numbers",
        "email_lowercase",
        "email_normalize",
        "email_canonical",
        "name_transliterate",
        "strip_titles",
        "strip_suffixes",
        "name_proper",
        "nickname_standardize",
        "name_initials",
        "strip_middle",
        "soundex",
        "double_metaphone_primary",
        "double_metaphone_alt",
    ];

    /// Append `s` transformed by this kernel into `out` (which the caller has
    /// cleared). Uses the `*_into` streaming variant where one exists (zero alloc);
    /// otherwise wraps the scalar `fn(&str)->String`. Byte-identical to the owned
    /// kernel the per-transform path calls.
    #[inline]
    fn apply_into(self, s: &str, out: &mut String) {
        match self {
            Kernel::Strip => out.push_str(text::strip(s)),
            Kernel::Lowercase => out.push_str(&text::lowercase(s)),
            Kernel::Uppercase => out.push_str(&text::uppercase(s)),
            Kernel::TitleCase => out.push_str(&text::title_case(s)),
            Kernel::FixMojibake => out.push_str(&text::fix_mojibake(s)),
            Kernel::CollapseWhitespace => text::collapse_whitespace_into(s, out),
            Kernel::NormalizeQuotes => text::normalize_quotes_into(s, out),
            Kernel::NormalizeLineEndings => text::normalize_line_endings_into(s, out),
            Kernel::NormalizeUnicode => text::normalize_unicode_into(s, out),
            Kernel::RemoveHtmlTags => text::remove_html_tags_into(s, out),
            Kernel::RemoveUrls => text::remove_urls_into(s, out),
            Kernel::RemoveDigits => text::remove_digits_into(s, out),
            Kernel::RemovePunctuation => text::remove_punctuation_into(s, out),
            Kernel::RemoveEmojis => text::remove_emojis_into(s, out),
            Kernel::ExtractNumbers => out.push_str(&text::extract_numbers(s)),
            Kernel::EmailLowercase => out.push_str(&email::email_lowercase(s)),
            Kernel::EmailNormalize => out.push_str(&email::email_normalize(s)),
            Kernel::EmailCanonical => out.push_str(&email::email_canonical(s)),
            Kernel::NameTransliterate => out.push_str(&names::name_transliterate(s)),
            Kernel::StripTitles => out.push_str(&names::strip_titles(s)),
            Kernel::StripSuffixes => out.push_str(&names::strip_suffixes(s)),
            Kernel::NameProper => out.push_str(&names::name_proper(s)),
            Kernel::NicknameStandardize => out.push_str(&names::nickname_standardize(s)),
            Kernel::NameInitials => out.push_str(&names::name_initials(s)),
            Kernel::StripMiddle => out.push_str(&names::strip_middle(s)),
            Kernel::Soundex => out.push_str(&phonetic::soundex(s)),
            Kernel::DoubleMetaphonePrimary => out.push_str(&phonetic::double_metaphone_primary(s)),
            Kernel::DoubleMetaphoneAlt => out.push_str(&phonetic::double_metaphone_alt(s)),
            Kernel::Truncate(n) => out.push_str(&text::truncate(s, n)),
            Kernel::PadLeft(w, p) => text::pad_left_into(s, w, p, out),
            Kernel::PadRight(w, p) => text::pad_right_into(s, w, p, out),
        }
    }
}

/// The fused output: the transformed column plus, for audit parity with the
/// per-transform path, the per-kernel affected-row count (`changed[i]` = rows the
/// i-th kernel altered, comparing the value before vs after that kernel — exactly
/// what the host's `(before != after).sum()` computes per transform). Generic over
/// the offset width so the output matches the input (Utf8 `i32` / LargeUtf8 `i64`).
/// Arrow-FREE total string chain, for non-arrow surfaces (WASM / TS): thread each
/// value through `kernels` and return `(transformed values, per-kernel changed
/// counts)`. Same composition + change-counting as the columnar [`apply_chain`]
/// (identical `apply_into` dispatch, two reused scratch buffers, `changed[i]` =
/// rows the i-th kernel altered), minus the offset-buffer machinery. Total kernels
/// never null, so callers pass only the NON-NULL values and scatter the results
/// back into their positions, leaving null cells untouched — hence `changed[i]`
/// already matches the host's per-op `(before != after)` count over non-null rows.
pub fn apply_chain_str(values: &[&str], kernels: &[Kernel]) -> (Vec<String>, Vec<u64>) {
    let mut out = Vec::with_capacity(values.len());
    let mut changed = vec![0u64; kernels.len()];
    let mut cur = String::new();
    let mut next = String::new();
    for &s0 in values {
        cur.clear();
        cur.push_str(s0);
        for (i, k) in kernels.iter().enumerate() {
            next.clear();
            k.apply_into(&cur, &mut next);
            if next != cur {
                changed[i] += 1;
            }
            std::mem::swap(&mut cur, &mut next);
        }
        out.push(cur.clone());
    }
    (out, changed)
}

#[cfg(feature = "arrow")]
pub struct ChainResult<O: OffsetSizeTrait> {
    pub array: GenericStringArray<O>,
    pub changed: Vec<u64>,
}

/// Apply `kernels` in order to every non-null row of `arr`, in one pass. Nulls pass
/// through unchanged (offset frozen, null bitmap cloned). `changed[i]` counts the
/// non-null rows the i-th kernel altered.
///
/// Generic over `O` (i32 for `StringArray`/Utf8, i64 for `LargeStringArray`/
/// LargeUtf8) because **Polars exports strings as LargeUtf8** — a non-generic i32
/// path would silently never fire on real Polars data.
#[cfg(feature = "arrow")]
pub fn apply_chain<O: OffsetSizeTrait>(
    arr: &GenericStringArray<O>,
    kernels: &[Kernel],
) -> ChainResult<O> {
    let len = arr.len();
    let mut changed = vec![0u64; kernels.len()];
    let mut offsets: Vec<O> = Vec::with_capacity(len + 1);
    offsets.push(O::from_usize(0).expect("0 fits any offset"));
    let mut values = String::with_capacity(arr.values().len());
    // Two reused scratch buffers ping-ponged across kernels; declared outside the
    // row loop so the allocation amortizes over the whole column.
    let mut cur = String::new();
    let mut next = String::new();
    for v in arr.iter() {
        if let Some(s0) = v {
            cur.clear();
            cur.push_str(s0);
            for (i, k) in kernels.iter().enumerate() {
                next.clear();
                k.apply_into(&cur, &mut next);
                if next != cur {
                    changed[i] += 1;
                }
                std::mem::swap(&mut cur, &mut next);
            }
            values.push_str(&cur);
        }
        offsets.push(O::from_usize(values.len()).expect("string column exceeds offset width"));
    }
    let array = GenericStringArray::<O>::new(
        OffsetBuffer::new(ScalarBuffer::from(offsets)),
        Buffer::from_vec(values.into_bytes()),
        arr.nulls().cloned(),
    );
    ChainResult { array, changed }
}

// ---------------------------------------------------------------------------
// Numeric (f64) fused chain — the second dtype. Same idea as the string chain
// above, but the run is a maximal sequence of owned f64->f64 kernels applied to
// a `Float64Array` in one Arrow pass instead of N per-transform round-trips.
// ---------------------------------------------------------------------------

/// One owned numeric kernel eligible for the fused f64 chain. Each dispatches to
/// the SAME `numeric::*` core fn the per-transform path calls, so a fused run is
/// byte-identical to applying the transforms sequentially. `round`/`clamp` carry
/// their params; `abs_value`/`fill_zero` are no-arg. `f64` has no `Eq`, so this
/// derives only `PartialEq`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum NumericKernel {
    Round(i32),
    Clamp(f64, f64),
    AbsValue,
    FillZero,
}

impl NumericKernel {
    /// Resolve a transform name + its (string) params to a numeric chain kernel,
    /// or `None` if not a fusable f64 op. Defaults mirror the per-transform arrow
    /// shims / Python signatures EXACTLY (`round` n=2; `clamp` min=0.0 max=1.0);
    /// a non-parseable param falls back to the default for that slot.
    pub fn from_op(name: &str, params: &[&str]) -> Option<NumericKernel> {
        let i32_arg = |i: usize, default: i32| {
            params
                .get(i)
                .and_then(|p| p.parse::<i32>().ok())
                .unwrap_or(default)
        };
        let f64_arg = |i: usize, default: f64| {
            params
                .get(i)
                .and_then(|p| p.parse::<f64>().ok())
                .unwrap_or(default)
        };
        match name {
            "round" => Some(NumericKernel::Round(i32_arg(0, 2))),
            "clamp" => Some(NumericKernel::Clamp(f64_arg(0, 0.0), f64_arg(1, 1.0))),
            "abs_value" => Some(NumericKernel::AbsValue),
            "fill_zero" => Some(NumericKernel::FillZero),
            _ => None,
        }
    }

    /// Names carrying params (need to be recognized as fusable even with args) —
    /// the host mirrors this so `round:2` / `clamp:0:100` join a run.
    pub const PARAM_NAMES: &'static [&'static str] = &["round", "clamp"];

    /// Every fusable numeric kernel name, for the coverage guard.
    pub const ALL_NAMES: &'static [&'static str] = &["round", "clamp", "abs_value", "fill_zero"];

    /// Apply this kernel to one optional value. Operates on `Option<f64>` because
    /// `fill_zero` is fundamentally a null-handling op (null -> 0.0); the value
    /// kernels pass a null straight through (`None -> None`).
    #[cfg(feature = "arrow")]
    #[inline]
    fn apply(self, v: Option<f64>) -> Option<f64> {
        match self {
            NumericKernel::Round(n) => v.map(|x| numeric::round_f64(x, n)),
            NumericKernel::Clamp(lo, hi) => v.map(|x| numeric::clamp_f64(x, lo, hi)),
            NumericKernel::AbsValue => v.map(numeric::abs_f64),
            NumericKernel::FillZero => Some(numeric::fill_zero(v)),
        }
    }
}

/// Fused f64 output: the transformed column + per-kernel affected-row counts.
#[cfg(feature = "arrow")]
pub struct F64ChainResult {
    pub array: Float64Array,
    pub changed: Vec<u64>,
}

/// Apply `kernels` in order to every row of `arr`, in one pass. Value kernels
/// leave nulls null; `fill_zero` turns a null into `0.0`.
///
/// `changed[i]` counts the rows the i-th kernel altered, matching the host's
/// per-transform `(before.cast(Utf8) != after.cast(Utf8)).sum()` — which in
/// Polars EXCLUDES a row whose *before* is null (a null `!=` yields null, and
/// `.sum()` skips it). So a `fill_zero` that turns null->0.0 is NOT counted,
/// exactly as the per-transform path reports it; hence the `cur.is_some()` guard.
/// (Edge: `-0.0`/`NaN` values compare by IEEE-754 here, not by their Utf8 text,
/// so a `-0.0`->`0.0` or `NaN` row's count could differ from the Utf8 path; the
/// output array is byte-identical regardless. Documented, not chased.)
#[cfg(feature = "arrow")]
pub fn apply_chain_f64(arr: &Float64Array, kernels: &[NumericKernel]) -> F64ChainResult {
    let len = arr.len();
    let mut changed = vec![0u64; kernels.len()];
    let mut builder = Float64Builder::with_capacity(len);
    for i in 0..len {
        let mut cur: Option<f64> = if arr.is_null(i) {
            None
        } else {
            Some(arr.value(i))
        };
        for (ki, k) in kernels.iter().enumerate() {
            let next = k.apply(cur);
            if cur.is_some() && next != cur {
                changed[ki] += 1;
            }
            cur = next;
        }
        match cur {
            Some(x) => builder.append_value(x),
            None => builder.append_null(),
        }
    }
    F64ChainResult {
        array: builder.finish(),
        changed,
    }
}

// ---------------------------------------------------------------------------
// Nullable string chain — the third fusable shape. The URL / company / email
// families return `Option<String>` (None when the input can't be parsed → a
// NULL output cell). They can't ride the total `apply_chain` (which never
// nulls), so this executor threads `Option<String>`: once a cell goes null it
// passes through the rest of the run untouched, exactly as the per-transform
// path does (each transform's `map_str_to_str` skips null input, nulls on None).
// A run may MIX total kernels (wrapped as `Total`) with nullable ones.
// ---------------------------------------------------------------------------

/// One kernel eligible for the nullable fused chain: either an existing total
/// kernel (always `Some`) or an `Option`-returning URL/company/email kernel.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum NullableKernel {
    Total(Kernel),
    UrlNormalize,
    UrlStripTracking,
    UrlStripWww,
    UrlCanonical,
    UrlExtractDomain,
    CompanyNormalize,
    CompanyStripLegal,
    CompanyExtractLegal,
    EmailMask,
    EmailExtractDomain,
}

impl NullableKernel {
    /// Resolve a name + params to a nullable chain kernel. Nullable names map to
    /// their own variant; everything else falls back to a `Total(Kernel)` (so a
    /// run mixing `strip`/`lowercase` with `url_normalize` fuses as one). `None`
    /// only if the name isn't fusable at all.
    pub fn from_op(name: &str, params: &[&str]) -> Option<NullableKernel> {
        Some(match name {
            "url_normalize" => NullableKernel::UrlNormalize,
            "url_strip_tracking" => NullableKernel::UrlStripTracking,
            "url_strip_www" => NullableKernel::UrlStripWww,
            "url_canonical" => NullableKernel::UrlCanonical,
            "url_extract_domain" => NullableKernel::UrlExtractDomain,
            "company_normalize" => NullableKernel::CompanyNormalize,
            "company_strip_legal" => NullableKernel::CompanyStripLegal,
            "company_extract_legal" => NullableKernel::CompanyExtractLegal,
            "email_mask" => NullableKernel::EmailMask,
            "email_extract_domain" => NullableKernel::EmailExtractDomain,
            _ => return Kernel::from_op(name, params).map(NullableKernel::Total),
        })
    }

    /// The `Option`-returning kernel names (need `apply_chain_nullable_arrow`).
    pub const NULLABLE_NAMES: &'static [&'static str] = &[
        "url_normalize",
        "url_strip_tracking",
        "url_strip_www",
        "url_canonical",
        "url_extract_domain",
        "company_normalize",
        "company_strip_legal",
        "company_extract_legal",
        "email_mask",
        "email_extract_domain",
    ];

    /// Apply to one present value, returning `None` when the kernel can't parse
    /// it (→ a null output cell). Total kernels always return `Some`.
    #[cfg(feature = "arrow")]
    #[inline]
    fn apply(self, s: &str) -> Option<String> {
        match self {
            NullableKernel::Total(k) => {
                let mut buf = String::new();
                k.apply_into(s, &mut buf);
                Some(buf)
            }
            NullableKernel::UrlNormalize => url::url_normalize(s),
            NullableKernel::UrlStripTracking => url::url_strip_tracking(s),
            NullableKernel::UrlStripWww => url::url_strip_www(s),
            NullableKernel::UrlCanonical => url::url_canonical(s),
            NullableKernel::UrlExtractDomain => url::url_extract_domain(s),
            NullableKernel::CompanyNormalize => company::company_normalize(s),
            NullableKernel::CompanyStripLegal => company::company_strip_legal(s),
            NullableKernel::CompanyExtractLegal => company::company_extract_legal(s),
            NullableKernel::EmailMask => email::email_mask(s),
            NullableKernel::EmailExtractDomain => email::email_extract_domain(s),
        }
    }
}

/// Apply `kernels` over `arr`, threading `Option<String>`: a null (input or
/// produced by a kernel returning `None`) passes through the rest of the run.
///
/// `changed[i]` matches the host's per-transform `(before != after).sum()`,
/// which in Polars counts a row only when BOTH before and after are non-null and
/// differ (a null on either side yields a null comparison, skipped by `.sum()`).
/// So a row a kernel turns non-null→null is NOT counted (nor is a null-before
/// row). Generic over offset width for the LargeUtf8 Polars shape.
#[cfg(feature = "arrow")]
pub fn apply_chain_nullable<O: OffsetSizeTrait>(
    arr: &GenericStringArray<O>,
    kernels: &[NullableKernel],
) -> ChainResult<O> {
    let len = arr.len();
    let mut changed = vec![0u64; kernels.len()];
    let mut offsets: Vec<O> = Vec::with_capacity(len + 1);
    offsets.push(O::from_usize(0).expect("0 fits any offset"));
    let mut values = String::with_capacity(arr.values().len());
    let mut nulls = arrow_buffer::NullBufferBuilder::new(len);
    for v in arr.iter() {
        let mut cur: Option<String> = v.map(str::to_string);
        for (i, k) in kernels.iter().enumerate() {
            let next = match &cur {
                Some(s) => k.apply(s),
                None => None,
            };
            if let (Some(b), Some(a)) = (&cur, &next) {
                if b != a {
                    changed[i] += 1;
                }
            }
            cur = next;
        }
        match cur {
            Some(s) => {
                values.push_str(&s);
                nulls.append_non_null();
            }
            None => nulls.append_null(),
        }
        offsets.push(O::from_usize(values.len()).expect("string column exceeds offset width"));
    }
    let array = GenericStringArray::<O>::new(
        OffsetBuffer::new(ScalarBuffer::from(offsets)),
        Buffer::from_vec(values.into_bytes()),
        nulls.finish(),
    );
    ChainResult { array, changed }
}

// Arrow-free tests — run on a plain `cargo test` (no `--features arrow`), so the
// WASM/pure chain path stays covered even when arrow isn't compiled in.
#[cfg(test)]
mod vec_tests {
    use super::*;

    #[test]
    fn apply_chain_str_threads_kernels_and_counts() {
        let vals = ["  John  SMITH  ", "<b>a</b>  B", "café  #7"];
        let kernels = [
            Kernel::Strip,
            Kernel::Lowercase,
            Kernel::CollapseWhitespace,
            Kernel::RemoveHtmlTags,
        ];
        let (out, changed) = apply_chain_str(&vals, &kernels);
        // Values: compare against a manual sequential fold through the same apply_into.
        let expect: Vec<String> = vals
            .iter()
            .map(|s| {
                let mut cur = s.to_string();
                for k in &kernels {
                    let mut b = String::new();
                    k.apply_into(&cur, &mut b);
                    cur = b;
                }
                cur
            })
            .collect();
        assert_eq!(out, expect);
        // Counts: independently recompute per-step (before != after) over the values.
        let mut cur: Vec<String> = vals.iter().map(|s| s.to_string()).collect();
        let mut exp_changed = vec![0u64; kernels.len()];
        for (i, k) in kernels.iter().enumerate() {
            let nxt: Vec<String> = cur
                .iter()
                .map(|s| {
                    let mut b = String::new();
                    k.apply_into(s, &mut b);
                    b
                })
                .collect();
            for r in 0..cur.len() {
                if cur[r] != nxt[r] {
                    exp_changed[i] += 1;
                }
            }
            cur = nxt;
        }
        assert_eq!(changed, exp_changed);
    }

    #[test]
    fn apply_chain_str_empty_and_single() {
        assert_eq!(
            apply_chain_str(&[], &[Kernel::Strip]),
            (Vec::<String>::new(), vec![0u64])
        );
        assert_eq!(
            apply_chain_str(&["  x  "], &[Kernel::Strip]),
            (vec!["x".to_string()], vec![1u64])
        );
    }
}

#[cfg(all(test, feature = "arrow"))]
mod tests {
    use super::*;
    use arrow_array::StringArray;

    fn sample() -> StringArray {
        StringArray::from(vec![
            Some("  John  SMITH  "),
            Some("<b>o'Brien</b>  http://x.com/y"),
            Some("caf\u{e9}  123!"),
            Some(""),
            None,
            Some("hi \u{1f600}  \u{201c}Q\u{201d}"),
        ])
    }

    /// Apply the same kernels sequentially (each producing a fresh StringArray, the
    /// per-transform shape) as the parity oracle.
    fn sequential(arr: &StringArray, kernels: &[Kernel]) -> StringArray {
        let mut cur = arr.clone();
        for k in kernels {
            let out: StringArray = cur
                .iter()
                .map(|v| {
                    v.map(|s| {
                        let mut b = String::new();
                        k.apply_into(s, &mut b);
                        b
                    })
                })
                .collect();
            cur = out;
        }
        cur
    }

    #[test]
    fn chain_matches_sequential() {
        // Exercise several orderings incl. the common cleanup chain.
        let chains: &[&[Kernel]] = &[
            &[Kernel::Strip, Kernel::Lowercase],
            &[
                Kernel::Strip,
                Kernel::Lowercase,
                Kernel::CollapseWhitespace,
                Kernel::RemovePunctuation,
            ],
            &[
                Kernel::RemoveHtmlTags,
                Kernel::RemoveUrls,
                Kernel::Strip,
                Kernel::CollapseWhitespace,
            ],
            &[
                Kernel::NormalizeUnicode,
                Kernel::Lowercase,
                Kernel::RemoveDigits,
            ],
            &[
                Kernel::RemoveEmojis,
                Kernel::NormalizeQuotes,
                Kernel::Uppercase,
            ],
            // widened families: email + name normalizers + extract_numbers.
            &[
                Kernel::Strip,
                Kernel::Lowercase,
                Kernel::EmailNormalize,
                Kernel::EmailCanonical,
            ],
            &[
                Kernel::NameTransliterate,
                Kernel::NameProper,
                Kernel::StripTitles,
                Kernel::StripSuffixes,
                Kernel::StripMiddle,
            ],
            &[Kernel::NicknameStandardize, Kernel::NameInitials],
            &[Kernel::ExtractNumbers],
            // parameterized string ops mixed into a run.
            &[Kernel::Strip, Kernel::Lowercase, Kernel::Truncate(5)],
            &[Kernel::Strip, Kernel::PadLeft(8, '0')],
            &[Kernel::Truncate(3), Kernel::PadRight(6, '_')],
        ];
        let arr = sample();
        for chain in chains {
            let fused = apply_chain(&arr, chain);
            let seq = sequential(&arr, chain);
            assert_eq!(fused.array, seq, "chain {chain:?} != sequential");
        }
    }

    #[test]
    fn from_op_parses_params_and_defaults() {
        // explicit params
        assert_eq!(
            Kernel::from_op("truncate", &["50"]),
            Some(Kernel::Truncate(50))
        );
        assert_eq!(
            Kernel::from_op("pad_left", &["10", "0"]),
            Some(Kernel::PadLeft(10, '0'))
        );
        assert_eq!(
            Kernel::from_op("pad_right", &["6", " "]),
            Some(Kernel::PadRight(6, ' '))
        );
        // defaults mirror the arrow shims when a param is missing
        assert_eq!(
            Kernel::from_op("truncate", &[]),
            Some(Kernel::Truncate(255))
        );
        assert_eq!(
            Kernel::from_op("pad_left", &[]),
            Some(Kernel::PadLeft(10, '0'))
        );
        assert_eq!(
            Kernel::from_op("pad_right", &[]),
            Some(Kernel::PadRight(10, ' '))
        );
        // negative width clamps to 0 (matches the shim)
        assert_eq!(
            Kernel::from_op("truncate", &["-5"]),
            Some(Kernel::Truncate(0))
        );
        // no-arg names delegate to from_name; unknown -> None
        assert_eq!(Kernel::from_op("strip", &[]), Some(Kernel::Strip));
        assert_eq!(Kernel::from_op("split_name", &[]), None);
    }

    #[test]
    fn changed_counts_match_per_step_diff() {
        let arr = sample();
        let chain = [Kernel::Strip, Kernel::Lowercase, Kernel::CollapseWhitespace];
        let fused = apply_chain(&arr, &chain);
        // Recompute per-step changed counts independently via the sequential path.
        let mut cur = arr.clone();
        let mut expected = vec![0u64; chain.len()];
        for (i, k) in chain.iter().enumerate() {
            let out = sequential(&cur, &[*k]);
            for r in 0..cur.len() {
                if !cur.is_null(r) && cur.value(r) != out.value(r) {
                    expected[i] += 1;
                }
            }
            cur = out;
        }
        assert_eq!(fused.changed, expected);
    }

    #[test]
    fn single_kernel_equals_the_owned_kernel() {
        let arr = sample();
        let fused = apply_chain(&arr, &[Kernel::CollapseWhitespace]);
        let direct: StringArray = arr
            .iter()
            .map(|v| v.map(text::collapse_whitespace))
            .collect();
        assert_eq!(fused.array, direct);
    }

    #[test]
    fn preserves_nulls_and_empty() {
        let arr = StringArray::from(vec![None, Some(""), Some("  X  "), None]);
        let out = apply_chain(&arr, &[Kernel::Strip, Kernel::Lowercase]);
        assert!(out.array.is_null(0));
        assert_eq!(out.array.value(1), "");
        assert_eq!(out.array.value(2), "x");
        assert!(out.array.is_null(3));
    }

    #[test]
    fn works_on_large_utf8_the_polars_shape() {
        // Polars exports strings as LargeUtf8 (i64 offsets); the generic path must
        // fire on it, not just Utf8. Same bytes as the i32 path.
        use arrow_array::LargeStringArray;
        let large = LargeStringArray::from(vec![Some("  A  b  "), None, Some("C")]);
        let small = StringArray::from(vec![Some("  A  b  "), None, Some("C")]);
        let chain = [Kernel::Strip, Kernel::Lowercase, Kernel::CollapseWhitespace];
        let lo = apply_chain(&large, &chain);
        let so = apply_chain(&small, &chain);
        assert_eq!(lo.changed, so.changed);
        for i in 0..lo.array.len() {
            assert_eq!(lo.array.is_null(i), so.array.is_null(i));
            if !lo.array.is_null(i) {
                assert_eq!(lo.array.value(i), so.array.value(i));
            }
        }
    }

    #[test]
    fn from_name_round_trips_all() {
        for name in Kernel::ALL_NAMES {
            assert!(Kernel::from_name(name).is_some(), "{name} unmapped");
        }
        assert_eq!(Kernel::from_name("truncate"), None);
        assert_eq!(Kernel::from_name("split_name"), None);
    }

    // --- numeric (f64) chain ---

    fn f64_sample() -> Float64Array {
        Float64Array::from(vec![
            Some(1.234_5),
            Some(-9.876),
            None,
            Some(0.0),
            Some(1000.0),
            Some(-0.4),
        ])
    }

    /// Apply numeric kernels sequentially (fresh array per step) as the oracle.
    fn seq_f64(arr: &Float64Array, kernels: &[NumericKernel]) -> Float64Array {
        let mut cur = arr.clone();
        for k in kernels {
            let out: Float64Array = (0..cur.len())
                .map(|i| {
                    let v = if cur.is_null(i) {
                        None
                    } else {
                        Some(cur.value(i))
                    };
                    k.apply(v)
                })
                .collect();
            cur = out;
        }
        cur
    }

    #[test]
    fn f64_chain_matches_sequential() {
        let chains: &[&[NumericKernel]] = &[
            &[NumericKernel::Round(2), NumericKernel::Clamp(0.0, 100.0)],
            &[NumericKernel::AbsValue, NumericKernel::Round(1)],
            &[NumericKernel::Round(0), NumericKernel::FillZero],
            &[
                NumericKernel::FillZero,
                NumericKernel::Clamp(-5.0, 5.0),
                NumericKernel::AbsValue,
            ],
        ];
        let arr = f64_sample();
        for chain in chains {
            let fused = apply_chain_f64(&arr, chain);
            let seq = seq_f64(&arr, chain);
            assert_eq!(fused.array, seq, "f64 chain {chain:?} != sequential");
        }
    }

    #[test]
    fn f64_from_op_parses_params_and_defaults() {
        assert_eq!(
            NumericKernel::from_op("round", &["3"]),
            Some(NumericKernel::Round(3))
        );
        assert_eq!(
            NumericKernel::from_op("clamp", &["0", "100"]),
            Some(NumericKernel::Clamp(0.0, 100.0))
        );
        // defaults mirror the arrow shims / Python signatures
        assert_eq!(
            NumericKernel::from_op("round", &[]),
            Some(NumericKernel::Round(2))
        );
        assert_eq!(
            NumericKernel::from_op("clamp", &[]),
            Some(NumericKernel::Clamp(0.0, 1.0))
        );
        assert_eq!(
            NumericKernel::from_op("abs_value", &[]),
            Some(NumericKernel::AbsValue)
        );
        assert_eq!(
            NumericKernel::from_op("fill_zero", &[]),
            Some(NumericKernel::FillZero)
        );
        // not a numeric op
        assert_eq!(NumericKernel::from_op("strip", &[]), None);
    }

    #[test]
    fn f64_fill_zero_null_not_counted_but_filled() {
        // Polars `(before != after).sum()` skips null-before rows, so fill_zero's
        // null->0.0 is filled in the output but NOT counted as affected.
        let arr = Float64Array::from(vec![None, Some(5.0), None, Some(2.0)]);
        let out = apply_chain_f64(&arr, &[NumericKernel::FillZero]);
        assert_eq!(out.array.value(0), 0.0);
        assert!(!out.array.is_null(0));
        assert_eq!(out.array.value(2), 0.0);
        // only non-null-before rows that CHANGE count; fill_zero changes none of them.
        assert_eq!(out.changed, vec![0]);
    }

    #[test]
    fn f64_changed_counts_only_altered_nonnull_rows() {
        let arr = Float64Array::from(vec![Some(1.4), Some(9.9), None, Some(2.0)]);
        // round(0): 1.4->1.0 (changed), 9.9->10.0 (changed), null skipped, 2.0->2.0 (same)
        let out = apply_chain_f64(&arr, &[NumericKernel::Round(0)]);
        assert_eq!(out.changed, vec![2]);
    }

    #[test]
    fn f64_all_names_round_trip() {
        for name in NumericKernel::ALL_NAMES {
            assert!(
                NumericKernel::from_op(name, &[]).is_some(),
                "{name} unmapped"
            );
        }
    }

    // --- nullable (Option<String>) chain ---

    /// Apply nullable kernels sequentially (fresh array per step, null-skipping
    /// like `map_str_to_str`) as the parity oracle.
    fn seq_nullable(arr: &StringArray, kernels: &[NullableKernel]) -> StringArray {
        let mut cur = arr.clone();
        for k in kernels {
            let out: StringArray = cur.iter().map(|v| v.and_then(|s| k.apply(s))).collect();
            cur = out;
        }
        cur
    }

    fn url_sample() -> StringArray {
        StringArray::from(vec![
            Some("HTTP://WWW.Example.com/Path/?utm_source=x&a=1"),
            Some("not a url at all"),
            None,
            Some("https://foo.com/"),
            Some(""),
        ])
    }

    #[test]
    fn nullable_chain_matches_sequential() {
        let chains: &[&[NullableKernel]] = &[
            &[
                NullableKernel::UrlNormalize,
                NullableKernel::UrlStripTracking,
            ],
            &[NullableKernel::UrlNormalize, NullableKernel::UrlStripWww],
            &[NullableKernel::UrlCanonical],
            &[
                NullableKernel::UrlNormalize,
                NullableKernel::UrlExtractDomain,
            ],
            // MIXED: total (strip/lowercase) + nullable (url_normalize).
            &[
                NullableKernel::Total(Kernel::Strip),
                NullableKernel::Total(Kernel::Lowercase),
                NullableKernel::UrlNormalize,
                NullableKernel::UrlStripWww,
            ],
        ];
        let arr = url_sample();
        for chain in chains {
            let fused = apply_chain_nullable(&arr, chain);
            let seq = seq_nullable(&arr, chain);
            assert_eq!(fused.array, seq, "nullable chain {chain:?} != sequential");
        }
    }

    #[test]
    fn nullable_company_and_email() {
        let arr = StringArray::from(vec![
            Some("  Acme, Inc.  "),
            Some("John Doe <j@x.com>"),
            None,
            Some("Widgets LLC"),
        ]);
        // total strip -> nullable company_normalize; and email_mask alone.
        let c1 = [
            NullableKernel::Total(Kernel::Strip),
            NullableKernel::CompanyNormalize,
        ];
        assert_eq!(
            apply_chain_nullable(&arr, &c1).array,
            seq_nullable(&arr, &c1)
        );
        let c2 = [NullableKernel::EmailMask];
        assert_eq!(
            apply_chain_nullable(&arr, &c2).array,
            seq_nullable(&arr, &c2)
        );
    }

    #[test]
    fn nullable_null_propagates_and_not_counted() {
        // email_extract_domain nulls a non-email (no `@`); the produced null then
        // passes through the next kernel. The changed count must match the Polars
        // rule: count a row only when BOTH before and after are non-null + differ.
        let arr = StringArray::from(vec![Some("  a@B.com  "), Some("not-an-email"), None]);
        let chain = [
            NullableKernel::Total(Kernel::Strip),
            NullableKernel::EmailExtractDomain,
            NullableKernel::Total(Kernel::Uppercase),
        ];
        let out = apply_chain_nullable(&arr, &chain);
        assert!(!out.array.is_null(0), "a@B.com has a domain");
        assert!(
            out.array.is_null(1),
            "non-email -> null, then passes through"
        );
        assert!(out.array.is_null(2), "null input stays null");
        // Independently recompute per-step changed via the sequential path with
        // the same both-non-null-and-differ rule (the null-not-counted claim).
        let mut cur = arr.clone();
        let mut expected = vec![0u64; chain.len()];
        for (i, k) in chain.iter().enumerate() {
            let nxt = seq_nullable(&cur, &[*k]);
            for r in 0..cur.len() {
                if !cur.is_null(r) && !nxt.is_null(r) && cur.value(r) != nxt.value(r) {
                    expected[i] += 1;
                }
            }
            cur = nxt;
        }
        assert_eq!(out.changed, expected, "null-aware changed counts diverged");
    }

    #[test]
    fn nullable_from_op_falls_back_to_total() {
        assert_eq!(
            NullableKernel::from_op("url_normalize", &[]),
            Some(NullableKernel::UrlNormalize)
        );
        assert_eq!(
            NullableKernel::from_op("strip", &[]),
            Some(NullableKernel::Total(Kernel::Strip))
        );
        assert_eq!(
            NullableKernel::from_op("truncate", &["5"]),
            Some(NullableKernel::Total(Kernel::Truncate(5)))
        );
        assert_eq!(NullableKernel::from_op("split_name", &[]), None);
        for name in NullableKernel::NULLABLE_NAMES {
            assert!(
                NullableKernel::from_op(name, &[]).is_some(),
                "{name} unmapped"
            );
        }
    }

    #[test]
    fn nullable_works_on_large_utf8() {
        use arrow_array::LargeStringArray;
        let large = LargeStringArray::from(vec![Some("HTTP://WWW.A.com/"), None, Some("bad url")]);
        let small = StringArray::from(vec![Some("HTTP://WWW.A.com/"), None, Some("bad url")]);
        let chain = [NullableKernel::UrlNormalize, NullableKernel::UrlStripWww];
        let lo = apply_chain_nullable(&large, &chain);
        let so = apply_chain_nullable(&small, &chain);
        assert_eq!(lo.changed, so.changed);
        for i in 0..lo.array.len() {
            assert_eq!(lo.array.is_null(i), so.array.is_null(i));
            if !lo.array.is_null(i) {
                assert_eq!(lo.array.value(i), so.array.value(i));
            }
        }
    }
}
