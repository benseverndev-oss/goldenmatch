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

// ---------------------------------------------------------------------------
// names-remainder kernels (Wave D). Each is the reference implementation; the
// Python (`goldenflow/transforms/names.py`) and TS (`transforms/names.ts`)
// fallbacks must reproduce these bytes exactly. Ported one-for-one from the
// existing pure-Python transforms (kernel = spec under reference-mode).
// ---------------------------------------------------------------------------

/// `\w` in the Python regexes = `[A-Za-z0-9_]` (ASCII; the transforms operate
/// on Latin-script name data).
fn is_word_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// Leading personal titles, in the Python alternation order. Mirrors the
/// `^(Mr\.?|...)\s+` regex in `names.py::strip_titles`. `Sr` before `Sra`
/// is fine: the `Sr` branch only matches when an optional dot + required
/// whitespace follows, so "Sra ..." correctly falls through to `Sra`.
const TITLES: [&str; 9] = ["Mr", "Mrs", "Ms", "Miss", "Dr", "Prof", "Rev", "Sr", "Sra"];

/// If `s` begins (case-insensitively) with `title` + optional `.` + one-or-more
/// whitespace, return the remainder after that run; else `None`. Replicates the
/// leading-title regex match; whitespace uses Unicode `char::is_whitespace`
/// (Python `\s` under `re.UNICODE` + polars `strip_chars`).
fn strip_leading_title(s: &str) -> Option<&str> {
    let lower = s.to_ascii_lowercase();
    for title in TITLES {
        let t = title.to_ascii_lowercase();
        if lower.starts_with(&t) {
            // ASCII-lowercasing preserves byte offsets, so `title.len()` indexes
            // the original safely (the prefix is ASCII).
            let after_title = &s[title.len()..];
            let after_dot = after_title.strip_prefix('.').unwrap_or(after_title);
            let after_ws = after_dot.trim_start();
            if after_ws.len() < after_dot.len() {
                // at least one whitespace consumed -> the `\s+` matched.
                return Some(after_ws);
            }
        }
    }
    None
}

/// Strip a leading personal title (Mr/Mrs/Ms/Miss/Dr/Prof/Rev/Sr/Sra) then
/// trim. Byte-identical to `names.py::strip_titles` (regex replace +
/// `strip_chars`): a no-title input is still trimmed.
pub fn strip_titles(s: &str) -> String {
    strip_leading_title(s).unwrap_or(s).trim().to_string()
}

/// Trailing professional suffixes as (suffix, allows-optional-trailing-dot),
/// in the Python alternation order. Mirrors the
/// `\s+(Jr\.?|Sr\.?|II|III|IV|MD|PhD|PharmD|DDS|DVM|Esq\.?|CPA|RN|DO)$` regex.
const SUFFIXES: [(&str, bool); 14] = [
    ("Jr", true),
    ("Sr", true),
    ("II", false),
    ("III", false),
    ("IV", false),
    ("MD", false),
    ("PhD", false),
    ("PharmD", false),
    ("DDS", false),
    ("DVM", false),
    ("Esq", true),
    ("CPA", false),
    ("RN", false),
    ("DO", false),
];

/// If `s` ends (case-insensitively) with `\s+ suffix (\.)? $`, return everything
/// before the trailing whitespace run; else `None`.
fn strip_trailing_suffix(s: &str) -> Option<&str> {
    let lower = s.to_ascii_lowercase();
    for (suf, allow_dot) in SUFFIXES {
        let suf_l = suf.to_ascii_lowercase();
        // `\.?$` greedily takes the trailing dot when present.
        let core = if allow_dot {
            lower.strip_suffix('.').unwrap_or(&lower)
        } else {
            &lower
        };
        if let Some(before) = core.strip_suffix(&suf_l) {
            // the `\s+` requires whitespace immediately before the suffix.
            if before.ends_with(char::is_whitespace) {
                // ASCII-lowercasing preserves byte offsets; `before.len()`
                // indexes the original (`before` is all-ASCII up to here only
                // where it matters -- it's a prefix, so the offset is valid).
                return Some(&s[..before.len()]);
            }
        }
    }
    None
}

