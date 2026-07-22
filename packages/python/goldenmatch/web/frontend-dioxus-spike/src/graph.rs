//! The knowledge-graph spec — the second framework-agnostic ECharts option in
//! this spike. Where `chart.rs` proves a line chart, this proves the `graph`
//! (force-directed network) series: GoldenMatch's Identity Graph rendered as an
//! interactive knowledge graph, built ONCE in Rust and rendered on both the
//! native `render_graph` binary and (unchanged) the Dioxus/WASM `WasmRenderer`.
//!
//! ONE resolved identity becomes: an entity node at the hub, one node per source
//! record, `member` links from the entity to its records, and the evidence
//! edges (`same_as` / `possible_same_as` / `conflicts_with`) between records,
//! weighted by score. Node color encodes provenance (a legend-toggleable
//! category per source); any record touched by a `conflicts_with` edge is
//! promoted to a distinct red "conflict" category so over-merges pop.
//!
//! Charming caveat (documented in README): charming 0.5's `GraphLink` exposes no
//! per-edge `lineStyle`/`label`, so edge KIND is surfaced via the conflict-node
//! coloring + the item tooltip rather than per-edge color. A production build
//! would use a newer charming (or drop to a raw ECharts option) for per-edge
//! kind coloring — a coverage gap, not a blocker.

use std::collections::{BTreeMap, BTreeSet};

use charming::{
    component::{Legend, Title},
    element::{Label, Tooltip, Trigger},
    series::{
        Graph, GraphCategory, GraphData, GraphLayout, GraphLayoutForce, GraphLink, GraphNode,
    },
    Chart,
};

use crate::model::{IdentityRecord, IdentityView, ResolvedGraph};

const ENTITY_CAT: u64 = 0;
const CONFLICT_CAT: u64 = 1;
const FIRST_SOURCE_CAT: u64 = 2;

pub fn identity_graph_chart(view: &IdentityView) -> Chart {
    // Records that participate in a `conflicts_with` edge get highlighted.
    let mut conflicted: BTreeSet<&str> = BTreeSet::new();
    for e in &view.edges {
        if e.kind == "conflicts_with" {
            conflicted.insert(e.record_a_id.as_str());
            conflicted.insert(e.record_b_id.as_str());
        }
    }

    // One category per distinct source, assigned after Entity + conflict.
    let mut source_cat: BTreeMap<String, u64> = BTreeMap::new();
    for r in &view.records {
        let src = r.source.clone().unwrap_or_else(|| "unknown".into());
        let next = source_cat.len() as u64 + FIRST_SOURCE_CAT;
        source_cat.entry(src).or_insert(next);
    }

    let mut categories = vec![
        GraphCategory {
            name: "Entity".into(),
        },
        GraphCategory {
            name: "\u{26a0} conflict".into(),
        },
    ];
    let mut by_idx: Vec<(&String, &u64)> = source_cat.iter().collect();
    by_idx.sort_by_key(|(_, i)| **i);
    for (src, _) in by_idx {
        categories.push(GraphCategory {
            name: format!("source: {src}"),
        });
    }

    let mut nodes: Vec<GraphNode> = Vec::with_capacity(view.records.len() + 1);
    let mut links: Vec<GraphLink> = Vec::new();

    // Hub: the entity itself. `value` = confidence so it shows in the tooltip.
    let conf = view.confidence.unwrap_or(0.0);
    nodes.push(GraphNode {
        id: view.entity_id.clone(),
        name: format!("entity {}", short(&view.entity_id)),
        x: 0.0,
        y: 0.0,
        value: conf,
        category: ENTITY_CAT,
        symbol_size: 46.0,
        label: None,
    });

    for r in &view.records {
        let src = r.source.clone().unwrap_or_else(|| "unknown".into());
        let category = if conflicted.contains(r.record_id.as_str()) {
            CONFLICT_CAT
        } else {
            *source_cat.get(&src).unwrap_or(&FIRST_SOURCE_CAT)
        };
        nodes.push(GraphNode {
            id: r.record_id.clone(),
            name: record_label(r),
            x: 0.0,
            y: 0.0,
            value: 1.0,
            category,
            symbol_size: 26.0,
            label: None,
        });
        // Structural `member` link: entity -> record.
        links.push(GraphLink {
            source: view.entity_id.clone(),
            target: r.record_id.clone(),
            value: Some(1.0),
        });
    }

    // Evidence edges between records; width tracks the match score.
    for e in &view.edges {
        links.push(GraphLink {
            source: e.record_a_id.clone(),
            target: e.record_b_id.clone(),
            value: e.score.or(Some(1.0)),
        });
    }

    let n_conf = conflicted.len();
    let subtitle = format!(
        "{} records \u{b7} {} evidence edges{} \u{b7} confidence {:.2}",
        view.records.len(),
        view.edges.len(),
        if n_conf > 0 {
            format!(" \u{b7} {n_conf} in conflict")
        } else {
            String::new()
        },
        conf,
    );

    Chart::new()
        .title(
            Title::new()
                .text(format!(
                    "Identity knowledge graph \u{2014} {}",
                    short(&view.entity_id)
                ))
                .subtext(subtitle),
        )
        .tooltip(Tooltip::new().trigger(Trigger::Item))
        .legend(
            Legend::new()
                .data(
                    categories
                        .iter()
                        .map(|c| c.name.clone())
                        .collect::<Vec<String>>(),
                )
                .top("bottom"),
        )
        .series(
            Graph::new()
                .layout(GraphLayout::Force)
                .force(GraphLayoutForce::new().gravity(0.08).edge_length(120.0))
                .roam(true)
                .label(Label::new().show(true))
                .data(GraphData {
                    nodes,
                    links,
                    categories,
                }),
        )
}

