//! `goldenhnsw` — a pure-Rust HNSW (Hierarchical Navigable Small World) index,
//! the native `IndexHNSWFlat` counterpart to GoldenMatch's brute-force
//! `IndexFlatIP` ANN path (`goldenmatch.core.ann_blocker.ANNBlocker`).
//!
//! ## Why this exists
//!
//! `ANNBlocker` today is exact inner-product only: FAISS `IndexFlatIP` (O(N)
//! per probe) or a numpy all-pairs fallback (O(N²) per batch). Both are exact
//! and both scale linearly-or-worse in the corpus size. HNSW gives sub-linear
//! ANN queries with recall approaching 1.0, and — unlike FAISS — carries **zero
//! C dependencies**, so it ships in the same lean maturin/abi3 wheel pattern as
//! `goldenembed` / `goldenmatch-native` without the `ort`/openssl build-container
//! friction documented in `extensions/CLAUDE.md`.
//!
//! ## Metric
//!
//! The score is the raw **inner product** `⟨q, x⟩`, byte-for-byte what FAISS
//! `IndexFlatIP.search` returns (FAISS does not normalize internally). On the
//! normal GoldenMatch path the embedder emits L2-normalized vectors, so the
//! inner product *is* the cosine similarity — the same invariant the numpy
//! fallback relies on. Graph navigation uses `dist = -⟨q, x⟩` (lower = nearer);
//! for normalized inputs this is monotonic with L2 distance, so the standard
//! HNSW metric-space search behaves correctly.
//!
//! ## Determinism
//!
//! Level assignment uses a seeded SplitMix64 PRNG (no `rand` crate), so a given
//! `(seed, insertion order)` always yields the same graph — tests and the
//! Python parity harness depend on this. `ef_search` auto-scales up to the
//! corpus size for small indexes, so recall is exact in the regimes the
//! fallback-parity tests exercise (the win is asymptotic; small N stays exact).
//!
//! No `rayon`: insertion is single-threaded by construction. The Python caller
//! (`ANNBlocker`, `score_buckets`) already parallelizes across probes/buckets,
//! so nothing is lost — and the #688 rayon `LockLatch` futex-park cannot recur.

use std::cmp::Ordering;
use std::collections::BinaryHeap;

/// Tuning parameters for the HNSW graph. Defaults mirror the common FAISS
/// `IndexHNSWFlat(d, M=16)` / hnswlib presets.
#[derive(Debug, Clone, Copy)]
pub struct HnswParams {
    /// Max neighbors per node on layers > 0 (`M`). Layer 0 uses `2*M`.
    pub m: usize,
    /// Size of the dynamic candidate list during construction.
    pub ef_construction: usize,
    /// Default size of the dynamic candidate list during search. Effective
    /// `ef` at query time is `max(ef_search, k)`, further bounded to the corpus
    /// size (so small indexes are searched exactly).
    pub ef_search: usize,
    /// PRNG seed for reproducible level assignment.
    pub seed: u64,
}

impl Default for HnswParams {
    fn default() -> Self {
        Self {
            m: 16,
            ef_construction: 200,
            ef_search: 64,
            seed: 0x9E37_79B9_7F4A_7C15,
        }
    }
}

/// SplitMix64: a tiny, dependency-free, well-distributed PRNG. Deterministic
/// level assignment so a `(seed, insertion order)` reproduces the same graph.
struct SplitMix64 {
    state: u64,
}

impl SplitMix64 {
    fn new(seed: u64) -> Self {
        Self { state: seed }
    }