/// Strip a trailing professional suffix (Jr/Sr/II/.../DO) then trim.
/// Byte-identical to `names.py::strip_suffixes`.
pub fn strip_suffixes(s: &str) -> String {
    strip_trailing_suffix(s).unwrap_or(s).trim().to_string()
}

/// ASCII-semantics `str.title()`: the first alphabetic char of each word is
/// upper-cased, the rest lower-cased; non-alphabetic chars pass through and
/// reset the word boundary. Matches Python `str.title()` on ASCII input; the
/// non-ASCII behavior is bounded by the Unicode `to_uppercase`/`to_lowercase`
/// case maps (documented boundary -- reference-mode resolves in Rust's favor).
pub(crate) fn ascii_title(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut prev_alpha = false;
    for c in s.chars() {
        if c.is_alphabetic() {
            if prev_alpha {
                out.extend(c.to_lowercase());
            } else {
                out.extend(c.to_uppercase());
            }
            prev_alpha = true;
        } else {
            out.push(c);
            prev_alpha = false;
        }
    }
    out
}

/// Uppercase the word-char immediately after a word-boundary `p0 p1` prefix
/// (`\bMc(\w)` / `\bO'(\w)` in `names.py`). Left-to-right, non-overlapping.
fn fixup_prefix(s: &str, p0: char, p1: char) -> String {
    let chars: Vec<char> = s.chars().collect();
    let n = chars.len();
    let mut out: Vec<char> = Vec::with_capacity(n);
    let mut i = 0;
    while i < n {
        let at_boundary = out.last().is_none_or(|&p| !is_word_char(p));
        if at_boundary
            && i + 2 < n
            && chars[i] == p0
            && chars[i + 1] == p1
            && is_word_char(chars[i + 2])
        {
            out.push(p0);
            out.push(p1);
            out.extend(chars[i + 2].to_uppercase());
            i += 3;
        } else {
            out.push(chars[i]);
            i += 1;
        }
    }
    out.into_iter().collect()
}

/// Proper-case a name: `str.title()` then the `Mc*`/`O'*` capitalization
/// fixups. Byte-identical to `names.py::name_proper` (title -> Mc sub -> O' sub)
/// on ASCII input.
pub fn name_proper(s: &str) -> String {
    let titled = ascii_title(s);
    let mc = fixup_prefix(&titled, 'M', 'c');
    fixup_prefix(&mc, 'O', '\'')
}

/// Map a common nickname (looked up by trimmed-lowercased key) to its formal
/// first name, or `None` if not a known nickname. Mirrors the `_NICKNAMES`
/// dict in `names.py` byte-for-byte; the dict is in-crate DATA (like the
/// transliterate map) so every surface replicates the same table.
fn nickname_lookup(key: &str) -> Option<&'static str> {
    Some(match key {
        "bob" | "rob" | "robby" | "robbie" | "bobby" => "Robert",
        "bill" | "billy" | "will" | "willy" => "William",
        "jim" | "jimmy" | "jamie" => "James",
        "mike" | "mikey" | "mick" => "Michael",
        "dick" | "rick" | "rich" | "ricky" => "Richard",
        "tom" | "tommy" => "Thomas",
        "joe" | "joey" => "Joseph",
        "jack" | "johnny" => "John",
        "jon" => "Jonathan",
        "dave" | "davy" => "David",
        "steve" | "stevie" => "Steven",
        "dan" | "danny" => "Daniel",
        "pat" => "Patrick",
        "patty" | "patsy" => "Patricia",
        "chris" | "kit" => "Christopher",
        "tony" => "Anthony",
        "ed" | "eddie" | "ted" | "teddy" => "Edward",
        "al" | "bert" => "Albert",
        "charlie" | "chuck" => "Charles",
        "sam" | "sammy" => "Samuel",
        "ben" | "benny" => "Benjamin",
        "matt" => "Matthew",
        "andy" | "drew" => "Andrew",
        "nick" => "Nicholas",
        "alex" => "Alexander",
        "liz" | "beth" | "betty" => "Elizabeth",
        "kate" | "kathy" | "katie" => "Katherine",
        "sue" | "susie" => "Susan",
        "meg" | "maggie" | "peggy" => "Margaret",
        "jenny" | "jen" => "Jennifer",
        "debbie" | "deb" => "Deborah",
        "barb" => "Barbara",
        "cindy" => "Cynthia",
        "sandy" => "Sandra",
        _ => return None,
    })
}

