//! The anti-shatter fusion scorer.
//!
//! This is where the MuSiQue multi-hop "graph shatter" is actually fixed. Two
//! failure modes pull in opposite directions:
//!
//! - **Row 3 (under-merge):** the same entity is described by DISJOINT
//!   neighborhoods across documents -- Doc A "Nabbes wrote Play X", Doc B
//!   "Nabbes born 1605". Raw-neighborhood comparison sees ~0% overlap and
//!   refuses to merge, shattering the multi-hop path.
//! - **Row 4 (over-merge):** raw-text dense embeddings blur entities that share
//!   a semantic neighborhood -- "Nabbes" and "Shakespeare" both land in
//!   "17th-century playwright" space and collapse into one entity.
//!
//! The fix exploits the rigid fingerprint structure:
//!
//! - the *defining attribute* is EXPECTED to diverge across documents (it is the
//!   per-document evidence), so it can only ADD confidence, never veto a merge
//!   -- killing the Row-3 under-merge.
//! - the *name* and *category* are the stable identity, so a hard gate requires
//!   them to agree before any merge is considered -- killing the Row-4
//!   over-merge regardless of how close the embeddings are.
//!
//! Everything in between (anchor, embedding cosine) is a soft, tunable signal,
//! and -- crucially -- a MISSING field (UNKNOWN) contributes a neutral prior,
//! never a penalty (a second Row-3 guard: absent evidence is not contradicting
//! evidence).

use std::collections::HashSet;

use goldenmatch_score_core::{jaro_winkler_similarity, token_sort_normalized_ratio};
use serde::{Deserialize, Serialize};

use crate::model::Profile;

/// Tunable weights + gates for the fusion scorer. Defaults are the engine's
/// zero-config stance; the host can override per corpus. Weights on the soft
/// signals sum with the gated base -- the attribute term is a positive-only
/// bonus and is intentionally OUTSIDE the weighted base so it can never drag a
/// score down.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(default)]
pub struct ScoreConfig {
    /// Hard gate: category similarity must reach this OR the embedding cosine
    /// must reach `category_embedding_gate`, else the pair scores 0. The
    /// embedding escape hatch lets the semantic signature bridge synonym
    /// categories ("Playwright" vs "Dramatist") that lexical scoring can't.
    pub category_gate: f64,
    /// Embedding-cosine (mapped to [0,1]) that alone satisfies the category gate
    /// when lexical category similarity falls short. Only consulted when an
    /// embedding is supplied for both sides.
    pub category_embedding_gate: f64,
    /// Hard gate: name similarity must reach this or the pair scores 0.
    pub name_gate: f64,
    /// Weight on name similarity in the soft base.
    pub w_name: f64,
    /// Weight on category similarity in the soft base.
    pub w_category: f64,
    /// Weight on the anchor term in the soft base.
    pub w_anchor: f64,
    /// Weight on the embedding-cosine term in the soft base.
    pub w_embedding: f64,
    /// Positive-only bonus weight for a corroborating defining attribute.
    pub w_attribute_bonus: f64,
    /// Neutral prior used when a soft signal is UNKNOWN / unavailable. 0.5 = "no
    /// information" on the [0,1] scale.
    pub neutral_prior: f64,
    /// Pairs scoring at/above this merge into the same entity.
    pub merge_threshold: f64,
}

impl Default for ScoreConfig {
    fn default() -> Self {
        ScoreConfig {
            category_gate: 0.60,
            category_embedding_gate: 0.85,
            name_gate: 0.80,
            w_name: 0.45,
            w_category: 0.25,
            w_anchor: 0.15,
            w_embedding: 0.15,
            w_attribute_bonus: 0.10,
            neutral_prior: 0.5,
            merge_threshold: 0.72,
        }
    }
}

/// The per-pair score breakdown -- kept (not just the scalar) because the North
/// Star is never-black-box: a host can show exactly WHY two fingerprints merged.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct PairScore {
    pub name: f64,
    pub category: f64,
    pub anchor: f64,
    pub embedding: f64,
    pub attribute_bonus: f64,
    /// `true` iff both hard gates passed. When `false`, `score == 0.0`.
    pub gated_in: bool,
    pub score: f64,
}

