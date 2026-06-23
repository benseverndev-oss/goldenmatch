//! End-to-end cross-document resolution over Virtual Fingerprints.
//!
//! `profiles[i]` is element `i`'s fingerprint; `embeddings[i]` (optional, but if
//! present must be aligned and equal-dim) is the dense embedding of its rendered
//! fingerprint. The pipeline:
//!
//! 1. **Block** -- structured token keys ∪ semantic SimHash band keys -> a
//!    recall-first candidate-pair set (`signature::candidate_pairs`).
//! 2. **Score** -- the anti-shatter fusion scorer on each candidate pair; keep
//!    pairs at/above `merge_threshold`.
//! 3. **Cluster** -- weak connected components over the kept pairs
//!    (`graph-core`), so transitive evidence chains (A~B, B~C) collapse A,B,C
//!    into one durable cross-document entity.
//!
//! Resolution is per `ElementKind` only by construction: node/edge profiles
//! never share a structured key and `score_pair` would gate them out anyway, so
//! a single pass over the mixed list cannot cross-link a node with an edge.

use goldenmatch_graph_core::connected_components;
use serde::{Deserialize, Serialize};

use crate::model::Profile;
use crate::score::{score_pair, PairScore, ScoreConfig};
use crate::signature::{
    candidate_pairs, semantic_band_keys, structured_block_keys, DEFAULT_SIM_BANDS,
    DEFAULT_SIM_PLANES,
};

/// Knobs for the whole pipeline. `scoring` carries the scorer weights/gates;
/// the rest tune blocking. Defaults are the zero-config stance.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(default)]
pub struct ResolveConfig {
    pub scoring: ScoreConfig,
    pub sim_planes: usize,
    pub sim_bands: usize,
    /// Skip any block bigger than this (over-broad-key O(n^2) guard).
    pub max_bucket: usize,
}

impl Default for ResolveConfig {
    fn default() -> Self {
        ResolveConfig {
            scoring: ScoreConfig::default(),
            sim_planes: DEFAULT_SIM_PLANES,
            sim_bands: DEFAULT_SIM_BANDS,
            max_bucket: 1_000,
        }
    }
}

/// A kept (merged) profile pair with its full score breakdown -- the audit
/// trail behind every cluster edge.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct ResolvedEdge {
    pub a: usize,
    pub b: usize,
    pub score: PairScore,
}

/// The resolution result. `clusters` partitions every profile index (including
/// singletons) into cross-document entities; `edges` are the scored merges that
/// justify them.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Resolution {
    pub clusters: Vec<Vec<usize>>,
    pub edges: Vec<ResolvedEdge>,
}

