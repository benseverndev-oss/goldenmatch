//! `graph-layout` — lay out an entity-resolution graph and render the layout
//! condensing, iteration by iteration, to PPM frames.
//!
//! ```text
//! # synthetic demo (no input needed):
//! graph-layout --clusters 8 --per 400 --out frames
//!
//! # a real goldenmatch graph (edge list: `a b [weight]` per line):
//! graph-layout --input identity_edges.tsv --out frames --width 1600 --height 1600
//!
//! ffmpeg -framerate 30 -i frames/frame_%05d.ppm -pix_fmt yuv420p layout.mp4
//! ```

use std::fs;
use std::path::Path;
use std::process::exit;

use goldenmatch_graph_layout::graph::Graph;
use goldenmatch_graph_layout::layout::{self, Params};
use goldenmatch_graph_layout::raster::{color_for, Canvas};
use goldenmatch_graph_layout::vec2::V2;

struct Args {
    input: Option<String>,
    out: String,
    width: usize,
    height: usize,
    frame_every: u32,
    clusters: usize,
    per: usize,
    params: Params,
}

impl Default for Args {
    fn default() -> Self {
        Args {
            input: None,
            out: "frames".to_string(),
            width: 1280,
            height: 1280,
            frame_every: 2,
            clusters: 8,
            per: 250,
            params: Params::default(),
        }
    }
}

fn parse_args() -> Args {
    let mut a = Args::default();
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let mut i = 0;
    let need = |i: usize, argv: &[String]| -> String {
        argv.get(i + 1).cloned().unwrap_or_else(|| {
            eprintln!("missing value for {}", argv[i]);
            exit(2);
        })
    };
    while i < argv.len() {
        match argv[i].as_str() {
            "--input" | "-i" => a.input = Some(need(i, &argv)),
            "--out" | "-o" => a.out = need(i, &argv),
            "--width" => a.width = need(i, &argv).parse().unwrap_or(a.width),
            "--height" => a.height = need(i, &argv).parse().unwrap_or(a.height),
            "--frame-every" => {
                a.frame_every = need(i, &argv).parse().unwrap_or(a.frame_every).max(1)
            }
            "--clusters" => a.clusters = need(i, &argv).parse().unwrap_or(a.clusters),
            "--per" => a.per = need(i, &argv).parse().unwrap_or(a.per),
            "--k" => a.params.k = need(i, &argv).parse().unwrap_or(a.params.k),
            "--theta" => a.params.theta = need(i, &argv).parse().unwrap_or(a.params.theta),
            "--iters" => {
                a.params.iters_fine = need(i, &argv).parse().unwrap_or(a.params.iters_fine)
            }
            "--iters-coarse" => {
                a.params.iters_coarse = need(i, &argv).parse().unwrap_or(a.params.iters_coarse)
            }
            "--seed" => a.params.seed = need(i, &argv).parse().unwrap_or(a.params.seed),
            "-h" | "--help" => {
                println!("graph-layout — Barnes-Hut + multilevel force layout → PPM frames");
                println!("  --input/-i FILE     edge list `a b [weight]` (default: synthetic)");
                println!("  --out/-o DIR        output frame dir (default: frames)");
                println!("  --width --height    canvas size (default 1280)");
                println!("  --frame-every N     render every Nth iteration (default 2)");
                println!("  --clusters --per    synthetic graph size (default 8 x 250)");
                println!("  --k --theta --iters --iters-coarse --seed  layout tunables");
                exit(0);
            }
            other => {
                eprintln!("unknown arg: {other} (try --help)");
                exit(2);
            }
        }
        // every recognized flag consumes a value except --help (handled above)
        i += 2;
    }
    a
}

