//! Barnes-Hut quadtree for O(n log n) repulsion.
//!
//! Built top-down by recursively partitioning an index slice into the four
//! quadrants of a square cell — cache-friendly (children live contiguously in a
//! flat arena `Vec<Cell>`) and the shape you'd later build per-quadrant in
//! parallel. Each internal cell stores the center-of-mass + body count of its
//! subtree; the force walk approximates a whole distant cell as one pseudo-body.

use crate::vec2::V2;

const EPS: f32 = 1e-4;
/// Depth cap so coincident / near-coincident points (which would subdivide
/// forever) collapse into one aggregate pseudo-body instead of recursing.
const MAX_DEPTH: u32 = 40;
const NO_CHILD: u32 = u32::MAX;

struct Cell {
    com: V2,        // center of mass of the subtree
    mass: f32,      // body count
    half: f32,      // half-width of this square cell
    body: i32,      // single-body leaf: body index; otherwise -1
    kids: [u32; 4], // child cell indices into the arena; NO_CHILD = empty
}

impl Cell {
    #[inline]
    fn is_leaf(&self) -> bool {
        self.kids == [NO_CHILD; 4]
    }
}

pub struct BarnesHut {
    cells: Vec<Cell>,
}

impl BarnesHut {
    /// Build a tree over `pos`. O(n) cells, O(n log n) work on well-spread input.
    pub fn build(pos: &[V2]) -> Self {
        let mut cells: Vec<Cell> = Vec::with_capacity(pos.len().saturating_mul(2) + 1);
        if pos.is_empty() {
            return BarnesHut { cells };
        }
        let (center, half) = bbox(pos);
        let mut idx: Vec<u32> = (0..pos.len() as u32).collect();
        build_rec(&mut cells, pos, &mut idx, center, half, 0);
        BarnesHut { cells }
    }

    /// Net repulsive force on body `i` at `pi` (Fruchterman-Reingold form:
    /// magnitude k^2/d per pair, directed away). `theta` is the opening
    /// criterion (cell width / distance); ~0.7-1.0 trades accuracy for speed.
    ///
    /// Iterative DFS over an on-stack array — no recursion, no heap on the hot
    /// path. Read-only against the tree, so callers run this across nodes in
    /// parallel with rayon.
    pub fn repulse(&self, i: usize, pi: V2, k: f32, theta: f32) -> V2 {
        if self.cells.is_empty() {
            return V2::ZERO;
        }
        let theta2 = theta * theta;
        let kk = k * k;
        let mut f = V2::ZERO;
        // Depth <= MAX_DEPTH, branching <= 4 → DFS frontier stays well under this.
        let mut stack = [0u32; 160];
        let mut sp = 1usize;
        while sp > 0 {
            sp -= 1;
            let c = &self.cells[stack[sp] as usize];
            let leaf = c.is_leaf();
            if leaf && c.body == i as i32 {
                continue; // never repel a body from itself
            }
            let d = c.com - pi;
            let dist2 = d.len2() + EPS;
            let width = 2.0 * c.half;
            // Treat the cell as one body when it's a leaf, or far enough that
            // (width / dist) < theta  <=>  width^2 < theta^2 * dist^2.
            if leaf || width * width < theta2 * dist2 {
                let mag = kk * c.mass / dist2; // k^2 * mass / d^2, away from com
                f = f - d.scale(mag);
            } else {
                for &ch in &c.kids {
                    if ch != NO_CHILD && sp < stack.len() {
                        stack[sp] = ch;
                        sp += 1;
                    }
                }
            }
        }
        f
    }
}