    fn next_u64(&mut self) -> u64 {
        self.state = self.state.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.state;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// Uniform f64 in the half-open interval `(0, 1]` (never 0, so `ln` is safe).
    fn next_unit(&mut self) -> f64 {
        // 53-bit mantissa; shift into [0,1), then map 0 -> 1 to keep it > 0.
        let bits = self.next_u64() >> 11;
        let u = (bits as f64) / ((1u64 << 53) as f64);
        if u <= 0.0 {
            1.0
        } else {
            u
        }
    }
}

/// A `(distance, id)` pair ordered by distance then id. `BinaryHeap<Item>` is a
/// max-heap (farthest first); `BinaryHeap<Reverse<Item>>` a min-heap.
#[derive(Clone, Copy, PartialEq)]
struct Item {
    dist: f32,
    id: u32,
}

impl Eq for Item {}

impl Ord for Item {
    fn cmp(&self, other: &Self) -> Ordering {
        // total_cmp: finite f32 distances, deterministic tie-break on id.
        self.dist
            .total_cmp(&other.dist)
            .then_with(|| self.id.cmp(&other.id))
    }
}

impl PartialOrd for Item {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

/// A hierarchical navigable small-world index over `dim`-length f32 vectors.
pub struct HnswIndex {
    dim: usize,
    params: HnswParams,
    ml: f64, // level-generation normalization factor 1/ln(M)
    // Flat vector storage, row-major: vectors[id*dim .. (id+1)*dim].
    vectors: Vec<f32>,
    // Per-node adjacency: neighbors[id][layer] = neighbor ids of `id` at `layer`.
    neighbors: Vec<Vec<Vec<u32>>>,
    entry_point: Option<u32>,
    max_level: usize,
    rng: SplitMix64,
}

impl HnswIndex {
    /// Create an empty index for `dim`-dimensional vectors.
    pub fn new(dim: usize, params: HnswParams) -> Self {
        assert!(dim > 0, "dim must be > 0");
        assert!(params.m >= 2, "m must be >= 2");
        let ml = 1.0 / (params.m as f64).ln();
        Self {
            dim,
            params,
            ml,
            vectors: Vec::new(),
            neighbors: Vec::new(),
            entry_point: None,
            max_level: 0,
            rng: SplitMix64::new(params.seed),
        }
    }

    pub fn dim(&self) -> usize {
        self.dim
    }

    pub fn len(&self) -> usize {
        self.neighbors.len()
    }

    pub fn is_empty(&self) -> bool {
        self.neighbors.is_empty()
    }

    #[inline]
    fn vec_at(&self, id: u32) -> &[f32] {
        let start = id as usize * self.dim;
        &self.vectors[start..start + self.dim]
    }

    /// HNSW navigation distance: negative inner product (lower = nearer).
    #[inline]
    fn dist(&self, q: &[f32], id: u32) -> f32 {
        let x = self.vec_at(id);
        let mut acc = 0.0f32;
        for i in 0..self.dim {
            acc += q[i] * x[i];
        }
        -acc
    }

    fn m_max(&self, layer: usize) -> usize {
        if layer == 0 {
            self.params.m * 2
        } else {
            self.params.m
        }
    }

    fn random_level(&mut self) -> usize {
        let r = self.rng.next_unit();
        (-r.ln() * self.ml).floor() as usize
    }

    /// Add one vector, returning its assigned id (`0..N`). `vec.len()` must equal
    /// `dim`. Vectors are stored verbatim (no normalization) to match FAISS
    /// `IndexFlatIP` — normalize upstream if you want cosine.
    pub fn add(&mut self, vec: &[f32]) -> u32 {
        assert_eq!(vec.len(), self.dim, "vector dim mismatch");
        let id = self.neighbors.len() as u32;
        self.vectors.extend_from_slice(vec);

        let level = self.random_level();
        // Allocate empty adjacency for every layer this node lives on.
        let mut adj: Vec<Vec<u32>> = Vec::with_capacity(level + 1);
        for _ in 0..=level {
            adj.push(Vec::new());
        }
        self.neighbors.push(adj);

        let ep = match self.entry_point {
            None => {
                // First node: it is the entry point, nothing to link.
                self.entry_point = Some(id);
                self.max_level = level;
                return id;
            }
            Some(ep) => ep,
        };

        // Copy q into an owned buffer so we don't hold an immutable borrow of
        // self.vectors while mutating adjacency below.
        let q: Vec<f32> = self.vec_at(id).to_vec();

        // Phase 1: greedy descent from the top down to level+1 with ef=1.
        let mut cur = ep;
        let mut cur_dist = self.dist(&q, cur);
        let top = self.max_level;
        let mut lc = top;
        while lc > level {
            let mut changed = true;
            while changed {
                changed = false;
                // `cur` may not exist on layer lc if it was the entry from a
                // higher layer; guard the lookup.
                let cand = self.neighbors_at(cur, lc);
                for &e in cand {
                    let d = self.dist(&q, e);
                    if d < cur_dist {
                        cur_dist = d;
                        cur = e;
                        changed = true;
                    }
                }
            }
            lc -= 1;
        }

        // Phase 2: from min(level, top) down to 0, search with ef_construction,
        // select neighbors, and wire bidirectional links.
        let mut entry_points = vec![cur];
        let start = level.min(top);
        for lc in (0..=start).rev() {
            let w = self.search_layer(&q, &entry_points, self.params.ef_construction, lc);
            // Candidates as (dist, id), nearest first.
            let mut candidates: Vec<Item> = w;
            candidates.sort_unstable();
            let m = self.m_max(lc);
            let selected = self.select_neighbors_heuristic(&candidates, m);

            // Link id -> selected.
            self.neighbors[id as usize][lc] = selected.clone();
            // Link selected -> id, pruning each back-neighbor to its m_max.
            for &nb in &selected {
                self.connect_and_prune(nb, id, lc);
            }
            // Entry points for the next lower layer = this layer's found set.
            entry_points = candidates.iter().map(|it| it.id).collect();
            if entry_points.is_empty() {
                entry_points = vec![cur];
            }
        }

        if level > self.max_level {
            self.max_level = level;
            self.entry_point = Some(id);
        }
        id
    }

