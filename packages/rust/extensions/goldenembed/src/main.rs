//! `goldenembed` CLI: embed text lines with a saved in-house model.
//!
//!     goldenembed --model <dir> [input.txt]
//!
//! Reads one text per line (from the file arg or stdin), prints one JSON array
//! of floats per line. With no input, prints the model dimension and exits.
use std::io::Write;

use anyhow::{bail, Result};
use goldenembed::GoldenEmbed;

fn main() -> Result<()> {
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
        println!("model loaded: dim={}", model.dim());
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
