//! The transform chain, pyo3-free, byte-identical to
//! `goldenmatch.utils.transforms`.
//!
//! # Why this exists
//!
//! It did not. Verified against `origin/main`: not one file under
//! `packages/rust/` mentioned `apply_transform`, `normalize_whitespace`,
//! `strip_honorifics` or any sibling. Normalization was Python-only, which is
//! why `config_pipeline._transformed` is an `arrow_udf` and why a Spark cluster
//! still needed a packed virtualenv to lowercase a column.
//!
//! # What is deliberately NOT here
//!
//! `bloom_filter` (PPRL CLK, HMAC-keyed, security-level presets) and plugin
//! transforms (arbitrary Python from a registry). Both are refused by
//! [`apply_transform`] rather than approximated.
//!
//! **A transform that almost matches is worse than one that is missing.**
//! Normalization feeds blocking and scoring, so a one-character difference does
//! not surface as a wrong answer -- it changes which records are ever compared,
//! and the pair simply never appears. There is no threshold to absorb that and
//! no test downstream that would notice.
//!
//! # Parity notes, where Python and Rust could plausibly disagree
//!
//! - **Case.** Python `str.lower()`/`.upper()` and Rust `to_lowercase()`/
//!   `to_uppercase()` both do full Unicode case mapping including the
//!   one-to-many cases (`ß` -> `SS`, `İ` -> `i̇`).
//! - **Whitespace.** Python's `\s` (on `str`, Unicode by default) and
//!   `char::is_whitespace` agree on the common set but NOT everywhere: Python's
//!   `str.isspace()` counts the C1 separators `U+001C..U+001F`, Rust's
//!   `is_whitespace` does not. [`is_py_space`] encodes Python's set explicitly
//!   rather than borrowing Rust's, so the two cannot drift apart on a control
//!   character nobody thinks about.
//! - **Slicing.** Python slices by CODE POINT and tolerates out-of-range
//!   indices. [`substring`] does the same over `char_indices`, not bytes --
//!   byte slicing would panic mid-character on any multi-byte value.
//! - **Sorting.** Python sorts `str` by code point; Rust sorts `&str` by bytes,
//!   and for UTF-8 those orders are identical. Relied on by `token_sort` and
//!   `qgram`.

use std::borrow::Cow;

/// Honorifics stripped by `strip_honorifics`, matching
/// `goldenmatch.utils.transforms._HONORIFICS`.
/// Tokens `strip_honorifics` drops, byte-for-byte from
/// `goldenmatch.utils.transforms._STRIP_HONORIFIC_TOKENS`.
///
/// Duplicated rather than derived because this crate must work with no Python
/// present -- and pinned against the Python set by
/// `scripts/transforms_parity_dump.py`, so the duplication cannot drift
/// unnoticed. It includes POST-NOMINALS (`jr`, `phd`, `esq`), which is why
/// stripping only LEADING tokens is wrong: the first version of this port did
/// that and turned "O'Brien-Smith Jr." into "O'Brien-Smith Jr." where Python
/// gives "O'Brien-Smith". Caught by the differential dump, not by review.
const HONORIFICS: &[&str] = &[
    // courtesy titles
    "mr",
    "mrs",
    "ms",
    "miss",
    "mstr",
    // academic / professional
    "dr",
    "prof",
    "professor",
    // religious (abbreviated titles only, not office names)
    "rev",
    "revd",
    "reverend",
    // knighthoods / unambiguous honorifics
    "sir",
    "dame",
    "kt",
    "bt",
    "baronet",
    // generational / post-nominal suffixes
    "jr",
    "sr",
    "esq",
    "esquire",
    "phd",
    "md",
    "dds",
    "dvm",
];

/// Python's whitespace set for `str`, which is what both `\s` in a Unicode
/// regex and `str.strip()` use.
///
/// Written out rather than delegating to `char::is_whitespace` because the two
/// differ: Python counts `U+001C..U+001F` (the ASCII file/group/record/unit
/// separators) as whitespace and Rust does not. Borrowing Rust's predicate would
/// leave a value containing one of those normalizing differently on the two
/// surfaces -- which changes its block and removes the pair from consideration
/// entirely, invisibly.
pub fn is_py_space(c: char) -> bool {
    c.is_whitespace() || matches!(c, '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{1f}')
}

