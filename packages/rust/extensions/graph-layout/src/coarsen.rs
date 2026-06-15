//! Multilevel coarsening via heavy-edge matching.
//!
//! This is the lever that actually makes large graphs settle. Single-level
//! force-directed needs hundreds of iterations *no matter how fast each one is* —
//! that iteration count is the wall. Coarsening builds a pyramid: match each node
//! to its heaviest unmatched neighbor and collapse the pair into a supernode,
//! halving the graph each level. Lay out the tiny top, interpolate down, refine a
//! few iterations per level (each starts near-solved).
//!
//! The tie to goldenmatch: a blocking pass IS a coarsening level — block
//! representatives are supernodes. Seed the hierarchy from blocks and you skip
//! straight to a near-solved coarse layout.

use std::collections::HashMap;

use crate::graph::Graph;

/// One coarsening step. Returns the coarser graph and `map`, where `map[i]` is
/// the coarse-node id that fine node `i` collapsed into.
pub fn coarsen(g: &Graph) -> (Graph, Vec<u32>) {
    // Weighted adjacency (undirected).
    let mut adj: Vec<Vec<(u32, f32)>> = vec![Vec::new(); g.n];
    for &(a, b, w) in &g.edges {
        adj[a as usize].push((b, w));
        adj[b as usize].push((a, w));
    }

    const UNMATCHED: u32 = u32::MAX;
    let mut coarse_id = vec![UNMATCHED; g.n];
    let mut next = 0u32;

    // Visit low-degree nodes first: they have the fewest chances to match, so
    // matching them early raises the overall match rate (better coarsening).
    let mut order: Vec<usize> = (0..g.n).collect();
    order.sort_by_key(|&i| adj[i].len());

    for &i in &order {
        if coarse_id[i] != UNMATCHED {
            continue;
        }
        // Heaviest unmatched neighbor.
        let mut best: Option<usize> = None;
        let mut best_w = f32::NEG_INFINITY;
        for &(nb, w) in &adj[i] {
            let nb = nb as usize;
            if nb != i && coarse_id[nb] == UNMATCHED && w > best_w {
                best_w = w;
                best = Some(nb);
            }
        }
        let id = next;
        next += 1;
        coarse_id[i] = id;
        if let Some(j) = best {
            coarse_id[j] = id; // collapse the matched pair into the same supernode
        }
    }

    // Project edges onto supernodes (sum weights, drop the collapsed self-loops).
    let mut em: HashMap<(u32, u32), f32> = HashMap::new();
    for &(a, b, w) in &g.edges {
        let (ca, cb) = (coarse_id[a as usize], coarse_id[b as usize]);
        if ca == cb {
            continue;
        }
        let key = if ca < cb { (ca, cb) } else { (cb, ca) };
        *em.entry(key).or_insert(0.0) += w;
    }
    let edges: Vec<(u32, u32, f32)> = em.into_iter().map(|((a, b), w)| (a, b, w)).collect();

    (
        Graph {
            n: next as usize,
            edges,
        },
        coarse_id,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn coarsening_shrinks_and_maps_completely() {
        // Two triangles joined by a weak bridge.
        let g = Graph::new(
            6,
            [
                (0, 1, 1.0),
                (1, 2, 1.0),
                (0, 2, 1.0),
                (3, 4, 1.0),
                (4, 5, 1.0),
                (3, 5, 1.0),
                (2, 3, 0.1),
            ],
        );
        let (c, map) = coarsen(&g);
        assert!(c.n < g.n, "coarse graph must be smaller");
        assert_eq!(map.len(), g.n, "every fine node is mapped");
        assert!(map.iter().all(|&m| (m as usize) < c.n), "map in range");
    }

    #[test]
    fn edgeless_graph_does_not_collapse() {
        let g = Graph::new(4, []);
        let (c, _map) = coarsen(&g);
        // No edges → no matches → no shrink; the driver uses this to stop.
        assert_eq!(c.n, g.n);
    }
}
