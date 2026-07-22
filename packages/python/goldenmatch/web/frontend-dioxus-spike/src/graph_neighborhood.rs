//! Click-to-expand neighborhood view — the answer to the whole-graph view's
//! browser-side scale ceiling.
//!
//! `resolved_graph_chart` (in `graph.rs`) renders EVERY record + evidence edge
//! up front; past a few thousand records that page gets heavy and the layout is
//! a hairball. This view instead starts COLLAPSED — one hub node per resolved
//! entity — so the initial payload scales with the ENTITY count, not the record
//! count, and reveals an entity's records + evidence edges (its neighborhood)
//! only when you click its hub. That is exactly the shape a live server-fed
//! build would take: the collapsed overview is cheap, and each click is one
//! `/api/v1/identities/{id}` neighborhood fetch. Here every neighborhood is
//! pre-serialized into the page so the interaction runs fully offline.
//!
//! Charming's `HtmlRenderer` emits a static ECharts option with no event hooks,
//! so the click handling uses the raw-ECharts escape hatch the README already
//! flagged for exactly this follow-on: `graph_neighborhood` builds the data in
//! Rust (reusing the same source-category / conflict-coloring / label logic as
//! `graph.rs`), and `bin/render_neighborhood` bakes it into a self-contained
//! page whose vanilla-JS `chart.on('click')` toggles each entity's neighborhood.

use std::collections::{BTreeMap, BTreeSet};

use serde::Serialize;

use crate::graph::{record_label, short};
use crate::model::ResolvedGraph;

/// Category index reserved for entity hub nodes (always visible).
const HUB_CAT: u64 = 0;
/// Category index reserved for records touched by a `conflicts_with` edge.
const CONFLICT_CAT: u64 = 1;
/// First per-source category; sources are assigned from here upward.
const FIRST_SOURCE_CAT: u64 = 2;

/// One ECharts graph node (hub or record). Field names match the ECharts
/// `series.data` item shape so the baked JSON is used verbatim client-side.
#[derive(Serialize)]
pub struct NeighborhoodNode {
    pub id: String,
    pub name: String,
    #[serde(rename = "symbolSize")]
    pub symbol_size: f64,
    pub category: u64,
    pub value: f64,
    /// True for hub nodes so the click handler knows what is toggle-able.
    #[serde(rename = "isHub")]
    pub is_hub: bool,
}

/// One ECharts graph link (`member` hub->record, or a record<->record edge).
#[derive(Serialize)]
pub struct NeighborhoodLink {
    pub source: String,
    pub target: String,
    pub value: f64,
}

/// The records + edges revealed when an entity's hub is expanded.
#[derive(Serialize)]
pub struct EntityNeighborhood {
    pub nodes: Vec<NeighborhoodNode>,
    pub links: Vec<NeighborhoodLink>,
}

/// The full baked payload: the always-visible hubs, every entity's collapsed
/// neighborhood keyed by `entity_id`, the shared category list, and a summary.
#[derive(Serialize)]
pub struct NeighborhoodPayload {
    pub categories: Vec<String>,
    pub hubs: Vec<NeighborhoodNode>,
    pub neighborhoods: BTreeMap<String, EntityNeighborhood>,
    pub summary: String,
}

/// Hub symbol size grows with cluster size so big entities read as big hubs.
fn hub_symbol_size(size: usize) -> f64 {
    match size {
        0 | 1 => 12.0,
        2 => 18.0,
        3 => 24.0,
        4..=6 => 30.0,
        _ => 38.0,
    }
}

