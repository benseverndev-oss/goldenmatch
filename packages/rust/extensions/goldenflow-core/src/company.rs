//! Owned company/organization kernels (pyo3-free): a dedup-normalization
//! family for B2B entity resolution. These are the reference implementations;
//! the Python/TS fallbacks must reproduce their bytes exactly (byte-parity
//! harness, `tests/parity/identifiers_corpus.jsonl`).
//!
//! Deliberately NOT using a `regex` crate dependency (mirrors the other
//! goldenflow-core kernels' no-regex policy for cross-surface parity
//! guarantees) -- tokenization is hand-rolled with ASCII char-class scanning.

/// Legal-form suffix tokens (lowercase, punctuation-free -- compared against a
/// token's `legal_key`, which strips punctuation, so `L.L.C.` matches `llc`).
/// A curated, stable set shared byte-for-byte with the Python/TS fallbacks --
/// keep this list in lockstep across the three surfaces.
const LEGAL_TOKENS: &[&str] = &[
    "inc",
    "incorporated",
    "llc",
    "llp",
    "lp",
    "ltd",
    "limited",
    "corp",
    "corporation",
    "co",
    "company",
    "companies",
    "gmbh",
    "ag",
    "sa",
    "ab",
    "plc",
    "pc",
    "pllc",
    "nv",
    "bv",
    "oy",
    "oyj",
    "asa",
    "kg",
    "kgaa",
    "srl",
    "spa",
    "pty",
    "sarl",
    "aps",
    "kk",
    "sas",
    "sl",
    "sro",
    "doo",
    "pvt",
    "bhd",
    "sdn",
    "ulc",
];

/// The comparison key for a token: ASCII alphanumerics only, lowercased (so
/// `Inc.`, `INC`, `L.L.C.` all reduce to their bare letters). Non-alnum-only
/// tokens (e.g. `&`) reduce to `""`.
fn legal_key(tok: &str) -> String {
    tok.chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .flat_map(|c| c.to_lowercase())
        .collect()
}

/// True when `tok` (after `legal_key` reduction) is a legal-form suffix.
fn is_legal(tok: &str) -> bool {
    let key = legal_key(tok);
    !key.is_empty() && LEGAL_TOKENS.contains(&key.as_str())
}

/// Trailing chars stripped between suffix-removal passes (whitespace + the
/// separators that trail a legal form: `.` `,`).
fn is_trailing_trim(c: char) -> bool {
    c.is_whitespace() || c == '.' || c == ','
}

/// Composite dedup key for a company name: lowercase, drop a leading `the`,
/// tokenize on non-alphanumeric (keeping `&`), and strip trailing legal-form
/// suffixes (repeatedly). Returns `None` for empty (post-trim) input; may
/// return `""` when the whole name was a legal form (e.g. `"LLC"`).
pub fn company_normalize(name: &str) -> Option<String> {
    let trimmed = name.trim();
    if trimmed.is_empty() {
        return None;
    }
    let lower = trimmed.to_lowercase();
    // Tokenize: keep ASCII alphanumerics + '&'; DROP '.' (acronym-preserving,
    // so `l.l.c.` -> `llc`); every other char is a word break.
    let mut cleaned = String::new();
    for c in lower.chars() {
        if c.is_ascii_alphanumeric() || c == '&' {
            cleaned.push(c);
        } else if c != '.' {
            cleaned.push(' ');
        }
    }
    let mut tokens: Vec<String> = cleaned.split_whitespace().map(str::to_string).collect();
    // Drop a leading standalone "the".
    if tokens.first().map(String::as_str) == Some("the") {
        tokens.remove(0);
    }
    // Drop trailing legal-form tokens and stray "&" separators.
    while let Some(last) = tokens.last() {
        if last == "&" || is_legal(last) {
            tokens.pop();
        } else {
            break;
        }
    }
    Some(tokens.join(" "))
}

