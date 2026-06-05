//! `goldenembed` CLI: embed text lines with a saved in-house model.
//!
//!     goldenembed --model <dir> [input.txt]
//!     goldenembed bench --model <dir> [--rows N] [--batch B,B,...]
//!
//! Reads one text per line (from the file arg or stdin), prints one JSON array
//! of floats per line. With no input, prints the model dimension and exits.
use std::io::Write;

use anyhow::{anyhow, bail, Result};
use goldenembed::GoldenEmbed;

const DEFAULT_ROWS: usize = 50_000;
const DEFAULT_BATCHES: &[usize] = &[64, 256, 1024, 4096];

fn run_bench(model_dir: &str, rows: usize, batches: &[usize]) -> Result<()> {
    use std::time::Instant;
    let mut model = GoldenEmbed::load(model_dir)?;
    // Deterministic synthetic corpus (vary by index; no RNG dep).
    let corpus: Vec<String> = (0..rows)
        .map(|i| format!("record number {i} acme corp"))
        .collect();
    println!(
        "rows={rows} dim={} model_id={}",
        model.dim(),
        model.model_id().unwrap_or("<onnx-only>")
    );
    for &b in batches {
        let refs: Vec<&str> = corpus.iter().map(String::as_str).collect();
        let mut latencies: Vec<f64> = Vec::new();
        let start = Instant::now();
        for chunk in refs.chunks(b) {
            let t = Instant::now();
            let _ = model.embed(chunk)?;
            latencies.push(t.elapsed().as_secs_f64() * 1000.0);
        }
        let wall = start.elapsed().as_secs_f64();
        latencies.sort_by(|a, c| a.partial_cmp(c).unwrap());
        let p =
            |q: f64| latencies[((latencies.len() as f64 * q) as usize).min(latencies.len() - 1)];
        println!(
            "batch={b:>6} rows/sec={:>10.0} p50={:>7.2}ms p95={:>7.2}ms",
            rows as f64 / wall,
            p(0.50),
            p(0.95)
        );
    }
    Ok(())
}

fn parse_batch_list(s: &str) -> Result<Vec<usize>> {
    let mut out = Vec::new();
    for part in s.split(',') {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        let n: usize = part
            .parse()
            .map_err(|_| anyhow!("invalid batch size {:?} in --batch list", part))?;
        out.push(n);
    }
    Ok(out)
}

fn main() -> Result<()> {
    // Branch at the top: if the first positional arg is "bench", handle that
    // subcommand entirely and return; otherwise fall through to the existing
    // smoke / file-embed logic.
    if std::env::args().nth(1).as_deref() == Some("bench") {
        let mut model_dir: Option<String> = None;
        let mut rows: usize = DEFAULT_ROWS;
        let mut batches: Vec<usize> = Vec::new();

        let mut args = std::env::args().skip(2); // skip binary + "bench"
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--model" | "-m" => {
                    model_dir = args.next();
                }
                "--rows" => {
                    let val = args
                        .next()
                        .ok_or_else(|| anyhow!("--rows requires a value"))?;
                    rows = val
                        .parse()
                        .map_err(|_| anyhow!("--rows must be a positive integer, got {:?}", val))?;
                }
                "--batch" => {
                    let val = args
                        .next()
                        .ok_or_else(|| anyhow!("--batch requires a comma-separated list"))?;
                    batches = parse_batch_list(&val)?;
                }
                "-h" | "--help" => {
                    println!("usage: goldenembed bench --model <dir> [--rows N] [--batch B,B,...]");
                    return Ok(());
                }
                other => bail!("unknown bench argument: {:?}", other),
            }
        }

        let Some(model_dir) = model_dir else {
            bail!("bench: missing --model <dir>");
        };
        if rows == 0 {
            bail!("bench: --rows must be > 0");
        }
        if batches.is_empty() {
            batches = DEFAULT_BATCHES.to_vec();
        }

        return run_bench(&model_dir, rows, &batches);
    }

    // --- existing smoke / file-embed logic (unchanged) ---
    let mut args = std::env::args().skip(1);
    let mut model_dir: Option<String> = None;
    let mut input: Option<String> = None;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--model" | "-m" => model_dir = args.next(),
            "-h" | "--help" => {
                println!("usage: goldenembed --model <dir> [input.txt]");
                return Ok(());
            }
            other => input = Some(other.to_string()),
        }
    }
    let Some(model_dir) = model_dir else {
        bail!("missing --model <dir> (a saved GoldenEmbedModel directory)");
    };

    let mut model = GoldenEmbed::load(&model_dir)?;

    let Some(path) = input else {
        // No input file: report the model dimension (smoke check).
        println!(
            "model loaded: dim={} model_id={}",
            model.dim(),
            model.model_id().unwrap_or("<onnx-only>")
        );
        return Ok(());
    };

    let text = std::fs::read_to_string(&path)?;
    let lines: Vec<&str> = text.lines().collect();
    let vectors = model.embed(&lines)?;

    let stdout = std::io::stdout();
    let mut out = std::io::BufWriter::new(stdout.lock());
    for row in vectors {
        let parts: Vec<String> = row.iter().map(|v| format!("{v:.6}")).collect();
        writeln!(out, "[{}]", parts.join(","))?;
    }
    Ok(())
}