    /// Neighbors of `id` at `layer`, or an empty slice if `id` doesn't reach
    /// that layer.
    #[inline]
    fn neighbors_at(&self, id: u32, layer: usize) -> &[u32] {
        let node = &self.neighbors[id as usize];
        if layer < node.len() {
            &node[layer]
        } else {
            &[]
        }
    }

    /// Add `new` to `node`'s adjacency at `layer`, then prune to `m_max` keeping
    /// the closest neighbors (by the heuristic).
    fn connect_and_prune(&mut self, node: u32, new: u32, layer: usize) {
        // Ensure `node` actually reaches `layer` (it must, since it was found by
        // search_layer at `layer`), then append.
        self.neighbors[node as usize][layer].push(new);
        let m = self.m_max(layer);
        if self.neighbors[node as usize][layer].len() <= m {
            return;
        }
        // Rebuild the neighbor list via the selection heuristic around `node`.
        let base: Vec<f32> = self.vec_at(node).to_vec();
        let existing: Vec<u32> = self.neighbors[node as usize][layer].clone();
        let mut cands: Vec<Item> = existing
            .iter()
            .map(|&e| Item {
                dist: self.dist(&base, e),
                id: e,
            })
            .collect();
        cands.sort_unstable();
        let pruned = self.select_neighbors_heuristic(&cands, m);
        self.neighbors[node as usize][layer] = pruned;
    }

    /// HNSW SELECT-NEIGHBORS-HEURISTIC (Algorithm 4, no extendCandidates /
    /// keepPrunedConnections). `candidates` must be sorted ascending by distance
    /// to the query. Keeps an edge only when the candidate is closer to the
    /// query than to every already-selected neighbor — spreading edges across
    /// directions for better graph reachability than plain top-M.
    fn select_neighbors_heuristic(&self, candidates: &[Item], m: usize) -> Vec<u32> {
        let mut result: Vec<u32> = Vec::with_capacity(m);
        for cand in candidates {
            if result.len() >= m {
                break;
            }
            let cvec: &[f32] = self.vec_at(cand.id);
            let mut keep = true;
            for &r in &result {
                // dist(cand, r) < dist(cand, query) => r "dominates" cand.
                let d_cr = self.dist(cvec, r);
                if d_cr < cand.dist {
                    keep = false;
                    break;
                }
            }
            if keep {
                result.push(cand.id);
            }
        }
        result
    }

