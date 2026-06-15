//! goldenmatch-graph-layout — CPU-only force-directed layout of entity-resolution
//! graphs, rendered to frames.
//!
//! The interesting part is the algorithm, not the renderer:
//!
//! - [`quadtree`] — Barnes-Hut: an O(n log n) approximation of all-pairs
//!   repulsion via a center-of-mass quadtree. (A quadtree IS spatial blocking:
//!   partition 2D space to avoid all-pairs comparison — the same instinct as
//!   partitioning record space to avoid all-pairs scoring.)
//! - [`coarsen`] — multilevel heavy-edge matching. This is what actually makes
//!   large graphs settle: lay out a tiny coarsened graph, then interpolate +
//!   refine down the hierarchy, so each level starts near-solved and needs only a
//!   few iterations. goldenmatch's blocking is itself a coarsening level.
//! - [`layout`] — the force step (rayon-parallel repulsion + per-edge attraction
//!   + cooling) and the multilevel driver.
//! - [`raster`] — a dependency-free anti-aliased PPM rasterizer (the layout, not
//!   the pixels, is the perf story). `--features skia` swaps in tiny-skia.
pub mod coarsen;
pub mod graph;
pub mod layout;
pub mod quadtree;
pub mod raster;
pub mod rng;
pub mod vec2;