/// `re.sub(r"\s+", "", value)`.
fn strip_all(value: &str) -> String {
    value.chars().filter(|c| !is_py_space(*c)).collect()
}

/// `re.sub(r"\s+", " ", value).strip()`.
fn normalize_whitespace(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    let mut in_space = false;
    for c in value.chars() {
        if is_py_space(c) {
            in_space = true;
        } else {
            if in_space && !out.is_empty() {
                out.push(' ');
            }
            in_space = false;
            out.push(c);
        }
    }
    out
}

/// Python's `value.strip()`.
fn py_strip(value: &str) -> &str {
    value.trim_matches(is_py_space)
}

/// Python's whitespace `split()`: no empty tokens, leading/trailing ignored.
fn py_split(value: &str) -> Vec<&str> {
    value.split(is_py_space).filter(|t| !t.is_empty()).collect()
}

/// Python's `value[start:end]`, by CODE POINT, tolerating out-of-range indices.
///
/// Byte slicing would panic in the middle of any multi-byte character; this is
/// the transform most likely to be handed a name with an accent in it.
fn substring(value: &str, start: i64, end: i64) -> String {
    let n = value.chars().count() as i64;
    let clamp = |i: i64| -> usize {
        let i = if i < 0 { (n + i).max(0) } else { i.min(n) };
        i as usize
    };
    let (s, e) = (clamp(start), clamp(end));
    if e <= s {
        return String::new();
    }
    value.chars().skip(s).take(e - s).collect()
}

/// `qgram:q` -- sorted unique q-grams of `##value##`, first 5, space-joined.
fn qgram(value: &str, q: usize) -> String {
    if q == 0 {
        return String::new();
    }
    let padded: Vec<char> = format!("##{value}##").chars().collect();
    if padded.len() < q {
        return String::new();
    }
    let mut grams: Vec<String> = (0..=padded.len() - q)
        .map(|i| padded[i..i + q].iter().collect::<String>())
        .collect();
    grams.sort_unstable();
    grams.dedup();
    grams.truncate(5);
    grams.join(" ")
}

/// `strip_honorifics`: drop leading honorific tokens. Returns `None` when the
/// value was honorifics only -- a MISSING value, not an empty string, because
/// downstream Fellegi-Sunter reads empty as an agreement on nothing.
fn strip_honorifics(value: &str) -> Option<String> {
    // EVERY token is tested, not just the leading ones: the set carries
    // post-nominals (`jr`, `phd`, `esq`), so "Smith Jr." must lose its suffix.
    // Stripping only a leading run was this port's first bug.
    let kept: Vec<&str> = py_split(value)
        .into_iter()
        .filter(|t| {
            let bare: String = t
                .chars()
                .filter(|c| c.is_alphanumeric())
                .flat_map(|c| c.to_lowercase())
                .collect();
            !bare.is_empty() && !HONORIFICS.contains(&bare.as_str())
        })
        .collect();
    // Nothing surviving is MISSING, not empty: an empty string reads downstream
    // as an agreement on nothing rather than an absence of evidence.
    let residual = kept.join(" ");
    let residual = py_strip(&residual).to_string();
    if residual.is_empty() {
        None
    } else {
        Some(residual)
    }
}

/// A transform this crate refuses, and why.
#[derive(Debug, PartialEq, Eq)]
pub struct Unsupported(pub String);

/// Whether [`apply_transform`] can run `name` at all.
///
/// Exposed so a HOST can refuse a whole chain up front -- at plan time, with the
/// offending transform named -- rather than discovering it per row, mid-job.
pub fn supports(name: &str) -> bool {
    match name {
        "lowercase"
        | "uppercase"
        | "strip"
        | "strip_all"
        | "soundex"
        | "metaphone"
        | "digits_only"
        | "alpha_only"
        | "normalize_whitespace"
        | "token_sort"
        | "first_token"
        | "last_token"
        | "strip_honorifics" => true,
        _ => {
            (name.starts_with("substring:") && parse_substring(name).is_some())
                || (name.starts_with("qgram:") && parse_qgram(name).is_some())
        }
    }
}

