//! The two signatures a profile carries, and the blocking keys derived from
//! them.
//!
//! 1. **Structured signature** -- a canonical hash over the rigid *identifying*
//!    fields (kind + category + name tokens). Two profiles can only land in the
//!    same structured block if their category agrees AND they share a name
//!    token. This is the Row-4 over-merge guard at the blocking layer: a
//!    `Playwright | William Shakespeare` profile shares no name token with
//!    `Playwright | Thomas Nabbes`, so they are never even compared.
//!
//! 2. **Semantic signature** -- SimHash band hashes (sign random-projection /
//!    cosine-LSH) over the host-supplied dense embedding of the *rendered
//!    fingerprint string*. This recalls paraphrase / morphological variants that
//!    share no literal token ("Nabbes" vs "Thomas Nabbes", "co-founded" vs
//!    "was a founder of"). Reused verbatim from `sketch-core` so the bits are
//!    byte-identical with every surface that already ships SimHash.
//!
//! Candidate pairs are the UNION of structured-block co-members and
//! semantic-band co-members (recall-first; the scorer is the precision stage).

use std::collections::HashMap;

use goldenmatch_fingerprint_core::{fingerprint_fields, FpValue};
use goldenmatch_sketch_core::simhash::simhash_band_hashes_batch;

use crate::model::Profile;

/// Default SimHash planes -- 64 hyperplanes give a 64-bit semantic signature,
/// enough resolution for fingerprint-length strings without ballooning the
/// projection matrix.
pub const DEFAULT_SIM_PLANES: usize = 64;
/// Default LSH bands over the 64-bit signature. 8 bands x 8 rows: a pair must
/// agree on all 8 bits of at least one band to be recalled -- a deliberately
/// loose semantic gate (recall-first), tightened by the scorer.
pub const DEFAULT_SIM_BANDS: usize = 8;
/// Fixed projection seed so every surface (and every run) draws the SAME
/// hyperplanes -- semantic band hashes are only comparable under one seed.
pub const SIM_SEED: u64 = 0x6f70_726f_6669_6c65; // "oprofile"

/// Token-blocking keys for one profile: `category||token` for every name token,
/// scoped by element kind so a node token never collides with an edge token.
/// Returns the canonical fingerprint hash of each `(kind, category, token)` so
/// the key is a fixed-width, cross-language-stable string (the same canonical
/// hashing the `:h1:` record id uses).
pub fn structured_block_keys(p: &Profile) -> Vec<String> {
    let kind = match p.kind {
        crate::model::ElementKind::Node => "node",
        crate::model::ElementKind::Edge => "edge",
    };
    let cat = Profile::norm(&p.category);
    let mut keys: Vec<String> = p
        .name_tokens()
        .into_iter()
        .map(|tok| {
            // Canonical, type-tagged hash -- identical bytes on every surface.
            // Unwrap is safe: all three values are plain non-NaN strings.
            fingerprint_fields(vec![
                ("kind".to_string(), FpValue::Str(kind.to_string())),
                ("category".to_string(), FpValue::Str(cat.clone())),
                ("token".to_string(), FpValue::Str(tok)),
            ])
            .expect("string fields never fail fingerprinting")
        })
        .collect();
    // Category-AGNOSTIC exact-name key: two mentions of the SAME proper name block
    // together even when the LLM labeled their category with divergent synonyms
    // ("Australia | Country" vs "Australia | Nation"). The per-token keys above are
    // category-scoped (Row-4 guard at the blocking layer), so a same-name pair whose
    // category label drifted is otherwise NEVER compared and its multi-hop chain
    // stays shattered. This key is keyed on the WHOLE normalized name (not per
    // token), so it groups only genuine same-name mentions -- it does not broaden
    // recall to everything sharing a common token -- and the scorer (name + category
    // gate) remains the precision stage that keeps cross-sense same-name entities
    // ("Apple" company vs fruit) apart.
    let full = Profile::norm(&p.name);
    if !full.is_empty() {
        keys.push(
            fingerprint_fields(vec![
                ("kind".to_string(), FpValue::Str(kind.to_string())),
                ("scope".to_string(), FpValue::Str("fullname".to_string())),
                ("name".to_string(), FpValue::Str(full)),
            ])
            .expect("string fields never fail fingerprinting"),
        );
    }
    keys
}

/// Semantic band keys for a batch of profiles from their host-supplied
/// embeddings. `embeddings[i]` is the dense vector of `profiles[i]`'s rendered
/// fingerprint (any dim; all rows must share it). Returns, per profile, the
/// list of `band||hash` keys. An empty embedding row yields no semantic keys
/// (that profile is still recalled via its structured keys).
pub fn semantic_band_keys(
    embeddings: &[Vec<f64>],
    planes: usize,
    bands: usize,
) -> Vec<Vec<String>> {
    if embeddings.is_empty() {
        return Vec::new();
    }
    // sketch-core builds the projection matrix once and reuses it across rows.
    let band_hashes = simhash_band_hashes_batch(embeddings, planes, bands, SIM_SEED);
    band_hashes
        .into_iter()
        .map(|row| {
            row.into_iter()
                .enumerate()
                .map(|(b, h)| format!("s{b}:{h:016x}"))
                .collect()
        })
        .collect()
}

