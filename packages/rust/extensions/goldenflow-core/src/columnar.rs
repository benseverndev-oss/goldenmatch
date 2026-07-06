//! Arrow-columnar apply paths (feature `arrow`, off by default so the wasm /
//! pure-logic surfaces stay arrow-free). The owned string kernels are scalar
//! `fn(&str)->String`; these turn the per-element apply loop
//! (`Option<String>` + `StringBuilder::append`, which allocates a `String` and
//! copies it per row) into a columnar path that writes into ONE shared buffer.
//!
//! Measured on 1M rows (benches/columnar_pilot.rs), byte-identical to the
//! scalar path:
//!   - [`map_str_columnar`] (generic, any op writing into a buffer): ~4-5x.
//!   - [`ascii_case`] (whole-buffer `make_ascii_{lower,upper}case`, offsets
//!     reused): ~9-10x for the common all-ASCII case; per-element Unicode
//!     fallback preserves exact parity when any non-ASCII byte is present.
//!
//! Generic over the offset width (`GenericStringArray<O>`: Utf8 `i32` /
//! LargeUtf8 `i64`) — **critical**, because **Polars exports strings as
//! LargeUtf8**. An i32-only path would silently take the scalar fallback on real
//! Polars data, so the measured speedup would never fire in production.

use arrow_array::builder::GenericStringBuilder;
use arrow_array::{Array, GenericStringArray, OffsetSizeTrait};
use arrow_buffer::{Buffer, OffsetBuffer, ScalarBuffer};

/// The CURRENT scalar apply shape (mirrors native-flow `util::map_str_to_str`):
/// a closure returning `Option<String>`, per-element `append`. Kept as the
/// reference + the non-ASCII fallback for [`ascii_case`].
pub fn scalar_map<O: OffsetSizeTrait, F: Fn(&str) -> Option<String>>(
    arr: &GenericStringArray<O>,
    f: F,
) -> GenericStringArray<O> {
    let len = arr.len();
    let mut b = GenericStringBuilder::<O>::with_capacity(len, len * 12);
    for v in arr.iter() {
        match v {
            Some(s) => match f(s) {
                Some(out) => b.append_value(out),
                None => b.append_null(),
            },
            None => b.append_null(),
        }
    }
    b.finish()
}

/// Generic columnar map: `f` writes each present element's transformed bytes
/// directly into one shared values buffer (no per-element `String` alloc, no
/// builder double-copy); offsets are accumulated as we go. Nulls pass through
/// (offset unchanged, null bitmap cloned). Byte-identical output to
/// `scalar_map(arr, |s| Some(kernel_producing_the_same_bytes(s)))`.
pub fn map_str_columnar<O: OffsetSizeTrait, F: Fn(&str, &mut String)>(
    arr: &GenericStringArray<O>,
    f: F,
) -> GenericStringArray<O> {
    let len = arr.len();
    let mut offsets: Vec<O> = Vec::with_capacity(len + 1);
    offsets.push(O::from_usize(0).expect("0 fits any offset"));
    // Hint: most trivial ops shrink or preserve length, so the input value byte
    // count is a good upper-ish bound for the output buffer.
    let mut values = String::with_capacity(arr.values().len());
    for v in arr.iter() {
        if let Some(s) = v {
            f(s, &mut values);
        }
        offsets.push(O::from_usize(values.len()).expect("string column exceeds offset width"));
    }
    GenericStringArray::<O>::new(
        OffsetBuffer::new(ScalarBuffer::from(offsets)),
        Buffer::from_vec(values.into_bytes()),
        arr.nulls().cloned(),
    )
}

