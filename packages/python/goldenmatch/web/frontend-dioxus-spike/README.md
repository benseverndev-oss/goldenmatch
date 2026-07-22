# gm-echarts-spike

A de-risking spike for the proposed **Rust/WASM (Dioxus) rewrite of GoldenMatch's
web UI**, driving **Apache ECharts from Rust** via the [`charming`](https://github.com/yuankunzhang/charming)
crate.

It answers one question before anyone commits to porting 22 components:
**does a chart spec built in Rust render as real, interactive ECharts, from
GoldenMatch's actual API data — and what do the ergonomics look like?**

Answer: yes. `src/chart.rs` builds one `charming::Chart` from the real
`/api/v1/sensitivity` response shape, and it renders on two surfaces from that
single spec:

- **`render` binary** (native, builds & runs anywhere) → a self-contained
  interactive ECharts HTML page.
- **`SensitivityView` Dioxus component** (`src/dioxus_app.rs`) → mounts the
  *same* `charming::Chart` into a live DOM node via `WasmRenderer`.

This is a like-for-like upgrade of `web/frontend/src/routes/Sensitivity.tsx`,
whose chart today is a hand-rolled `<svg>` sparkline (cluster count only). The
ECharts version adds, for free, what SVG can't cheaply do: an axis-triggered
tooltip, a toggleable legend, a second y-axis (TWI), and a dataZoom brush.

## Run it now (native, no wasm toolchain needed)

```bash
cd packages/python/goldenmatch/web/frontend-dioxus-spike
cargo run --bin render                    # uses the baked sample_response.json
open sensitivity.html                     # interactive ECharts page

# ...or feed it a real API response:
cargo run --bin render -- response.json
curl -s -XPOST localhost:5050/api/v1/sensitivity -d @req.json | cargo run --bin render -- -
```

> The generated page loads `echarts.min.js` from a CDN (charming's default
> `HtmlRenderer` behaviour), so *viewing* it needs network. A shipped GM UI
> would vendor `echarts.min.js` locally instead — a one-line renderer swap.

## Run the real Dioxus web build (the migration target)

Not buildable in the CI sandbox (no `wasm32` target / no `dx`). On a dev box:

```bash
rustup target add wasm32-unknown-unknown
cargo install dioxus-cli
dx serve --features web        # serves the WASM app; proxy /api to FastAPI
```

`src/dioxus_app.rs` is the faithful port of the React route: a `use_resource`
fetch (≙ TanStack `useQuery`), the ECharts mount, and the detail table.

## What the spike deliberately shows

- **One chart spec, two surfaces** (`src/chart.rs`) — no per-surface chart code.
  The native binary and the Dioxus component call the identical function.
- **`lib/types.ts` → serde** (`src/model.rs`) — deserializes the unchanged wire
  contract. The FastAPI backend and `/api/v1/sensitivity` are untouched; this is
  a *frontend-only* swap.
- **`lib/api.ts` → gloo-net** (`fetch_sensitivity` in `dioxus_app.rs`).

## Honest caveats (the ergonomics notes)

- **Data grids are the thin spot.** Rust/WASM has no first-class TanStack
  Table/Virtual equivalent. For the row counts GM's API returns (server-capped —
  `identity` ≤500, `runs` cursor-paginated, `match` `ROW_CAP`), a plain element
  loop like `DetailTable` is fine. A large *client-side* grid would need
  hand-rolled windowing. This is latent today, not blocking.
- **`charming` ≈ ECharts option builder, not 100% coverage.** It models the
  common option surface well; an exotic option occasionally needs a raw-JSON
  escape hatch. The sensitivity chart hit no such gap.
- **Bundle size.** A WASM app ships a larger initial payload than the React
  bundle. Irrelevant for a locally-served dev tool; worth a look only if this UI
  is ever hosted for remote users.

## Isolation

Standalone cargo workspace (own empty `[workspace]`), and **not** a
pnpm/uv/turbo member (the pnpm glob is the exact path `.../web/frontend`; this
sibling does not match). Nothing in the monorepo's CI picks it up. It touches no
existing file — purely additive.
