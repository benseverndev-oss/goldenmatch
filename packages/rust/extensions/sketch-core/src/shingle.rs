//! Shingling: turn a string into a sorted, deduplicated set of shingle hashes.
//! Mirrors `goldenmatch/core/sketch.py::shingle` exactly.

use crate::hash::base_hash;

/// Shingle granularity.
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum ShingleMode {
    /// Window over Unicode code points.
    Char,
    /// Window over tokens split on the ASCII whitespace set.
    Word,
}

impl ShingleMode {
    /// Parse the wire string (`"char"` / `"word"`). Returns `None` on anything else.
    pub fn parse(s: &str) -> Option<ShingleMode> {
        match s {
            "char" => Some(ShingleMode::Char),
            "word" => Some(ShingleMode::Word),
            _ => None,
        }
    }
}

/// Exactly these six code points are word-mode separators (matches the Python
/// `_ASCII_WS` set). NOT `char::is_whitespace` / `split_whitespace`, which
/// include Unicode whitespace and would break parity.
#[inline]
fn is_ascii_ws(c: char) -> bool {
    matches!(c, '\t' | '\n' | '\u{0B}' | '\u{0C}' | '\r' | ' ')
}

fn word_tokens(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start: Option<usize> = None;
    for (i, c) in text.char_indices() {
        if is_ascii_ws(c) {
            if let Some(s) = start.take() {
                out.push(&text[s..i]);
            }
        } else if start.is_none() {
            start = Some(i);
        }
    }
    if let Some(s) = start {
        out.push(&text[s..]);
    }
    out
}

/// Return the sorted, deduplicated set of shingle hashes for `text`.
///
/// `n == 0` (empty / whitespace-only) yields the empty set (precedence over the
/// short-input branch); `1 <= n < k` yields a single whole-sequence shingle.
///
/// # Panics
/// Panics if `k == 0` — every language port must reject `k < 1` identically
/// (Python/TS raise; Rust panics loudly rather than silently windowing nothing).
pub fn shingle(text: &str, mode: ShingleMode, k: usize) -> Vec<u64> {
    assert!(k >= 1, "shingle k must be >= 1, got {k}");
    let mut hs: Vec<u64> = match mode {
        ShingleMode::Char => {
            let units: Vec<char> = text.chars().collect();
            let n = units.len();
            if n == 0 {
                return Vec::new();
            }
            if n < k {
                vec![base_hash(text.as_bytes())]
            } else {
                let mut out = Vec::with_capacity(n - k + 1);
                for window in units.windows(k) {
                    let s: String = window.iter().collect();
                    out.push(base_hash(s.as_bytes()));
                }
                out
            }
        }
        ShingleMode::Word => {
            let tokens = word_tokens(text);
            let n = tokens.len();
            if n == 0 {
                return Vec::new();
            }
            if n < k {
                vec![base_hash(tokens.join(" ").as_bytes())]
            } else {
                let mut out = Vec::with_capacity(n - k + 1);
                for window in tokens.windows(k) {
                    out.push(base_hash(window.join(" ").as_bytes()));
                }
                out
            }
        }
    };
    hs.sort_unstable();
    hs.dedup();
    hs
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn char_basic_count() {
        let sh = shingle("hello world", ShingleMode::Char, 3);
        assert_eq!(sh.len(), 9);
        assert!(sh.windows(2).all(|w| w[0] < w[1])); // sorted + deduped
    }

    #[test]
    fn word_ascii_whitespace_only() {
        // U+00A0 (non-breaking space) is NOT a separator -> one token.
        assert_eq!(shingle("a\u{A0}b", ShingleMode::Word, 1).len(), 1);
        // ASCII tab / newline / VT / FF / CR ARE separators -> two tokens.
        for sep in ["\t", "\n", "\u{0B}", "\u{0C}", "\r", " "] {
            assert_eq!(shingle(&format!("a{sep}b"), ShingleMode::Word, 1).len(), 2);
        }
    }

    #[test]
    fn short_input_single_shingle() {
        assert_eq!(shingle("ab", ShingleMode::Char, 5), vec![base_hash(b"ab")]);
        assert_eq!(shingle("x", ShingleMode::Word, 3), vec![base_hash(b"x")]);
    }

    #[test]
    fn empty_and_whitespace_only_is_empty() {
        assert!(shingle("", ShingleMode::Char, 3).is_empty());
        assert!(shingle("   \t\n", ShingleMode::Word, 2).is_empty());
    }

    #[test]
    fn mode_parse() {
        assert_eq!(ShingleMode::parse("char"), Some(ShingleMode::Char));
        assert_eq!(ShingleMode::parse("word"), Some(ShingleMode::Word));
        assert_eq!(ShingleMode::parse("bigram"), None);
    }

    #[test]
    #[should_panic]
    fn k_zero_panics() {
        shingle("hello", ShingleMode::Char, 0);
    }
}