/// Strip trailing legal-form suffixes from a company name while preserving the
/// core name's original case and internal formatting (e.g. `Apple Inc.` ->
/// `Apple`, `Microsoft, Corporation` -> `Microsoft`). Returns `None` for empty
/// (post-trim) input; may return `""` when the whole name was a legal form.
pub fn company_strip_legal(name: &str) -> Option<String> {
    let trimmed = name.trim();
    if trimmed.is_empty() {
        return None;
    }
    let mut t = trimmed.to_string();
    loop {
        let core = t.trim_end_matches(is_trailing_trim);
        // Split off the last whitespace-delimited word.
        let (head, candidate) = match core.rfind(char::is_whitespace) {
            Some(i) => (core[..i].to_string(), &core[i + 1..]),
            None => (String::new(), core),
        };
        if is_legal(candidate) {
            t = head;
        } else {
            t = core.to_string();
            break;
        }
    }
    Some(t.trim().to_string())
}

/// Extract the canonical (lowercase, punctuation-free) legal-form token of a
/// company name -- the last word, when it is a legal form (e.g. `Apple Inc.`
/// -> `inc`, `Google L.L.C.` -> `llc`). `None` when there is no trailing legal
/// form (or empty input).
pub fn company_extract_legal(name: &str) -> Option<String> {
    let core = name.trim().trim_end_matches(is_trailing_trim);
    if core.is_empty() {
        return None;
    }
    let last = match core.rfind(char::is_whitespace) {
        Some(i) => &core[i + 1..],
        None => core,
    };
    let key = legal_key(last);
    if !key.is_empty() && LEGAL_TOKENS.contains(&key.as_str()) {
        Some(key)
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_strips_legal_and_the() {
        assert_eq!(company_normalize("Apple Inc."), Some("apple".to_string()));
        assert_eq!(
            company_normalize("The Coca-Cola Company"),
            Some("coca cola".to_string())
        );
        assert_eq!(
            company_normalize("Microsoft Corporation"),
            Some("microsoft".to_string())
        );
        assert_eq!(
            company_normalize("Google L.L.C."),
            Some("google".to_string())
        );
        assert_eq!(
            company_normalize("Procter & Gamble Co"),
            Some("procter & gamble".to_string())
        );
        // "the" only stripped as a standalone leading token.
        assert_eq!(company_normalize("Theranos"), Some("theranos".to_string()));
        // All-legal input reduces to "".
        assert_eq!(company_normalize("LLC"), Some(String::new()));
        assert_eq!(company_normalize(""), None);
        assert_eq!(company_normalize("   "), None);
    }

    #[test]
    fn strip_legal_preserves_case() {
        assert_eq!(company_strip_legal("Apple Inc."), Some("Apple".to_string()));
        assert_eq!(
            company_strip_legal("Microsoft, Corporation"),
            Some("Microsoft".to_string())
        );
        assert_eq!(
            company_strip_legal("MICROSOFT CORP"),
            Some("MICROSOFT".to_string())
        );
        // Multi-word legal form.
        assert_eq!(company_strip_legal("Acme Co Ltd"), Some("Acme".to_string()));
        // No legal form -> unchanged (trimmed).
        assert_eq!(
            company_strip_legal("  Berkshire Hathaway  "),
            Some("Berkshire Hathaway".to_string())
        );
        assert_eq!(company_strip_legal("Ltd"), Some(String::new()));
        assert_eq!(company_strip_legal(""), None);
    }

    #[test]
    fn extract_legal_returns_canonical_token() {
        assert_eq!(company_extract_legal("Apple Inc."), Some("inc".to_string()));
        assert_eq!(
            company_extract_legal("Google L.L.C."),
            Some("llc".to_string())
        );
        assert_eq!(
            company_extract_legal("Microsoft Corporation"),
            Some("corporation".to_string())
        );
        assert_eq!(company_extract_legal("Berkshire Hathaway"), None);
        assert_eq!(company_extract_legal("Standalone"), None);
        assert_eq!(company_extract_legal(""), None);
    }
}
