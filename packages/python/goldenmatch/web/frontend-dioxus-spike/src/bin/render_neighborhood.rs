//! Native spike (KG surface, click-to-expand): load the WHOLE resolved graph
//! (`{"entities": [...]}` from the Identity Graph) and render it as a COLLAPSED
//! overview — one hub node per entity — that expands an entity's records +
//! evidence edges (its neighborhood) on click.
//!
//!   cargo run --bin render_neighborhood                 # baked sample
//!   cargo run --bin render_neighborhood -- resolved.json
//!   python scratchpad/kg_big.py /tmp/kg.json && \
//!       cargo run --bin render_neighborhood -- /tmp/kg.json
//!
//! Unlike `render_graph` (which draws every record up front), this scales the
//! initial payload with the ENTITY count, so it stays readable at graph sizes
//! where the whole-graph view is a hairball. Charming's `HtmlRenderer` can't
//! emit the `chart.on('click')` handler this needs, so the page is hand-baked
//! here from the Rust-built `NeighborhoodPayload` + a small vanilla-JS ECharts
//! interaction layer (the raw-ECharts escape hatch the README flags for exactly
//! this follow-on). The data model is still built ONCE in Rust.

use std::io::Read;

use gm_echarts_spike::{
    graph_neighborhood::build_neighborhood_payload, model::ResolvedGraph,
};

const SAMPLE: &str = include_str!("../../sample_identity_graph.json");
const OUT: &str = "identity_neighborhood.html";

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let raw = match std::env::args().nth(1).as_deref() {
        None => SAMPLE.to_string(),
        Some("-") => {
            let mut s = String::new();
            std::io::stdin().read_to_string(&mut s)?;
            s
        }
        Some(path) => std::fs::read_to_string(path)?,
    };

    // Accept either the `{"entities": [...]}` whole-graph wrapper or a single
    // bare identity view (which becomes a one-entity graph).
    let graph: ResolvedGraph = match serde_json::from_str::<ResolvedGraph>(&raw) {
        Ok(g) if !g.entities.is_empty() => g,
        _ => {
            let view = serde_json::from_str(&raw)?;
            ResolvedGraph {
                entities: vec![view],
            }
        }
    };

    let payload = build_neighborhood_payload(&graph);
    let summary = payload.summary.clone();
    let n_hubs = payload.hubs.len();
    let payload_json = js_safe_json(&serde_json::to_string(&payload)?);

    let html = PAGE_TEMPLATE
        .replace("__SUMMARY__", &html_escape(&summary))
        .replace("__PAYLOAD__", &payload_json);
    std::fs::write(OUT, html)?;

    eprintln!("rendered neighborhood view: {n_hubs} entity hubs (collapsed) -> {OUT}");
    eprintln!("  {summary}");
    Ok(())
}

/// Minimal HTML-escape for the summary text injected into the page chrome.
fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

/// Make a serialized-JSON string safe to embed DIRECTLY as a JS value
/// (`const X = <json>;`). JSON is valid JS, so the only hazards are the three
/// sequences that could break OUT of the `<script>` element (`<`, `>`, `&` —
/// e.g. a `</script>` inside a record label) and the two line separators JS
/// treats as newlines. Each is replaced with its `\uXXXX` escape, which parses
/// back to the identical character — the payload is unchanged after load.
fn js_safe_json(json: &str) -> String {
    json.replace('<', "\\u003c")
        .replace('>', "\\u003e")
        .replace('&', "\\u0026")
        .replace('\u{2028}', "\\u2028")
        .replace('\u{2029}', "\\u2029")
}