/// Specialized ASCII case-fold. When the entire values buffer is ASCII, apply
/// `make_ascii_{lower,upper}case` in one pass and reuse the offsets + nulls
/// verbatim (byte lengths are unchanged for ASCII case-folding) -- zero
/// per-element allocation. If any non-ASCII byte is present, fall back to the
/// `scalar` Unicode kernel per element, so the output is byte-identical to the
/// scalar path in every case (`make_ascii_*` != Unicode casing outside ASCII).
pub fn ascii_case<O: OffsetSizeTrait, F: Fn(&str) -> String>(
    arr: &GenericStringArray<O>,
    upper: bool,
    scalar: F,
) -> GenericStringArray<O> {
    let bytes: &[u8] = arr.values().as_slice();
    if !bytes.is_ascii() {
        return scalar_map(arr, |s| Some(scalar(s)));
    }
    let mut v = bytes.to_vec();
    if upper {
        v.make_ascii_uppercase();
    } else {
        v.make_ascii_lowercase();
    }
    GenericStringArray::<O>::new(arr.offsets().clone(), Buffer::from_vec(v), arr.nulls().cloned())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::text;
    use arrow_array::{LargeStringArray, StringArray};

    // A dataset spanning every parity-relevant shape: ASCII mixed-case, leading/
    // trailing/internal whitespace, empty string, null, and a non-ASCII row
    // (forces the ascii_case Unicode fallback + exercises multi-byte offsets).
    fn sample() -> StringArray {
        StringArray::from(vec![
            Some("John SMITH"),
            Some("  Mary  "),
            Some("o'Brien"),
            Some(""),
            None,
            Some("STRASSE"),
            Some("café"), // non-ASCII -> ascii_case must fall back
            Some("  Renée "),
        ])
    }

    #[test]
    fn ascii_case_matches_scalar_lower() {
        let arr = sample();
        let columnar = ascii_case(&arr, false, text::lowercase);
        let scalar = scalar_map(&arr, |s| Some(text::lowercase(s)));
        assert_eq!(columnar, scalar);
    }

    #[test]
    fn ascii_case_matches_scalar_upper() {
        let arr = sample();
        let columnar = ascii_case(&arr, true, text::uppercase);
        let scalar = scalar_map(&arr, |s| Some(text::uppercase(s)));
        assert_eq!(columnar, scalar);
    }

    #[test]
    fn ascii_case_all_ascii_uses_fast_path_bytes() {
        // All-ASCII input: the fast path must equal the scalar Unicode path.
        let arr = StringArray::from(vec![Some("AbC"), Some("xyZ"), None, Some("")]);
        assert_eq!(
            ascii_case(&arr, false, text::lowercase),
            scalar_map(&arr, |s| Some(text::lowercase(s)))
        );
    }

    #[test]
    fn map_str_columnar_matches_scalar_strip() {
        let arr = sample();
        let columnar = map_str_columnar(&arr, |s, buf| buf.push_str(text::strip(s)));
        let scalar = scalar_map(&arr, |s| Some(text::strip(s).to_string()));
        assert_eq!(columnar, scalar);
    }

    #[test]
    fn map_str_columnar_matches_scalar_collapse() {
        let arr = sample();
        let columnar = map_str_columnar(&arr, |s, buf| buf.push_str(&text::collapse_whitespace(s)));
        let scalar = scalar_map(&arr, |s| Some(text::collapse_whitespace(s)));
        assert_eq!(columnar, scalar);
    }

    // The `_into` streaming kernels (Wave: trivial text family) must be
    // byte-identical to their `String`-returning wrappers when threaded through
    // `map_str_columnar` -- this is the contract native-flow's shims rely on.
    #[test]
    fn into_kernels_match_string_wrappers() {
        // Rows that actually trigger each removal/normalize branch (plus null,
        // empty, and a multi-byte tail) so the parity is over real transforms,
        // not identity.
        let arr = StringArray::from(vec![
            Some("a  b\tc"),                            // collapse
            Some("<b>hi</b> http://x.com/y z"),         // html + url
            Some("abc123 caf\u{e9}!"),                  // digits + punctuation + multibyte
            Some("hi \u{1f600} \u{201c}q\u{201d}\r\n"), // emoji + quotes + CRLF
            Some(""),
            None,
            Some("Jos\u{e9}"), // normalize_unicode
        ]);
        macro_rules! check {
            ($into:path, $whole:path) => {
                assert_eq!(
                    map_str_columnar(&arr, |s, buf| $into(s, buf)),
                    scalar_map(&arr, |s| Some($whole(s))),
                    concat!(stringify!($into), " != ", stringify!($whole))
                );
            };
        }
        check!(text::collapse_whitespace_into, text::collapse_whitespace);
        check!(text::normalize_quotes_into, text::normalize_quotes);
        check!(
            text::normalize_line_endings_into,
            text::normalize_line_endings
        );
        check!(text::normalize_unicode_into, text::normalize_unicode);
        check!(text::remove_html_tags_into, text::remove_html_tags);
        check!(text::remove_urls_into, text::remove_urls);
        check!(text::remove_digits_into, text::remove_digits);
        check!(text::remove_punctuation_into, text::remove_punctuation);
        check!(text::remove_emojis_into, text::remove_emojis);
    }

    #[test]
    fn pad_into_kernels_match_string_wrappers() {
        // pad_* carry width/pad args -> the closure captures them (the shape
        // native-flow's parametrized shims use).
        let arr = sample();
        assert_eq!(
            map_str_columnar(&arr, |s, buf| text::pad_left_into(s, 8, '0', buf)),
            scalar_map(&arr, |s| Some(text::pad_left(s, 8, '0'))),
        );
        assert_eq!(
            map_str_columnar(&arr, |s, buf| text::pad_right_into(s, 8, ' ', buf)),
            scalar_map(&arr, |s| Some(text::pad_right(s, 8, ' '))),
        );
    }

    #[test]
    fn preserves_nulls_and_empty() {
        let arr = StringArray::from(vec![None, Some(""), Some("x"), None]);
        let out = map_str_columnar(&arr, |s, buf| buf.push_str(s));
        assert_eq!(out.len(), 4);
        assert!(out.is_null(0));
        assert!(!out.is_null(1) && out.value(1).is_empty());
        assert_eq!(out.value(2), "x");
        assert!(out.is_null(3));
    }

    #[test]
    fn fires_on_large_utf8_the_polars_shape() {
        // Polars exports strings as LargeUtf8 (i64 offsets). The generic path must
        // produce the same bytes on LargeUtf8 as on Utf8 -- otherwise the columnar
        // fast path silently never fires on real Polars data (it did before this
        // was made generic). Covers both map_str_columnar and the ascii_case
        // fast-path + non-ASCII fallback (the sample has "café"/"Renée").
        let rows = vec![Some("John SMITH"), Some("  Mary  "), None, Some("café"), Some("")];
        let large = LargeStringArray::from(rows.clone());
        let small = StringArray::from(rows);
        let l_map = map_str_columnar(&large, |s, buf| buf.push_str(text::strip(s)));
        let s_map = map_str_columnar(&small, |s, buf| buf.push_str(text::strip(s)));
        let l_case = ascii_case(&large, false, text::lowercase);
        let s_case = ascii_case(&small, false, text::lowercase);
        for i in 0..small.len() {
            assert_eq!(l_map.is_null(i), s_map.is_null(i));
            assert_eq!(l_case.is_null(i), s_case.is_null(i));
            if !small.is_null(i) {
                assert_eq!(l_map.value(i), s_map.value(i), "map @ {i}");
                assert_eq!(l_case.value(i), s_case.value(i), "case @ {i}");
            }
        }
    }
}
