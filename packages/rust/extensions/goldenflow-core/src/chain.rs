//! Fused columnar apply (feature `arrow`, off by default) — Pillar-1 of the Rust
//! cutover: run a WHOLE chain of owned string kernels over a `StringArray` in one
//! pass, instead of crossing the Python/Polars/Arrow boundary once per transform.
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
//! Scope (Phase 1): the no-arg, TOTAL (never-null) string→string text family. Ops
//! that return `Option` (email_extract_domain, url_extract_domain, *_validate,
//! *_mask), carry params (truncate/pad), or change arity (split/merge) are NOT
//! fusable here and stay on the per-transform path — see the boundary note in the
//! design doc.

use arrow_array::{Array, GenericStringArray, OffsetSizeTrait};
use arrow_buffer::{Buffer, OffsetBuffer, ScalarBuffer};

use crate::{email, names, text};

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
            _ => return None,
        })
    }

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
        }
    }
}

/// The fused output: the transformed column plus, for audit parity with the
/// per-transform path, the per-kernel affected-row count (`changed[i]` = rows the
/// i-th kernel altered, comparing the value before vs after that kernel — exactly
/// what the host's `(before != after).sum()` computes per transform). Generic over
/// the offset width so the output matches the input (Utf8 `i32` / LargeUtf8 `i64`).
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

#[cfg(test)]
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
        ];
        let arr = sample();
        for chain in chains {
            let fused = apply_chain(&arr, chain);
            let seq = sequential(&arr, chain);
            assert_eq!(fused.array, seq, "chain {chain:?} != sequential");
        }
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
}