    /// HNSW SEARCH-LAYER: greedily explore `layer` from `entry_points`, keeping
    /// the `ef` nearest to `q`. Returns those `ef` (or fewer) as `Item`s.
    fn search_layer(&self, q: &[f32], entry_points: &[u32], ef: usize, layer: usize) -> Vec<Item> {
        let ef = ef.max(1);
        let n = self.len();
        let mut visited = vec![false; n];
        // candidates: min-heap (nearest first) via Reverse.
        let mut candidates: BinaryHeap<std::cmp::Reverse<Item>> = BinaryHeap::new();
        // w: max-heap (farthest first), holds the current best `ef`.
        let mut w: BinaryHeap<Item> = BinaryHeap::new();

        for &ep in entry_points {
            if (ep as usize) < n && !visited[ep as usize] {
                visited[ep as usize] = true;
                let d = self.dist(q, ep);
                let it = Item { dist: d, id: ep };
                candidates.push(std::cmp::Reverse(it));
                w.push(it);
            }
        }
        while w.len() > ef {
            w.pop();
        }

        while let Some(std::cmp::Reverse(c)) = candidates.pop() {
            // If the nearest candidate is farther than the current farthest kept,
            // no unexplored node can improve W — stop.
            let farthest = w.peek().map(|it| it.dist).unwrap_or(f32::INFINITY);
            if c.dist > farthest && w.len() >= ef {
                break;
            }
            for &e in self.neighbors_at(c.id, layer) {
                if visited[e as usize] {
                    continue;
                }
                visited[e as usize] = true;
                let d = self.dist(q, e);
                let farthest = w.peek().map(|it| it.dist).unwrap_or(f32::INFINITY);
                if d < farthest || w.len() < ef {
                    let it = Item { dist: d, id: e };
                    candidates.push(std::cmp::Reverse(it));
                    w.push(it);
                    if w.len() > ef {
                        w.pop();
                    }
                }
            }
        }

        w.into_vec()
    }

