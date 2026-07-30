//! NFKD (compatibility decomposition) over `char`, std-only.
//!
//! jellyfish's `soundex` / `metaphone` run `s.to_uppercase().nfkd()` (the
//! `unicode-normalization` crate) before their ASCII encoding logic. To be
//! byte-identical WITHOUT that runtime dependency, we vendor the full NFKD
//! decomposition table (`nfkd_table::NFKD`, generated from Python `unicodedata`
//! at Unicode 15.0.0) and decompose Hangul syllables algorithmically.
//!
//! Note on canonical ordering: full NFKD also canonically REORDERS trailing
//! combining marks by combining class. We deliberately skip that reorder — every
//! combining mark is a non-`[A-Z]` codepoint, so it is treated identically by
//! both phonetic encoders (soundex maps it to the `*` "separator", metaphone
//! drops it), and mark order among themselves never changes the emitted key.
//! Base letters always sort before their marks in a per-codepoint decomposition,
//! which is all the encoders observe. Proven byte-identical against jellyfish
//! over the fuzz corpus in `scripts/check_phonetic_parity.py`.

use crate::nfkd_table::NFKD;

// Hangul syllable decomposition constants (Unicode 3.12 / algorithm in §3.12).
const S_BASE: u32 = 0xAC00;
const L_BASE: u32 = 0x1100;
const V_BASE: u32 = 0x1161;
const T_BASE: u32 = 0x11A7;
const V_COUNT: u32 = 21;
const T_COUNT: u32 = 28;
const N_COUNT: u32 = V_COUNT * T_COUNT; // 588
const S_COUNT: u32 = 19 * N_COUNT; // 11172

/// Append the NFKD decomposition of one `char` to `out` (recursive form is
/// already baked into the table; Hangul is expanded here).
fn decompose_char(c: char, out: &mut Vec<char>) {
    let cp = c as u32;
    // Hangul syllable block -> conjoining jamo (L, V[, T]).
    if (S_BASE..S_BASE + S_COUNT).contains(&cp) {
        let si = cp - S_BASE;
        let l = L_BASE + si / N_COUNT;
        let v = V_BASE + (si % N_COUNT) / T_COUNT;
        let t = T_BASE + si % T_COUNT;
        out.push(char::from_u32(l).unwrap());
        out.push(char::from_u32(v).unwrap());
        if t != T_BASE {
            out.push(char::from_u32(t).unwrap());
        }
        return;
    }
    // Table lookup for every other codepoint with a differing NFKD.
    match NFKD.binary_search_by(|&(k, _)| k.cmp(&cp)) {
        Ok(idx) => out.extend(NFKD[idx].1.chars()),
        Err(_) => out.push(c),
    }
}

/// Full NFKD of a `char` slice (compatibility decomposition), std-only.
/// Byte-identical to `unicode_normalization`'s `.nfkd()` for the purposes of the
/// phonetic encoders (see the module note on canonical ordering).
pub fn nfkd_chars(input: &[char]) -> Vec<char> {
    let mut out = Vec::with_capacity(input.len());
    for &c in input {
        decompose_char(c, &mut out);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn nfkd_str(s: &str) -> String {
        let v: Vec<char> = s.chars().collect();
        nfkd_chars(&v).into_iter().collect()
    }

    #[test]
    fn ascii_identity() {
        assert_eq!(nfkd_str("ROBERT"), "ROBERT");
        assert_eq!(nfkd_str(""), "");
    }

    #[test]
    fn latin_decomposes() {
        // É -> E + combining acute (U+0301)
        assert_eq!(nfkd_str("\u{c9}"), "E\u{301}");
        // Ç -> C + combining cedilla (U+0327)
        assert_eq!(nfkd_str("\u{c7}"), "C\u{327}");
        // ñ -> n + combining tilde
        assert_eq!(nfkd_str("\u{f1}"), "n\u{303}");
        // ø has NO decomposition
        assert_eq!(nfkd_str("\u{f8}"), "\u{f8}");
    }

    #[test]
    fn ligatures_and_compat() {
        // ﬀ (U+FB00) -> "ff"
        assert_eq!(nfkd_str("\u{fb00}"), "ff");
        // Fullwidth A (U+FF21) -> "A"
        assert_eq!(nfkd_str("\u{ff21}"), "A");
    }

    #[test]
    fn hangul_algorithmic() {
        // 각 (U+AC01) -> ᄀ ᅡ ᆨ  (L=U+1100, V=U+1161, T=U+11A8)
        assert_eq!(nfkd_str("\u{ac01}"), "\u{1100}\u{1161}\u{11a8}");
        // 가 (U+AC00) -> ᄀ ᅡ  (no trailing T)
        assert_eq!(nfkd_str("\u{ac00}"), "\u{1100}\u{1161}");
    }
}