/// Fit a world bbox into the canvas (uniform scale, centered, padded). Returns a
/// closure mapping world → screen for THIS frame (per-frame autoscale keeps the
/// condensing layout in view the whole time).
fn make_projection(pos: &[V2], w: usize, h: usize) -> impl Fn(V2) -> (f32, f32) {
    let (mut lo, mut hi) = (pos[0], pos[0]);
    for &p in pos {
        lo.x = lo.x.min(p.x);
        lo.y = lo.y.min(p.y);
        hi.x = hi.x.max(p.x);
        hi.y = hi.y.max(p.y);
    }
    let pad = 0.06;
    let span_x = (hi.x - lo.x).max(1e-3);
    let span_y = (hi.y - lo.y).max(1e-3);
    let s = ((w as f32 * (1.0 - 2.0 * pad)) / span_x).min((h as f32 * (1.0 - 2.0 * pad)) / span_y);
    // Center the scaled drawing in the canvas.
    let ox = (w as f32 - span_x * s) * 0.5 - lo.x * s;
    let oy = (h as f32 - span_y * s) * 0.5 - lo.y * s;
    move |p: V2| (p.x * s + ox, p.y * s + oy)
}

fn render_frame(
    pos: &[V2],
    g: &Graph,
    colors: &[u32],
    w: usize,
    h: usize,
    path: &Path,
) -> std::io::Result<()> {
    let mut canvas = Canvas::new(w, h, [12, 14, 20]);
    let proj = make_projection(pos, w, h);

    // Edges first (low alpha → density glows under the nodes).
    for &(a, b, _) in &g.edges {
        let (ax, ay) = proj(pos[a as usize]);
        let (bx, by) = proj(pos[b as usize]);
        canvas.line(ax, ay, bx, by, [120, 140, 200], 0.07);
    }
    // Nodes, colored by connected component (resolved cluster).
    let r = (w.min(h) as f32 / 640.0).max(1.2);
    for (i, &p) in pos.iter().enumerate() {
        let (x, y) = proj(p);
        canvas.disc(x, y, r, color_for(colors[i]), 0.95);
    }
    canvas.save_ppm(path)
}

fn main() {
    let args = parse_args();

    let (graph, source) = match &args.input {
        Some(path) => match Graph::read_edge_list(path) {
            Ok((g, _labels)) => (g, format!("{path}")),
            Err(e) => {
                eprintln!("failed to read {path}: {e}");
                exit(1);
            }
        },
        None => (
            // p_out = 0: clusters are genuine connected components, mirroring an
            // ER match graph thresholded into resolved entities (distinct colors).
            Graph::synthetic(args.clusters, args.per, 0.06, 0.0, args.params.seed),
            format!("synthetic {}x{}", args.clusters, args.per),
        ),
    };

    if graph.n == 0 {
        eprintln!("empty graph");
        exit(1);
    }

    let colors = graph.components();
    let n_components = colors.iter().copied().max().map(|m| m + 1).unwrap_or(0);
    println!(
        "graph: {} nodes, {} edges, {} components  ({})",
        graph.n,
        graph.edges.len(),
        n_components,
        source
    );

    if let Err(e) = fs::create_dir_all(&args.out) {
        eprintln!("cannot create {}: {e}", args.out);
        exit(1);
    }

    let t0 = std::time::Instant::now();
    let mut saved = 0u32;
    let mut last_pos: Vec<V2> = Vec::new();
    let pos = layout::run(&graph, &args.params, |frame_pos, fidx| {
        if fidx % args.frame_every == 0 {
            let path = Path::new(&args.out).join(format!("frame_{:05}.ppm", saved));
            if let Err(e) = render_frame(frame_pos, &graph, &colors, args.width, args.height, &path)
            {
                eprintln!("frame write failed: {e}");
            }
            saved += 1;
        }
        last_pos = frame_pos.to_vec();
    });
    let _ = last_pos;

    // Always render a clean final frame.
    let final_path = Path::new(&args.out).join(format!("frame_{:05}.ppm", saved));
    let _ = render_frame(&pos, &graph, &colors, args.width, args.height, &final_path);
    saved += 1;

    let secs = t0.elapsed().as_secs_f32();
    println!(
        "layout + render: {saved} frames in {secs:.2}s  ({:.0} node-iters/s)",
        (graph.n as f32 * args.params.iters_fine as f32) / secs.max(1e-3)
    );
    println!(
        "stitch:  ffmpeg -framerate 30 -i {}/frame_%05d.ppm -pix_fmt yuv420p layout.mp4",
        args.out
    );
}
