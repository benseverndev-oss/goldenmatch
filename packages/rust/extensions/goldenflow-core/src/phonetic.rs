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

// --- Double Metaphone ------------------------------------------------------

/// `/[AEIOUY]/` on a single char. The out-of-bounds sentinel `'\0'` is not a
/// vowel (matching JS `vowels.test(undefined)` -> `false`: "undefined" has no
/// UPPERCASE vowel).
fn dm_is_vowel(c: char) -> bool {
    matches!(c, 'A' | 'E' | 'I' | 'O' | 'U' | 'Y')
}

/// JS `String.prototype.slice(a, b)` over the char buffer: negative indices count
/// from the end, indices clamp to `[0, len]`, and `start >= end` yields `""`.
fn dm_slice(chars: &[char], a: i64, b: i64) -> String {
    let len = chars.len() as i64;
    let start = if a < 0 { (len + a).max(0) } else { a.min(len) };
    let end = if b < 0 { (len + b).max(0) } else { b.min(len) };
    if start >= end {
        return String::new();
    }
    chars[start as usize..end as usize].iter().collect()
}

/// Double Metaphone (Lawrence Philips) -> `(primary, secondary)` phonetic codes.
/// A faithful port of the canonical `words/double-metaphone` reference,
/// byte-identical to it (validated against its test vectors). Blocking/match
/// keys for entity resolution.
pub fn double_metaphone(value: &str) -> (String, String) {
    let mut primary = String::new();
    let mut secondary = String::new();
    // JS `value.length` (UTF-16 units == char count for BMP/ASCII names).
    let length = value.chars().count() as i64;
    let last = length - 1;
    // normalized = uppercase + 5-space pad so small look-aheads hit spaces.
    let mut chars: Vec<char> = value.to_uppercase().chars().collect();
    chars.extend(['\u{20}'; 5]);
    let norm: String = chars.iter().collect();

    let is_slavo_germanic =
        norm.contains('W') || norm.contains('K') || norm.contains("CZ") || norm.contains("WITZ");
    let is_germanic =
        norm.starts_with("VAN ") || norm.starts_with("VON ") || norm.starts_with("SCH");

    // char at index; '\0' for out-of-bounds (JS `undefined`: not a vowel, unequal
    // to every real char).
    let at = |i: i64| -> char {
        if i < 0 || (i as usize) >= chars.len() {
            '\0'
        } else {
            chars[i as usize]
        }
    };
    let slice = |a: i64, b: i64| -> String { dm_slice(&chars, a, b) };

    let mut index: i64 = 0;

    // Skip initial GN, KN, PN, WR, PS.
    if matches!(
        norm.as_str(),
        s if s.starts_with("GN") || s.starts_with("KN") || s.starts_with("PN")
            || s.starts_with("WR") || s.starts_with("PS")
    ) {
        index += 1;
    }

    // Initial X (Xavier) -> S.
    if at(0) == 'X' {
        primary.push('S');
        secondary.push('S');
        index += 1;
    }

    while index < length {
        let previous = at(index - 1);
        let next = at(index + 1);
        let nextnext = at(index + 2);

        match at(index) {
            'A' | 'E' | 'I' | 'O' | 'U' | 'Y' | 'À' | 'Ê' | 'É' => {
                if index == 0 {
                    primary.push('A');
                    secondary.push('A');
                }
                index += 1;
            }
            'B' => {
                primary.push('P');
                secondary.push('P');
                if next == 'B' {
                    index += 1;
                }
                index += 1;
            }
            'Ç' => {
                primary.push('S');
                secondary.push('S');
                index += 1;
            }
            'C' => {
                // Various Germanic.
                if previous == 'A'
                    && next == 'H'
                    && nextnext != 'I'
                    && !dm_is_vowel(at(index - 2))
                    && (nextnext != 'E' || {
                        let sv = slice(index - 2, index + 4);
                        sv == "BACHER" || sv == "MACHER"
                    })
                {
                    primary.push('K');
                    secondary.push('K');
                    index += 2;
                } else if index == 0 && slice(index + 1, index + 6) == "AESAR" {
                    // Caesar.
                    primary.push('S');
                    secondary.push('S');
                    index += 2;
                } else if slice(index + 1, index + 4) == "HIA" {
                    // Chianti.
                    primary.push('K');
                    secondary.push('K');
                    index += 2;
                } else if next == 'H' {
                    if index > 0 && nextnext == 'A' && at(index + 3) == 'E' {
                        // Michael.
                        primary.push('K');
                        secondary.push('X');
                        index += 2;
                    } else if index == 0 && dm_initial_greek_ch(&norm, &at) {
                        primary.push('K');
                        secondary.push('K');
                        index += 2;
                    } else {
                        if is_germanic
                            || dm_greek_ch(&slice(index - 2, index + 4))
                            || nextnext == 'T'
                            || nextnext == 'S'
                            || ((index == 0
                                || previous == 'A'
                                || previous == 'E'
                                || previous == 'O'
                                || previous == 'U')
                                && matches!(
                                    nextnext,
                                    ' ' | 'B' | 'F' | 'H' | 'L' | 'M' | 'N' | 'R' | 'V' | 'W'
                                ))
                        {
                            primary.push('K');
                            secondary.push('K');
                        } else if index == 0 {
                            primary.push('X');
                            secondary.push('X');
                        } else if slice(0, 2) == "MC" {
                            primary.push('K');
                            secondary.push('K');
                        } else {
                            primary.push('X');
                            secondary.push('K');
                        }
                        index += 2;
                    }
                } else if next == 'Z' && slice(index - 2, index) != "WI" {
                    // Czerny.
                    primary.push('S');
                    secondary.push('X');
                    index += 2;
                } else if slice(index + 1, index + 4) == "CIA" {
                    // Focaccia.
                    primary.push('X');
                    secondary.push('X');
                    index += 3;
                } else if next == 'C' && !(index == 1 && at(0) == 'M') {
                    // Double C, not McClellan.
                    if (nextnext == 'I' || nextnext == 'E' || nextnext == 'H')
                        && slice(index + 2, index + 4) != "HU"
                    {
                        let sv = slice(index - 1, index + 4);
                        if (index == 1 && previous == 'A') || sv == "UCCEE" || sv == "UCCES" {
                            primary.push_str("KS");
                            secondary.push_str("KS");
                        } else {
                            primary.push('X');
                            secondary.push('X');
                        }
                        index += 3;
                    } else {
                        primary.push('K');
                        secondary.push('K');
                        index += 2;
                    }
                } else if next == 'G' || next == 'K' || next == 'Q' {
                    primary.push('K');
                    secondary.push('K');
                    index += 2;
                } else if next == 'I' && (nextnext == 'E' || nextnext == 'O') {
                    // Italian.
                    primary.push('S');
                    secondary.push('X');
                    index += 2;
                } else if next == 'I' || next == 'E' || next == 'Y' {
                    primary.push('S');
                    secondary.push('S');
                    index += 2;
                } else {
                    primary.push('K');
                    secondary.push('K');
                    if next == ' ' && (nextnext == 'C' || nextnext == 'G' || nextnext == 'Q') {
                        index += 3;
                    } else {
                        index += 1;
                    }
                }
            }
            'D' => {
                if next == 'G' {
                    if nextnext == 'E' || nextnext == 'I' || nextnext == 'Y' {
                        primary.push('J');
                        secondary.push('J');
                        index += 3;
                    } else {
                        primary.push_str("TK");
                        secondary.push_str("TK");
                        index += 2;
                    }
                } else if next == 'T' || next == 'D' {
                    primary.push('T');
                    secondary.push('T');
                    index += 2;
                } else {
                    primary.push('T');
                    secondary.push('T');
                    index += 1;
                }
            }
            'F' => {
                if next == 'F' {
                    index += 1;
                }
                index += 1;
                primary.push('F');
                secondary.push('F');
            }
            'G' => {
                if next == 'H' {
                    if index > 0 && !dm_is_vowel(previous) {
                        primary.push('K');
                        secondary.push('K');
                        index += 2;
                    } else if index == 0 {
                        if nextnext == 'I' {
                            primary.push('J');
                            secondary.push('J');
                        } else {
                            primary.push('K');
                            secondary.push('K');
                        }
                        index += 2;
                    } else if (matches!(at(index - 2), 'B' | 'H' | 'D'))
                        || (matches!(at(index - 3), 'B' | 'H' | 'D'))
                        || (matches!(at(index - 4), 'B' | 'H'))
                    {
                        index += 2;
                    } else {
                        if index > 2 && previous == 'U' && matches!(at(index - 3), 'C' | 'G' | 'L' | 'R' | 'T')
                        {
                            primary.push('F');
                            secondary.push('F');
                        } else if index > 0 && previous != 'I' {
                            primary.push('K');
                            secondary.push('K');
                        }
                        index += 2;
                    }
                } else if next == 'N' {
                    if index == 1 && dm_is_vowel(at(0)) && !is_slavo_germanic {
                        primary.push_str("KN");
                        secondary.push('N');
                    } else if slice(index + 2, index + 4) != "EY"
                        && slice(index + 1, i64::MAX) != "Y"
                        && !is_slavo_germanic
                    {
                        primary.push('N');
                        secondary.push_str("KN");
                    } else {
                        primary.push_str("KN");
                        secondary.push_str("KN");
                    }
                    index += 2;
                } else if slice(index + 1, index + 3) == "LI" && !is_slavo_germanic {
                    // Tagliaro.
                    primary.push_str("KL");
                    secondary.push('L');
                    index += 2;
                } else if (index == 0 && dm_initial_g_for_kj(&slice(1, 3)))
                    || (slice(index + 1, index + 3) == "ER"
                        && previous != 'I'
                        && previous != 'E'
                        && !dm_initial_anger_exception(&slice(0, 6)))
                    || (next == 'Y' && !matches!(previous, 'E' | 'G' | 'I' | 'R'))
                {
                    // -ges-/-gep-/-gel- at start, or -ger-/-gy- (all sound K/J).
                    primary.push('K');
                    secondary.push('J');
                    index += 2;
                } else if next == 'E'
                    || next == 'I'
                    || next == 'Y'
                    || ((previous == 'A' || previous == 'O') && next == 'G' && nextnext == 'I')
                {
                    if slice(index + 1, index + 3) == "ET" || is_germanic {
                        primary.push('K');
                        secondary.push('K');
                    } else {
                        primary.push('J');
                        secondary.push(if slice(index + 1, index + 5) == "IER " {
                            'J'
                        } else {
                            'K'
                        });
                    }
                    index += 2;
                } else {
                    if next == 'G' {
                        index += 1;
                    }
                    index += 1;
                    primary.push('K');
                    secondary.push('K');
                }
            }
            'H' => {
                if dm_is_vowel(next) && (index == 0 || dm_is_vowel(previous)) {
                    primary.push('H');
                    secondary.push('H');
                    index += 1;
                }
                index += 1;
            }
            'J' => {
                if slice(index, index + 4) == "JOSE" || slice(0, 4) == "SAN " {
                    if slice(0, 4) == "SAN " || (index == 0 && at(index + 4) == ' ') {
                        primary.push('H');
                        secondary.push('H');
                    } else {
                        primary.push('J');
                        secondary.push('H');
                    }
                    index += 1;
                } else {
                    if index == 0 {
                        primary.push('J');
                        secondary.push('A');
                    } else if !is_slavo_germanic
                        && (next == 'A' || next == 'O')
                        && dm_is_vowel(previous)
                    {
                        primary.push('J');
                        secondary.push('H');
                    } else if index == last {
                        primary.push('J');
                    } else if previous != 'S'
                        && previous != 'K'
                        && previous != 'L'
                        && !matches!(next, 'L' | 'T' | 'K' | 'S' | 'N' | 'M' | 'B' | 'Z')
                    {
                        primary.push('J');
                        secondary.push('J');
                    } else if next == 'J' {
                        index += 1;
                    }
                    index += 1;
                }
            }
            'K' => {
                if next == 'K' {
                    index += 1;
                }
                primary.push('K');
                secondary.push('K');
                index += 1;
            }
            'L' => {
                if next == 'L' {
                    if (index == length - 3
                        && ((previous == 'A' && nextnext == 'E')
                            || (previous == 'I' && (nextnext == 'O' || nextnext == 'A'))))
                        || (previous == 'A'
                            && nextnext == 'E'
                            && (at(last) == 'A'
                                || at(last) == 'O'
                                || {
                                    let s = slice(last - 1, length);
                                    s.contains("AS") || s.contains("OS")
                                }))
                    {
                        primary.push('L');
                        index += 2;
                    } else {
                        index += 1;
                        primary.push('L');
                        secondary.push('L');
                        index += 1;
                    }
                } else {
                    primary.push('L');
                    secondary.push('L');
                    index += 1;
                }
            }
            'M' => {
                if next == 'M'
                    || (previous == 'U'
                        && next == 'B'
                        && (index + 1 == last || slice(index + 2, index + 4) == "ER"))
                {
                    index += 1;
                }
                index += 1;
                primary.push('M');
                secondary.push('M');
            }
            'N' => {
                if next == 'N' {
                    index += 1;
                }
                index += 1;
                primary.push('N');
                secondary.push('N');
            }
            'Ñ' => {
                index += 1;
                primary.push('N');
                secondary.push('N');
            }
            'P' => {
                if next == 'H' {
                    primary.push('F');
                    secondary.push('F');
                    index += 2;
                } else {
                    if next == 'P' || next == 'B' {
                        index += 1;
                    }
                    index += 1;
                    primary.push('P');
                    secondary.push('P');
                }
            }
            'Q' => {
                if next == 'Q' {
                    index += 1;
                }
                index += 1;
                primary.push('K');
                secondary.push('K');
            }
            'R' => {
                if index == last
                    && !is_slavo_germanic
                    && previous == 'E'
                    && at(index - 2) == 'I'
                    && at(index - 4) != 'M'
                    && at(index - 3) != 'E'
                    && at(index - 3) != 'A'
                {
                    secondary.push('R');
                } else {
                    primary.push('R');
                    secondary.push('R');
                }
                if next == 'R' {
                    index += 1;
                }
                index += 1;
            }
            'S' => {
                if next == 'L' && (previous == 'I' || previous == 'Y') {
                    index += 1;
                } else if index == 0 && slice(1, 5) == "UGAR" {
                    primary.push('X');
                    secondary.push('S');
                    index += 1;
                } else if next == 'H' {
                    let s = slice(index + 1, index + 5);
                    if s.contains("EIM") || s.contains("OEK") || s.contains("OLM") || s.contains("OLZ")
                    {
                        primary.push('S');
                        secondary.push('S');
                    } else {
                        primary.push('X');
                        secondary.push('X');
                    }
                    index += 2;
                } else if next == 'I' && (nextnext == 'O' || nextnext == 'A') {
                    if is_slavo_germanic {
                        primary.push('S');
                        secondary.push('S');
                    } else {
                        primary.push('S');
                        secondary.push('X');
                    }
                    index += 3;
                } else if next == 'Z'
                    || (index == 0 && (next == 'L' || next == 'M' || next == 'N' || next == 'W'))
                {
                    primary.push('S');
                    secondary.push('X');
                    if next == 'Z' {
                        index += 1;
                    }
                    index += 1;
                } else if next == 'C' {
                    if nextnext == 'H' {
                        let sv = slice(index + 3, index + 5);
                        if (sv.starts_with('E')
                            && matches!(sv.chars().nth(1), Some('D') | Some('M') | Some('N') | Some('R')))
                            || sv == "UY"
                            || sv == "OO"
                        {
                            if sv == "ER" || sv == "EN" {
                                primary.push('X');
                                secondary.push_str("SK");
                            } else {
                                primary.push_str("SK");
                                secondary.push_str("SK");
                            }
                            index += 3;
                        } else if index == 0 && !dm_is_vowel(at(3)) && at(3) != 'W' {
                            primary.push('X');
                            secondary.push('S');
                            index += 3;
                        } else {
                            primary.push('X');
                            secondary.push('X');
                            index += 3;
                        }
                    } else if nextnext == 'I' || nextnext == 'E' || nextnext == 'Y' {
                        primary.push('S');
                        secondary.push('S');
                        index += 3;
                    } else {
                        primary.push_str("SK");
                        secondary.push_str("SK");
                        index += 3;
                    }
                } else {
                    let sv = slice(index - 2, index);
                    if index == last && (sv == "AI" || sv == "OI") {
                        secondary.push('S');
                    } else {
                        primary.push('S');
                        secondary.push('S');
                    }
                    if next == 'S' {
                        index += 1;
                    }
                    index += 1;
                }
            }
            'T' => {
                if (next == 'I' && nextnext == 'O' && at(index + 3) == 'N')
                    || (next == 'I' && nextnext == 'A')
                    || (next == 'C' && nextnext == 'H')
                {
                    // -TION, -TIA, -TCH all sound X.
                    primary.push('X');
                    secondary.push('X');
                    index += 3;
                } else if next == 'H' || (next == 'T' && nextnext == 'H') {
                    if is_germanic
                        || ((nextnext == 'O' || nextnext == 'A') && at(index + 3) == 'M')
                    {
                        primary.push('T');
                        secondary.push('T');
                    } else {
                        primary.push('0');
                        secondary.push('T');
                    }
                    index += 2;
                } else {
                    if next == 'T' || next == 'D' {
                        index += 1;
                    }
                    index += 1;
                    primary.push('T');
                    secondary.push('T');
                }
            }
            'V' => {
                if next == 'V' {
                    index += 1;
                }
                primary.push('F');
                secondary.push('F');
                index += 1;
            }
            'W' => {
                if next == 'R' {
                    primary.push('R');
                    secondary.push('R');
                    index += 2;
                } else {
                    if index == 0 {
                        if dm_is_vowel(next) {
                            primary.push('A');
                            secondary.push('F');
                        } else if next == 'H' {
                            primary.push('A');
                            secondary.push('A');
                        }
                    }
                    if ((previous == 'E' || previous == 'O')
                        && next == 'S'
                        && nextnext == 'K'
                        && (at(index + 3) == 'I' || at(index + 3) == 'Y'))
                        || slice(0, 3) == "SCH"
                        || (index == last && dm_is_vowel(previous))
                    {
                        secondary.push('F');
                        index += 1;
                    } else if next == 'I'
                        && (nextnext == 'C' || nextnext == 'T')
                        && at(index + 3) == 'Z'
                    {
                        primary.push_str("TS");
                        secondary.push_str("FX");
                        index += 4;
                    } else {
                        index += 1;
                    }
                }
            }
            'X' => {
                if !(index == last
                    && previous == 'U'
                    && (at(index - 2) == 'A' || at(index - 2) == 'O'))
                {
                    primary.push_str("KS");
                    secondary.push_str("KS");
                }
                if next == 'C' || next == 'X' {
                    index += 1;
                }
                index += 1;
            }
            'Z' => {
                if next == 'H' {
                    primary.push('J');
                    secondary.push('J');
                    index += 2;
                } else {
                    if (next == 'Z' && (nextnext == 'A' || nextnext == 'I' || nextnext == 'O'))
                        || (is_slavo_germanic && index > 0 && previous != 'T')
                    {
                        primary.push('S');
                        secondary.push_str("TS");
                    } else {
                        primary.push('S');
                        secondary.push('S');
                    }
                    if next == 'Z' {
                        index += 1;
                    }
                    index += 1;
                }
            }
            _ => {
                index += 1;
            }
        }
    }

    (primary, secondary)
}

