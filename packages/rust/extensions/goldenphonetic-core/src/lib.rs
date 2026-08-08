//! Byte-identical-to-`jellyfish` phonetic encoders — GoldenMatch's OWN pyo3-free,
//! zero-runtime-dependency port of the five phonetic algorithms the Python
//! `jellyfish` package (>= 1.0) exposes, replacing a `jellyfish` dependency on the
//! shipped path so the "one authoritative semantic owner per capability"
//! invariant holds for the phonetic keys themselves.
//!
//!   * [`soundex`]                 == `jellyfish.soundex`               (American Soundex)
//!   * [`metaphone`]               == `jellyfish.metaphone`             (original Metaphone, Philips 1990)
//!   * [`nysiis`]                  == `jellyfish.nysiis`                (NYSIIS)
//!   * [`match_rating_codex`]      == `jellyfish.match_rating_codex`    (Match Rating Approach codex)
//!   * [`match_rating_comparison`] == `jellyfish.match_rating_comparison` (MRA comparison)
//!
//! jellyfish (>= 1.0) is itself a Rust implementation exposed via pyo3, so these
//! are faithful ports of its `src/*.rs`. The one external behaviour jellyfish
//! leans on is Unicode: `soundex`/`metaphone` run `s.to_uppercase().nfkd()` and
//! `nysiis`/`match_rating` run `s.to_uppercase()` over grapheme clusters. We match
//! `to_uppercase()` for free (std `str::to_uppercase`, the very function jellyfish
//! calls) and vendor NFKD as generated static data (see [`nfkd`]) so the crate
//! stays dependency-free. Grapheme segmentation collapses to codepoints on every
//! input these encoders actually accept (see the `match_rating`/`nysiis` notes).
//!
//! Byte-identical output is PROVEN, not assumed: the `emit_phonetic_corpus` test
//! dumps a >5000-input + >2000-pair deterministic corpus with our outputs, and
//! `scripts/check_phonetic_parity.py` recomputes each with Python `jellyfish` and
//! asserts exact string / `Option<bool>` equality (including that jellyfish RAISES
//! exactly where our `match_rating_codex` returns `Err`).
//!
//! # Authoritative sources
//!
//! Read these instead of inferring behaviour from the implementation. Which
//! guarantees are load-bearing, which fallbacks are deliberate, and which
//! parities are contract-tested are documented *decisions* -- the code shows
//! what one path does, not why:
//!
//!   * <https://docs.rs/goldenphonetic-core> -- this API.
//!   * <https://docs.bensevern.dev/llms.txt> -- index of every Golden Suite
//!     surface, written for machine readers.
//!   * <https://github.com/benseverndev-oss/goldenmatch> -- source, issues and
//!     the design records.

mod combining_table;
mod grapheme;
mod nfkd;
mod nfkd_table;

use grapheme::graphemes;
use nfkd::nfkd_chars;

// ---------------------------------------------------------------------------
// Soundex  (jellyfish src/soundex.rs)
// ---------------------------------------------------------------------------

/// American Soundex, byte-identical to `jellyfish.soundex`.
///
/// `s.to_uppercase().nfkd()`, keep the first codepoint verbatim, then map
/// consonants to Soundex digits (collapsing adjacent equal digits, `H`/`W`
/// transparent), pad/truncate to length 4.
///
/// ## ASCII fast path
/// For pure-ASCII input (the overwhelming common case for names) `to_uppercase`
/// == `to_ascii_uppercase` char-for-char and NFKD is the identity, so we skip the
/// vendored per-char NFKD table lookup ([`nfkd_chars`]) entirely and build the
/// working `char` vector directly. The unicode path below is preserved verbatim as
/// the byte-identical-to-jellyfish reference for accented / decomposed input. Both
/// feed the same downstream `Vec<char>` loop — no algorithm duplication.
pub fn soundex(s: &str) -> String {
    if s.is_empty() {
        return String::new();
    }

    let v: Vec<char> = if s.is_ascii() {
        // ASCII: uppercase in place, NFKD is identity — skip the table lookup.
        s.chars().map(|c| c.to_ascii_uppercase()).collect()
    } else {
        let upper: Vec<char> = s.to_uppercase().chars().collect();
        nfkd_chars(&upper)
    };
    if v.is_empty() {
        // to_uppercase()/nfkd() cannot empty a non-empty string in practice, but
        // guard the v[0] index defensively (jellyfish indexes v[0] unconditionally).
        return String::new();
    }

    let replacement = |ch: char| -> char {
        match ch {
            'B' | 'F' | 'P' | 'V' => '1',
            'C' | 'G' | 'J' | 'K' | 'Q' | 'S' | 'X' | 'Z' => '2',
            'D' | 'T' => '3',
            'L' => '4',
            'M' | 'N' => '5',
            'R' => '6',
            _ => '*',
        }
    };

    let mut result: Vec<char> = Vec::new();
    result.push(v[0]);
    let mut last = replacement(v[0]);

    for &letter in v.iter().skip(1) {
        let sub = replacement(letter);
        if sub != '*' {
            if sub != last {
                result.push(sub);
                if result.len() == 4 {
                    break;
                }
            }
            last = sub;
        } else if letter != 'H' && letter != 'W' {
            last = '*';
        }
    }

    while result.len() < 4 {
        result.push('0');
    }
    result.into_iter().collect()
}

