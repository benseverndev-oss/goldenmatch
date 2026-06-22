//! The rigid Virtual Fingerprint schema.
//!
//! A `Profile` is the standardized, LLM-synthesized identifying summary of ONE
//! graph element -- a node (entity) OR an edge (relationship). The whole point
//! of the engine is to move resolution OFF raw extraction text and raw
//! neighborhood text and ONTO this compressed, deterministic representation, so
//! cross-document linking compares "what this thing uniquely is" rather than the
//! happenstance of where it was mentioned.
//!
//! The schema is intentionally a brutally rigid 4-part pipe-delimited string
//! (the #spec lesson: free-text fingerprints collapse straight back into the
//! Row-4 semantic over-merge). The host (the LLM synthesis pass) MUST emit:
//!
//! ```text
//! <name> | <category> | <anchor> | <attribute>
//! ```
//!
//! - `name`      -- the normalized identifying name (node) / relation phrase (edge).
//! - `category`  -- the primary class/role (node) / predicate type (edge).
//! - `anchor`    -- a temporal/spatial anchor ("17th Century England", "1605").
//! - `attribute` -- the single most defining attribute ("Wrote Play X").
//!
//! Any unknown part is the literal `UNKNOWN`. Missing trailing parts are padded
//! to `UNKNOWN` so a 2-part synth still parses.

use serde::{Deserialize, Serialize};

/// The sentinel an LLM emits for an unknown field. Compared case-insensitively;
/// canonicalizes to an empty normalized form (so "unknown" never spuriously
/// matches across two different entities that both happen to lack an anchor).
pub const UNKNOWN: &str = "UNKNOWN";

/// Whether a profile describes a graph node (entity) or an edge (relationship).
/// Resolution is run WITHIN a kind: a node never merges with an edge.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ElementKind {
    Node,
    Edge,
}

impl Default for ElementKind {
    fn default() -> Self {
        ElementKind::Node
    }
}

/// One Virtual Fingerprint. Fields hold the raw (trimmed) synthesized text;
/// normalized comparison forms are derived on demand via [`Profile::norm`] so
/// the stored value stays human-readable and the normalization is centralized.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Profile {
    #[serde(default)]
    pub kind: ElementKind,
    pub name: String,
    pub category: String,
    pub anchor: String,
    pub attribute: String,
}

impl Profile {
    /// Parse the rigid `name | category | anchor | attribute` string. Splits on
    /// the FIRST three `|` only (so a `|` inside the attribute survives), trims
    /// each part, and pads missing trailing parts with `UNKNOWN`. Never fails --
    /// a degenerate synth still yields a (mostly-UNKNOWN) profile rather than an
    /// error, because dropping an element silently shatters the graph worse than
    /// a weak fingerprint does.
    pub fn parse(kind: ElementKind, s: &str) -> Profile {
        let mut parts = s.splitn(4, '|');
        let name = parts.next().unwrap_or("").trim().to_string();
        let category = parts.next().unwrap_or(UNKNOWN).trim().to_string();
        let anchor = parts.next().unwrap_or(UNKNOWN).trim().to_string();
        let attribute = parts.next().unwrap_or(UNKNOWN).trim().to_string();
        Profile {
            kind,
            name: if name.is_empty() { UNKNOWN.to_string() } else { name },
            category: if category.is_empty() { UNKNOWN.to_string() } else { category },
            anchor: if anchor.is_empty() { UNKNOWN.to_string() } else { anchor },
            attribute: if attribute.is_empty() { UNKNOWN.to_string() } else { attribute },
        }
    }

    /// The canonical pipe-delimited rendering -- what the host embeds to get the
    /// semantic signature, and what a human reads in an explainability dump.
    /// Stable across runs (no field is reordered).
    pub fn render(&self) -> String {
        format!("{} | {} | {} | {}", self.name, self.category, self.anchor, self.attribute)
    }

    /// Is `field` the UNKNOWN sentinel (case-insensitive, post-trim)? An UNKNOWN
    /// field is *missing evidence*, never *conflicting evidence* -- the Row-3
    /// guard. The scorer treats two UNKNOWN anchors as "no information", not "a
    /// match".
    pub fn is_unknown(field: &str) -> bool {
        field.trim().eq_ignore_ascii_case(UNKNOWN) || field.trim().is_empty()
    }

    /// Normalized comparison form of a field: lowercased, punctuation→space,
    /// whitespace collapsed, trimmed. UNKNOWN normalizes to `""`. This is the
    /// form the field scorers and the structured block-key consume.
    pub fn norm(field: &str) -> String {
        if Profile::is_unknown(field) {
            return String::new();
        }
        let mut out = String::with_capacity(field.len());
        let mut prev_space = true; // trims leading space
        for c in field.chars() {
            let c = if c.is_alphanumeric() {
                c.to_ascii_lowercase()
            } else {
                ' '
            };
            if c == ' ' {
                if !prev_space {
                    out.push(' ');
                    prev_space = true;
                }
            } else {
                out.push(c);
                prev_space = false;
            }
        }
        if out.ends_with(' ') {
            out.pop();
        }
        out
    }

    /// Significant normalized tokens of the name -- the units of token blocking.
    /// Empty when the name is UNKNOWN.
    pub fn name_tokens(&self) -> Vec<String> {
        Profile::norm(&self.name)
            .split_whitespace()
            .map(|t| t.to_string())
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_full_four_parts() {
        let p = Profile::parse(ElementKind::Node, "Thomas Nabbes | Playwright | 17th Century England | Wrote Play X");
        assert_eq!(p.name, "Thomas Nabbes");
        assert_eq!(p.category, "Playwright");
        assert_eq!(p.anchor, "17th Century England");
        assert_eq!(p.attribute, "Wrote Play X");
    }

    #[test]
    fn parse_pads_missing_trailing_parts() {
        let p = Profile::parse(ElementKind::Node, "Nabbes | Playwright");
        assert_eq!(p.name, "Nabbes");
        assert_eq!(p.category, "Playwright");
        assert_eq!(p.anchor, UNKNOWN);
        assert_eq!(p.attribute, UNKNOWN);
    }

    #[test]
    fn parse_keeps_pipe_inside_attribute() {
        let p = Profile::parse(ElementKind::Edge, "wrote | authored | 1638 | play X | and play Y");
        assert_eq!(p.attribute, "play X | and play Y");
    }

    #[test]
    fn norm_strips_punct_and_case() {
        assert_eq!(Profile::norm("Thomas  NABBES, Jr."), "thomas nabbes jr");
        assert_eq!(Profile::norm(UNKNOWN), "");
        assert_eq!(Profile::norm("   "), "");
    }

    #[test]
    fn unknown_is_missing_not_matching() {
        assert!(Profile::is_unknown("unknown"));
        assert!(Profile::is_unknown("  UNKNOWN "));
        assert!(Profile::is_unknown(""));
        assert!(!Profile::is_unknown("1605"));
    }

    #[test]
    fn name_tokens_split_and_normalized() {
        let p = Profile::parse(ElementKind::Node, "Thomas Nabbes | Playwright | UNKNOWN | UNKNOWN");
        assert_eq!(p.name_tokens(), vec!["thomas", "nabbes"]);
    }
}