/// Build the collapsed-by-default neighborhood payload from a resolved graph.
pub fn build_neighborhood_payload(graph: &ResolvedGraph) -> NeighborhoodPayload {
    // Records in any `conflicts_with` edge, across ALL entities (same rule as
    // `resolved_graph_chart`: a conflicted record is promoted to the red cat).
    let mut conflicted: BTreeSet<String> = BTreeSet::new();
    for ent in &graph.entities {
        for e in &ent.edges {
            if e.kind == "conflicts_with" {
                conflicted.insert(e.record_a_id.clone());
                conflicted.insert(e.record_b_id.clone());
            }
        }
    }

    // One category per distinct source (0 = hub, 1 = conflict reserved above).
    let mut source_cat: BTreeMap<String, u64> = BTreeMap::new();
    for ent in &graph.entities {
        for r in &ent.records {
            let src = r.source.clone().unwrap_or_else(|| "unknown".into());
            let next = source_cat.len() as u64 + FIRST_SOURCE_CAT;
            source_cat.entry(src).or_insert(next);
        }
    }
    let mut categories = vec!["entity".to_string(), "\u{26a0} conflict".to_string()];
    let mut by_idx: Vec<(&String, &u64)> = source_cat.iter().collect();
    by_idx.sort_by_key(|(_, i)| **i);
    for (src, _) in by_idx {
        categories.push(format!("source: {src}"));
    }

    let mut hubs: Vec<NeighborhoodNode> = Vec::with_capacity(graph.entities.len());
    let mut neighborhoods: BTreeMap<String, EntityNeighborhood> = BTreeMap::new();
    let mut n_multi = 0usize;
    let mut n_records = 0usize;
    let mut n_edges = 0usize;

    for ent in &graph.entities {
        let size = ent.records.len();
        if size > 1 {
            n_multi += 1;
        }
        n_records += size;

        hubs.push(NeighborhoodNode {
            id: ent.entity_id.clone(),
            name: format!("{} ({size})", short(&ent.entity_id)),
            symbol_size: hub_symbol_size(size),
            category: HUB_CAT,
            value: size as f64,
            is_hub: true,
        });

        let mut nodes: Vec<NeighborhoodNode> = Vec::with_capacity(size);
        let mut links: Vec<NeighborhoodLink> = Vec::new();
        for r in &ent.records {
            let src = r.source.clone().unwrap_or_else(|| "unknown".into());
            let category = if conflicted.contains(&r.record_id) {
                CONFLICT_CAT
            } else {
                *source_cat.get(&src).unwrap_or(&FIRST_SOURCE_CAT)
            };
            nodes.push(NeighborhoodNode {
                id: r.record_id.clone(),
                name: record_label(r),
                symbol_size: 14.0,
                category,
                value: 1.0,
                is_hub: false,
            });
            // Structural `member` link: the hub anchors its records so an
            // expansion visibly bursts out of the clicked entity.
            links.push(NeighborhoodLink {
                source: ent.entity_id.clone(),
                target: r.record_id.clone(),
                value: 0.6,
            });
        }
        for e in &ent.edges {
            links.push(NeighborhoodLink {
                source: e.record_a_id.clone(),
                target: e.record_b_id.clone(),
                value: e.score.unwrap_or(1.0),
            });
            n_edges += 1;
        }
        neighborhoods.insert(
            ent.entity_id.clone(),
            EntityNeighborhood { nodes, links },
        );
    }

    let summary = format!(
        "{} entities ({} multi-record) \u{b7} {} records \u{b7} {} evidence edges \u{b7} \
         click an entity to expand its neighborhood",
        graph.entities.len(),
        n_multi,
        n_records,
        n_edges,
    );

    NeighborhoodPayload {
        categories,
        hubs,
        neighborhoods,
        summary,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{EvidenceEdge, IdentityRecord, IdentityView};

    fn rec(id: &str, source: &str, name: &str) -> IdentityRecord {
        let mut payload = BTreeMap::new();
        payload.insert("name".to_string(), serde_json::Value::String(name.into()));
        IdentityRecord {
            record_id: id.into(),
            source: Some(source.into()),
            source_pk: None,
            payload,
        }
    }

    fn graph() -> ResolvedGraph {
        ResolvedGraph {
            entities: vec![
                IdentityView {
                    entity_id: "ent-A".into(),
                    status: None,
                    confidence: Some(0.9),
                    dataset: None,
                    records: vec![rec("crm:1", "crm", "Maya Patel"), rec("web:2", "web", "M. Patel")],
                    edges: vec![EvidenceEdge {
                        record_a_id: "crm:1".into(),
                        record_b_id: "web:2".into(),
                        kind: "same_as".into(),
                        score: Some(0.82),
                        matchkey_name: None,
                        run_name: None,
                    }],
                },
                IdentityView {
                    entity_id: "ent-B".into(),
                    status: None,
                    confidence: None,
                    dataset: None,
                    records: vec![rec("erp:9", "erp", "James Wei")],
                    edges: vec![],
                },
            ],
        }
    }

    #[test]
    fn hubs_are_one_per_entity_and_collapsed() {
        let p = build_neighborhood_payload(&graph());
        assert_eq!(p.hubs.len(), 2, "one hub per entity");
        assert!(p.hubs.iter().all(|h| h.is_hub && h.category == HUB_CAT));
        // Records live in neighborhoods, NOT in the initial hub set.
        assert_eq!(p.neighborhoods["ent-A"].nodes.len(), 2);
        assert_eq!(p.neighborhoods["ent-B"].nodes.len(), 1);
    }

    #[test]
    fn neighborhood_has_member_and_evidence_links() {
        let p = build_neighborhood_payload(&graph());
        // ent-A: 2 member links (hub->record) + 1 evidence edge = 3 links.
        assert_eq!(p.neighborhoods["ent-A"].links.len(), 3);
        // A member link is anchored on the hub id.
        assert!(p.neighborhoods["ent-A"]
            .links
            .iter()
            .any(|l| l.source == "ent-A" && l.target == "crm:1"));
        // ent-B: 1 member link, no evidence.
        assert_eq!(p.neighborhoods["ent-B"].links.len(), 1);
    }

    #[test]
    fn categories_reserve_hub_and_conflict_slots() {
        let p = build_neighborhood_payload(&graph());
        assert_eq!(p.categories[0], "entity");
        assert_eq!(p.categories[1], "\u{26a0} conflict");
        // Three sources -> three trailing source categories.
        assert!(p.categories.iter().any(|c| c == "source: crm"));
        assert!(p.categories.iter().any(|c| c == "source: web"));
        assert!(p.categories.iter().any(|c| c == "source: erp"));
    }
}