/// Self-contained page: ECharts from a CDN (charming's own default too — a
/// shipped build would vendor it locally), the baked payload, and the
/// collapse/expand interaction. `__SUMMARY__` / `__PAYLOAD__` are filled above.
const PAGE_TEMPLATE: &str = r#"<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>GoldenMatch - identity neighborhood (ECharts spike)</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
  html, body { margin: 0; height: 100%; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
  #chart { width: 100vw; height: 100vh; }
  #panel {
    position: fixed; top: 10px; left: 12px; right: 12px; z-index: 10;
    display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap;
    pointer-events: none;
  }
  #panel h1 { font-size: 15px; margin: 0; font-weight: 650; }
  #panel .sub { font-size: 12px; color: #555; }
  #panel .status { font-size: 12px; color: #2563eb; font-weight: 600; margin-left: auto; pointer-events: auto; }
  #panel button {
    pointer-events: auto; font-size: 12px; border: 1px solid #cbd5e1; background: #fff;
    border-radius: 6px; padding: 2px 8px; cursor: pointer; color: #334155;
  }
  #panel button:hover { background: #f1f5f9; }
  @media (prefers-color-scheme: dark) {
    #panel h1 { color: #e5e7eb; } #panel .sub { color: #9ca3af; }
    #panel button { background: #1f2937; border-color: #374151; color: #d1d5db; }
    #panel button:hover { background: #374151; }
  }
</style>
</head>
<body>
<div id="panel">
  <h1>Resolved identity graph</h1>
  <span class="sub">__SUMMARY__</span>
  <button id="collapseAll" title="Collapse every expanded entity">Collapse all</button>
  <span class="status" id="status"></span>
</div>
<div id="chart"></div>
<script>
const PAYLOAD = __PAYLOAD__;
const CATS = PAYLOAD.categories.map(function (c) { return { name: c }; });
const chart = echarts.init(document.getElementById('chart'));
const status = document.getElementById('status');
const expanded = new Set();

// Build the current node/link set: always the hubs, plus the records + edges
// of every expanded entity (deduped by node id).
function currentData() {
  const nodes = [];
  const nodeIds = new Set();
  for (const h of PAYLOAD.hubs) {
    nodes.push(h);
    nodeIds.add(h.id);
  }
  const links = [];
  for (const eid of expanded) {
    const nb = PAYLOAD.neighborhoods[eid];
    if (!nb) continue;
    for (const n of nb.nodes) {
      if (!nodeIds.has(n.id)) {
        // Expanded records carry a label; hubs stay unlabeled (too many).
        nodes.push(Object.assign({}, n, { label: { show: true, fontSize: 11 } }));
        nodeIds.add(n.id);
      }
    }
    for (const l of nb.links) links.push(l);
  }
  return { nodes: nodes, links: links };
}

function updateStatus() {
  const n = expanded.size;
  status.textContent = n === 0
    ? 'click an entity hub to expand'
    : n + (n === 1 ? ' entity' : ' entities') + ' expanded';
}

function render() {
  const d = currentData();
  chart.setOption({
    tooltip: { trigger: 'item' },
    legend: [{ data: PAYLOAD.categories, top: 'bottom', type: 'scroll' }],
    series: [{
      type: 'graph',
      layout: 'force',
      roam: true,
      categories: CATS,
      // layoutAnimation off so the (possibly thousands of) hubs settle up
      // front instead of churning; expansions are small and snap in cleanly.
      force: { repulsion: 90, gravity: 0.06, edgeLength: 55, friction: 0.2, layoutAnimation: false },
      label: { show: false },
      emphasis: { focus: 'adjacency', label: { show: true } },
      lineStyle: { color: 'source', opacity: 0.5, curveness: 0 },
      data: d.nodes,
      links: d.links
    }]
  }, { notMerge: true });
  updateStatus();
}

chart.on('click', function (p) {
  if (p.dataType !== 'node') return;
  const id = p.data && p.data.id;
  if (!id) return;
  // Only hubs toggle; a hub is any node with a neighborhood entry.
  if (Object.prototype.hasOwnProperty.call(PAYLOAD.neighborhoods, id)) {
    if (expanded.has(id)) expanded.delete(id); else expanded.add(id);
    render();
  }
});

document.getElementById('collapseAll').addEventListener('click', function () {
  expanded.clear();
  render();
});

window.addEventListener('resize', function () { chart.resize(); });
render();
</script>
</body>
</html>
"#;