/// Standardize a nickname to its formal first name; unknown names pass through
/// UNCHANGED (the original, not the trimmed lookup key). Byte-identical to
/// `names.py::nickname_standardize` (`_NICKNAMES.get(val.strip().lower(), val)`).
pub fn nickname_standardize(s: &str) -> String {
    match nickname_lookup(&s.trim().to_ascii_lowercase()) {
        Some(canon) => canon.to_string(),
        None => s.to_string(),
    }
}

/// True if `s` contains a middle-initial pattern `\b[A-Z]\.\s` (a word-boundary
/// uppercase letter, a dot, then whitespace). The owned kernel behind
/// `names.py::initial_expand`'s flag detection (the value output is the input
/// unchanged; only the flag is computed here).
pub fn has_initial(s: &str) -> bool {
    let chars: Vec<char> = s.chars().collect();
    let n = chars.len();
    for i in 0..n {
        if chars[i].is_ascii_uppercase() {
            let at_boundary = i == 0 || !is_word_char(chars[i - 1]);
            if at_boundary && i + 2 < n && chars[i + 1] == '.' && chars[i + 2].is_whitespace() {
                return true;
            }
        }
    }
    false
}

/// Split `"First Last"` into `(first, last)` on the LAST space (after trimming).
/// No space -> `(whole, "")`. Byte-identical to `names.py::split_name`
/// (`val.strip().rsplit(" ", 1)`). The null-row case (`-> (None, None)`) is
/// handled by the caller/marshaling layer, not this kernel.
pub fn split_name(s: &str) -> (String, String) {
    let t = s.trim();
    match t.rsplit_once(' ') {
        Some((first, last)) => (first.to_string(), last.to_string()),
        None => (t.to_string(), String::new()),
    }
}

/// Split `"Last, First"` into `(first, last)` on the FIRST comma; each part is
/// trimmed. No comma -> `(trimmed-whole, "")`. Byte-identical to
/// `names.py::split_name_reverse` (`val.split(",", 1)`).
pub fn split_name_reverse(s: &str) -> (String, String) {
    match s.split_once(',') {
        Some((last, first)) => (first.trim().to_string(), last.trim().to_string()),
        None => (s.trim().to_string(), String::new()),
    }
}

