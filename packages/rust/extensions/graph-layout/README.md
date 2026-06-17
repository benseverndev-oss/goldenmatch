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

# a connected "network galaxy" — 40 communities with weak inter-community links
# (--p-out > 0), colored by planted community; force layout pulls them into islands:
./target/release/graph-layout --clusters 40 --per 250 --p-in 0.05 --p-out 0.00003 \
    --single-level --iters 650 --k 52 --out frames

# stitch the frames into a video:
ffmpeg -framerate 30 -i frames/frame_%05d.ppm -pix_fmt yuv420p layout.mp4
```

Input is a plain edge list (`a b [weight]` per line; `#` comments). Node tokens are
arbitrary strings, remapped internally. For an edge-list input, nodes are colored by
**connected component** (the resolved entity); for the synthetic demo they're colored
by **planted community**, so `--p-out` can add inter-community edges (which would
otherwise fuse the palette into one connected component) and still show structure.

Two honest graph shapes: `--p-out 0` (default) makes communities genuine
disconnected components — a *dedup* match graph, where each entity's noisy records
form a near-clique that collapses to a point (a constellation of resolved entities,
no macro-structure to arrange). `--p-out > 0` adds weak inter-community links — a
*relationship / graph-ER* shape — which is what force layout actually reveals as
separated, glowing community islands.

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

### One-command dogfood + the demo reel

`examples/dogfood_goldenmatch.py` runs the whole chain on the **real engine** —
generate noisy people → `goldenmatch.dedupe_df` → its scored pairs → edge list:

```bash
python examples/dogfood_goldenmatch.py                              # -> dogfood_pairs.csv
python export_graph_layout.py from-pairs dogfood_pairs.csv -o e.tsv # -> edge list
# --single-level = full condensation reel (no coarsening); node radius ∝ cluster size
cargo run --release -- --input e.tsv --single-level --iters 240 --frame-every 1 --out frames
ffmpeg -framerate 30 -i frames/frame_%05d.ppm -pix_fmt yuv420p dogfood.mp4
```

Node radius scales with record count, so heavily-duplicated entities read as big
dots and the long tail as small ones. Pure dedup match graphs are unions of
near-cliques (an entity's noisy records all match each other), which collapse to a
point under force layout — so real ER renders as a *constellation of resolved
entities* (size = record count), not big sparse blobs. The synthetic `--clusters`
demo models sparse *communities* instead (a relationship / graph-ER shape) — both
honest, different graph structures.

## Honest scope

Real-time animation of a full 10M-node graph on CPU isn't the goal here — Barnes-Hut
is ~n·log n *per iteration* and layout needs many iterations, so a full settle is
seconds-to-minutes offline, not 60fps interactive. Multilevel coarsening is what
keeps "many iterations" tractable; for truly huge graphs the honest visualization is
to lay out **block / cluster representatives** (the coarse level) and expand, since
nobody reads 30M individual edges anyway.

This crate is a standalone demo binary — its own workspace, excluded from the default
CI rust build (like `native`).