// ---------------------------------------------------------------------------
// Metaphone  (jellyfish src/metaphone.rs — original Metaphone, Philips 1990)
// ---------------------------------------------------------------------------

#[inline]
fn meta_isvowel(s: char) -> bool {
    matches!(s, 'A' | 'E' | 'I' | 'O' | 'U')
}

#[inline]
fn is_iey(s: char) -> bool {
    matches!(s, 'I' | 'E' | 'Y')
}

/// Original Metaphone (Lawrence Philips, 1990), byte-identical to
/// `jellyfish.metaphone`. NOT double-metaphone.
pub fn metaphone(s: &str) -> String {
    if s.is_empty() {
        return String::new();
    }

    let upper = s.to_uppercase();
    let upper_chars: Vec<char> = upper.chars().collect();
    let mut v = nfkd_chars(&upper_chars);
    let mut ret: Vec<char> = Vec::new();

    // skip first character if s starts with these
    if upper.starts_with("KN")
        || upper.starts_with("GN")
        || upper.starts_with("PN")
        || upper.starts_with("WR")
        || upper.starts_with("AE")
    {
        // s is non-empty (guarded above) so v (its NFKD) is non-empty; jellyfish
        // removes unconditionally.
        v.remove(0);
    }

    let mut i = 0;
    while i < v.len() {
        let c = v[i];
        let next = if i + 1 < v.len() { v[i + 1] } else { '*' };
        let nextnext = if i + 2 < v.len() { v[i + 2] } else { '*' };

        // skip doubles except for CC
        if c == next && c != 'C' {
            i += 1;
            continue;
        }

        match c {
            'A' | 'E' | 'I' | 'O' | 'U' => {
                if i == 0 || v[i - 1] == ' ' {
                    ret.push(c);
                }
            }
            'B' => {
                if (i == 0 || v[i - 1] != 'M') || next != '*' {
                    ret.push('B');
                }
            }
            'C' => {
                if next == 'I' && nextnext == 'A' || next == 'H' {
                    i += 1;
                    ret.push('X');
                } else if is_iey(next) {
                    i += 1;
                    ret.push('S');
                } else {
                    ret.push('K');
                }
            }
            'D' => {
                if next == 'G' && is_iey(nextnext) {
                    i += 2;
                    ret.push('J');
                } else {
                    ret.push('T');
                }
            }
            'F' | 'J' | 'L' | 'M' | 'N' | 'R' => {
                ret.push(c);
            }
            'G' => {
                if is_iey(next) {
                    ret.push('J');
                } else if (next == 'H' && nextnext != '*' && !meta_isvowel(nextnext))
                    || (next == 'N' && nextnext == '*')
                {
                    i += 1;
                } else {
                    ret.push('K');
                }
            }
            'H' => {
                if i == 0 || meta_isvowel(next) || !meta_isvowel(v[i - 1]) {
                    ret.push('H');
                }
            }
            'K' => {
                if i == 0 || v[i - 1] != 'C' {
                    ret.push('K');
                }
            }
            'P' => {
                if next == 'H' {
                    i += 1;
                    ret.push('F');
                } else {
                    ret.push('P');
                }
            }
            'Q' => {
                ret.push('K');
            }
            'S' => {
                if next == 'H' {
                    i += 1;
                    ret.push('X');
                } else if next == 'I' && (nextnext == 'O' || nextnext == 'A') {
                    i += 2;
                    ret.push('X');
                } else {
                    ret.push('S');
                }
            }
            'T' => {
                if next == 'I' && (nextnext == 'O' || nextnext == 'A') {
                    ret.push('X');
                } else if next == 'H' {
                    i += 1;
                    ret.push('0');
                } else if next != 'C' || nextnext != 'H' {
                    ret.push('T');
                }
            }
            'V' => {
                ret.push('F');
            }
            'W' => {
                if i == 0 && next == 'H' {
                    i += 1;
                    ret.push('W');
                } else if meta_isvowel(next) {
                    ret.push('W');
                }
            }
            'X' => {
                if i == 0 {
                    if next == 'H' || (next == 'I' && (nextnext == 'O' || nextnext == 'A')) {
                        ret.push('X');
                    } else {
                        ret.push('S');
                    }
                } else {
                    ret.push('K');
                    ret.push('S');
                }
            }
            'Y' => {
                if meta_isvowel(next) {
                    ret.push('Y');
                }
            }
            'Z' => {
                ret.push('S');
            }
            ' ' if !ret.is_empty() && ret[ret.len() - 1] != ' ' => {
                ret.push(' ');
            }
            _ => {}
        }
        i += 1;
    }

    ret.into_iter().collect()
}

