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
    collapse_whitespace_into(s, &mut out);
    out
}

/// Streaming variant of [`collapse_whitespace`]: append the transformed bytes to
/// `out` (does NOT clear it). Byte-identical to `collapse_whitespace`; used by the
/// Arrow-columnar apply path (`columnar::map_str_columnar`) to avoid a per-element
/// `String` allocation.
pub fn collapse_whitespace_into(s: &str, out: &mut String) {
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
}

// ---------------------------------------------------------------------------
// Wave D text-1: mechanical / ASCII-bound kernels. Each is the reference
// implementation; the Python (`goldenflow/transforms/text.py`) and TS
// (`transforms/text.ts`) fallbacks reproduce these bytes exactly. Ported
// one-for-one from the polars transforms; NO regex dep (JS/Py/Rust regex
// engines differ on `\s`/`\d`/greedy). `char::is_whitespace` == polars `\s`
// (proven in tests/text_golden.rs), so the whitespace-based kernels are exact;
// `\d` is bounded to ASCII digits (documented boundary, reference-mode).
// ---------------------------------------------------------------------------

/// Replace smart/curly quotes with straight ASCII quotes. Byte-identical to the
/// chained `str.replace_all` in `text.py::normalize_quotes` (left/right double
/// + double-prime -> `"`; left/right single + prime -> `'`).
pub fn normalize_quotes(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    normalize_quotes_into(s, &mut out);
    out
}

/// Streaming [`normalize_quotes`] (appends to `out`) for the columnar apply path.
pub fn normalize_quotes_into(s: &str, out: &mut String) {
    for c in s.chars() {
        match c {
            '\u{201c}' | '\u{201d}' | '\u{2033}' => out.push('"'),
            '\u{2018}' | '\u{2019}' | '\u{2032}' => out.push('\''),
            _ => out.push(c),
        }
    }
}

/// Normalize `\r\n` and lone `\r` to `\n`. Byte-identical to
/// `text.py::normalize_line_endings` (replace `\r\n` first, then any `\r`).
pub fn normalize_line_endings(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    normalize_line_endings_into(s, &mut out);
    out
}

/// Streaming [`normalize_line_endings`] (appends to `out`) for the columnar path.
pub fn normalize_line_endings_into(s: &str, out: &mut String) {
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\r' {
            if chars.peek() == Some(&'\n') {
                chars.next();
            }
            out.push('\n');
        } else {
            out.push(c);
        }
    }
}

