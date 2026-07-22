# gm-echarts-spike

A de-risking spike for the proposed **Rust/WASM (Dioxus) rewrite of GoldenMatch's
web UI**, driving **Apache ECharts from Rust** via the [`charming`](https://github.com/yuankunzhang/charming)
crate.

It now goes past "can Rust draw an ECharts chart" to **one full route ported
end-to-end** — `web/frontend/src/routes/Sensitivity.tsx` rebuilt in Dioxus,
form and grid included — so the real cost of the rewrite is visible, not guessed.

`src/chart.rs` builds one `charming::Chart` from the real `/api/v1/sensitivity`
response shape, and it renders on two surfaces from that single spec:

- **`render` binary** (native) → a self-contained interactive ECharts HTML page.
- **`web` binary** (`src/dioxus_app.rs`, Dioxus/WASM) → the full Sensitivity
  route: parameter form, loading/error/empty states, the ECharts chart via
  `WasmRenderer`, the stability panel, and a **sortable** detail grid.

## Verified builds (in the CI sandbox, real toolchain)

Both targets compile from a clean checkout after `rustup target add
wasm32-unknown-unknown`:

| build | command | result |
|---|---|---|
| native binary | `cargo build --bin render` | ✅ builds + runs (emits `sensitivity.html`) |
| wasm web app | `cargo build --target wasm32-unknown-unknown --features web --bin web` | ✅ builds (Dioxus 0.6 + charming/wasm + gloo-net) |

**Bundle size** (measured on this crate):

| stage | size |
|---|---|
| release `web.wasm`, raw (pre-`wasm-bindgen`) | 2.4 MB / 617 KB gzipped |
| after `wasm-bindgen` (`--target web`) | 1.4 MB / **397 KB gzipped** wasm + 11 KB gzipped JS glue |

So the real shipped payload is **~408 KB gzipped**, and `wasm-opt` (which
`dx build --release` also runs) trims it further. Fine for a locally-served dev
tool; a number to watch only if this UI is ever hosted for remote users. For
reference, the current React bundle is a comparable order of magnitude.

## Run it now (native ECharts page, no wasm needed)

```bash
cargo run --bin render                    # uses the baked sample_response.json
open sensitivity.html                     # interactive ECharts page
cargo run --bin render -- response.json   # ...or a real saved API response
```

> The generated page loads `echarts.min.js` from a CDN (charming's default
> `HtmlRenderer` behaviour). A shipped GM UI would vendor it locally — a one-line
> renderer swap.

## Knowledge graph view (the Identity Graph)

The second chart spec (`src/graph.rs`) proves the **`graph` (force-directed
network) series** — GoldenMatch's **Identity Graph** rendered as an interactive
knowledge graph. It answers the same question the line chart did, for the graph
surface: does a real KG built in Rust from the actual API data render as
interactive ECharts? Yes.

One resolved identity (the `/api/v1/identities/{entity_id}` response) becomes:

- an **entity node** at the hub,
- one **record node** per source record — colored by provenance (a
  legend-toggleable category per `source`),
- **member** links from the entity to its records, and
- the **evidence edges** (`same_as` / `possible_same_as` / `conflicts_with`)
  between records, weighted by score.

Any record touched by a `conflicts_with` edge is promoted to a distinct red
**"⚠ conflict"** category, so an over-merge pops out of the graph at a glance.
Force layout, zoom/drag (`roam`), category legend, and item tooltips are all free
from ECharts once the option is built.

```bash
cargo run --bin render_graph                 # baked sample_identity_graph.json
open identity_graph.html                      # interactive force-directed KG
cargo run --bin render_graph -- identity.json # ...or a real /identities/{id} response
```

Same single-spec / two-surface story as the line chart: `identity_graph_chart()`
is the exact `charming::Chart` the Dioxus `WasmRenderer` would mount — no
KG-drawing code duplicated per surface. The data source already exists (the
Identity Graph REST endpoints — `/identities`, `/identities/{id}/evidence`,
`/conflicts`), so this is a *visualization* layer, not new backend work.

### Whole-graph mode (`resolved_graph_chart`)

A single entity is a small star; the compelling view is the **whole resolved
dataset as one network** — every record a node, evidence edges connecting them,
so the cluster structure and cross-source stitching are visible at a glance.
Pass `{"entities": [<IdentityView>, ...]}` (the full identity store) and
`render_graph` auto-detects it and renders `resolved_graph_chart`: records
colored by source (legend-toggleable), sized by cluster size, conflict records
red, no per-node labels (tooltip on hover) so hundreds of nodes stay readable.

```bash
# real end-to-end: dedupe a messy multi-source dataset, dump the whole graph:
python scratchpad/kg_big.py /tmp/kg_resolved.json
cargo run --bin render_graph -- /tmp/kg_resolved.json   # 472 records -> 180 entities
```

> **charming caveat (edge styling).** charming 0.5's `GraphLink` exposes only
> `source`/`target`/`value` — no per-edge `lineStyle`/`label`. So edge KIND is
> surfaced via the conflict-node coloring + the tooltip rather than per-edge
> color. A production build would use a newer charming (or a raw-JSON escape
> hatch for the `links` array) to color `same_as` vs `possible_same_as` vs
> `conflicts_with` edges individually — a coverage gap, not a blocker.

Natural follow-ons if this direction is taken: expand the neighborhood on node
click (the `/identities/{id}` + `/by-record` endpoints already support it), and a
multi-entity view fed by `/conflicts` to show over-merge candidates across
entities.

## Run the full Dioxus web app

```bash
rustup target add wasm32-unknown-unknown
cargo install dioxus-cli --version 0.6.3
dx serve --features web        # serves the WASM app; proxy /api -> FastAPI
```

## What the spike shows

- **One chart spec, two surfaces** (`src/chart.rs`; and `src/graph.rs` for the
  knowledge-graph view) — no per-surface chart code. The native binary and the
  Dioxus component call the identical function.
- **Full route parity** (`src/dioxus_app.rs`) — the `Sensitivity.tsx` port is
  feature-complete: form inputs (`use_signal`), a `useMutation`-style async
  submit (`spawn` + lifecycle signals), all render states, and the grid.
- **`lib/types.ts` → serde** (`src/model.rs`), **`lib/api.ts` → gloo-net**
  (`fetch_sensitivity`). The FastAPI backend and `/api/v1/sensitivity` are
  untouched — this is a *frontend-only* swap.

## The grid, priced honestly

The React detail table is static; this port makes it **sortable by column** on
purpose, because that is where the real cost lives. In React those grids use
TanStack Table, which gives sort/filter/paginate/virtualize for free. Rust/WASM
has no first-class equivalent, so the port hand-rolls it: `SortState`, a `Col`
enum mapping each column to a sort key, header click handlers, and the
comparator (`DetailGrid` in `dioxus_app.rs`). For Sensitivity's 7-column table
that's ~40 lines and trivial.

**The extrapolation:** the heavy GM grids (`ClusterTable`, `RunInspector`) lean
on TanStack Table for multi-column sort, filtering, and — if client-side row
counts ever grow past the server caps — virtualization. Each of those is
hand-rolled work in Dioxus at roughly the shape shown here. That, not the charts,
is the dominant line item in a full port. Charts are solved; grids are the
budget.

## Other caveats

- **`charming` ≈ ECharts option builder, not 100% coverage.** It models the
  common option surface well; an exotic option occasionally needs a raw-JSON
  escape hatch. The sensitivity chart hit no such gap.
- **Chart mount timing.** `WasmRenderer` mounts into a DOM node by id via a
  `use_effect` that re-runs when the result changes; the target `div` must be
  rendered first. Works cleanly here; a busier route may want an explicit mount
  guard.

## Isolation

Standalone cargo workspace (own empty `[workspace]`), and **not** a
pnpm/uv/turbo member (the pnpm glob is the exact path `.../web/frontend`; this
sibling does not match). Nothing in the monorepo's CI picks it up. It touches no
existing file — purely additive.
