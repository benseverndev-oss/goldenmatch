//! Minimal grapheme-cluster segmentation, std-only.
//!
//! jellyfish's `nysiis` segments its uppercased input into grapheme clusters
//! (`unicode-segmentation`) and operates on those units. To be byte-identical
//! WITHOUT that dependency we implement UAX-29 rule **GB9** — a run of
//! `Grapheme_Extend` (combining marks) attaches to the preceding character — over
//! the vendored [`combining_table`] (`Mn`/`Me`, the combining-mark core of
//! `Grapheme_Extend`). This reproduces `unicode-segmentation`'s clustering for
//! every combining-mark / accented input, which is the only multi-codepoint
//! cluster that occurs in name data.
//!
//! Scope: the emoji-ZWJ (GB11), regional-indicator (GB12/13) and Hangul-conjoining
//! (GB6/7/8) rules are intentionally NOT implemented — those sequences never occur
//! in phonetic name input, and precomposed Hangul syllables are already one
//! codepoint per cluster. For all-precomposed input (no combining marks) this is
//! exactly `chars()`, so it never perturbs the ASCII / precomposed-accented path.

use crate::combining_table::COMBINING;

/// Is `c` a combining mark (general category `Mn` or `Me`) — i.e. a
/// `Grapheme_Extend` codepoint for GB9 purposes?
fn is_combining(c: char) -> bool {
    let cp = c as u32;
    COMBINING
        .binary_search_by(|&(lo, hi)| {
            if cp < lo {
                std::cmp::Ordering::Greater
            } else if cp > hi {
                std::cmp::Ordering::Less
            } else {
                std::cmp::Ordering::Equal
            }
        })
        .is_ok()
}

/// Segment a codepoint slice into grapheme-cluster strings (UAX-29 GB9 / combining
/// marks). Each returned `String` is one cluster: a base codepoint followed by its
/// run of combining marks. A leading combining mark forms its own cluster.
pub fn graphemes(chars: &[char]) -> Vec<String> {
    let mut out: Vec<String> = Vec::with_capacity(chars.len());
    let mut cur = String::new();
    for &c in chars {
        if is_combining(c) && !cur.is_empty() {
            cur.push(c);
        } else {
            if !cur.is_empty() {
                out.push(std::mem::take(&mut cur));
            }
            cur.push(c);
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seg(s: &str) -> Vec<String> {
        graphemes(&s.chars().collect::<Vec<_>>())
    }

    #[test]
    fn ascii_is_one_per_char() {
        assert_eq!(seg("ROBERT"), vec!["R", "O", "B", "E", "R", "T"]);
        assert!(seg("").is_empty());
    }

    #[test]
    fn precomposed_is_one_per_char() {
        // precomposed é is a single codepoint -> single cluster
        assert_eq!(seg("JOS\u{c9}"), vec!["J", "O", "S", "\u{c9}"]);
    }

    #[test]
    fn combining_marks_attach() {
        // base + combining mark cluster together
        assert_eq!(seg("U\u{308}"), vec!["U\u{308}"]);
        assert_eq!(
            seg("MU\u{308}LLER"),
            vec!["M", "U\u{308}", "L", "L", "E", "R"]
        );
        // two combining marks on one base
        assert_eq!(seg("a\u{301}\u{308}b"), vec!["a\u{301}\u{308}", "b"]);
    }

    #[test]
    fn leading_combining_forms_own_cluster() {
        assert_eq!(seg("\u{301}\u{301}a"), vec!["\u{301}\u{301}", "a"]);
    }
}