/// Cosine similarity of two equal-length dense vectors, mapped from [-1,1] to
/// [0,1]. Returns `None` when either vector is empty or zero-norm (no semantic
/// signal -> the scorer falls back to the neutral prior).
pub fn cosine01(a: &[f64], b: &[f64]) -> Option<f64> {
    if a.is_empty() || a.len() != b.len() {
        return None;
    }
    let mut dot = 0.0;
    let mut na = 0.0;
    let mut nb = 0.0;
    for i in 0..a.len() {
        dot += a[i] * b[i];
        na += a[i] * a[i];
        nb += b[i] * b[i];
    }
    if na == 0.0 || nb == 0.0 {
        return None;
    }
    let cos = dot / (na.sqrt() * nb.sqrt());
    Some(((cos + 1.0) / 2.0).clamp(0.0, 1.0))
}

/// Score one profile pair. `emb_a`/`emb_b` are the host-supplied fingerprint
/// embeddings (pass empty slices to score on the structured fields alone).
pub fn score_pair(
    a: &Profile,
    b: &Profile,
    emb_a: &[f64],
    emb_b: &[f64],
    cfg: &ScoreConfig,
) -> PairScore {
    // Name: the cross-document identity signal. Short forms ("Nabbes") and full
    // forms ("Thomas Nabbes") must score high, so combine token_sort (word-order
    // invariant) with a token-containment coefficient (a short name that is a
    // token-subset of a longer one scores 1.0) -- the token_set behavior the
    // three base scorers lack.
    let name = name_similarity(&a.name, &b.name);
    // Category: jaro_winkler -- categories are short controlled-ish vocab.
    let category = field_sim(&a.category, &b.category, cfg.neutral_prior, true);
    // Embedding cosine of the rendered fingerprints, computed once. `None` when
    // no embedding was supplied for a side; the soft signals fall back to the
    // neutral prior in that case.
    let emb_cos = cosine01(emb_a, emb_b);
    let embedding = emb_cos.unwrap_or(cfg.neutral_prior);
    let anchor = field_sim(&a.anchor, &b.anchor, cfg.neutral_prior, false);

    // Hard gate. Name is the true Row-4 discriminator (Nabbes vs Shakespeare
    // share a category, not a name), so it is always required. Category guards
    // cross-sense collisions ("Apple" the company vs the fruit) and passes on
    // EITHER lexical agreement OR a strong embedding cosine -- the latter bridges
    // synonym categories the lexical scorer would wrongly veto.
    let category_ok = category >= cfg.category_gate
        || emb_cos.is_some_and(|c| c >= cfg.category_embedding_gate);
    let gated_in = category_ok && name >= cfg.name_gate;
    if !gated_in {
        return PairScore {
            name,
            category,
            anchor,
            embedding,
            attribute_bonus: 0.0,
            gated_in: false,
            score: 0.0,
        };
    }

    let base = cfg.w_name * name
        + cfg.w_category * category
        + cfg.w_anchor * anchor
        + cfg.w_embedding * embedding;

    // Defining attribute: positive-only. If the two documents happen to state a
    // similar defining attribute, that's corroboration -> bonus. If they state
    // DIFFERENT attributes (the common, expected Row-3 case), there is NO
    // penalty -- the term floors at 0.
    let attr_sim = field_sim(&a.attribute, &b.attribute, 0.0, false);
    let attribute_bonus = cfg.w_attribute_bonus * attr_sim.max(0.0);

    let score = (base + attribute_bonus).clamp(0.0, 1.0);
    PairScore {
        name,
        category,
        anchor,
        embedding,
        attribute_bonus,
        gated_in: true,
        score,
    }
}

/// Name similarity = `max(token_sort_ratio, token_overlap_coefficient)`.
///
/// The overlap coefficient `|A∩B| / min(|A|,|B|)` is 1.0 exactly when the
/// shorter name's tokens are a subset of the longer's -- so "Nabbes" matches
/// "Thomas Nabbes" perfectly while "Thomas Nabbes" vs "Thomas Heywood" (one
/// shared token of two) scores 0.5 and is gated out. Both names UNKNOWN -> 0
/// (no identity evidence; the pair should not have blocked together anyway).
pub fn name_similarity(a: &str, b: &str) -> f64 {
    let na = Profile::norm(a);
    let nb = Profile::norm(b);
    let ta: HashSet<&str> = na.split_whitespace().collect();
    let tb: HashSet<&str> = nb.split_whitespace().collect();
    if ta.is_empty() || tb.is_empty() {
        return 0.0;
    }
    let inter = ta.intersection(&tb).count();
    let overlap = inter as f64 / ta.len().min(tb.len()) as f64;
    overlap.max(token_sort_normalized_ratio(&na, &nb))
}

