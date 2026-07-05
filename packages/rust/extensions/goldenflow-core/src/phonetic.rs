//! Phonetic key encoders — blocking/match keys for entity resolution. Owned
//! reference kernels; the Python/TS fallbacks reproduce these bytes exactly
//! (byte-parity harness).

/// American **Soundex** (NARA rules). Returns a 4-char code: the leading letter
/// followed by three digits (zero-padded / truncated). The classic mapping:
/// `bfpv`->1, `cgjkqsxz`->2, `dt`->3, `l`->4, `mn`->5, `r`->6; vowels
/// (`aeiouy`) and `hw` code to 0. Adjacency rules: two letters with the same
/// digit are coded once; a **vowel** between them resets the run (both kept),
/// while `h`/`w` are transparent (still coded once). Non-letters are ignored;
/// an input with no ASCII letters yields `""`.
pub fn soundex(s: &str) -> String {
    // ASCII letters only, uppercased.
    let letters: Vec<char> = s
        .chars()
        .filter(|c| c.is_ascii_alphabetic())
        .map(|c| c.to_ascii_uppercase())
        .collect();
    if letters.is_empty() {
        return String::new();
    }

    let mut code = String::with_capacity(4);
    code.push(letters[0]);
    // Seed adjacency with the first letter's digit so an immediately-following
    // same-digit consonant is dropped (e.g. "Pf" -> P, the f is elided).
    let mut last = soundex_digit(letters[0]);

    for &c in &letters[1..] {
        if code.len() >= 4 {
            break;
        }
        let d = soundex_digit(c);
        if d != 0 {
            if d != last {
                code.push((b'0' + d) as char);
            }
            last = d;
        } else if c != 'H' && c != 'W' {
            // A vowel (aeiouy) resets the run; H/W are transparent (last kept).
            last = 0;
        }
    }

    // Pad to exactly 4 with trailing zeros.
    while code.len() < 4 {
        code.push('0');
    }
    code
}

/// Soundex digit for an uppercase ASCII letter: 0 for vowels / `H` / `W` / `Y`
/// (and any non-letter), 1-6 for the consonant classes.
fn soundex_digit(c: char) -> u8 {
    match c {
        'B' | 'F' | 'P' | 'V' => 1,
        'C' | 'G' | 'J' | 'K' | 'Q' | 'S' | 'X' | 'Z' => 2,
        'D' | 'T' => 3,
        'L' => 4,
        'M' | 'N' => 5,
        'R' => 6,
        _ => 0, // A E I O U Y H W (+ anything else)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_examples() {
        // The textbook NARA reference set.
        assert_eq!(soundex("Robert"), "R163");
        assert_eq!(soundex("Rupert"), "R163");
        assert_eq!(soundex("Rubin"), "R150");
        assert_eq!(soundex("Ashcraft"), "A261"); // h transparent: shc -> 2
        assert_eq!(soundex("Ashcroft"), "A261");
        assert_eq!(soundex("Tymczak"), "T522");
        assert_eq!(soundex("Pfister"), "P236"); // Pf -> P (f elided)
        assert_eq!(soundex("Honeyman"), "H555");
    }

    #[test]
    fn vowels_reset_run_hw_transparent() {
        assert_eq!(soundex("Gauss"), "G200"); // vowels between, then s+s -> one 2
        assert_eq!(soundex("Jackson"), "J250");
        assert_eq!(soundex("Washington"), "W252");
    }

    #[test]
    fn case_and_separators_and_empty() {
        assert_eq!(soundex("robert"), "R163");
        assert_eq!(soundex("O'Brien"), "O165"); // apostrophe ignored
        assert_eq!(soundex(""), "");
        assert_eq!(soundex("12345"), ""); // no letters
        assert_eq!(soundex("H"), "H000");
    }
}
