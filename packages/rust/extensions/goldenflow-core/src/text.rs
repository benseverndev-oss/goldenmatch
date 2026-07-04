//! Text-normalization transform kernels (owned reference), byte-identical to the
//! GoldenFlow polars transforms `strip` and `collapse_whitespace`. Both hinge on
//! the Unicode `White_Space` property (`char::is_whitespace`) — the SAME set
//! polars' `str.strip_chars()` (Rust std `trim`) and its regex `\s` (the `regex`
//! crate's Unicode `\s`) use. Proven byte-for-byte against a polars-generated
//! Unicode corpus in `tests/text_golden.rs` (`golden/text_golden.json`), which
//! exercises the tricky boundary: NBSP, VT, NEL, line-sep, U+205F, U+3000,
//! U+2009, U+1680 (all whitespace) and ZWSP U+200B (NOT whitespace).

/// `strip` transform: remove leading/trailing Unicode whitespace. Matches
/// polars `pl.col(c).str.strip_chars()`, which is Rust std `str::trim`.
pub fn strip(s: &str) -> &str {
    s.trim()
}

/// `collapse_whitespace` transform: replace each maximal run of **2 or more**
/// Unicode whitespace chars with a single ASCII space; a run of length 1 (a lone
/// whitespace char) is left unchanged. Matches polars
/// `pl.col(c).str.replace_all(r"\s{2,}", " ")` — `\s{2,}` is a maximal 2+ run of
/// the same Unicode `White_Space` set, replaced by a literal space.
pub fn collapse_whitespace(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if !c.is_whitespace() {
            out.push(c);
            continue;
        }
        // Consume the rest of this contiguous whitespace run.
        let mut run = 1usize;
        while let Some(&n) = chars.peek() {
            if !n.is_whitespace() {
                break;
            }
            chars.next();
            run += 1;
        }
        out.push(if run >= 2 { ' ' } else { c });
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strip_trims_unicode_whitespace() {
        assert_eq!(strip("  hello  "), "hello");
        assert_eq!(strip("a    b   c"), "a    b   c"); // internal untouched
        assert_eq!(strip("\u{00a0}nbsp\u{00a0}"), "nbsp"); // NBSP is whitespace
        assert_eq!(strip("\u{200b}zwsp\u{200b}"), "\u{200b}zwsp\u{200b}"); // ZWSP is not
    }

    #[test]
    fn collapse_only_runs_of_two_or_more() {
        assert_eq!(collapse_whitespace("a    b   c"), "a b c");
        assert_eq!(collapse_whitespace("a\tb"), "a\tb"); // lone ws unchanged
        assert_eq!(collapse_whitespace("a\t\tb"), "a b");
        assert_eq!(
            collapse_whitespace("ideo\u{3000}\u{3000}graph"),
            "ideo graph"
        );
        assert_eq!(
            collapse_whitespace("\u{200b}\u{200b}zwrun"),
            "\u{200b}\u{200b}zwrun"
        );
    }
}
