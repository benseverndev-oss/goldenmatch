//! Owned i18n-name kernels (pyo3-free): ASCII transliteration + Unicode
//! script detection. These are the reference implementations; the
//! Python/TS fallbacks must reproduce their bytes exactly (byte-parity
//! harness, `tests/parity/identifiers_corpus.jsonl`).
//!
//! Deliberately NOT implemented via `unicode-normalization` / NFD or
//! Python's `unicodedata.normalize` -- those depend on the runtime's bundled
//! Unicode version and could silently drift between Rust/Python/JS. Instead
//! both `name_transliterate` and `name_script` use an EXPLICIT, hand-curated
//! char map / codepoint-range table that is replicated byte-for-byte in the
//! Python fallback (`goldenflow/transforms/names.py`).

/// ASCII-fold a single non-ASCII char to its closest ASCII replacement.
/// `None` means "no mapping" -- the caller drops the character.
///
/// Map coverage (common Latin-script diacritics; documented, not
/// exhaustive -- any char not listed here is dropped by
/// [`name_transliterate`]):
/// - a/e/i/o/u with acute, grave, circumflex, diaeresis -> the base vowel
///   (all five vowels, both cases).
/// - a/o with tilde, a with ring -> the base vowel (the common precomposed
///   vowel-tilde/-ring codepoints; e/i/u-tilde and e/i/o-ring are rare
///   enough in real name data that they are out of scope for this map).
/// - n-tilde (ñ), c-cedilla (ç), y-acute (ý), y-diaeresis (ÿ) -> n, c, y.
/// - s/z/c/r/e with caron, c/z with acute (š ž ź č ć ř ě + upper) -> s z c
///   r e (one Latin base letter each).
/// - Ligatures/specials: ß -> ss, æ/Æ -> ae/AE, œ/Œ -> oe/OE, ø/Ø -> o/O,
///   đ/Đ -> d/D, ł/Ł -> l/L, þ/Þ -> th/Th, ð/Ð -> d/D.
fn transliterate_char(c: char) -> Option<&'static str> {
    Some(match c {
        // acute
        'á' => "a",
        'Á' => "A",
        'é' => "e",
        'É' => "E",
        'í' => "i",
        'Í' => "I",
        'ó' => "o",
        'Ó' => "O",
        'ú' => "u",
        'Ú' => "U",
        // grave
        'à' => "a",
        'À' => "A",
        'è' => "e",
        'È' => "E",
        'ì' => "i",
        'Ì' => "I",
        'ò' => "o",
        'Ò' => "O",
        'ù' => "u",
        'Ù' => "U",
        // circumflex
        'â' => "a",
        'Â' => "A",
        'ê' => "e",
        'Ê' => "E",
        'î' => "i",
        'Î' => "I",
        'ô' => "o",
        'Ô' => "O",
        'û' => "u",
        'Û' => "U",
        // diaeresis
        'ä' => "a",
        'Ä' => "A",
        'ë' => "e",
        'Ë' => "E",
        'ï' => "i",
        'Ï' => "I",
        'ö' => "o",
        'Ö' => "O",
        'ü' => "u",
        'Ü' => "U",
        // tilde (a, o -- the common precomposed vowel-tilde chars)
        'ã' => "a",
        'Ã' => "A",
        'õ' => "o",
        'Õ' => "O",
        // ring (a -- the common precomposed vowel-ring char)
        'å' => "a",
        'Å' => "A",
        // n-tilde / c-cedilla / y-acute / y-diaeresis
        'ñ' => "n",
        'Ñ' => "N",
        'ç' => "c",
        'Ç' => "C",
        'ý' => "y",
        'Ý' => "Y",
        'ÿ' => "y",
        'Ÿ' => "Y",
        // caron/acute consonants
        'š' => "s",
        'Š' => "S",
        'ž' => "z",
        'Ž' => "Z",
        'ź' => "z",
        'Ź' => "Z",
        'č' => "c",
        'Č' => "C",
        'ć' => "c",
        'Ć' => "C",
        'ř' => "r",
        'Ř' => "R",
        'ě' => "e",
        'Ě' => "E",
        // ligatures / specials
        'ß' => "ss",
        'æ' => "ae",
        'Æ' => "AE",
        'œ' => "oe",
        'Œ' => "OE",
        'ø' => "o",
        'Ø' => "O",
        'đ' => "d",
        'Đ' => "D",
        'ł' => "l",
        'Ł' => "L",
        'þ' => "th",
        'Þ' => "Th",
        'ð' => "d",
        'Ð' => "D",
        _ => return None,
    })
}

