# goldenmatch-graph-layout

CPU-only force-directed layout of entity-resolution graphs, rendered to frames —
watch resolved clusters condense, iteration by iteration.

The honest framing: on CPU the "performance" part of an ER visual isn't the
pixel-pushing — it's the **layout solver**. Drawing nodes and edges to a buffer is
cheap; computing where a million nodes go is an n-body simulation. So the
interesting work here is the algorithm, not the renderer.

## What's in the box

| Piece | File | Why |
|---|---|---|
| **Barnes-Hut** repulsion | `quadtree.rs` | O(n log n) instead of naive O(n²) all-pairs. A quadtree *is* spatial blocking — partition 2D space to avoid all-pairs comparison, the same instinct as partitioning record space to avoid all-pairs scoring. Repulsion is read-only per node → rayon-parallel. |
| **Multilevel coarsening** | `coarsen.rs` | The lever that actually makes large graphs settle. Heavy-edge matching builds a pyramid; lay out the tiny top, interpolate down, refine a few iterations per level. goldenmatch's **blocking is itself a coarsening level**. |
| Force step + driver | `layout.rs` | FR attraction/repulsion + cooling; rayon across nodes. |
| Rasterizer | `raster.rs` | Dependency-free anti-aliased PPM. `--features skia` swaps in tiny-skia for PNG. |

## Run it

```bash
cd packages/rust/extensions/graph-layout
cargo build --release

# synthetic demo (no input needed) — 8 clusters of 250 nodes:
./target/release/graph-layout --clusters 8 --per 250 --out frames

# stitch the frames into a video:
ffmpeg -framerate 30 -i frames/frame_%05d.ppm -pix_fmt yuv420p layout.mp4
```

Input is a plain edge list (`a b [weight]` per line; `#` comments). Node tokens are
arbitrary strings, remapped internally. Nodes are colored by **connected component**,
i.e. resolved entity.

## On real goldenmatch output

`export_graph_layout.py` (stdlib only) turns goldenmatch output into the edge list:

```bash
# from a persistent identity graph (.goldenmatch/identity.db) — the durable
# resolved-entity output; edges are evidence_edges between source records:
python export_graph_layout.py from-identity .goldenmatch/identity.db -o edges.tsv
./target/release/graph-layout --input edges.tsv --out frames

# or from any scored-pair CSV (e.g. exported dedupe pair scores):
python export_graph_layout.py from-pairs pairs.csv --a id_a --b id_b --score score -o edges.tsv
```

Connected components of the thresholded match graph **are** the resolved entities,
so the colored blobs you see condensing are the clusters goldenmatch produced.

## Honest scope

Real-time animation of a full 10M-node graph on CPU isn't the goal here — Barnes-Hut
is ~n·log n *per iteration* and layout needs many iterations, so a full settle is
seconds-to-minutes offline, not 60fps interactive. Multilevel coarsening is what
keeps "many iterations" tractable; for truly huge graphs the honest visualization is
to lay out **block / cluster representatives** (the coarse level) and expand, since
nobody reads 30M individual edges anyway.

This crate is a standalone demo binary — its own workspace, excluded from the default
CI rust build (like `native`).
