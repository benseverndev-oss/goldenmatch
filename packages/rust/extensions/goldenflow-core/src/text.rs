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
    out
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
    for c in s.chars() {
        match c {
            '\u{201c}' | '\u{201d}' | '\u{2033}' => out.push('"'),
            '\u{2018}' | '\u{2019}' | '\u{2032}' => out.push('\''),
            _ => out.push(c),
        }
    }
    out
}

/// Normalize `\r\n` and lone `\r` to `\n`. Byte-identical to
/// `text.py::normalize_line_endings` (replace `\r\n` first, then any `\r`).
pub fn normalize_line_endings(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
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
    out
}

/// Truncate to the first `n` characters (polars `str.slice(0, n)` is
/// character-based). Byte-identical to `text.py::truncate`.
pub fn truncate(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Left-pad to `width` characters with `pad` (polars `str.pad_start`); a string
/// already at/over `width` is unchanged. Byte-identical to `text.py::pad_left`.
pub fn pad_left(s: &str, width: usize, pad: char) -> String {
    let len = s.chars().count();
    if len >= width {
        return s.to_string();
    }
    let mut out = String::with_capacity(width);
    for _ in 0..(width - len) {
        out.push(pad);
    }
    out.push_str(s);
    out
}

/// Right-pad to `width` characters with `pad` (polars `str.pad_end`); a string
/// already at/over `width` is unchanged. Byte-identical to `text.py::pad_right`.
pub fn pad_right(s: &str, width: usize, pad: char) -> String {
    let len = s.chars().count();
    if len >= width {
        return s.to_string();
    }
    let mut out = String::from(s);
    for _ in 0..(width - len) {
        out.push(pad);
    }
    out
}

/// Strip HTML tags: remove each `<...>` span with at least one char between the
/// angle brackets (regex `<[^>]+>`, minimal to the first `>`). `<>` and an
/// unclosed `<` are left intact. Byte-identical to `text.py::remove_html_tags`.
pub fn remove_html_tags(s: &str) -> String {
    let chars: Vec<char> = s.chars().collect();
    let n = chars.len();
    let mut out = String::with_capacity(s.len());
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
    out
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
    let chars: Vec<char> = s.chars().collect();
    let n = chars.len();
    let mut out = String::with_capacity(s.len());
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
    out
}

/// Remove ASCII digit characters. The old polars `\d` was Unicode-aware; this
/// kernel is bounded to ASCII `0-9` (documented reference-mode boundary --
/// exotic Unicode digits are out of the bounded set). Byte-identical to
/// `text.py::remove_digits`.
pub fn remove_digits(s: &str) -> String {
    s.chars().filter(|c| !c.is_ascii_digit()).collect()
}

/// Remove punctuation: keep ASCII alphanumerics and whitespace, drop everything
/// else (regex `[^a-zA-Z0-9\s]` -> ""). `\s` == `is_whitespace`; non-ASCII
/// letters are dropped (as in the old polars behavior). Byte-identical to
/// `text.py::remove_punctuation`.
pub fn remove_punctuation(s: &str) -> String {
    s.chars()
        .filter(|c| c.is_ascii_alphanumeric() || c.is_whitespace())
        .collect()
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
}