/// ASCII-fold `s`: ASCII chars pass through unchanged; a mapped non-ASCII
/// char emits its (possibly multi-char) ASCII replacement; an unmapped
/// non-ASCII char is dropped. Always returns a `String` (never `None`) --
/// there is no "invalid input" for a name string.
pub fn name_transliterate(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        if c.is_ascii() {
            out.push(c);
        } else if let Some(rep) = transliterate_char(c) {
            out.push_str(rep);
        }
        // else: unmapped non-ASCII -- drop.
    }
    out
}

/// Script labels, in tie-break priority order (highest count wins; an exact
/// count tie resolves to whichever label appears earliest in this list).
const SCRIPT_PRIORITY: [&str; 10] = [
    "Latin",
    "Cyrillic",
    "Greek",
    "Han",
    "Hiragana",
    "Katakana",
    "Hangul",
    "Arabic",
    "Hebrew",
    "Devanagari",
];

/// Classify a single char into one of the tracked scripts via explicit
/// Unicode codepoint ranges, or `None` if it falls outside all of them
/// (digits, ASCII punctuation/space, and any script not tracked here all
/// fall through to `None` -- the caller treats that as "Common").
fn classify_char(c: char) -> Option<&'static str> {
    match c {
        'A'..='Z' | 'a'..='z' | '\u{00C0}'..='\u{024F}' => Some("Latin"),
        '\u{0400}'..='\u{04FF}' => Some("Cyrillic"),
        '\u{0370}'..='\u{03FF}' => Some("Greek"),
        '\u{4E00}'..='\u{9FFF}' => Some("Han"),
        '\u{3040}'..='\u{309F}' => Some("Hiragana"),
        '\u{30A0}'..='\u{30FF}' => Some("Katakana"),
        '\u{AC00}'..='\u{D7A3}' => Some("Hangul"),
        '\u{0600}'..='\u{06FF}' => Some("Arabic"),
        '\u{0590}'..='\u{05FF}' => Some("Hebrew"),
        '\u{0900}'..='\u{097F}' => Some("Devanagari"),
        _ => None,
    }
}

/// Detect the dominant script in `s` by counting chars in each tracked
/// script's Unicode range. `Unknown` for an empty string; `Common` when no
/// tracked-script char is present (all ASCII digits/punct/space, or a
/// script this kernel doesn't track). Ties resolve via `SCRIPT_PRIORITY`.
pub fn name_script(s: &str) -> String {
    if s.is_empty() {
        return "Unknown".to_string();
    }
    let mut counts: [usize; 10] = [0; 10];
    for c in s.chars() {
        if let Some(label) = classify_char(c) {
            let idx = SCRIPT_PRIORITY.iter().position(|&l| l == label).unwrap();
            counts[idx] += 1;
        }
    }
    let (best_idx, &best_count) = counts
        .iter()
        .enumerate()
        .max_by_key(|&(idx, &count)| (count, std::cmp::Reverse(idx)))
        .unwrap();
    if best_count == 0 {
        return "Common".to_string();
    }
    SCRIPT_PRIORITY[best_idx].to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn transliterate_common_diacritics() {
        assert_eq!(name_transliterate("José"), "Jose");
        assert_eq!(name_transliterate("Müller"), "Muller");
        assert_eq!(name_transliterate("Straße"), "Strasse");
        assert_eq!(name_transliterate("Łódź"), "Lodz");
        assert_eq!(name_transliterate("Renée"), "Renee");
        assert_eq!(name_transliterate("Æsir"), "AEsir");
    }

    #[test]
    fn transliterate_passthrough_and_edge_cases() {
        assert_eq!(name_transliterate("Smith"), "Smith");
        assert_eq!(name_transliterate(""), "");
        // CJK char + emoji: both unmapped -> dropped.
        assert_eq!(name_transliterate("张\u{1F600}"), "");
    }

    #[test]
    fn script_detection() {
        assert_eq!(name_script("Smith"), "Latin");
        assert_eq!(name_script("José"), "Latin");
        assert_eq!(name_script("Иван"), "Cyrillic");
        assert_eq!(name_script("Ολγα"), "Greek");
        assert_eq!(name_script("张伟"), "Han");
        assert_eq!(name_script("محمد"), "Arabic");
        assert_eq!(name_script("123"), "Common");
        assert_eq!(name_script(""), "Unknown");
    }
}