/// The primary Double Metaphone code -- the common blocking key.
pub fn double_metaphone_primary(s: &str) -> String {
    double_metaphone(s).0
}

/// The alternate (secondary) Double Metaphone code.
pub fn double_metaphone_alt(s: &str) -> String {
    double_metaphone(s).1
}

/// `/^CH(IA|EM|OR([^E])|YM|ARAC|ARIS)/` on the normalized string.
fn dm_initial_greek_ch(norm: &str, at: &dyn Fn(i64) -> char) -> bool {
    norm.starts_with("CHIA")
        || norm.starts_with("CHEM")
        || norm.starts_with("CHYM")
        || norm.starts_with("CHARAC")
        || norm.starts_with("CHARIS")
        || (norm.starts_with("CHOR") && at(4) != 'E')
}

/// `/ORCHES|ARCHIT|ORCHID/` .test on a slice.
fn dm_greek_ch(s: &str) -> bool {
    s.contains("ORCHES") || s.contains("ARCHIT") || s.contains("ORCHID")
}

/// `/Y[\s\S]|E[BILPRSY]|I[BELN]/` on a 2-char slice.
fn dm_initial_g_for_kj(s: &str) -> bool {
    let mut it = s.chars();
    let (a, b) = (it.next().unwrap_or('\0'), it.next().unwrap_or('\0'));
    a == 'Y'
        || (a == 'E' && matches!(b, 'B' | 'I' | 'L' | 'P' | 'R' | 'S' | 'Y'))
        || (a == 'I' && matches!(b, 'B' | 'E' | 'L' | 'N'))
}

