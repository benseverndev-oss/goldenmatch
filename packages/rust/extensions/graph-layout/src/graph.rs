//! Weighted undirected graph + I/O + connected components.
//!
//! Node ids are dense `0..n`. [`Graph::read_edge_list`] remaps arbitrary string
//! tokens (e.g. goldenmatch record ids) to dense ids and hands back the labels.

use std::collections::HashMap;
use std::fs;
use std::io;

use crate::rng::Rng;

#[derive(Clone)]
pub struct Graph {
    pub n: usize,
    /// `(a, b, weight)`, `a < b`, deduped (parallel edges summed).
    pub edges: Vec<(u32, u32, f32)>,
}

impl Graph {
    pub fn new(n: usize, raw_edges: impl IntoIterator<Item = (u32, u32, f32)>) -> Self {
        let mut m: HashMap<(u32, u32), f32> = HashMap::new();
        for (a, b, w) in raw_edges {
            if a == b {
                continue; // drop self-loops
            }
            let key = if a < b { (a, b) } else { (b, a) };
            *m.entry(key).or_insert(0.0) += w;
        }
        let edges = m.into_iter().map(|((a, b), w)| (a, b, w)).collect();
        Graph { n, edges }
    }

    /// Read a whitespace/TSV edge list: `a b [weight]` per line, `#` comments and
    /// blank lines ignored. Tokens are arbitrary strings, remapped to dense ids.
    /// Returns the graph plus the label for each dense id.
    pub fn read_edge_list(path: &str) -> io::Result<(Graph, Vec<String>)> {
        let text = fs::read_to_string(path)?;
        let mut ids: HashMap<String, u32> = HashMap::new();
        let mut labels: Vec<String> = Vec::new();
        let mut raw: Vec<(u32, u32, f32)> = Vec::new();
        let intern = |tok: &str, ids: &mut HashMap<String, u32>, labels: &mut Vec<String>| -> u32 {
            if let Some(&id) = ids.get(tok) {
                id
            } else {
                let id = labels.len() as u32;
                ids.insert(tok.to_string(), id);
                labels.push(tok.to_string());
                id
            }
        };
        for line in text.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let mut it = line.split_whitespace();
            let (a, b) = match (it.next(), it.next()) {
                (Some(a), Some(b)) => (a, b),
                _ => continue,
            };
            let w: f32 = it.next().and_then(|s| s.parse().ok()).unwrap_or(1.0);
            let ai = intern(a, &mut ids, &mut labels);
            let bi = intern(b, &mut ids, &mut labels);
            raw.push((ai, bi, w));
        }
        let n = labels.len();
        Ok((Graph::new(n, raw), labels))
    }

    /// A synthetic clustered graph: `clusters` blobs of `per` nodes each, dense
    /// within a blob (`p_in`) and sparse across (`p_out`). The thing a layout
    /// should visibly pull apart.
    pub fn synthetic(clusters: usize, per: usize, p_in: f32, p_out: f32, seed: u64) -> Graph {
        let n = clusters * per;
        let mut rng = Rng::new(seed);
        let mut raw: Vec<(u32, u32, f32)> = Vec::new();
        let cluster_of = |i: usize| i / per;
        for i in 0..n {
            // Guarantee connectivity within a blob: link to the blob's first node.
            let first = cluster_of(i) * per;
            if i != first {
                raw.push((first as u32, i as u32, 1.0));
            }
            for j in (i + 1)..n {
                let same = cluster_of(i) == cluster_of(j);
                let p = if same { p_in } else { p_out };
                if rng.unit01() < p {
                    raw.push((i as u32, j as u32, if same { 1.0 } else { 0.4 }));
                }
            }
        }
        Graph::new(n, raw)
    }

    /// Connected-component id per node (dense, `0..k`), via union-find.
    ///
    /// This is the coloring seed that makes resolved clusters read as distinct
    /// blobs. (goldenmatch ships the same primitive in `graph-core` as
    /// `connected_components`; a built-in union-find is used here to keep this
    /// demo crate dependency-free — enable an `engine` feature to delegate.)
    pub fn components(&self) -> Vec<u32> {
        let mut parent: Vec<u32> = (0..self.n as u32).collect();
        fn find(parent: &mut [u32], mut x: u32) -> u32 {
            while parent[x as usize] != x {
                parent[x as usize] = parent[parent[x as usize] as usize]; // path-halving
                x = parent[x as usize];
            }
            x
        }
        for &(a, b, _) in &self.edges {
            let (ra, rb) = (find(&mut parent, a), find(&mut parent, b));
            if ra != rb {
                parent[ra as usize] = rb;
            }
        }
        // Relabel roots to a dense 0..k.
        let mut label: HashMap<u32, u32> = HashMap::new();
        let mut out = vec![0u32; self.n];
        for i in 0..self.n as u32 {
            let r = find(&mut parent, i);
            let next = label.len() as u32;
            let id = *label.entry(r).or_insert(next);
            out[i as usize] = id;
        }
        out
    }
}