// ---------------------------------------------------------------------------
// NYSIIS  (jellyfish src/nysiis.rs)
// ---------------------------------------------------------------------------
//
// jellyfish segments the uppercased string into GRAPHEME clusters and works on
// those `&str` units; we mirror that with `Vec<String>` grapheme units (see
// `grapheme::graphemes`, UAX-29 GB9). This is byte-identical to jellyfish for both
// precomposed AND decomposed (combining-mark) accented input — José, Müller, Ærø
// precomposed, and their NFD forms — proven over the fuzz corpus in
// `scripts/check_phonetic_parity.py`.

/// One unit of the NYSIIS working sequence. The unicode path uses grapheme
/// clusters (`String`); the ASCII fast path uses bare `char`s. The core algorithm
/// ([`nysiis_core`]) is written once over this trait so there is a single source of
/// truth for the NYSIIS logic — only the unit representation differs.
trait NyUnit: Clone + PartialEq {
    /// Build a unit from a single ASCII letter (the algorithm only ever synthesizes
    /// single-ASCII-letter units: `A`/`F`/`G`/`S`/`N`/`C`/`D`/`Y`).
    fn from_ascii(c: u8) -> Self;
    /// Does this unit equal the single ASCII letter `c`? (A multi-codepoint
    /// grapheme is never equal to a one-byte ASCII letter.)
    fn eq_ascii(&self, c: u8) -> bool;
    /// Append this unit to the output key string.
    fn push_key(&self, out: &mut String);
    /// Is this unit one of the NYSIIS vowels `A`/`E`/`I`/`O`/`U`?
    #[inline]
    fn is_vowel(&self) -> bool {
        self.eq_ascii(b'A')
            || self.eq_ascii(b'E')
            || self.eq_ascii(b'I')
            || self.eq_ascii(b'O')
            || self.eq_ascii(b'U')
    }
}

impl NyUnit for char {
    #[inline]
    fn from_ascii(c: u8) -> Self {
        c as char
    }
    #[inline]
    fn eq_ascii(&self, c: u8) -> bool {
        *self == c as char
    }
    #[inline]
    fn push_key(&self, out: &mut String) {
        out.push(*self);
    }
}

impl NyUnit for String {
    #[inline]
    fn from_ascii(c: u8) -> Self {
        (c as char).to_string()
    }
    #[inline]
    fn eq_ascii(&self, c: u8) -> bool {
        // A grapheme equals an ASCII letter iff it is exactly that one byte.
        self.len() == 1 && self.as_bytes()[0] == c
    }
    #[inline]
    fn push_key(&self, out: &mut String) {
        out.push_str(self);
    }
}