/// Similarity of two free-text fields with the Row-3 unknown rule. When EITHER
/// field is UNKNOWN, return `unknown_prior` (no information) rather than 0. When
/// both are present, score them (`jw` selects jaro_winkler vs token_sort).
fn field_sim(a: &str, b: &str, unknown_prior: f64, jw: bool) -> f64 {
    if Profile::is_unknown(a) || Profile::is_unknown(b) {
        return unknown_prior;
    }
    let (na, nb) = (Profile::norm(a), Profile::norm(b));
    if jw {
        jaro_winkler_similarity(&na, &nb)
    } else {
        token_sort_normalized_ratio(&na, &nb)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::ElementKind;

    fn node(s: &str) -> Profile {
        Profile::parse(ElementKind::Node, s)
    }

    #[test]
    fn row3_disjoint_attributes_still_merge() {
        // The headline case: same name+category, DISJOINT defining attributes,
        // one anchor known one not. Must clear the merge threshold.
        let a = node("Thomas Nabbes | Playwright | 17th Century England | Wrote Play X");
        let b = node("Thomas Nabbes | Playwright | UNKNOWN | Born 1605");
        let cfg = ScoreConfig::default();
        let ps = score_pair(&a, &b, &[], &[], &cfg);
        assert!(ps.gated_in);
        assert!(
            ps.score >= cfg.merge_threshold,
            "disjoint-attribute same-entity must merge, got {}",
            ps.score
        );
    }

    #[test]
    fn row4_distinct_entities_blocked_even_with_close_embeddings() {
        // Different names, same category, and DELIBERATELY identical embeddings
        // (the over-merge trap). The name gate must veto regardless.
        let a = node("Thomas Nabbes | Playwright | 17th Century | UNKNOWN");
        let b = node("William Shakespeare | Playwright | 17th Century | UNKNOWN");
        let emb = vec![0.3, 0.4, 0.5, 0.6];
        let ps = score_pair(&a, &b, &emb, &emb, &ScoreConfig::default());
        assert!(!ps.gated_in);
        assert_eq!(ps.score, 0.0, "name gate must veto the over-merge");
    }

    #[test]
    fn attribute_agreement_is_bonus_only() {
        let a = node("Acme Corp | Company | UNKNOWN | Makes anvils");
        let b_same = node("Acme Corp | Company | UNKNOWN | Makes anvils");
        let b_diff = node("Acme Corp | Company | UNKNOWN | Sells rockets");
        let cfg = ScoreConfig::default();
        let s_same = score_pair(&a, &b_same, &[], &[], &cfg).score;
        let s_diff = score_pair(&a, &b_diff, &[], &[], &cfg).score;
        // Matching attribute scores higher, but the divergent one must NOT be
        // penalized below the threshold (no veto).
        assert!(s_same > s_diff);
        assert!(s_diff >= cfg.merge_threshold);
    }

    #[test]
    fn unknown_anchor_is_neutral_not_penalty() {
        let a = node("Globex | Company | 1989 | UNKNOWN");
        let b_unknown = node("Globex | Company | UNKNOWN | UNKNOWN");
        let b_conflict = node("Globex | Company | 1750 | UNKNOWN");
        let cfg = ScoreConfig::default();
        let s_unknown = score_pair(&a, &b_unknown, &[], &[], &cfg);
        let s_conflict = score_pair(&a, &b_conflict, &[], &[], &cfg);
        // An UNKNOWN anchor (neutral 0.5) must beat a genuinely conflicting one.
        assert!(s_unknown.anchor > s_conflict.anchor);
    }

    #[test]
    fn cosine01_maps_and_guards() {
        assert_eq!(cosine01(&[1.0, 0.0], &[1.0, 0.0]), Some(1.0));
        assert_eq!(cosine01(&[1.0, 0.0], &[-1.0, 0.0]), Some(0.0));
        assert!((cosine01(&[1.0, 0.0], &[0.0, 1.0]).unwrap() - 0.5).abs() < 1e-12);
        assert_eq!(cosine01(&[], &[]), None);
        assert_eq!(cosine01(&[0.0, 0.0], &[1.0, 1.0]), None);
    }
}