/// Truncate to the first `n` characters (polars `str.slice(0, n)` is
/// character-based). Byte-identical to `text.py::truncate`.
pub fn truncate(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Left-pad to `width` characters with `pad` (polars `str.pad_start`); a string
/// already at/over `width` is unchanged. Byte-identical to `text.py::pad_left`.
pub fn pad_left(s: &str, width: usize, pad: char) -> String {
    let mut out = String::with_capacity(width.max(s.len()));
    pad_left_into(s, width, pad, &mut out);
    out
}

/// Streaming [`pad_left`] (appends to `out`) for the columnar path.
pub fn pad_left_into(s: &str, width: usize, pad: char, out: &mut String) {
    let len = s.chars().count();
    if len < width {
        for _ in 0..(width - len) {
            out.push(pad);
        }
    }
    out.push_str(s);
}

/// Right-pad to `width` characters with `pad` (polars `str.pad_end`); a string
/// already at/over `width` is unchanged. Byte-identical to `text.py::pad_right`.
pub fn pad_right(s: &str, width: usize, pad: char) -> String {
    let mut out = String::with_capacity(width.max(s.len()));
    pad_right_into(s, width, pad, &mut out);
    out
}

/// Streaming [`pad_right`] (appends to `out`) for the columnar path.
pub fn pad_right_into(s: &str, width: usize, pad: char, out: &mut String) {
    let len = s.chars().count();
    out.push_str(s);
    if len < width {
        for _ in 0..(width - len) {
            out.push(pad);
        }
    }
}

/// Strip HTML tags: remove each `<...>` span with at least one char between the
/// angle brackets (regex `<[^>]+>`, minimal to the first `>`). `<>` and an
/// unclosed `<` are left intact. Byte-identical to `text.py::remove_html_tags`.
pub fn remove_html_tags(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    remove_html_tags_into(s, &mut out);
    out
}

/// Streaming [`remove_html_tags`] (appends to `out`) for the columnar path.
pub fn remove_html_tags_into(s: &str, out: &mut String) {
    let chars: Vec<char> = s.chars().collect();
    let n = chars.len();
    let mut i = 0;
    while i < n {
        if chars[i] == '<' {
            let mut j = i + 1;
            while j < n && chars[j] != '>' {
                j += 1;
            }
            // matched `<[^>]+>`: at least one non-`>` char (j > i+1) then a `>`.
            if j < n && j > i + 1 {
                i = j + 1;
                continue;
            }
        }
        out.push(chars[i]);
        i += 1;
    }
}

/// `"http://"` (7 chars) or `"https://"` (8 chars) at `chars[i..]`; returns the
/// index just past the `://`. `https` is tried first (regex `s?` is greedy).
fn match_url_scheme(chars: &[char], i: usize) -> Option<usize> {
    const HTTP: [char; 7] = ['h', 't', 't', 'p', ':', '/', '/'];
    const HTTPS: [char; 8] = ['h', 't', 't', 'p', 's', ':', '/', '/'];
    if chars[i..].starts_with(&HTTPS) {
        Some(i + HTTPS.len())
    } else if chars[i..].starts_with(&HTTP) {
        Some(i + HTTP.len())
    } else {
        None
    }
}

/// Strip URLs: remove each `https?://` followed by one-or-more non-whitespace
/// chars (regex `https?://\S+`). `\S` = non-`is_whitespace` (== polars `\s`
/// complement). Byte-identical to `text.py::remove_urls`.
pub fn remove_urls(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    remove_urls_into(s, &mut out);
    out
}

/// Streaming [`remove_urls`] (appends to `out`) for the columnar path.
pub fn remove_urls_into(s: &str, out: &mut String) {
    let chars: Vec<char> = s.chars().collect();
    let n = chars.len();
    let mut i = 0;
    while i < n {
        if let Some(after) = match_url_scheme(&chars, i) {
            let mut j = after;
            while j < n && !chars[j].is_whitespace() {
                j += 1;
            }
            if j > after {
                // scheme + `\S+` (>=1 non-ws char) -> drop the whole URL.
                i = j;
                continue;
            }
        }
        out.push(chars[i]);
        i += 1;
    }
}

/// Remove ASCII digit characters. The old polars `\d` was Unicode-aware; this
/// kernel is bounded to ASCII `0-9` (documented reference-mode boundary --
/// exotic Unicode digits are out of the bounded set). Byte-identical to
/// `text.py::remove_digits`.
pub fn remove_digits(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    remove_digits_into(s, &mut out);
    out
}

/// Streaming [`remove_digits`] (appends to `out`) for the columnar path.
pub fn remove_digits_into(s: &str, out: &mut String) {
    out.extend(s.chars().filter(|c| !c.is_ascii_digit()));
}

/// Remove punctuation: keep ASCII alphanumerics and whitespace, drop everything
/// else (regex `[^a-zA-Z0-9\s]` -> ""). `\s` == `is_whitespace`; non-ASCII
/// letters are dropped (as in the old polars behavior). Byte-identical to
/// `text.py::remove_punctuation`.
pub fn remove_punctuation(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    remove_punctuation_into(s, &mut out);
    out
}

/// Streaming [`remove_punctuation`] (appends to `out`) for the columnar path.
pub fn remove_punctuation_into(s: &str, out: &mut String) {
    out.extend(
        s.chars()
            .filter(|c| c.is_ascii_alphanumeric() || c.is_whitespace()),
    );
}

/// Extract all number runs (regex `\d+\.?\d*`: one-or-more digits, an optional
/// dot, zero-or-more digits) joined by single spaces. ASCII digits only
/// (documented boundary). Byte-identical to `text.py::extract_numbers`.
pub fn extract_numbers(s: &str) -> String {
    let chars: Vec<char> = s.chars().collect();
    let n = chars.len();
    let mut nums: Vec<String> = Vec::new();
    let mut i = 0;
    while i < n {
        if chars[i].is_ascii_digit() {
            let start = i;
            while i < n && chars[i].is_ascii_digit() {
                i += 1; // \d+
            }
            if i < n && chars[i] == '.' {
                i += 1; // greedy \.?
                while i < n && chars[i].is_ascii_digit() {
                    i += 1; // \d*
                }
            }
            nums.push(chars[start..i].iter().collect());
        } else {
            i += 1;
        }
    }
    nums.join(" ")
}

/// True if `c` is in the emoji codepoint set (the exact ranges of the Python
/// `_EMOJI_PATTERN`). Explicit ranges -> portable across surfaces, NO
/// Unicode-DB dependency (mirrors the `name_script` range approach).
///
/// The source pattern's ranges OVERLAP (the wide `0x24C2..=0x1F251` "enclosed
/// characters" range subsumes the later dingbats / misc-symbols / flags /
/// variation-selector arms), so several arms are `unreachable` -- harmless
/// since the union is identical, but a hard error under `-D warnings`. We keep
/// the arms 1:1 with the Python `_EMOJI_PATTERN` (readable, auditable against
/// the source) and allow the lint rather than silently pruning them.
#[allow(unreachable_patterns)]
fn is_emoji(c: char) -> bool {
    matches!(c as u32,
        0x1F600..=0x1F64F   // emoticons
        | 0x1F300..=0x1F5FF // symbols & pictographs
        | 0x1F680..=0x1F6FF // transport & map
        | 0x1F1E0..=0x1F1FF // flags
        | 0x2702..=0x27B0   // dingbats
        | 0x24C2..=0x1F251  // enclosed characters (wide range, per the source)
        | 0x1F900..=0x1F9FF // supplemental symbols
        | 0x1FA00..=0x1FA6F // chess symbols
        | 0x1FA70..=0x1FAFF // symbols extended-A
        | 0x2600..=0x26FF   // misc symbols
        | 0x200D            // zero-width joiner
        | 0xFE0F            // variation selector
    )
}

/// Remove emoji characters (regex `[<emoji ranges>]+` -> ""). Removing each
/// matching char is equivalent to removing maximal runs. Byte-identical to
/// `text.py::remove_emojis`.
pub fn remove_emojis(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    remove_emojis_into(s, &mut out);
    out
}

/// Streaming [`remove_emojis`] (appends to `out`) for the columnar path.
pub fn remove_emojis_into(s: &str, out: &mut String) {
    out.extend(s.chars().filter(|c| !is_emoji(*c)));
}

// ---------------------------------------------------------------------------
// Wave D text-2: Unicode-heavy kernels. Casing uses Rust std (the same fn
// polars already calls); title_case reuses the ASCII-title helper; mojibake is
// a portable byte round-trip; normalize_unicode uses an EXPLICIT generated
// decompose map (NOT a runtime Unicode DB) for guaranteed cross-surface parity.
// ---------------------------------------------------------------------------

/// Lowercase via Rust std (`str::to_lowercase`) -- the reference. Python
/// `str.lower` / JS `toLowerCase` agree on Latin/ASCII; exotic casing (Greek
/// final sigma, Turkish dotted-I) is the documented reference-mode boundary.
pub fn lowercase(s: &str) -> String {
    s.to_lowercase()
}

/// Uppercase via Rust std (`str::to_uppercase`). eszett -> "SS" agrees across
/// Rust/Python/JS.
pub fn uppercase(s: &str) -> String {
    s.to_uppercase()
}

/// ASCII title-case (first alphabetic char of each word upper, rest lower;
/// non-alpha resets the word). Reuses `names::ascii_title` -- byte-identical to
/// the `name_proper` ASCII path. Bounded to ASCII semantics (polars
/// `to_titlecase` is Unicode-aware; documented boundary).
pub fn title_case(s: &str) -> String {
    crate::names::ascii_title(s)
}

/// Fix common UTF-8/Latin-1 mojibake: re-encode the string as Latin-1 (each
/// char must be <= U+00FF) then decode the bytes as UTF-8. On any failure
/// (a char > U+00FF, or the bytes aren't valid UTF-8) return the original.
/// Byte-identical to `text.py::fix_mojibake` (`val.encode("latin-1").decode(
/// "utf-8")`). Deterministic, no Unicode DB.
pub fn fix_mojibake(s: &str) -> String {
    let mut bytes = Vec::with_capacity(s.len());
    for c in s.chars() {
        let cp = c as u32;
        if cp > 0xFF {
            return s.to_string(); // not Latin-1 encodable
        }
        bytes.push(cp as u8);
    }
    match std::str::from_utf8(&bytes) {
        Ok(decoded) => decoded.to_string(),
        Err(_) => s.to_string(),
    }
}

/// The explicit NFKD-decompose+strip-combining replacement for a non-ASCII
/// char, or `None` to pass the char through unchanged. GENERATED by
/// `scripts/gen_normalize_unicode_map.py` from Python `unicodedata` over
/// U+00C0-U+017F + U+1E00-U+1EFF -- the SAME table is replicated to the Python
/// and TS fallbacks, so all surfaces are byte-identical regardless of their
/// bundled Unicode DB. Non-decomposing chars (eszett, ae/oe ligatures,
/// o-slash, l-stroke, ...) are deliberately absent (NFKD leaves them).
fn normalize_char(c: char) -> Option<&'static str> {
    Some(match c {
        '\u{C0}' => "A",
        '\u{C1}' => "A",
        '\u{C2}' => "A",
        '\u{C3}' => "A",
        '\u{C4}' => "A",
        '\u{C5}' => "A",
        '\u{C7}' => "C",
        '\u{C8}' => "E",
        '\u{C9}' => "E",
        '\u{CA}' => "E",
        '\u{CB}' => "E",
        '\u{CC}' => "I",
        '\u{CD}' => "I",
        '\u{CE}' => "I",
        '\u{CF}' => "I",
        '\u{D1}' => "N",
        '\u{D2}' => "O",
        '\u{D3}' => "O",
        '\u{D4}' => "O",
        '\u{D5}' => "O",
        '\u{D6}' => "O",
        '\u{D9}' => "U",
        '\u{DA}' => "U",
        '\u{DB}' => "U",
        '\u{DC}' => "U",
        '\u{DD}' => "Y",
        '\u{E0}' => "a",
        '\u{E1}' => "a",
        '\u{E2}' => "a",
        '\u{E3}' => "a",
        '\u{E4}' => "a",
        '\u{E5}' => "a",
        '\u{E7}' => "c",
        '\u{E8}' => "e",
        '\u{E9}' => "e",
        '\u{EA}' => "e",
        '\u{EB}' => "e",
        '\u{EC}' => "i",
        '\u{ED}' => "i",
        '\u{EE}' => "i",
        '\u{EF}' => "i",
        '\u{F1}' => "n",
        '\u{F2}' => "o",
        '\u{F3}' => "o",
        '\u{F4}' => "o",
        '\u{F5}' => "o",
        '\u{F6}' => "o",
        '\u{F9}' => "u",
        '\u{FA}' => "u",
        '\u{FB}' => "u",
        '\u{FC}' => "u",
        '\u{FD}' => "y",
        '\u{FF}' => "y",
        '\u{100}' => "A",
        '\u{101}' => "a",
        '\u{102}' => "A",
        '\u{103}' => "a",
        '\u{104}' => "A",
        '\u{105}' => "a",
        '\u{106}' => "C",
        '\u{107}' => "c",
        '\u{108}' => "C",
        '\u{109}' => "c",
        '\u{10A}' => "C",
        '\u{10B}' => "c",
        '\u{10C}' => "C",
        '\u{10D}' => "c",
        '\u{10E}' => "D",
        '\u{10F}' => "d",
        '\u{112}' => "E",
        '\u{113}' => "e",
        '\u{114}' => "E",
        '\u{115}' => "e",
        '\u{116}' => "E",
        '\u{117}' => "e",
        '\u{118}' => "E",
        '\u{119}' => "e",
        '\u{11A}' => "E",
        '\u{11B}' => "e",
        '\u{11C}' => "G",
        '\u{11D}' => "g",
        '\u{11E}' => "G",
        '\u{11F}' => "g",
        '\u{120}' => "G",
        '\u{121}' => "g",
        '\u{122}' => "G",
        '\u{123}' => "g",
        '\u{124}' => "H",
        '\u{125}' => "h",
        '\u{128}' => "I",
        '\u{129}' => "i",
        '\u{12A}' => "I",
        '\u{12B}' => "i",
        '\u{12C}' => "I",
        '\u{12D}' => "i",
        '\u{12E}' => "I",
        '\u{12F}' => "i",
        '\u{130}' => "I",
        '\u{132}' => "IJ",
        '\u{133}' => "ij",
        '\u{134}' => "J",
        '\u{135}' => "j",
        '\u{136}' => "K",
        '\u{137}' => "k",
        '\u{139}' => "L",
        '\u{13A}' => "l",
        '\u{13B}' => "L",
        '\u{13C}' => "l",
        '\u{13D}' => "L",
        '\u{13E}' => "l",
        '\u{13F}' => "L\u{B7}",
        '\u{140}' => "l\u{B7}",
        '\u{143}' => "N",
        '\u{144}' => "n",
        '\u{145}' => "N",
        '\u{146}' => "n",
        '\u{147}' => "N",
        '\u{148}' => "n",
        '\u{149}' => "\u{2BC}n",
        '\u{14C}' => "O",
        '\u{14D}' => "o",
        '\u{14E}' => "O",
        '\u{14F}' => "o",
        '\u{150}' => "O",
        '\u{151}' => "o",
        '\u{154}' => "R",
        '\u{155}' => "r",
        '\u{156}' => "R",
        '\u{157}' => "r",
        '\u{158}' => "R",
        '\u{159}' => "r",
        '\u{15A}' => "S",
        '\u{15B}' => "s",
        '\u{15C}' => "S",
        '\u{15D}' => "s",
        '\u{15E}' => "S",
        '\u{15F}' => "s",
        '\u{160}' => "S",
        '\u{161}' => "s",
        '\u{162}' => "T",
        '\u{163}' => "t",
        '\u{164}' => "T",
        '\u{165}' => "t",
        '\u{168}' => "U",
        '\u{169}' => "u",
        '\u{16A}' => "U",
        '\u{16B}' => "u",
        '\u{16C}' => "U",
        '\u{16D}' => "u",
        '\u{16E}' => "U",
        '\u{16F}' => "u",
        '\u{170}' => "U",
        '\u{171}' => "u",
        '\u{172}' => "U",
        '\u{173}' => "u",
        '\u{174}' => "W",
        '\u{175}' => "w",
        '\u{176}' => "Y",
        '\u{177}' => "y",
        '\u{178}' => "Y",
        '\u{179}' => "Z",
        '\u{17A}' => "z",
        '\u{17B}' => "Z",
        '\u{17C}' => "z",
        '\u{17D}' => "Z",
        '\u{17E}' => "z",
        '\u{17F}' => "s",
        '\u{1E00}' => "A",
        '\u{1E01}' => "a",
        '\u{1E02}' => "B",
        '\u{1E03}' => "b",
        '\u{1E04}' => "B",
        '\u{1E05}' => "b",
        '\u{1E06}' => "B",
        '\u{1E07}' => "b",
        '\u{1E08}' => "C",
        '\u{1E09}' => "c",
        '\u{1E0A}' => "D",
        '\u{1E0B}' => "d",
        '\u{1E0C}' => "D",
        '\u{1E0D}' => "d",
        '\u{1E0E}' => "D",
        '\u{1E0F}' => "d",
        '\u{1E10}' => "D",
        '\u{1E11}' => "d",
        '\u{1E12}' => "D",
        '\u{1E13}' => "d",
        '\u{1E14}' => "E",
        '\u{1E15}' => "e",
        '\u{1E16}' => "E",
        '\u{1E17}' => "e",
        '\u{1E18}' => "E",
        '\u{1E19}' => "e",
        '\u{1E1A}' => "E",
        '\u{1E1B}' => "e",
        '\u{1E1C}' => "E",
        '\u{1E1D}' => "e",
        '\u{1E1E}' => "F",
        '\u{1E1F}' => "f",
        '\u{1E20}' => "G",
        '\u{1E21}' => "g",
        '\u{1E22}' => "H",
        '\u{1E23}' => "h",
        '\u{1E24}' => "H",
        '\u{1E25}' => "h",
        '\u{1E26}' => "H",
        '\u{1E27}' => "h",
        '\u{1E28}' => "H",
        '\u{1E29}' => "h",
        '\u{1E2A}' => "H",
        '\u{1E2B}' => "h",
        '\u{1E2C}' => "I",
        '\u{1E2D}' => "i",
        '\u{1E2E}' => "I",
        '\u{1E2F}' => "i",
        '\u{1E30}' => "K",
        '\u{1E31}' => "k",
        '\u{1E32}' => "K",
        '\u{1E33}' => "k",
        '\u{1E34}' => "K",
        '\u{1E35}' => "k",
        '\u{1E36}' => "L",
        '\u{1E37}' => "l",
        '\u{1E38}' => "L",
        '\u{1E39}' => "l",
        '\u{1E3A}' => "L",
        '\u{1E3B}' => "l",
        '\u{1E3C}' => "L",
        '\u{1E3D}' => "l",
        '\u{1E3E}' => "M",
        '\u{1E3F}' => "m",
        '\u{1E40}' => "M",
        '\u{1E41}' => "m",
        '\u{1E42}' => "M",
        '\u{1E43}' => "m",
        '\u{1E44}' => "N",
        '\u{1E45}' => "n",
        '\u{1E46}' => "N",
        '\u{1E47}' => "n",
        '\u{1E48}' => "N",
        '\u{1E49}' => "n",
        '\u{1E4A}' => "N",
        '\u{1E4B}' => "n",
        '\u{1E4C}' => "O",
        '\u{1E4D}' => "o",
        '\u{1E4E}' => "O",
        '\u{1E4F}' => "o",
        '\u{1E50}' => "O",
        '\u{1E51}' => "o",
        '\u{1E52}' => "O",
        '\u{1E53}' => "o",
        '\u{1E54}' => "P",
        '\u{1E55}' => "p",
        '\u{1E56}' => "P",
        '\u{1E57}' => "p",
        '\u{1E58}' => "R",
        '\u{1E59}' => "r",
        '\u{1E5A}' => "R",
        '\u{1E5B}' => "r",
        '\u{1E5C}' => "R",
        '\u{1E5D}' => "r",
        '\u{1E5E}' => "R",
        '\u{1E5F}' => "r",
        '\u{1E60}' => "S",
        '\u{1E61}' => "s",
        '\u{1E62}' => "S",
        '\u{1E63}' => "s",
        '\u{1E64}' => "S",
        '\u{1E65}' => "s",
        '\u{1E66}' => "S",
        '\u{1E67}' => "s",
        '\u{1E68}' => "S",
        '\u{1E69}' => "s",
        '\u{1E6A}' => "T",
        '\u{1E6B}' => "t",
        '\u{1E6C}' => "T",
        '\u{1E6D}' => "t",
        '\u{1E6E}' => "T",
        '\u{1E6F}' => "t",
        '\u{1E70}' => "T",
        '\u{1E71}' => "t",
        '\u{1E72}' => "U",
        '\u{1E73}' => "u",
        '\u{1E74}' => "U",
        '\u{1E75}' => "u",
        '\u{1E76}' => "U",
        '\u{1E77}' => "u",
        '\u{1E78}' => "U",
        '\u{1E79}' => "u",
        '\u{1E7A}' => "U",
        '\u{1E7B}' => "u",
        '\u{1E7C}' => "V",
        '\u{1E7D}' => "v",
        '\u{1E7E}' => "V",
        '\u{1E7F}' => "v",
        '\u{1E80}' => "W",
        '\u{1E81}' => "w",
        '\u{1E82}' => "W",
        '\u{1E83}' => "w",
        '\u{1E84}' => "W",
        '\u{1E85}' => "w",
        '\u{1E86}' => "W",
        '\u{1E87}' => "w",
        '\u{1E88}' => "W",
        '\u{1E89}' => "w",
        '\u{1E8A}' => "X",
        '\u{1E8B}' => "x",
        '\u{1E8C}' => "X",
        '\u{1E8D}' => "x",
        '\u{1E8E}' => "Y",
        '\u{1E8F}' => "y",
        '\u{1E90}' => "Z",
        '\u{1E91}' => "z",
        '\u{1E92}' => "Z",
        '\u{1E93}' => "z",
        '\u{1E94}' => "Z",
        '\u{1E95}' => "z",
        '\u{1E96}' => "h",
        '\u{1E97}' => "t",
        '\u{1E98}' => "w",
        '\u{1E99}' => "y",
        '\u{1E9A}' => "a\u{2BE}",
        '\u{1E9B}' => "s",
        '\u{1EA0}' => "A",
        '\u{1EA1}' => "a",
        '\u{1EA2}' => "A",
        '\u{1EA3}' => "a",
        '\u{1EA4}' => "A",
        '\u{1EA5}' => "a",
        '\u{1EA6}' => "A",
        '\u{1EA7}' => "a",
        '\u{1EA8}' => "A",
        '\u{1EA9}' => "a",
        '\u{1EAA}' => "A",
        '\u{1EAB}' => "a",
        '\u{1EAC}' => "A",
        '\u{1EAD}' => "a",
        '\u{1EAE}' => "A",
        '\u{1EAF}' => "a",
        '\u{1EB0}' => "A",
        '\u{1EB1}' => "a",
        '\u{1EB2}' => "A",
        '\u{1EB3}' => "a",
        '\u{1EB4}' => "A",
        '\u{1EB5}' => "a",
        '\u{1EB6}' => "A",
        '\u{1EB7}' => "a",
        '\u{1EB8}' => "E",
        '\u{1EB9}' => "e",
        '\u{1EBA}' => "E",
        '\u{1EBB}' => "e",
        '\u{1EBC}' => "E",
        '\u{1EBD}' => "e",
        '\u{1EBE}' => "E",
        '\u{1EBF}' => "e",
        '\u{1EC0}' => "E",
        '\u{1EC1}' => "e",
        '\u{1EC2}' => "E",
        '\u{1EC3}' => "e",
        '\u{1EC4}' => "E",
        '\u{1EC5}' => "e",
        '\u{1EC6}' => "E",
        '\u{1EC7}' => "e",
        '\u{1EC8}' => "I",
        '\u{1EC9}' => "i",
        '\u{1ECA}' => "I",
        '\u{1ECB}' => "i",
        '\u{1ECC}' => "O",
        '\u{1ECD}' => "o",
        '\u{1ECE}' => "O",
        '\u{1ECF}' => "o",
        '\u{1ED0}' => "O",
        '\u{1ED1}' => "o",
        '\u{1ED2}' => "O",
        '\u{1ED3}' => "o",
        '\u{1ED4}' => "O",
        '\u{1ED5}' => "o",
        '\u{1ED6}' => "O",
        '\u{1ED7}' => "o",
        '\u{1ED8}' => "O",
        '\u{1ED9}' => "o",
        '\u{1EDA}' => "O",
        '\u{1EDB}' => "o",
        '\u{1EDC}' => "O",
        '\u{1EDD}' => "o",
        '\u{1EDE}' => "O",
        '\u{1EDF}' => "o",
        '\u{1EE0}' => "O",
        '\u{1EE1}' => "o",
        '\u{1EE2}' => "O",
        '\u{1EE3}' => "o",
        '\u{1EE4}' => "U",
        '\u{1EE5}' => "u",
        '\u{1EE6}' => "U",
        '\u{1EE7}' => "u",
        '\u{1EE8}' => "U",
        '\u{1EE9}' => "u",
        '\u{1EEA}' => "U",
        '\u{1EEB}' => "u",
        '\u{1EEC}' => "U",
        '\u{1EED}' => "u",
        '\u{1EEE}' => "U",
        '\u{1EEF}' => "u",
        '\u{1EF0}' => "U",
        '\u{1EF1}' => "u",
        '\u{1EF2}' => "Y",
        '\u{1EF3}' => "y",
        '\u{1EF4}' => "Y",
        '\u{1EF5}' => "y",
        '\u{1EF6}' => "Y",
        '\u{1EF7}' => "y",
        '\u{1EF8}' => "Y",
        '\u{1EF9}' => "y",
        _ => return None,
    })
}