/// The NYSIIS algorithm over an abstract unit sequence, byte-identical to
/// `jellyfish.nysiis`. `v` is the (grapheme- or char-) segmented, uppercased input;
/// `upper` is the uppercased string used only for the prefix/suffix `starts_with` /
/// `ends_with` tests (identical between paths on ASCII, and the reference behaviour
/// on unicode). See [`nysiis`] for the ASCII/unicode split that feeds this.
fn nysiis_core<U: NyUnit>(mut v: Vec<U>, upper: &str) -> String {
    // step 1: prefixes
    if upper.starts_with("MAC") {
        v[1] = U::from_ascii(b'C'); // MAC -> MCC
    } else if upper.starts_with("KN") {
        v.remove(0); // strip leading K from KN
    } else if upper.starts_with('K') {
        v[0] = U::from_ascii(b'C'); // K -> C
    } else if upper.starts_with("PH") || upper.starts_with("PF") {
        v[0] = U::from_ascii(b'F');
        v[1] = U::from_ascii(b'F'); // -> FF
    } else if upper.starts_with("SCH") {
        v[1] = U::from_ascii(b'S');
        v[2] = U::from_ascii(b'S'); // SCH -> SSS
    }

    // step 2: suffixes
    if upper.ends_with("IE") || upper.ends_with("EE") {
        v.pop();
        v.pop();
        v.push(U::from_ascii(b'Y'));
    } else if upper.ends_with("DT")
        || upper.ends_with("RT")
        || upper.ends_with("RD")
        || upper.ends_with("NT")
        || upper.ends_with("ND")
    {
        v.pop();
        v.pop();
        v.push(U::from_ascii(b'D'));
    }

    // step 3: key starts with first character
    let mut key: Vec<U> = Vec::with_capacity(v.len());
    key.push(v[0].clone());

    // step 4: translate remaining characters.
    //
    // Each iteration produces a 1- or 2-unit replacement group. jellyfish builds a
    // `Vec` per character; we hold the group in a stack pair `(u0, u1)` so the hot
    // loop makes ZERO heap allocations per character. Every arm produces at least
    // `u0` (the `vec` was never empty), so the old `!chars.is_empty()` guard drops.
    let mut i = 1;
    while i < v.len() {
        // Ordered exactly like jellyfish's `match v[i] { .. }` guard chain.
        let u0: U;
        let mut u1: Option<U> = None;
        if v[i].eq_ascii(b'E') && i + 1 < v.len() && v[i + 1].eq_ascii(b'V') {
            i += 1;
            u0 = U::from_ascii(b'A');
            u1 = Some(U::from_ascii(b'F'));
        } else if v[i].is_vowel() {
            u0 = U::from_ascii(b'A');
        } else if v[i].eq_ascii(b'Q') {
            u0 = U::from_ascii(b'G');
        } else if v[i].eq_ascii(b'Z') {
            u0 = U::from_ascii(b'S');
        } else if v[i].eq_ascii(b'M') {
            u0 = U::from_ascii(b'N');
        } else if v[i].eq_ascii(b'K') {
            u0 = if i + 1 < v.len() && v[i + 1].eq_ascii(b'N') {
                U::from_ascii(b'N')
            } else {
                U::from_ascii(b'C')
            };
        } else if v[i].eq_ascii(b'S')
            && i + 2 < v.len()
            && v[i + 1].eq_ascii(b'C')
            && v[i + 2].eq_ascii(b'H')
        {
            i += 2;
            u0 = U::from_ascii(b'S');
            u1 = Some(U::from_ascii(b'S'));
        } else if v[i].eq_ascii(b'P') && i + 1 < v.len() && v[i + 1].eq_ascii(b'H') {
            i += 1;
            u0 = U::from_ascii(b'F');
        } else if v[i].eq_ascii(b'H')
            && (!v[i - 1].is_vowel()
                || (i + 1 < v.len() && !v[i + 1].is_vowel())
                || (i + 1 == v.len()))
        {
            u0 = if v[i - 1].is_vowel() {
                U::from_ascii(b'A')
            } else {
                v[i - 1].clone()
            };
        } else if v[i].eq_ascii(b'W') && v[i - 1].is_vowel() {
            u0 = v[i - 1].clone();
        } else {
            u0 = v[i].clone();
        }

        // Dedup on the group's LAST unit vs key's last; push the whole group if it
        // differs (jellyfish: `if chars[-1] != key[-1]: key.extend(chars)`).
        let differs = {
            let last = u1.as_ref().unwrap_or(&u0);
            *last != key[key.len() - 1]
        };
        if differs {
            key.push(u0);
            if let Some(u1) = u1 {
                key.push(u1);
            }
        }

        i += 1;
    }

    // step 5: remove trailing S
    if key[key.len() - 1].eq_ascii(b'S') && key.len() > 1 {
        key.pop();
    }

    // step 6: replace AY with Y
    if key.len() >= 2 && key[key.len() - 2].eq_ascii(b'A') && key[key.len() - 1].eq_ascii(b'Y') {
        key.remove(key.len() - 2);
    }

    // step 7: remove trailing A
    if key[key.len() - 1].eq_ascii(b'A') && key.len() > 1 {
        key.pop();
    }

    let mut out = String::with_capacity(key.len());
    for u in &key {
        u.push_key(&mut out);
    }
    out
}

/// NYSIIS phonetic encoding, byte-identical to `jellyfish.nysiis`.
///
/// ## ASCII fast path
/// For pure-ASCII input each `char` is its own grapheme cluster, so we skip the
/// UAX-29 grapheme segmentation ([`graphemes`]) and its per-unit `String`
/// allocations — the working sequence is a `Vec<char>` fed to [`nysiis_core`]. The
/// unicode path builds the grapheme `Vec<String>` exactly as jellyfish does and is
/// the byte-identical reference for accented / combining-mark input. Both call the
/// same generic core, so there is one NYSIIS implementation, not two.
pub fn nysiis(s: &str) -> String {
    if s.is_empty() {
        return String::new();
    }

    if s.is_ascii() {
        let upper = s.to_ascii_uppercase();
        let v: Vec<char> = upper.chars().collect();
        nysiis_core(v, &upper)
    } else {
        let upper = s.to_uppercase();
        let v: Vec<String> = graphemes(&upper.chars().collect::<Vec<_>>());
        nysiis_core(v, &upper)
    }
}