/// Merge `(first, last)` into a full name: join the parts that are present and
/// non-blank (after trimming) with a single space, keeping each part's ORIGINAL
/// (unstripped) text; `None` if both are absent/blank. Byte-identical to
/// `names.py::merge_name`.
pub fn merge_name(first: Option<&str>, last: Option<&str>) -> Option<String> {
    let parts: Vec<&str> = [first, last]
        .into_iter()
        .flatten()
        .filter(|p| !p.trim().is_empty())
        .collect();
    if parts.is_empty() {
        None
    } else {
        Some(parts.join(" "))
    }
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

    #[test]
    fn strip_titles_cases() {
        assert_eq!(strip_titles("Dr. Smith"), "Smith");
        assert_eq!(strip_titles("Mr Smith"), "Smith");
        assert_eq!(strip_titles("Mrs. Jane Doe"), "Jane Doe");
        assert_eq!(strip_titles("Prof. Alan Turing"), "Alan Turing");
        assert_eq!(strip_titles("Sra Garcia"), "Garcia");
        assert_eq!(strip_titles("Miss Ellie"), "Ellie");
        // no title -> still trimmed
        assert_eq!(strip_titles("  John Smith  "), "John Smith");
        // "Missy" is not the title "Miss" (no dot/space boundary after)
        assert_eq!(strip_titles("Missy"), "Missy");
        // multiple spaces after the title are all consumed
        assert_eq!(strip_titles("Dr.   Smith"), "Smith");
    }

    #[test]
    fn strip_suffixes_cases() {
        assert_eq!(strip_suffixes("John Smith Jr"), "John Smith");
        assert_eq!(strip_suffixes("John Smith Jr."), "John Smith");
        assert_eq!(strip_suffixes("Jane Doe MD"), "Jane Doe");
        assert_eq!(strip_suffixes("Bob III"), "Bob");
        assert_eq!(strip_suffixes("Bob II"), "Bob");
        assert_eq!(strip_suffixes("Alice Esq."), "Alice");
        assert_eq!(strip_suffixes("Sam RN"), "Sam");
        // no suffix -> unchanged (but trimmed)
        assert_eq!(strip_suffixes("Robert"), "Robert");
        // "DODO" does not end in the standalone suffix "DO" (no ws before)
        assert_eq!(strip_suffixes("John DODO"), "John DODO");
    }

    #[test]
    fn name_proper_cases() {
        assert_eq!(name_proper("john smith"), "John Smith");
        assert_eq!(name_proper("JOHN SMITH"), "John Smith");
        assert_eq!(name_proper("mcdonald"), "McDonald");
        assert_eq!(name_proper("old mcdonald"), "Old McDonald");
        assert_eq!(name_proper("o'brien"), "O'Brien");
        assert_eq!(name_proper("d'angelo"), "D'Angelo");
        // "Mac" is distinct from "Mc" -- not fixed up
        assert_eq!(name_proper("macdonald"), "Macdonald");
    }

    #[test]
    fn nickname_standardize_cases() {
        assert_eq!(nickname_standardize("Bob"), "Robert");
        assert_eq!(nickname_standardize("bob"), "Robert");
        assert_eq!(nickname_standardize("  bob  "), "Robert");
        assert_eq!(nickname_standardize("JIM"), "James");
        assert_eq!(nickname_standardize("patty"), "Patricia");
        assert_eq!(nickname_standardize("pat"), "Patrick");
        // unknown -> original, unchanged (NOT the trimmed key)
        assert_eq!(nickname_standardize("Xavier"), "Xavier");
        assert_eq!(nickname_standardize("  Zed  "), "  Zed  ");
    }

    #[test]
    fn has_initial_cases() {
        assert!(has_initial("John Q. Public"));
        assert!(has_initial("J. Smith"));
        assert!(!has_initial("John Smith"));
        assert!(!has_initial("J.Smith")); // no whitespace after the dot
        assert!(!has_initial(""));
    }

    #[test]
    fn split_name_cases() {
        assert_eq!(split_name("John Smith"), ("John".into(), "Smith".into()));
        assert_eq!(
            split_name("John Michael Smith"),
            ("John Michael".into(), "Smith".into())
        );
        assert_eq!(split_name("Madonna"), ("Madonna".into(), "".into()));
        // rsplit on the LAST single space; interior double-space is kept
        assert_eq!(split_name("  Jane  Doe  "), ("Jane ".into(), "Doe".into()));
    }

    #[test]
    fn split_name_reverse_cases() {
        assert_eq!(
            split_name_reverse("Smith, John"),
            ("John".into(), "Smith".into())
        );
        assert_eq!(
            split_name_reverse("Smith,John"),
            ("John".into(), "Smith".into())
        );
        assert_eq!(
            split_name_reverse("Smith, John, Jr"),
            ("John, Jr".into(), "Smith".into())
        );
        assert_eq!(split_name_reverse("Madonna"), ("Madonna".into(), "".into()));
    }

    #[test]
    fn merge_name_cases() {
        assert_eq!(merge_name(Some("John"), Some("Smith")).as_deref(), Some("John Smith"));
        assert_eq!(merge_name(Some("John"), None).as_deref(), Some("John"));
        assert_eq!(merge_name(Some("John"), Some("")).as_deref(), Some("John"));
        assert_eq!(merge_name(Some(""), Some("")), None);
        assert_eq!(merge_name(None, None), None);
        // parts keep their ORIGINAL (unstripped) text when joined
        assert_eq!(
            merge_name(Some("  John  "), Some("Smith")).as_deref(),
            Some("  John   Smith")
        );
    }
}