/// Resolve `profiles` into cross-document entities. `embeddings` may be empty
/// (structured-only blocking + scoring) or aligned with `profiles`. The optional
/// `category_embeddings` (also empty or aligned) drive the category gate's
/// synonym escape hatch -- a category-specific signal that, unlike the
/// whole-fingerprint `embeddings`, is not polluted by the by-design-divergent
/// defining attribute (see `score::score_pair`). Blocking still uses
/// `embeddings` only.
pub fn resolve(
    profiles: &[Profile],
    embeddings: &[Vec<f64>],
    category_embeddings: &[Vec<f64>],
    cfg: &ResolveConfig,
) -> Resolution {
    let n = profiles.len();
    if n == 0 {
        return Resolution {
            clusters: Vec::new(),
            edges: Vec::new(),
        };
    }

    // 1. Blocking keys: structured (always) ∪ semantic (when embeddings given).
    let mut keys: Vec<Vec<String>> = profiles.iter().map(structured_block_keys).collect();
    if embeddings.len() == n && embeddings.iter().any(|e| !e.is_empty()) {
        let sem = semantic_band_keys(embeddings, cfg.sim_planes, cfg.sim_bands);
        for (i, row) in sem.into_iter().enumerate() {
            keys[i].extend(row);
        }
    }
    let cands = candidate_pairs(&keys, cfg.max_bucket);

    // 2. Score candidates; keep the merges.
    let empty: Vec<f64> = Vec::new();
    let emb = |i: usize| -> &[f64] {
        embeddings
            .get(i)
            .map(|v| v.as_slice())
            .unwrap_or(empty.as_slice())
    };
    let cat_emb = |i: usize| -> &[f64] {
        category_embeddings
            .get(i)
            .map(|v| v.as_slice())
            .unwrap_or(empty.as_slice())
    };
    let mut edges: Vec<ResolvedEdge> = Vec::new();
    for (a, b) in cands {
        let ps = score_pair(
            &profiles[a],
            &profiles[b],
            emb(a),
            emb(b),
            cat_emb(a),
            cat_emb(b),
            &cfg.scoring,
        );
        if ps.score >= cfg.scoring.merge_threshold {
            edges.push(ResolvedEdge { a, b, score: ps });
        }
    }

    // 3. Cluster via WCC. graph-core works in i64 id space; profile indices map
    // 1:1 to ids 0..n.
    let wcc_edges: Vec<(i64, i64, f64)> = edges
        .iter()
        .map(|e| (e.a as i64, e.b as i64, e.score.score))
        .collect();
    let all_ids: Vec<i64> = (0..n as i64).collect();
    let comps = connected_components(&wcc_edges, &all_ids);
    let clusters: Vec<Vec<usize>> = comps
        .into_iter()
        .map(|c| {
            let mut v: Vec<usize> = c.into_iter().map(|x| x as usize).collect();
            v.sort_unstable();
            v
        })
        .collect();

    Resolution { clusters, edges }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::ElementKind;

    fn node(s: &str) -> Profile {
        Profile::parse(ElementKind::Node, s)
    }

    #[test]
    fn musique_shatter_repair_three_docs_one_entity() {
        // Three documents mention Nabbes with DISJOINT neighborhoods (different
        // anchors, different defining attributes), plus a distinct Shakespeare.
        // Structured-only (no embeddings): correct outcome is {0,1,2} merged on
        // name+category transitivity, 3 alone.
        let profiles = vec![
            node("Thomas Nabbes | Playwright | 17th Century England | Wrote Play X"),
            node("Nabbes | Playwright | UNKNOWN | Born 1605"),
            node("Thomas Nabbes | Playwright | London | Covent Garden circle"),
            node("William Shakespeare | Playwright | 1564 1616 | Wrote Hamlet"),
        ];
        let res = resolve(&profiles, &[], &[], &ResolveConfig::default());
        let nabbes = res.clusters.iter().find(|c| c.contains(&0)).unwrap();
        assert_eq!(
            nabbes,
            &vec![0, 1, 2],
            "disjoint Nabbes mentions must reunite"
        );
        let shakes = res.clusters.iter().find(|c| c.contains(&3)).unwrap();
        assert_eq!(shakes, &vec![3], "Shakespeare must stay distinct");
    }

    #[test]
    fn synonym_category_bridged_by_embedding() {
        // Same entity, but one document labels the category "Dramatist" -- a
        // synonym the lexical category gate rejects. A strong fingerprint
        // embedding cosine must satisfy the category gate and reunite them,
        // while the distinct Shakespeare (orthogonal embedding) stays apart.
        let profiles = vec![
            node("Thomas Nabbes | Playwright | UNKNOWN | Wrote Play X"),
            node("Thomas Nabbes | Dramatist | UNKNOWN | Born 1605"),
            node("William Shakespeare | Playwright | UNKNOWN | Wrote Hamlet"),
        ];
        // 0 and 1 near-identical embeddings; 2 orthogonal.
        let embeddings = vec![
            vec![1.0, 0.2, 0.0, 0.1],
            vec![0.98, 0.25, 0.02, 0.08],
            vec![0.0, 0.1, 1.0, -0.2],
        ];
        // Without embeddings the synonym pair would NOT merge...
        let no_emb = resolve(&profiles, &[], &[], &ResolveConfig::default());
        assert!(
            no_emb.clusters.iter().any(|c| c == &vec![0]),
            "lexical-only must leave the synonym-category pair unmerged (conservative)"
        );
        // ...with embeddings, it does.
        let res = resolve(&profiles, &embeddings, &[], &ResolveConfig::default());
        let nabbes = res.clusters.iter().find(|c| c.contains(&0)).unwrap();
        assert_eq!(
            nabbes,
            &vec![0, 1],
            "embedding must bridge the synonym category"
        );
        assert!(
            res.clusters.iter().any(|c| c == &vec![2]),
            "Shakespeare stays distinct"
        );
    }

    #[test]
    fn category_embedding_reunites_exact_name_synonym_category() {
        // The exact-name shatter end-to-end: two docs, SAME proper name, synonym
        // category labels, DIVERGENT attributes (so the whole-fingerprint cosine
        // can't bridge), plus a distinct same-category entity. Only the
        // category-specific embedding can reunite 0,1 without dragging in 2.
        let profiles = vec![
            node("Australia | Country | UNKNOWN | Federal monarchy"),
            node("Australia | Nation | UNKNOWN | Smallest continent"),
            node("Canada | Country | UNKNOWN | Maple syrup exporter"),
        ];
        // Whole-fingerprint embeddings: all roughly orthogonal (attributes diverge),
        // so the legacy hatch bridges nothing.
        let fp = vec![
            vec![1.0, 0.0, 0.0],
            vec![0.0, 1.0, 0.0],
            vec![0.0, 0.0, 1.0],
        ];
        // Category embeddings: "Country" ~ "Nation" close; "Country" (Canada) is
        // the same label as 0 but its NAME differs, so the name gate keeps it out.
        let cat = vec![
            vec![1.0, 0.05, 0.0],
            vec![0.97, 0.12, 0.0],
            vec![1.0, 0.05, 0.0],
        ];
        let res = resolve(&profiles, &fp, &cat, &ResolveConfig::default());
        let aus = res.clusters.iter().find(|c| c.contains(&0)).unwrap();
        assert_eq!(
            aus,
            &vec![0, 1],
            "exact-name synonym-category pair must reunite"
        );
        assert!(
            res.clusters.iter().any(|c| c == &vec![2]),
            "distinct name stays apart"
        );
    }

    #[test]
    fn empty_input_is_empty() {
        let res = resolve(&[], &[], &[], &ResolveConfig::default());
        assert!(res.clusters.is_empty() && res.edges.is_empty());
    }

    #[test]
    fn singletons_are_their_own_clusters() {
        let profiles = vec![
            node("Acme | Company | UNKNOWN | UNKNOWN"),
            node("Globex | Company | UNKNOWN | UNKNOWN"),
        ];
        let res = resolve(&profiles, &[], &[], &ResolveConfig::default());
        assert_eq!(res.clusters.len(), 2);
        assert!(res.edges.is_empty());
    }

    #[test]
    fn edges_carry_score_breakdown_for_audit() {
        let profiles = vec![
            node("Thomas Nabbes | Playwright | 17th c | Wrote Play X"),
            node("Thomas Nabbes | Playwright | UNKNOWN | Born 1605"),
        ];
        let res = resolve(&profiles, &[], &[], &ResolveConfig::default());
        assert_eq!(res.edges.len(), 1);
        assert!(res.edges[0].score.gated_in);
        assert!(res.edges[0].score.name > 0.99);
    }
}