// ---------------------------------------------------------------------------
// Match Rating Approach  (jellyfish src/match_rating.rs)
// ---------------------------------------------------------------------------
//
// jellyfish segments graphemes here too, but `match_rating_codex` first REJECTS
// any input whose codepoints are not all alphabetic-or-space (`Err`), and combining
// marks are non-alphabetic — so every input that reaches the codex loop is free of
// combining marks and `chars() == graphemes()`. Byte-identical on the accepted
// domain (proven over the corpus).

/// Match Rating Approach codex, faithful to `jellyfish.match_rating_codex`.
///
/// Returns `Err` (jellyfish's `Result<String, String>` core signature — the pyo3
/// binding maps this to a Python `ValueError`) when the input contains any
/// non-alphabetic, non-space codepoint. `Ok` otherwise, including `Ok("")` for the
/// empty string.
pub fn match_rating_codex(s: &str) -> Result<String, String> {
    let upper = s.to_uppercase();
    let v: Vec<char> = upper.chars().collect();
    let mut codex = String::new();
    let mut prev = '\u{0}'; // stands in for jellyfish's "~tmp~" sentinel (never a real char)
    let mut have_prev = false;

    let is_alpha = upper.chars().all(|c| c.is_alphabetic() || c == ' ');
    if !is_alpha {
        return Err(String::from(
            "Strings must only contain alphabetical characters",
        ));
    }

    for (i, &c) in v.iter().enumerate() {
        let vowel = c == 'A' || c == 'E' || c == 'I' || c == 'O' || c == 'U';
        // not a space && ((starting char & vowel) || non-double consonant)
        // Operator precedence mirrors jellyfish exactly:
        //   c != ' ' && (i == 0 && vowel) || (!vowel && c != prev)
        let dup = have_prev && c == prev;
        if c != ' ' && (i == 0 && vowel) || (!vowel && !dup) {
            codex.push(c);
        }
        prev = c;
        have_prev = true;
    }

    if codex.len() > 6 {
        // NOTE: `codex.len()` is BYTES (jellyfish's `String::len`), but the slice is
        // by CHARS — replicated exactly (matters only for multi-byte codepoints).
        let first_three: String = codex.chars().take(3).collect();
        let last_three: String = {
            let rev: String = codex.chars().rev().take(3).collect();
            rev.chars().rev().collect()
        };
        return Ok(first_three + &last_three);
    }

    Ok(codex)
}

/// The `Result` form of the MRA comparison, faithful to jellyfish's core
/// `match_rating_comparison` (`Err` on non-alpha codex or a codex-length
/// difference >= 3). Prefer the public [`match_rating_comparison`].
fn match_rating_comparison_result(s1: &str, s2: &str) -> Result<bool, String> {
    let codex1 = match_rating_codex(s1)?;
    let codex2 = match_rating_codex(s2)?;

    // byte lengths, as jellyfish (String::len)
    let (longer, shorter) = if codex1.len() > codex2.len() {
        (codex1, codex2)
    } else {
        (codex2, codex1)
    };

    let lensum = longer.len() + shorter.len();

    // can't compare when the length difference is >= 3
    if longer.len() - shorter.len() >= 3 {
        return Err(String::from("strings differ in length by more than 2"));
    }

    // remove matching characters going forward
    let mut res1: Vec<char> = Vec::new();
    let mut res2: Vec<char> = Vec::new();
    let mut iter1 = longer.chars();
    let mut iter2 = shorter.chars();
    loop {
        match (iter1.next(), iter2.next()) {
            (Some(x), Some(y)) => {
                if x != y {
                    res1.push(x);
                    res2.push(y);
                }
            }
            (Some(x), None) => res1.push(x),
            (None, Some(y)) => res2.push(y),
            (None, None) => break,
        }
    }

    // count unmatched characters going backwards
    let mut unmatched_count1 = 0i64;
    let mut unmatched_count2 = 0i64;
    let mut it1 = res1.iter().rev();
    let mut it2 = res2.iter().rev();
    loop {
        match (it1.next(), it2.next()) {
            (Some(x), Some(y)) => {
                if x != y {
                    unmatched_count1 += 1;
                    unmatched_count2 += 1;
                }
            }
            (Some(_), None) => unmatched_count1 += 1,
            (None, Some(_)) => unmatched_count2 += 1,
            (None, None) => break,
        }
    }

    let score = 6 - std::cmp::max(unmatched_count1, unmatched_count2);
    Ok(match lensum {
        0..=4 => score >= 5,
        5..=7 => score >= 4,
        8..=11 => score >= 3,
        _ => score >= 2,
    })
}

