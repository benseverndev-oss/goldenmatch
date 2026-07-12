//! Fused single-pass string-column digest for the scan's format/encoding
//! profilers. Today a string column makes ~10 separate passes over the same
//! bytes: `n_unique` (`count_distinct`), `null_count`, and up to 7
//! `str_match_count(pattern)` regex scans (3 format patterns + 4 encoding
//! patterns), each a distinct materialization. This kernel folds all of them
//! into ONE pass over the `StringArray` / `LargeStringArray`.
//!
//! # Parity contract
//!
//! - `null_count` == `arr.null_count` (pyarrow).
//! - `n_unique` == pyarrow `count_distinct(mode="all")`: distinct **non-null**
//!   values plus one if any null is present (nulls collapse to a single
//!   distinct). Built with a `HashSet<&str>` borrowing straight from the
//!   array's value buffer -- no per-element `String` allocation.
//! - `match_counts[i]` == `str_match_count(patterns[i])` (mirrors
//!   `pc.match_substring_regex(...).sum()` on the non-null column). Each pattern
//!   is compiled once with the `regex` crate -- the same engine Polars uses and
//!   the one the encoding patterns' `\uXXXX` escapes already route to today
//!   (pyarrow RE2 cannot compile them). Match is an unanchored `is_match`
//!   search, agreeing with pyarrow's unanchored `match_substring_regex`; the
//!   `^`/`$` anchors live inside the email/phone/url patterns themselves.
//!
//! The digest fuses only SCALAR reductions. Materialized-value tails (pattern
//! skeletons, sample rows, fuzzy clusters) stay on their own passes.
use arrow::array::{Array, LargeStringArray, StringArray};
use arrow::datatypes::DataType;
use regex::Regex;
use rustc_hash::FxHashSet;

/// One-pass string-column summary. `match_counts` is aligned to the `patterns`
/// slice passed to [`string_column_digest`] (same length, same order).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StringDigest {
    pub null_count: usize,
    pub n_unique: usize,
    pub match_counts: Vec<usize>,
}