fn parse_substring(name: &str) -> Option<(i64, i64)> {
    let mut parts = name.split(':');
    parts.next()?;
    let a = parts.next()?.parse::<i64>().ok()?;
    let b = parts.next()?.parse::<i64>().ok()?;
    if parts.next().is_some() {
        return None;
    }
    Some((a, b))
}

fn parse_qgram(name: &str) -> Option<usize> {
    let mut parts = name.split(':');
    parts.next()?;
    let q = parts.next()?.parse::<usize>().ok()?;
    if parts.next().is_some() {
        return None;
    }
    Some(q)
}

/// One named transform. `None` in, `None` out, exactly as Python.
///
/// Returns `Err(Unsupported)` for `bloom_filter` and for plugin transforms
/// rather than guessing. Those are refusals by design, not gaps to fill later:
/// `bloom_filter` is HMAC-keyed PPRL and a plugin is arbitrary Python.
pub fn apply_transform(value: Option<&str>, name: &str) -> Result<Option<String>, Unsupported> {
    let Some(v) = value else {
        return Ok(None);
    };
    let out = match name {
        "lowercase" => v.to_lowercase(),
        "uppercase" => v.to_uppercase(),
        "strip" => py_strip(v).to_string(),
        "strip_all" => strip_all(v),
        // score-core's, NOT goldenphonetic's. `canonical_soundex` documents
        // itself as "byte-identical to score-core `soundex`", and the two
        // differ: goldenphonetic seeds on the first character whatever it is,
        // so "  Dr. ..." coded to " 362" and "123-456" to "1000" instead of
        // "" . Binding the plausible crate rather than the documented one was
        // this port's other bug, and only the differential dump found it.
        "soundex" => goldenmatch_score_core::soundex(v),
        "metaphone" => goldenphonetic_core::metaphone(v),
        "digits_only" => v.chars().filter(|c| c.is_ascii_digit()).collect(),
        "alpha_only" => v.chars().filter(|c| c.is_ascii_alphabetic()).collect(),
        "normalize_whitespace" => normalize_whitespace(v),
        "token_sort" => {
            let mut t = py_split(v);
            t.sort_unstable();
            t.join(" ")
        }
        "first_token" => py_split(v)
            .first()
            .map_or_else(|| v.to_string(), |s| s.to_string()),
        "last_token" => py_split(v)
            .last()
            .map_or_else(|| v.to_string(), |s| s.to_string()),
        "strip_honorifics" => return Ok(strip_honorifics(v)),
        _ => {
            if let Some((a, b)) = name
                .starts_with("substring:")
                .then(|| parse_substring(name))
                .flatten()
            {
                substring(v, a, b)
            } else if let Some(q) = name
                .starts_with("qgram:")
                .then(|| parse_qgram(name))
                .flatten()
            {
                qgram(v, q)
            } else {
                return Err(Unsupported(format!(
                    "transform {name:?} is not available outside Python. \
                     `bloom_filter` is HMAC-keyed PPRL and plugin transforms are \
                     arbitrary Python; both are refused rather than approximated, \
                     because normalization feeds BLOCKING -- a value that \
                     normalizes differently lands in a different block and the \
                     pair is never compared at all."
                )));
            }
        }
    };
    Ok(Some(out))
}

/// A whole chain, left to right. Short-circuits on `None`, exactly as
/// `apply_transforms` does: once a value is missing, later transforms have
/// nothing to act on and must not resurrect it as `""`.
pub fn apply_transforms(
    value: Option<&str>,
    names: &[&str],
) -> Result<Option<String>, Unsupported> {
    let mut cur: Option<String> = value.map(|s| s.to_string());
    for name in names {
        match apply_transform(cur.as_deref(), name)? {
            Some(next) => cur = Some(next),
            None => return Ok(None),
        }
    }
    Ok(cur)
}

/// Convenience for hosts holding an owned chain.
pub fn apply_transforms_owned(
    value: Option<&str>,
    names: &[String],
) -> Result<Option<String>, Unsupported> {
    let refs: Vec<&str> = names.iter().map(|s| s.as_str()).collect();
    apply_transforms(value, &refs)
}

