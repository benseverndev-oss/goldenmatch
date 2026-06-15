//! The force step + the multilevel driver.

use rayon::prelude::*;

use crate::coarsen::coarsen;
use crate::graph::Graph;
use crate::quadtree::BarnesHut;
use crate::rng::Rng;
use crate::vec2::V2;

const EPS: f32 = 1e-4;

/// Tunables. `k` is the ideal edge length (the natural scale); everything else
/// derives from it.
#[derive(Clone, Copy)]
pub struct Params {
    pub k: f32,
    pub theta: f32,
    pub iters_coarse: u32,
    pub iters_fine: u32,
    /// Stop coarsening once a level has <= this many nodes.
    pub coarsest: usize,
    pub seed: u64,
}

impl Default for Params {
    fn default() -> Self {
        Params {
            k: 30.0,
            theta: 0.8,
            iters_coarse: 120,
            iters_fine: 60,
            coarsest: 50,
            seed: 0x9E3779B97F4A7C15,
        }
    }
}

/// One Fruchterman-Reingold iteration with Barnes-Hut repulsion.
///
/// Repulsion is per-node independent and read-only against the tree → rayon. The
/// per-edge attraction is a scatter, so it runs sequentially over the (typically
/// far smaller) edge set after the parallel reduction.
pub fn step(g: &Graph, pos: &mut [V2], k: f32, theta: f32, temp: f32) {
    let tree = BarnesHut::build(pos);

    // --- repulsion: O(n log n), parallel across nodes ---
    let mut forces: Vec<V2> = pos
        .par_iter()
        .enumerate()
        .map(|(i, &p)| tree.repulse(i, p, k, theta))
        .collect();

    // --- attraction along edges: F = (d^2 / k) * weight, toward each other ---
    for &(a, b, w) in &g.edges {
        let (a, b) = (a as usize, b as usize);
        let d = pos[b].sub(pos[a]);
        let dist = d.len() + EPS;
        let mag = (dist / k) * w; // (d^2/k)/d * w, as a unit-vector scale
        forces[a] = forces[a].add(d.scale(mag));
        forces[b] = forces[b].sub(d.scale(mag));
    }

    // --- integrate with a temperature cap on displacement (cooling) ---
    for (p, f) in pos.iter_mut().zip(&forces) {
        let len = f.len() + EPS;
        let scale = len.min(temp) / len;
        *p = p.add(f.scale(scale));
    }
}

/// Max per-iteration displacement, decaying over a level's run (simulated
/// annealing): large early moves, fine late adjustments.
fn cooling(k: f32, it: u32, iters: u32) -> f32 {
    let t = 1.0 - (it as f32) / (iters.max(1) as f32);
    k * (0.04 + 0.30 * t)
}

/// Multilevel force-directed layout. Builds a coarsening hierarchy, lays out the
/// coarsest level from a random seed, then projects down and refines each level.
///
/// `on_frame(positions, frame_index)` is called once per iteration of the
/// **finest** level — the visually interesting "clusters condensing" pass. (Coarse
/// levels solve without emitting; their projection jumps would just read as
/// flicker.)
pub fn run(g: &Graph, p: &Params, mut on_frame: impl FnMut(&[V2], u32)) -> Vec<V2> {
    // Build the hierarchy: levels[0] = g (finest) … levels[top] = coarsest.
    // maps[l] projects level l onto level l+1.
    let mut levels: Vec<Graph> = vec![g.clone()];
    let mut maps: Vec<Vec<u32>> = Vec::new();
    loop {
        let cur = levels.last().unwrap();
        if cur.n <= p.coarsest {
            break;
        }
        let (coarse, map) = coarsen(cur);
        if coarse.n >= cur.n {
            break; // no progress (e.g. edgeless) → stop
        }
        maps.push(map);
        levels.push(coarse);
    }

    let top = levels.len() - 1;
    let mut rng = Rng::new(p.seed);
    let mut pos: Vec<V2> = Vec::new();
    let mut frame = 0u32;

    for l in (0..=top).rev() {
        let lvl = &levels[l];
        if l == top {
            // Seed the coarsest level in a disk of radius ~ k*sqrt(n).
            let r = p.k * (lvl.n.max(1) as f32).sqrt();
            pos = (0..lvl.n)
                .map(|_| V2::new(rng.unit() * r, rng.unit() * r))
                .collect();
        } else {
            // Interpolate fine positions from the coarser level just solved, with
            // a touch of jitter so collapsed pairs separate.
            let prev = pos;
            let map = &maps[l];
            pos = (0..lvl.n)
                .map(|i| {
                    prev[map[i] as usize]
                        .add(V2::new(rng.unit() * p.k * 0.15, rng.unit() * p.k * 0.15))
                })
                .collect();
        }

        // The finest/only level always gets the full refinement + frames.
        let iters = if l == top && top > 0 {
            p.iters_coarse
        } else {
            p.iters_fine
        };
        let emit = l == 0;
        for it in 0..iters {
            let temp = cooling(p.k, it, iters);
            step(lvl, &mut pos, p.k, p.theta, temp);
            if emit {
                on_frame(&pos, frame);
                frame += 1;
            }
        }
    }

    pos
}

#[cfg(test)]
mod tests {
    use super::*;

    /// After layout, intra-cluster pairs should sit closer than inter-cluster
    /// pairs — the whole point. Two dense blobs joined by one weak edge.
    #[test]
    fn separates_two_clusters() {
        let g = Graph::synthetic(2, 25, 0.5, 0.0, 7);
        let p = Params {
            iters_coarse: 200,
            iters_fine: 120,
            ..Default::default()
        };
        let pos = run(&g, &p, |_, _| {});
        let centroid = |lo: usize, hi: usize| {
            let mut c = V2::ZERO;
            for i in lo..hi {
                c = c.add(pos[i]);
            }
            c.scale(1.0 / (hi - lo) as f32)
        };
        let c0 = centroid(0, 25);
        let c1 = centroid(25, 50);
        let inter = c1.sub(c0).len();
        // Mean intra-cluster spread of blob 0.
        let mut spread = 0.0;
        for i in 0..25 {
            spread += pos[i].sub(c0).len();
        }
        spread /= 25.0;
        assert!(
            inter > spread * 1.5,
            "clusters not separated: inter={inter} spread={spread}"
        );
    }

    #[test]
    fn finest_level_emits_frames() {
        let g = Graph::synthetic(3, 40, 0.2, 0.01, 1);
        let p = Params {
            iters_fine: 30,
            ..Default::default()
        };
        let mut frames = 0u32;
        let mut last = 0u32;
        run(&g, &p, |_, fidx| {
            frames += 1;
            last = fidx;
        });
        assert_eq!(frames, p.iters_fine, "one frame per finest-level iteration");
        assert_eq!(last, p.iters_fine - 1);
    }
}