    /// Search for the `k` nearest neighbors of `query`, returned as
    /// `(id, inner_product)` sorted by descending inner product (FAISS
    /// `IndexFlatIP` order). Fewer than `k` results are returned only when the
    /// index holds fewer than `k` vectors.
    pub fn search(&self, query: &[f32], k: usize) -> Vec<(u32, f32)> {
        assert_eq!(query.len(), self.dim, "query dim mismatch");
        if self.is_empty() || k == 0 {
            return Vec::new();
        }
        let ep = self
            .entry_point
            .expect("non-empty index has an entry point");
        // ef is bounded by the corpus size, so small indexes are searched
        // exactly (recall 1.0) — the fallback-parity regime.
        let ef = self.params.ef_search.max(k).min(self.len());

        // Descend the upper layers with ef=1.
        let mut cur = ep;
        let mut cur_dist = self.dist(query, cur);
        let mut lc = self.max_level;
        while lc > 0 {
            let mut changed = true;
            while changed {
                changed = false;
                for &e in self.neighbors_at(cur, lc) {
                    let d = self.dist(query, e);
                    if d < cur_dist {
                        cur_dist = d;
                        cur = e;
                        changed = true;
                    }
                }
            }
            lc -= 1;
        }

        // Layer 0: full ef search.
        let found = self.search_layer(query, &[cur], ef, 0);
        // Sort by ascending nav-distance (= descending inner product), tie-break
        // on id for determinism, then take k and flip the sign back to raw IP.
        let mut items: Vec<Item> = found;
        items.sort_unstable();
        items.truncate(k);
        items.into_iter().map(|it| (it.id, -it.dist)).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seeded_rng(seed: u64) -> SplitMix64 {
        SplitMix64::new(seed)
    }

    /// Random unit vectors for a `dim`-dimensional corpus.
    fn random_normalized(n: usize, dim: usize, seed: u64) -> Vec<Vec<f32>> {
        let mut rng = seeded_rng(seed);
        let mut out = Vec::with_capacity(n);
        for _ in 0..n {
            let mut v: Vec<f32> = (0..dim)
                .map(|_| (rng.next_unit() as f32) * 2.0 - 1.0)
                .collect();
            let norm: f32 = v.iter().map(|x| x * x).sum::<f32>().sqrt().max(1e-12);
            for x in &mut v {
                *x /= norm;
            }
            out.push(v);
        }
        out
    }

    fn brute_topk(corpus: &[Vec<f32>], q: &[f32], k: usize) -> Vec<u32> {
        let mut scored: Vec<(f32, u32)> = corpus
            .iter()
            .enumerate()
            .map(|(i, x)| {
                let ip: f32 = q.iter().zip(x).map(|(a, b)| a * b).sum();
                (ip, i as u32)
            })
            .collect();
        // Descending IP, tie-break on id ascending (matches Item ordering flip).
        scored.sort_by(|a, b| b.0.total_cmp(&a.0).then_with(|| a.1.cmp(&b.1)));
        scored.into_iter().take(k).map(|(_, i)| i).collect()
    }

    #[test]
    fn empty_index_returns_nothing() {
        let idx = HnswIndex::new(4, HnswParams::default());
        assert!(idx.is_empty());
        assert_eq!(idx.search(&[1.0, 0.0, 0.0, 0.0], 5), Vec::new());
    }

    #[test]
    fn single_vector_is_its_own_neighbor() {
        let mut idx = HnswIndex::new(3, HnswParams::default());
        let id = idx.add(&[1.0, 0.0, 0.0]);
        assert_eq!(id, 0);
        let res = idx.search(&[1.0, 0.0, 0.0], 5);
        assert_eq!(res.len(), 1);
        assert_eq!(res[0].0, 0);
        assert!((res[0].1 - 1.0).abs() < 1e-6);
    }

    #[test]
    fn small_n_is_exact_parity_with_brute_force() {
        // At small N, ef auto-scales to the corpus so search is exhaustive:
        // the neighbor SET must match brute force exactly.
        let dim = 16;
        for &n in &[10usize, 50, 200] {
            let corpus = random_normalized(n, dim, 42 + n as u64);
            let mut idx = HnswIndex::new(dim, HnswParams::default());
            for v in &corpus {
                idx.add(v);
            }
            assert_eq!(idx.len(), n);
            for (qi, q) in corpus.iter().enumerate() {
                let k = 10.min(n);
                let got: Vec<u32> = idx.search(q, k).into_iter().map(|(i, _)| i).collect();
                let want = brute_topk(&corpus, q, k);
                assert_eq!(
                    got, want,
                    "n={n} query={qi}: HNSW top-{k} must equal brute force at small N"
                );
                // The query vector itself is the top hit (IP == 1 on unit vecs).
                assert_eq!(got[0], qi as u32);
            }
        }
    }

    #[test]
    fn high_recall_at_scale() {
        // At larger N, HNSW is approximate — assert recall@10 stays very high.
        let dim = 32;
        let n = 4000;
        let corpus = random_normalized(n, dim, 7);
        let params = HnswParams {
            m: 16,
            ef_construction: 200,
            ef_search: 128,
            seed: 123,
        };
        let mut idx = HnswIndex::new(dim, params);
        for v in &corpus {
            idx.add(v);
        }
        let k = 10;
        let n_queries = 200;
        let mut hits = 0usize;
        let mut total = 0usize;
        for qi in 0..n_queries {
            let q = &corpus[qi * (n / n_queries)];
            let got: std::collections::HashSet<u32> =
                idx.search(q, k).into_iter().map(|(i, _)| i).collect();
            let want = brute_topk(&corpus, q, k);
            for w in &want {
                total += 1;
                if got.contains(w) {
                    hits += 1;
                }
            }
        }
        let recall = hits as f64 / total as f64;
        assert!(recall >= 0.95, "recall@{k} = {recall:.4} < 0.95 at n={n}");
    }

    #[test]
    fn scores_are_descending_inner_product() {
        let dim = 8;
        let corpus = random_normalized(100, dim, 99);
        let mut idx = HnswIndex::new(dim, HnswParams::default());
        for v in &corpus {
            idx.add(v);
        }
        let res = idx.search(&corpus[0], 10);
        for w in res.windows(2) {
            assert!(
                w[0].1 >= w[1].1 - 1e-6,
                "scores must be non-increasing: {} then {}",
                w[0].1,
                w[1].1
            );
        }
    }

    #[test]
    fn deterministic_across_builds() {
        let dim = 12;
        let corpus = random_normalized(300, dim, 555);
        let build = || {
            let mut idx = HnswIndex::new(dim, HnswParams::default());
            for v in &corpus {
                idx.add(v);
            }
            idx.search(&corpus[3], 10)
        };
        assert_eq!(build(), build(), "same seed + order => identical results");
    }
}