/// Fold `null_count`, `n_unique`, and per-pattern match counts into a single
/// pass over a Utf8 / LargeUtf8 Arrow column. Non-string dtypes yield a
/// zero/empty digest (the caller only invokes this on string columns).
///
/// Patterns are compiled once up front; a pattern that fails to compile in the
/// `regex` crate propagates as `regex::Error` so the caller can fall back to
/// the per-pattern path for it rather than silently miscounting.
pub fn string_column_digest(
    array: &dyn Array,
    patterns: &[String],
) -> Result<StringDigest, regex::Error> {
    let regexes: Vec<Regex> = patterns
        .iter()
        .map(|p| Regex::new(p))
        .collect::<Result<_, _>>()?;

    let mut null_count = 0usize;
    let mut any_null = false;
    let mut seen: FxHashSet<&str> = FxHashSet::default();
    let mut match_counts = vec![0usize; regexes.len()];

    macro_rules! scan {
        ($arrty:ty) => {{
            let arr = array.as_any().downcast_ref::<$arrty>().unwrap();
            seen.reserve(arr.len());
            for i in 0..arr.len() {
                if arr.is_null(i) {
                    null_count += 1;
                    any_null = true;
                    continue;
                }
                let v: &str = arr.value(i);
                seen.insert(v);
                for (j, re) in regexes.iter().enumerate() {
                    if re.is_match(v) {
                        match_counts[j] += 1;
                    }
                }
            }
        }};
    }

    match array.data_type() {
        DataType::Utf8 => scan!(StringArray),
        DataType::LargeUtf8 => scan!(LargeStringArray),
        // Non-string dtype: caller never reaches here on the fused path, but
        // return an empty digest rather than panic.
        _ => {}
    }

    // count_distinct(mode="all"): non-null distinct + 1 for the null "value".
    let n_unique = seen.len() + usize::from(any_null);
    Ok(StringDigest {
        null_count,
        n_unique,
        match_counts,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow::array::{LargeStringArray, StringArray};

    // The 7 fixed scan patterns (exact strings passed at the seam).
    const EMAIL: &str = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$";
    const PHONE: &str = r"^\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}$";
    const URL: &str = r"^https?://";
    const NONASCII: &str = r"[^\x00-\x7F]";
    const ZEROWID: &str = r"[​‌‍﻿]";
    const SMARTQ: &str = r"[‘’“”]";
    const CONTROL: &str = r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]";

    fn all_patterns() -> Vec<String> {
        [EMAIL, PHONE, URL, NONASCII, ZEROWID, SMARTQ, CONTROL]
            .iter()
            .map(|s| s.to_string())
            .collect()
    }

    #[test]
    fn all_seven_patterns_compile() {
        for p in all_patterns() {
            Regex::new(&p).unwrap_or_else(|e| panic!("pattern {p:?} failed: {e}"));
        }
    }

    #[test]
    fn null_and_unique_semantics() {
        let a = StringArray::from(vec![Some("x"), Some("y"), Some("x"), None, None]);
        let d = string_column_digest(&a, &[]).unwrap();
        assert_eq!(d.null_count, 2);
        // distinct non-null {x, y} = 2, plus 1 for the null value = 3.
        assert_eq!(d.n_unique, 3);
    }

    #[test]
    fn no_nulls_no_phantom_distinct() {
        let a = StringArray::from(vec!["a", "b", "c", "a"]);
        let d = string_column_digest(&a, &[]).unwrap();
        assert_eq!(d.null_count, 0);
        assert_eq!(d.n_unique, 3); // {a,b,c}, no null bump
    }

    #[test]
    fn email_phone_url_counts() {
        let a = StringArray::from(vec![
            Some("a@b.com"),
            Some("x@y.org"),
            Some("(123) 456-7890"),
            Some("https://example.com"),
            Some("plain text"),
            None,
        ]);
        let pats: Vec<String> = [EMAIL, PHONE, URL].iter().map(|s| s.to_string()).collect();
        let d = string_column_digest(&a, &pats).unwrap();
        assert_eq!(d.match_counts, vec![2, 1, 1]);
        assert_eq!(d.null_count, 1);
    }

    #[test]
    fn encoding_patterns_count() {
        let a = StringArray::from(vec![
            Some("café"),            // non-ascii é
            Some("zero\u{200B}wid"), // zero-width space
            Some("smart\u{2019}q"),  // right single quote
            Some("ctrl\u{0007}"),    // bell control char
            Some("clean"),
        ]);
        let pats: Vec<String> = [NONASCII, ZEROWID, SMARTQ, CONTROL]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let d = string_column_digest(&a, &pats).unwrap();
        // non-ascii: café, zero-width, smart-quote all contain non-ascii = 3
        assert_eq!(d.match_counts[0], 3);
        assert_eq!(d.match_counts[1], 1); // zero-width
        assert_eq!(d.match_counts[2], 1); // smart quote
        assert_eq!(d.match_counts[3], 1); // control
    }

    #[test]
    fn large_utf8_supported() {
        let a = LargeStringArray::from(vec![Some("a@b.com"), None, Some("a@b.com")]);
        let pats: Vec<String> = [EMAIL].iter().map(|s| s.to_string()).collect();
        let d = string_column_digest(&a, &pats).unwrap();
        assert_eq!(d.match_counts, vec![2]);
        assert_eq!(d.null_count, 1);
        assert_eq!(d.n_unique, 2); // {a@b.com} + null
    }

    #[test]
    fn empty_column() {
        let a = StringArray::from(Vec::<Option<&str>>::new());
        let d = string_column_digest(&a, &all_patterns()).unwrap();
        assert_eq!(d.null_count, 0);
        assert_eq!(d.n_unique, 0);
        assert_eq!(d.match_counts, vec![0; 7]);
    }

    #[test]
    fn bad_pattern_errors() {
        let a = StringArray::from(vec!["x"]);
        assert!(string_column_digest(&a, &["[unclosed".to_string()]).is_err());
    }
}
