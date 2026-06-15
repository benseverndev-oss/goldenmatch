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
    p_in: f32,
    p_out: f32,
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
            p_in: 0.06,
            p_out: 0.0,
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
            "--p-in" => a.p_in = need(i, &argv).parse().unwrap_or(a.p_in),
            "--p-out" => a.p_out = need(i, &argv).parse().unwrap_or(a.p_out),
            "--k" => a.params.k = need(i, &argv).parse().unwrap_or(a.params.k),
            "--theta" => a.params.theta = need(i, &argv).parse().unwrap_or(a.params.theta),
            "--iters" => {
                a.params.iters_fine = need(i, &argv).parse().unwrap_or(a.params.iters_fine)
            }
            "--iters-coarse" => {
                a.params.iters_coarse = need(i, &argv).parse().unwrap_or(a.params.iters_coarse)
            }
            "--seed" => a.params.seed = need(i, &argv).parse().unwrap_or(a.params.seed),
            "--coarsest" => a.params.coarsest = need(i, &argv).parse().unwrap_or(a.params.coarsest),
            "--single-level" => {
                // Disable multilevel coarsening → the full single-level condensation
                // from a random seed (the dramatic "untangling" reel; multilevel
                // pre-solves so its finest level barely moves). Valueless flag.
                a.params.coarsest = usize::MAX;
                i += 1;
                continue;
            }
            "-h" | "--help" => {
                println!("graph-layout — Barnes-Hut + multilevel force layout → PPM frames");
                println!("  --input/-i FILE     edge list `a b [weight]` (default: synthetic)");
                println!("  --out/-o DIR        output frame dir (default: frames)");
                println!("  --width --height    canvas size (default 1280)");
                println!("  --frame-every N     render every Nth iteration (default 2)");
                println!("  --clusters --per    synthetic graph size (default 8 x 250)");
                println!("  --p-in --p-out      synthetic intra/inter-community edge prob");
                println!("  --single-level      full condensation reel (no coarsening)");
                println!("  --k --theta --iters --iters-coarse --coarsest --seed  tunables");
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
    radii: &[f32],
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
    // Nodes, colored by connected component (resolved entity), sized per node so
    // a heavily-duplicated entity reads as a big dot.
    for (i, &p) in pos.iter().enumerate() {
        let (x, y) = proj(p);
        canvas.disc(x, y, radii[i], color_for(colors[i]), 0.95);
    }
    canvas.save_ppm(path)
}

fn main() {
    let args = parse_args();

    // `synthetic` is true when we built the graph ourselves (no --input), so we
    // know the *planted* community of every node and can color by it even when
    // --p-out adds inter-community edges (which would otherwise fuse everything
    // into one connected component).
    let synthetic = args.input.is_none();
    let (graph, source) = match &args.input {
        Some(path) => match Graph::read_edge_list(path) {
            Ok((g, _labels)) => (g, format!("{path}")),
            Err(e) => {
                eprintln!("failed to read {path}: {e}");
                exit(1);
            }
        },
        None => (
            // --p-out 0 (default): clusters are genuine connected components,
            // mirroring an ER match graph thresholded into resolved entities.
            // --p-out > 0: weak inter-community links → one connected web with
            // community structure (a relationship / graph-ER shape).
            Graph::synthetic(
                args.clusters,
                args.per,
                args.p_in,
                args.p_out,
                args.params.seed,
            ),
            format!(
                "synthetic {}x{} p_out={}",
                args.clusters, args.per, args.p_out
            ),
        ),
    };

    if graph.n == 0 {
        eprintln!("empty graph");
        exit(1);
    }

    // Color by planted community for synthetic graphs (so inter-community edges
    // don't collapse the palette to one color); by connected component otherwise
    // — for a thresholded match graph the components ARE the resolved entities.
    let colors: Vec<u32> = if synthetic {
        (0..graph.n).map(|i| (i / args.per) as u32).collect()
    } else {
        graph.components()
    };
    let n_components = colors.iter().copied().max().map(|m| m + 1).unwrap_or(0);
    println!(
        "graph: {} nodes, {} edges, {} components  ({})",
        graph.n,
        graph.edges.len(),
        n_components,
        source
    );

    // Per-node disc radius ∝ sqrt(cluster size) so a disc's AREA tracks the
    // entity's record count — a 30-record entity reads as a big dot, a singleton
    // as a small one. Clamped so one giant cluster doesn't swallow the frame.
    let mut comp_size = vec![0usize; n_components.max(1) as usize];
    for &c in &colors {
        comp_size[c as usize] += 1;
    }
    let base = (args.width.min(args.height) as f32 / 640.0).max(1.3);
    let radii: Vec<f32> = colors
        .iter()
        .map(|&c| (base * (comp_size[c as usize] as f32).sqrt()).min(base * 6.0))
        .collect();

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
            if let Err(e) = render_frame(
                frame_pos,
                &graph,
                &colors,
                &radii,
                args.width,
                args.height,
                &path,
            ) {
                eprintln!("frame write failed: {e}");
            }
            saved += 1;
        }
        last_pos = frame_pos.to_vec();
    });
    let _ = last_pos;

    // Always render a clean final frame.
    let final_path = Path::new(&args.out).join(format!("frame_{:05}.ppm", saved));
    let _ = render_frame(
        &pos,
        &graph,
        &colors,
        &radii,
        args.width,
        args.height,
        &final_path,
    );
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