fn build_rec(
    cells: &mut Vec<Cell>,
    pos: &[V2],
    idx: &mut [u32],
    center: V2,
    half: f32,
    depth: u32,
) -> u32 {
    let me = cells.len() as u32;
    cells.push(Cell {
        com: V2::ZERO,
        mass: 0.0,
        half,
        body: -1,
        kids: [NO_CHILD; 4],
    });

    if idx.len() == 1 {
        let b = idx[0];
        let cell = &mut cells[me as usize];
        cell.com = pos[b as usize];
        cell.mass = 1.0;
        cell.body = b as i32;
        return me;
    }

    if depth >= MAX_DEPTH {
        // Coincident-ish cluster: aggregate into a single leaf pseudo-body.
        let mut com = V2::ZERO;
        for &b in idx.iter() {
            com = com + pos[b as usize];
        }
        let n = idx.len() as f32;
        let cell = &mut cells[me as usize];
        cell.com = com.scale(1.0 / n);
        cell.mass = n; // stays a leaf (kids all NO_CHILD)
        return me;
    }

    // 4-way partition by quadrant. (Clarity version: per-quadrant Vecs. The perf
    // version is an in-place Morton/radix partition with no per-cell alloc.)
    let mut q: [Vec<u32>; 4] = [Vec::new(), Vec::new(), Vec::new(), Vec::new()];
    for &b in idx.iter() {
        let p = pos[b as usize];
        let k = (p.x >= center.x) as usize | (((p.y >= center.y) as usize) << 1);
        q[k].push(b);
    }

    let h = half * 0.5;
    let mut com = V2::ZERO;
    let mut mass = 0.0f32;
    let mut kids = [NO_CHILD; 4];
    for (k, bucket) in q.iter_mut().enumerate() {
        if bucket.is_empty() {
            continue;
        }
        let nc = V2::new(
            center.x + if k & 1 == 1 { h } else { -h },
            center.y + if k & 2 == 2 { h } else { -h },
        );
        let ci = build_rec(cells, pos, bucket, nc, h, depth + 1);
        kids[k] = ci;
        let kid = &cells[ci as usize];
        com = com + kid.com.scale(kid.mass);
        mass += kid.mass;
    }

    let cell = &mut cells[me as usize];
    cell.kids = kids;
    cell.mass = mass;
    cell.com = com.scale(1.0 / mass.max(EPS));
    me
}

/// Square bounding box (center + half-width) covering all points, lightly padded.
fn bbox(pos: &[V2]) -> (V2, f32) {
    let (mut lo, mut hi) = (pos[0], pos[0]);
    for &p in pos {
        lo.x = lo.x.min(p.x);
        lo.y = lo.y.min(p.y);
        hi.x = hi.x.max(p.x);
        hi.y = hi.y.max(p.y);
    }
    let center = V2::new((lo.x + hi.x) * 0.5, (lo.y + hi.y) * 0.5);
    let half = (((hi.x - lo.x).max(hi.y - lo.y)) * 0.5).max(1.0) * 1.05;
    (center, half)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Barnes-Hut must approximate the exact O(n^2) repulsion. At theta=0 it
    /// degenerates to exact (every cell is opened to leaves), so the two agree to
    /// float tolerance; at theta=0.7 it stays close.
    #[test]
    fn approximates_exact_repulsion() {
        let pos = vec![
            V2::new(0.0, 0.0),
            V2::new(10.0, 0.0),
            V2::new(0.0, 10.0),
            V2::new(8.0, 9.0),
            V2::new(-5.0, 3.0),
            V2::new(20.0, -4.0),
        ];
        let k = 4.0;
        let exact = |i: usize| -> V2 {
            let mut f = V2::ZERO;
            for (j, &pj) in pos.iter().enumerate() {
                if j == i {
                    continue;
                }
                let d = pj - pos[i];
                let dist2 = d.len2() + EPS;
                f = f - d.scale(k * k / dist2);
            }
            f
        };
        let tree0 = BarnesHut::build(&pos);
        for i in 0..pos.len() {
            let bh = tree0.repulse(i, pos[i], k, 0.0); // theta=0 → exact
            let ex = exact(i);
            assert!((bh.x - ex.x).abs() < 1e-2, "x mismatch at {i}");
            assert!((bh.y - ex.y).abs() < 1e-2, "y mismatch at {i}");
        }
    }

    #[test]
    fn self_force_is_skipped() {
        let pos = vec![V2::new(1.0, 1.0)];
        let t = BarnesHut::build(&pos);
        let f = t.repulse(0, pos[0], 5.0, 0.8);
        assert_eq!(f, V2::ZERO);
    }
}