/// Match Rating Approach comparison, byte-identical to the Python
/// `jellyfish.match_rating_comparison`: `Some(bool)` when comparable, `None` when
/// not — i.e. the codex-length difference is >= 3 OR either input is rejected by
/// the codex (non-alphabetic). jellyfish's pyo3 binding folds both `Err` cases to
/// `None` (it never raises for this function).
pub fn match_rating_comparison(s1: &str, s2: &str) -> Option<bool> {
    match_rating_comparison_result(s1, s2).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn directed_identities() {
        assert_eq!(soundex("Robert"), "R163");
        assert_eq!(soundex(""), "");
        assert_eq!(metaphone("Thompson"), "0MPSN"); // TH -> 0 (theta)
        assert_eq!(nysiis("Catherine"), "CATARAN");
        assert_eq!(match_rating_codex("Byrne").unwrap(), "BYRN");
        assert!(match_rating_codex("O'Brien").is_err());
        assert_eq!(match_rating_comparison("Byrne", "Boern"), Some(true));
        assert_eq!(match_rating_comparison("Tim", "Timothy"), None); // length diff
        assert_eq!(match_rating_comparison("O'Brien", "OBrien"), None); // non-alpha
    }

    // ---- Deterministic corpus + jellyfish oracle (the proof gate) -------------
    // jellyfish is NOT a dependency (its algorithms are ported above); the corpus
    // this test emits is cross-checked against the Python `jellyfish` package by
    // `scripts/check_phonetic_parity.py`, asserting exact equality.

    fn next(state: &mut u64) -> u64 {
        *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = *state;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    // Real-ish name fragments (given + surname) across a range of phonetic shapes.
    const NAMES: &[&str] = &[
        "robert",
        "rupert",
        "rubin",
        "ashcraft",
        "ashcroft",
        "tymczak",
        "pfister",
        "honeyman",
        "catherine",
        "kathryn",
        "kathy",
        "smith",
        "smyth",
        "smithe",
        "jonathan",
        "john",
        "jon",
        "jonathon",
        "thompson",
        "thomson",
        "tomson",
        "byrne",
        "boern",
        "michael",
        "mike",
        "michel",
        "michelle",
        "gutierrez",
        "vandeusen",
        "deusen",
        "van",
        "der",
        "berg",
        "vanderberg",
        "mcdonald",
        "macdonald",
        "macintosh",
        "mackenzie",
        "knight",
        "knuth",
        "gnat",
        "wright",
        "wrangler",
        "pneumonia",
        "aegean",
        "phillip",
        "philip",
        "schwartz",
        "schmidt",
        "scholar",
        "school",
        "quentin",
        "quincy",
        "xavier",
        "xerxes",
        "yvonne",
        "yolanda",
        "zachary",
        "zoe",
        "anne",
        "mary",
        "maryanne",
        "obrien",
        "oconnor",
        "dangelo",
        "edwards",
        "edward",
        "richard",
        "richards",
        "nathan",
        "nathaniel",
        "elizabeth",
        "beth",
        "william",
        "will",
        "bill",
        "andrew",
        "drew",
        "matthew",
        "matt",
        "christopher",
        "chris",
        "kristopher",
        "jennifer",
        "jenny",
        "gennifer",
        "wojcik",
        "kowalski",
        "nowak",
        "muller",
        "mueller",
        "schneider",
        "fischer",
        "weber",
        "meyer",
        "wagner",
        "becker",
        "hoffmann",
        "koch",
        "richter",
        "klein",
        "wolf",
        "schroeder",
        "neumann",
        "a",
        "i",
        "o",
        "ed",
        "al",
        "jo",
        "ng",
        "ba",
        "sz",
        "cz",
        "th",
        "ph",
    ];

    // Precomposed accented / non-ASCII names (one codepoint per grapheme).
    const ACCENTED: &[&str] = &[
        "jos\u{e9}",           // josé
        "m\u{fc}ller",         // müller
        "\u{c6}r\u{f8}",       // Ærø
        "na\u{ef}ve",          // naïve
        "beyonc\u{e9}",        // beyoncé
        "zo\u{eb}",            // zoë
        "fran\u{e7}ois",       // françois
        "h\u{e5}kon",          // håkon
        "\u{152}uvre",         // Œuvre
        "\u{111}okovi\u{107}", // đoković
        "stra\u{df}e",         // straße (ß -> SS on uppercase)
        "g\u{f6}del",          // gödel
        "\u{e9}mile",          // émile
        "renn\u{e9}",          // renné
        "\u{f1}o\u{f1}o",      // ñoño
        "\u{e5}sa",            // åsa
        "j\u{fc}rgen",         // jürgen
        "\u{e7}etin",          // çetin
        "\u{fc}nal",           // ünal
        "\u{f6}zil",           // özil
    ];

    // DECOMPOSED accented names: base letter + a trailing COMBINING mark, the one
    // case where a grapheme cluster spans multiple codepoints (jellyfish segments
    // graphemes; we use codepoints). Included to PROVE parity holds here too
    // (soundex/metaphone NFKD-normalize anyway; for nysiis the combining mark is a
    // distinct non-vowel codepoint that both sides carry identically).
    const DECOMPOSED: &[&str] = &[
        "jose\u{301}",        // josé (e + combining acute)
        "mu\u{308}ller",      // müller (u + combining diaeresis)
        "nai\u{308}ve",       // naïve
        "beyonce\u{301}",     // beyoncé
        "zo\u{308}e",         // zöe-ish
        "franc\u{327}ois",    // françois (c + combining cedilla)
        "ha\u{30a}kon",       // håkon (a + combining ring)
        "go\u{308}del",       // gödel
        "c\u{327}etin",       // çetin
        "n\u{303}on\u{303}o", // ñoño (n + combining tilde)
        "A\u{301}",           // single letter + combining accent
    ];

    // Hyphen / apostrophe / punctuation (soundex & metaphone & nysiis accept these;
    // match_rating_codex rejects them -> Err, which the corpus records + verifies).
    const PUNCT: &[&str] = &[
        "o'brien",
        "o'connor",
        "d'angelo",
        "d'artagnan",
        "smith-jones",
        "jean-luc",
        "mary-jane",
        "anne-marie",
        "o'neill",
        "l'amour",
        "de'ath",
    ];

    const CASES: &[fn(&str) -> String] = &[
        |s: &str| s.to_string(),
        |s: &str| s.to_uppercase(),
        |s: &str| {
            let mut c = s.chars();
            match c.next() {
                Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
                None => String::new(),
            }
        },
        |s: &str| {
            // aLtErNaTiNg case
            s.chars()
                .enumerate()
                .map(|(i, ch)| {
                    if i % 2 == 0 {
                        ch.to_uppercase().collect::<String>()
                    } else {
                        ch.to_lowercase().collect::<String>()
                    }
                })
                .collect()
        },
    ];

    fn pick<'a>(rng: &mut u64, pool: &[&'a str]) -> &'a str {
        pool[(next(rng) as usize) % pool.len()]
    }

    /// Build a deterministic corpus of >5000 single inputs covering: real names,
    /// all-caps / mixed / alternating case, accented, hyphen/apostrophe, digits,
    /// whitespace, compound (spaces), single-char and empty.
    fn unary_corpus() -> Vec<String> {
        let mut rng: u64 = 0xC0FF_EE12_3456_789A;
        let mut out: Vec<String> = Vec::new();

        // Directed edge cases.
        for s in [
            "",
            " ",
            "  ",
            "a",
            "A",
            "1",
            "123",
            "a1b2",
            "  spaces  ",
            "mixed CASE",
            "O'Brien",
            "Smith-Jones",
            "MacDonald",
            "van der Berg",
            "\u{e9}",
            "\u{df}",
            "\u{c6}",
            "\u{f8}",
            "KNIGHT",
            "GNAT",
            "WRIGHT",
            "PNEUMONIA",
            "AEGEAN",
        ] {
            out.push(s.to_string());
        }

        // ~6000 randomized inputs.
        for _ in 0..6000 {
            let cat = (next(&mut rng) as usize) % 13;
            let s = match cat {
                0 => pick(&mut rng, NAMES).to_string(),
                1 => {
                    // two-token compound "first last"
                    format!("{} {}", pick(&mut rng, NAMES), pick(&mut rng, NAMES))
                }
                2 => pick(&mut rng, ACCENTED).to_string(),
                3 => pick(&mut rng, PUNCT).to_string(),
                4 => {
                    // apply a random case transform to a name
                    let n = pick(&mut rng, NAMES);
                    CASES[(next(&mut rng) as usize) % CASES.len()](n)
                }
                5 => {
                    // accented, cased
                    let n = pick(&mut rng, ACCENTED);
                    CASES[(next(&mut rng) as usize) % CASES.len()](n)
                }
                6 => {
                    // digits mixed into a name
                    format!("{}{}", pick(&mut rng, NAMES), (next(&mut rng) % 1000))
                }
                7 => {
                    // single character (letter/digit/space/accent)
                    let alpha = ['a', 'Q', 'z', '1', ' ', '\u{e9}', '\u{f8}', 'K'];
                    alpha[(next(&mut rng) as usize) % alpha.len()].to_string()
                }
                8 => {
                    // whitespace-heavy compound (multiple spaces, leading/trailing)
                    format!("  {}   {} ", pick(&mut rng, NAMES), pick(&mut rng, NAMES))
                }
                9 => {
                    // three-token
                    format!(
                        "{} {} {}",
                        pick(&mut rng, NAMES),
                        pick(&mut rng, NAMES),
                        pick(&mut rng, NAMES)
                    )
                }
                10 => {
                    // char-level edit of a name (substitute one letter)
                    let n = pick(&mut rng, NAMES);
                    let mut cs: Vec<char> = n.chars().collect();
                    if !cs.is_empty() {
                        let idx = (next(&mut rng) as usize) % cs.len();
                        let letters = ['a', 'e', 'i', 'o', 'u', 'y', 'h', 'w', 'c', 'k'];
                        cs[idx] = letters[(next(&mut rng) as usize) % letters.len()];
                    }
                    cs.into_iter().collect()
                }
                11 => pick(&mut rng, DECOMPOSED).to_string(),
                _ => {
                    // accented + punct + digits blend (stresses codex rejection etc.)
                    format!("{}'{}", pick(&mut rng, ACCENTED), pick(&mut rng, NAMES))
                }
            };
            out.push(s);
        }
        out
    }

    /// Over 2000 name PAIRS for match_rating_comparison (many comparable, some
    /// rejected as non-alpha, some with a codex-length difference of 3 or more).
    fn pair_corpus() -> Vec<(String, String)> {
        let mut rng: u64 = 0x1357_9BDF_2468_ACE0;
        let mut out: Vec<(String, String)> = Vec::new();

        // Directed pairs.
        for (a, b) in [
            ("Byrne", "Boern"),
            ("Smith", "Smyth"),
            ("Catherine", "Kathryn"),
            ("Michael", "Mike"),
            ("Tim", "Timothy"),
            ("", ""),
            ("a", "a"),
            ("O'Brien", "OBrien"),
            ("Robert", "Rupert"),
            ("Jon", "John"),
        ] {
            out.push((a.to_string(), b.to_string()));
        }

        for _ in 0..2500 {
            let pool = match (next(&mut rng) as usize) % 5 {
                0 => NAMES,
                1 => ACCENTED,
                2 => PUNCT,
                3 => DECOMPOSED,
                _ => NAMES,
            };
            let a = pick(&mut rng, pool).to_string();
            let b = if (next(&mut rng) & 3) == 0 {
                // sometimes compare against a longer compound (forces length-diff)
                format!("{} {}", pick(&mut rng, NAMES), pick(&mut rng, NAMES))
            } else {
                pick(&mut rng, &[NAMES, ACCENTED, PUNCT].concat()).to_string()
            };
            // occasionally case-vary
            let a = if (next(&mut rng) & 1) == 0 {
                a.to_uppercase()
            } else {
                a
            };
            out.push((a, b));
        }
        out
    }

    fn json_escape(s: &str) -> String {
        let mut out = String::from("\"");
        for c in s.chars() {
            match c {
                '"' => out.push_str("\\\""),
                '\\' => out.push_str("\\\\"),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                '\t' => out.push_str("\\t"),
                c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
                c => out.push(c),
            }
        }
        out.push('"');
        out
    }

    #[test]
    fn emit_phonetic_corpus() {
        let unary = unary_corpus();
        let pairs = pair_corpus();
        assert!(
            unary.len() >= 5000,
            "unary corpus too small: {}",
            unary.len()
        );
        assert!(
            pairs.len() >= 2000,
            "pair corpus too small: {}",
            pairs.len()
        );

        let mut buf = String::new();
        for s in &unary {
            let codex = match match_rating_codex(s) {
                Ok(c) => json_escape(&c),
                Err(_) => String::from("null"),
            };
            buf.push_str(&format!(
                "{{\"kind\":\"u\",\"s\":{},\"soundex\":{},\"metaphone\":{},\"nysiis\":{},\"mrc\":{}}}\n",
                json_escape(s),
                json_escape(&soundex(s)),
                json_escape(&metaphone(s)),
                json_escape(&nysiis(s)),
                codex,
            ));
        }
        for (a, b) in &pairs {
            let cmp = match match_rating_comparison(a, b) {
                Some(true) => "true",
                Some(false) => "false",
                None => "null",
            };
            buf.push_str(&format!(
                "{{\"kind\":\"p\",\"s1\":{},\"s2\":{},\"mrcmp\":{}}}\n",
                json_escape(a),
                json_escape(b),
                cmp,
            ));
        }

        let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("target");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("goldenphonetic_corpus.jsonl");
        std::fs::write(&path, buf).unwrap();
        eprintln!(
            "emitted {} unary + {} pair records to {}",
            unary.len(),
            pairs.len(),
            path.display()
        );
    }
}