/// Group profile indices that share ANY blocking key into candidate pairs.
/// `keys_per_item[i]` is the full key set (structured ∪ semantic) of profile
/// `i`. Returns deduplicated unordered pairs `(a, b)` with `a < b`. A key
/// shared by a huge bucket is capped by `max_bucket` (skip blocks bigger than
/// the cap -- a single over-broad key, e.g. an all-UNKNOWN category, must not
/// explode into an O(n^2) candidate set; those profiles still pair via their
/// other, more specific keys).
pub fn candidate_pairs(keys_per_item: &[Vec<String>], max_bucket: usize) -> Vec<(usize, usize)> {
    let mut buckets: HashMap<&str, Vec<usize>> = HashMap::new();
    for (i, keys) in keys_per_item.iter().enumerate() {
        for k in keys {
            buckets.entry(k.as_str()).or_default().push(i);
        }
    }
    let mut pairs: Vec<(usize, usize)> = Vec::new();
    for members in buckets.values() {
        if members.len() < 2 || members.len() > max_bucket {
            continue;
        }
        for a in 0..members.len() {
            for b in (a + 1)..members.len() {
                let (x, y) = (members[a], members[b]);
                pairs.push(if x < y { (x, y) } else { (y, x) });
            }
        }
    }
    pairs.sort_unstable();
    pairs.dedup();
    pairs
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::ElementKind;

    #[test]
    fn shared_name_token_same_category_blocks_together() {
        let a = Profile::parse(
            ElementKind::Node,
            "Thomas Nabbes | Playwright | 17th c | wrote X",
        );
        let b = Profile::parse(ElementKind::Node, "Nabbes | Playwright | 1605 | UNKNOWN");
        let ka = structured_block_keys(&a);
        let kb = structured_block_keys(&b);
        // They share the (node, playwright, nabbes) key.
        assert!(ka.iter().any(|k| kb.contains(k)));
    }

    #[test]
    fn same_name_divergent_category_blocks_on_fullname_key() {
        // The exact-name shatter at the blocking layer: identical proper name, the
        // LLM drifted the category label. The category-scoped per-token keys differ,
        // so only the category-agnostic full-name key can recall the pair.
        let a = Profile::parse(
            ElementKind::Node,
            "Australia | Country | UNKNOWN | Federal monarchy",
        );
        let b = Profile::parse(
            ElementKind::Node,
            "Australia | Nation | UNKNOWN | Smallest continent",
        );
        let ka = structured_block_keys(&a);
        let kb = structured_block_keys(&b);
        assert!(
            ka.iter().any(|k| kb.contains(k)),
            "same name must block despite category drift"
        );
        // A genuinely different name must NOT share the full-name key.
        let c = Profile::parse(
            ElementKind::Node,
            "Austria | Country | UNKNOWN | Alpine republic",
        );
        let kc = structured_block_keys(&c);
        assert!(
            !ka.iter().any(|k| kc.contains(k)),
            "different name must not block"
        );
    }

    #[test]
    fn different_name_same_category_does_not_block() {
        let nabbes = Profile::parse(
            ElementKind::Node,
            "Thomas Nabbes | Playwright | UNKNOWN | UNKNOWN",
        );
        let shakes = Profile::parse(
            ElementKind::Node,
            "William Shakespeare | Playwright | UNKNOWN | UNKNOWN",
        );
        let ka = structured_block_keys(&nabbes);
        let kb = structured_block_keys(&shakes);
        assert!(!ka.iter().any(|k| kb.contains(k)));
    }

    #[test]
    fn node_and_edge_never_share_a_structured_key() {
        let node = Profile::parse(ElementKind::Node, "wrote | Playwright | UNKNOWN | UNKNOWN");
        let edge = Profile::parse(ElementKind::Edge, "wrote | Playwright | UNKNOWN | UNKNOWN");
        let kn = structured_block_keys(&node);
        let ke = structured_block_keys(&edge);
        assert!(!kn.iter().any(|k| ke.contains(k)));
    }

    #[test]
    fn candidate_pairs_dedup_and_cap() {
        // Items 0,1,2 all share key "x"; the cap=2 drops that bucket. Items 0,3
        // additionally share "y" (bucket size 2, kept).
        let keys = vec![
            vec!["x".to_string(), "y".to_string()],
            vec!["x".to_string()],
            vec!["x".to_string()],
            vec!["y".to_string()],
        ];
        assert_eq!(candidate_pairs(&keys, 2), vec![(0, 3)]);
        // With a higher cap the "x" bucket of 3 contributes all its pairs.
        let mut all = candidate_pairs(&keys, 10);
        all.sort_unstable();
        assert_eq!(all, vec![(0, 1), (0, 2), (0, 3), (1, 2)]);
    }

    #[test]
    fn semantic_keys_recall_token_disjoint_paraphrase() {
        // Two near-identical embeddings collide on every band; an orthogonal one
        // does not share all 8 bits of any band.
        let e = vec![
            vec![1.0, 0.9, 0.8, 0.05, 0.0, -0.1, 0.2, 0.3],
            vec![0.99, 0.92, 0.78, 0.06, 0.01, -0.09, 0.21, 0.29],
            vec![-1.0, -0.9, -0.8, -0.05, 0.0, 0.1, -0.2, -0.3],
        ];
        let keys = semantic_band_keys(&e, 64, 8);
        let shared01 = keys[0].iter().any(|k| keys[1].contains(k));
        assert!(shared01, "near-identical vectors must share a band");
    }
}
