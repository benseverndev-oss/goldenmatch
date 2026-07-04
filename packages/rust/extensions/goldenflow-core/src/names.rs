//! Owned i18n-name kernels (pyo3-free): ASCII transliteration (Unicode
//! script detection follows in a later kernel). These are the reference
//! implementations; the Python/TS fallbacks must reproduce their bytes
//! exactly (byte-parity harness, `tests/parity/identifiers_corpus.jsonl`).
//!
//! Deliberately NOT implemented via `unicode-normalization` / NFD or
//! Python's `unicodedata.normalize` -- those depend on the runtime's bundled
//! Unicode version and could silently drift between Rust/Python/JS. Instead
//! `name_transliterate` uses an EXPLICIT, hand-curated char map that is
//! replicated byte-for-byte in the Python fallback
//! (`goldenflow/transforms/names.py`).

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
}