/// NFKD-normalize then drop combining marks, via the explicit generated map.
/// ASCII chars pass through (the `mode="series"` Python transform keeps a
/// column-level all-ASCII fast-path on top of this); a non-ASCII char emits its
/// mapped replacement, or passes through unchanged if it's outside the covered
/// ranges (CJK, rare precomposed -- the documented boundary). Byte-identical to
/// `text.py::_normalize_unicode_py`.
pub fn normalize_unicode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    normalize_unicode_into(s, &mut out);
    out
}

/// Streaming [`normalize_unicode`] (appends to `out`) for the columnar path.
pub fn normalize_unicode_into(s: &str, out: &mut String) {
    for c in s.chars() {
        if c.is_ascii() {
            out.push(c);
        } else if let Some(rep) = normalize_char(c) {
            out.push_str(rep);
        } else {
            out.push(c);
        }
    }
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

    #[test]
    fn normalize_quotes_cases() {
        assert_eq!(normalize_quotes("\u{201c}hi\u{201d}"), "\"hi\"");
        assert_eq!(normalize_quotes("it\u{2019}s"), "it's");
        assert_eq!(normalize_quotes("\u{2018}a\u{2019}"), "'a'");
        assert_eq!(normalize_quotes("5\u{2033} x 3\u{2032}"), "5\" x 3'");
        assert_eq!(normalize_quotes("plain"), "plain");
    }

    #[test]
    fn normalize_line_endings_cases() {
        assert_eq!(normalize_line_endings("a\r\nb"), "a\nb");
        assert_eq!(normalize_line_endings("a\rb"), "a\nb");
        assert_eq!(normalize_line_endings("a\nb"), "a\nb");
        assert_eq!(normalize_line_endings("a\r\r b"), "a\n\n b");
        assert_eq!(normalize_line_endings("a\r\n\rb"), "a\n\nb");
    }

    #[test]
    fn truncate_cases() {
        assert_eq!(truncate("hello world", 5), "hello");
        assert_eq!(truncate("hi", 5), "hi");
        assert_eq!(truncate("", 3), "");
        assert_eq!(truncate("caf\u{e9}s", 4), "caf\u{e9}"); // char-based, not byte
    }

    #[test]
    fn pad_cases() {
        assert_eq!(pad_left("42", 5, '0'), "00042");
        assert_eq!(pad_left("already", 3, '0'), "already");
        assert_eq!(pad_right("42", 5, ' '), "42   ");
        assert_eq!(pad_right("already", 3, ' '), "already");
    }

    #[test]
    fn remove_html_tags_cases() {
        assert_eq!(remove_html_tags("<b>hi</b>"), "hi");
        assert_eq!(remove_html_tags("a <a href=\"x\">link</a> b"), "a link b");
        assert_eq!(remove_html_tags("<>"), "<>"); // empty tag not matched
        assert_eq!(remove_html_tags("2 < 3"), "2 < 3"); // no closing '>'
        assert_eq!(remove_html_tags("<unclosed"), "<unclosed");
    }

    #[test]
    fn remove_urls_cases() {
        assert_eq!(remove_urls("see http://x.com/y now"), "see  now");
        assert_eq!(remove_urls("https://a.com?q=1 end"), " end");
        assert_eq!(remove_urls("no url here"), "no url here");
        // bare scheme with no following non-ws char is not a match
        assert_eq!(remove_urls("http:// x"), "http:// x");
    }

    #[test]
    fn remove_digits_cases() {
        assert_eq!(remove_digits("abc123def"), "abcdef");
        assert_eq!(remove_digits("no digits"), "no digits");
        assert_eq!(remove_digits("42"), "");
    }

    #[test]
    fn remove_punctuation_cases() {
        assert_eq!(remove_punctuation("hello, world!"), "hello world");
        assert_eq!(remove_punctuation("a-b_c.d"), "abcd");
        assert_eq!(remove_punctuation("keep 123 ok"), "keep 123 ok");
        // non-ASCII letters are dropped (matches the old [^a-zA-Z0-9\s])
        assert_eq!(remove_punctuation("caf\u{e9}"), "caf");
    }

    #[test]
    fn extract_numbers_cases() {
        assert_eq!(extract_numbers("abc12.5def7"), "12.5 7");
        assert_eq!(extract_numbers("price $9.99 x2"), "9.99 2");
        assert_eq!(extract_numbers("no numbers"), "");
        assert_eq!(extract_numbers("12."), "12."); // greedy dot, empty \d*
        assert_eq!(extract_numbers("3.14.159"), "3.14 159");
    }

    #[test]
    fn remove_emojis_cases() {
        assert_eq!(remove_emojis("hi \u{1f600} there"), "hi  there");
        assert_eq!(remove_emojis("\u{1f680}\u{1f600}rocket"), "rocket");
        assert_eq!(remove_emojis("no emoji"), "no emoji");
        assert_eq!(remove_emojis("caf\u{e9}"), "caf\u{e9}"); // accented letter kept
    }

    #[test]
    fn lowercase_uppercase_cases() {
        assert_eq!(lowercase("Hello WORLD"), "hello world");
        assert_eq!(uppercase("Hello world"), "HELLO WORLD");
        assert_eq!(lowercase(""), "");
        // eszett uppercases to SS (agrees across Rust/Py/JS)
        assert_eq!(uppercase("stra\u{df}e"), "STRASSE");
    }

    #[test]
    fn title_case_cases() {
        assert_eq!(title_case("hello world"), "Hello World");
        assert_eq!(title_case("JOHN SMITH"), "John Smith");
        assert_eq!(title_case("a-b c"), "A-B C");
    }

    #[test]
    fn fix_mojibake_cases() {
        // "Ã©" (U+00C3 U+00A9) is UTF-8 "é" misdecoded as Latin-1 -> re-encode fixes it
        assert_eq!(fix_mojibake("caf\u{c3}\u{a9}"), "caf\u{e9}");
        // pure ASCII unchanged
        assert_eq!(fix_mojibake("hello"), "hello");
        // a char > U+00FF can't be Latin-1-encoded -> original returned
        assert_eq!(fix_mojibake("\u{4e00}x"), "\u{4e00}x");
    }

    #[test]
    fn normalize_unicode_cases() {
        // common diacritics -> base letter
        assert_eq!(normalize_unicode("Jos\u{e9}"), "Jose");
        assert_eq!(normalize_unicode("M\u{fc}ller"), "Muller");
        assert_eq!(normalize_unicode("\u{f1}"), "n");
        // ASCII passthrough
        assert_eq!(normalize_unicode("Smith"), "Smith");
        assert_eq!(normalize_unicode(""), "");
        // NON-decomposing chars pass through UNCHANGED (unlike name_transliterate)
        assert_eq!(normalize_unicode("stra\u{df}e"), "stra\u{df}e"); // eszett stays
        assert_eq!(normalize_unicode("\u{e6}"), "\u{e6}"); // ae ligature stays
        assert_eq!(normalize_unicode("\u{f8}"), "\u{f8}"); // o-slash stays
                                                           // multi-char decomposition (IJ ligature)
        assert_eq!(normalize_unicode("\u{132}"), "IJ");
        // unmapped non-ASCII (CJK) passes through
        assert_eq!(normalize_unicode("\u{4e2d}"), "\u{4e2d}");
    }
}