/// `/^[DMR]ANGER/` on a slice.
fn dm_initial_anger_exception(s: &str) -> bool {
    let mut it = s.chars();
    matches!(it.next(), Some('D') | Some('M') | Some('R')) && s.len() >= 6 && &s[1..] == "ANGER"
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

    fn dm(s: &str) -> (String, String) {
        double_metaphone(s)
    }

    #[test]
    fn double_metaphone_reference_vectors() {
        // Canonical vectors from the `words/double-metaphone` reference suite.
        let cases: &[(&str, &str, &str)] = &[
            ("ptah", "PT", "PT"),
            ("ceasar", "SSR", "SSR"),
            ("ach", "AK", "AK"),
            ("chemical", "KMKL", "KMKL"),
            ("choral", "KRL", "KRL"),
            ("cabrillo", "KPRL", "KPR"),
            ("villa", "FL", "F"),
            ("crevalle", "KRFL", "KRF"),
            ("allegretto", "ALKRT", "AKRT"),
            ("allegros", "ALKRS", "AKRS"),
            ("sz", "S", "X"),
            ("th", "0", "T"),
            ("Tsjaikowski", "TSKSK", "TSKFSK"),
            ("Filipowicz", "FLPTS", "FLPFX"),
            ("zza", "S", "TS"),
            ("zzi", "S", "TS"),
            ("zzo", "S", "TS"),
        ];
        for (input, p, s) in cases {
            assert_eq!(dm(input), (p.to_string(), s.to_string()), "double_metaphone({input:?})");
        }
    }

    #[test]
    fn double_metaphone_edges() {
        assert_eq!(dm("gnarl").0.chars().next(), Some('N')); // initial GN -> N
        assert_eq!(dm("knack").0.chars().next(), Some('N')); // initial KN -> N
        assert_eq!(dm("HiCcUpS"), dm("hiccups")); // case-insensitive
        assert_eq!(dm("alexander"), dm("aleksander"));
        assert_eq!(dm(""), (String::new(), String::new()));
    }
}