/// Borrowed passthrough for the common single-transform case.
pub fn apply_transform_cow<'a>(value: &'a str, name: &str) -> Result<Cow<'a, str>, Unsupported> {
    match apply_transform(Some(value), name)? {
        Some(s) => Ok(Cow::Owned(s)),
        None => Ok(Cow::Borrowed("")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn t(v: &str, name: &str) -> String {
        apply_transform(Some(v), name).unwrap().unwrap_or_default()
    }

    #[test]
    fn none_passes_through_every_transform() {
        for name in ["lowercase", "strip", "token_sort", "soundex"] {
            assert_eq!(apply_transform(None, name).unwrap(), None);
        }
    }

    #[test]
    fn basic_string_ops() {
        assert_eq!(t("AbC", "lowercase"), "abc");
        assert_eq!(t("AbC", "uppercase"), "ABC");
        assert_eq!(t("  x  ", "strip"), "x");
        assert_eq!(t("a b\tc", "strip_all"), "abc");
        assert_eq!(t("a  b\t\nc ", "normalize_whitespace"), "a b c");
        assert_eq!(t("a1b2-c", "digits_only"), "12");
        assert_eq!(t("a1b2-c", "alpha_only"), "abc");
        assert_eq!(t(" b a ", "token_sort"), "a b");
        assert_eq!(t(" first second ", "first_token"), "first");
        assert_eq!(t(" first second ", "last_token"), "second");
    }

    #[test]
    fn substring_slices_by_code_point_not_bytes() {
        // The bug byte slicing would cause: panic or a mangled character on any
        // accented name.
        assert_eq!(t("café", "substring:0:3"), "caf");
        assert_eq!(t("café", "substring:3:4"), "é");
        assert_eq!(t("日本語テキスト", "substring:0:3"), "日本語");
        // Python tolerates out-of-range indices rather than raising.
        assert_eq!(t("ab", "substring:0:99"), "ab");
        assert_eq!(t("ab", "substring:5:9"), "");
    }

    #[test]
    fn qgram_matches_the_python_construction() {
        // "##ab##" -> bigrams: ##, #a, ab, b#, ## -> unique sorted, first 5.
        assert_eq!(t("ab", "qgram:2"), "## #a ab b#");
    }

    #[test]
    fn strip_honorifics_returns_missing_not_empty() {
        assert_eq!(
            apply_transform(Some("Dr. Jonathan Smith"), "strip_honorifics").unwrap(),
            Some("Jonathan Smith".to_string())
        );
        // Honorific-only is MISSING. Returning "" would read downstream as an
        // agreement on an empty value rather than an absence of evidence.
        assert_eq!(
            apply_transform(Some("Dr. Mr."), "strip_honorifics").unwrap(),
            None
        );
    }

    #[test]
    fn a_chain_short_circuits_on_missing() {
        // Once strip_honorifics yields None the value is GONE; a later
        // `lowercase` must not turn it back into "".
        assert_eq!(
            apply_transforms(Some("Dr."), &["strip_honorifics", "lowercase"]).unwrap(),
            None
        );
    }

    #[test]
    fn python_only_transforms_are_refused_not_approximated() {
        for name in [
            "bloom_filter",
            "bloom_filter:high",
            "legal_form_strip",
            "nope",
        ] {
            assert!(!supports(name), "{name} should not be supported");
            assert!(apply_transform(Some("x"), name).is_err(), "{name}");
        }
    }

    #[test]
    fn malformed_parameterised_specs_are_refused() {
        // A typo must not silently become a no-op transform.
        for name in [
            "substring:",
            "substring:a:b",
            "substring:1",
            "qgram:",
            "qgram:x",
        ] {
            assert!(!supports(name), "{name} should not be supported");
            assert!(apply_transform(Some("x"), name).is_err(), "{name}");
        }
    }

    #[test]
    fn python_counts_the_c1_separators_as_whitespace() {
        // Rust's char::is_whitespace does NOT. Borrowing it would leave a value
        // containing one of these normalizing differently on the two surfaces --
        // a different block, and the pair never compared.
        assert!(is_py_space('\u{1c}'));
        assert!(!'\u{1c}'.is_whitespace());
        assert_eq!(t("a\u{1c}b", "strip_all"), "ab");
    }
}