/// Truncate long ids (UUIDs / hashes) for display; char-safe (never slices a
/// multi-byte boundary).
pub(crate) fn short(id: &str) -> String {
    let n = id.chars().count();
    if n > 12 {
        format!("{}\u{2026}", id.chars().take(12).collect::<String>())
    } else {
        id.to_string()
    }
}

/// Prefer a human-readable payload field for the node label; fall back to the id.
pub(crate) fn record_label(r: &IdentityRecord) -> String {
    for key in ["name", "full_name", "email", "company"] {
        if let Some(v) = r.payload.get(key) {
            if let Some(s) = v.as_str() {
                if !s.is_empty() {
                    return s.to_string();
                }
            }
        }
    }
    short(&r.record_id)
}

/// The WHOLE resolved graph as one force network: every entity's records are
/// nodes (colored by source; conflict records red), evidence edges are links.
/// No per-entity hub nodes and no always-on labels — at hundreds of records the
/// CLUSTER STRUCTURE itself is the point (records of one entity clump via their
/// evidence edges); labels live in the hover tooltip. Node size scales with the
/// entity's record count so big clusters stand out.
pub fn resolved_graph_chart(graph: &ResolvedGraph) -> Chart {
    // Records in any `conflicts_with` edge, across ALL entities.
    let mut conflicted: BTreeSet<String> = BTreeSet::new();
    for ent in &graph.entities {
        for e in &ent.edges {
            if e.kind == "conflicts_with" {
                conflicted.insert(e.record_a_id.clone());
                conflicted.insert(e.record_b_id.clone());
            }
        }
    }

    // One category per source (index 0 reserved for the conflict category).
    let mut source_cat: BTreeMap<String, u64> = BTreeMap::new();
    for ent in &graph.entities {
        for r in &ent.records {
            let src = r.source.clone().unwrap_or_else(|| "unknown".into());
            let next = source_cat.len() as u64 + 1;
            source_cat.entry(src).or_insert(next);
        }
    }
    let mut categories = vec![GraphCategory {
        name: "\u{26a0} conflict".into(),
    }];
    let mut by_idx: Vec<(&String, &u64)> = source_cat.iter().collect();
    by_idx.sort_by_key(|(_, i)| **i);
    for (src, _) in by_idx {
        categories.push(GraphCategory {
            name: format!("source: {src}"),
        });
    }

    let mut nodes: Vec<GraphNode> = Vec::new();
    let mut links: Vec<GraphLink> = Vec::new();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut n_multi = 0usize;
    for ent in &graph.entities {
        let size = ent.records.len();
        if size > 1 {
            n_multi += 1;
        }
        let sym = match size {
            0 | 1 => 7.0,
            2 => 12.0,
            3 => 16.0,
            _ => 22.0,
        };
        for r in &ent.records {
            if !seen.insert(r.record_id.clone()) {
                continue;
            }
            let src = r.source.clone().unwrap_or_else(|| "unknown".into());
            let category = if conflicted.contains(&r.record_id) {
                0
            } else {
                *source_cat.get(&src).unwrap_or(&1)
            };
            nodes.push(GraphNode {
                id: r.record_id.clone(),
                name: record_label(r),
                x: 0.0,
                y: 0.0,
                value: size as f64,
                category,
                symbol_size: sym,
                label: None,
            });
        }
        for e in &ent.edges {
            links.push(GraphLink {
                source: e.record_a_id.clone(),
                target: e.record_b_id.clone(),
                value: e.score.or(Some(1.0)),
            });
        }
    }

    let n_records = nodes.len();
    let subtitle = format!(
        "{} records \u{2192} {} entities ({} multi-record) \u{b7} {} evidence edges \u{b7} {} sources{}",
        n_records,
        graph.entities.len(),
        n_multi,
        links.len(),
        source_cat.len(),
        if !conflicted.is_empty() {
            format!(" \u{b7} {} in conflict", conflicted.len())
        } else {
            String::new()
        },
    );

    Chart::new()
        .title(
            Title::new()
                .text("Resolved identity graph")
                .subtext(subtitle),
        )
        .tooltip(Tooltip::new().trigger(Trigger::Item))
        .legend(
            Legend::new()
                .data(
                    categories
                        .iter()
                        .map(|c| c.name.clone())
                        .collect::<Vec<String>>(),
                )
                .top("bottom"),
        )
        .series(
            Graph::new()
                .layout(GraphLayout::Force)
                // layout_animation(false): compute the force layout up front
                // instead of animating every frame, so thousands of nodes settle
                // instantly rather than wiggling for tens of seconds.
                .force(
                    GraphLayoutForce::new()
                        .gravity(0.06)
                        .edge_length(28.0)
                        .friction(0.18)
                        .layout_animation(false),
                )
                .roam(true)
                .data(GraphData {
                    nodes,
                    links,
                    categories,
                }),
        )
}
