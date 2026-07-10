//! Per-kernel cost ranking — WHICH owned kernels dominate the fused-apply wall.
//!
//! The frame-container spike showed the native kernel is ~97% of a fused run; this
//! ranks the individual kernels so optimization targets the hot ones (measure
//! before designing — the repo's standing perf lesson). Plain `fn main()` +
//! 5-run-median wall, arrow-free (times the raw `text/email/names` fns per value
//! over a realistic messy corpus). Run: `cargo bench --bench kernel_rank`.
// The kernel table trades a couple of clippy nits for a uniform, readable shape.
#![allow(clippy::type_complexity)] // `&[(&str, Box<dyn Fn>)]` is fine for a bench
#![allow(clippy::redundant_closure)] // uniform `|s| f(s)` reads better than mixed fn paths

use std::hint::black_box;
use std::time::Instant;

use goldenflow_core::{email, names, text};

const RUNS: usize = 5;
const REPEAT: usize = 40_000; // corpus rows = REPEAT * base.len()

/// A realistic messy corpus: a mix of clean ASCII, multi-space, HTML, URLs,
/// punctuation, digits, accented Latin, and names/emails — the shapes these
/// kernels actually see in a cleanup pipeline.
fn corpus() -> Vec<String> {
    let base = [
        "  John   SMITH  ",
        "<b>o'Brien</b>  http://x.com/y?utm_source=a",
        "café  éé  #7  résumé",
        "Dr. Jane A. Doe Jr.",
        "MARY-JANE   o'neil",
        "hello world this is a normal sentence",
        "Contact: JANE@Example.COM  ",
        "price is $1,234.56 and 99.9%",
        "  multiple    spaces   everywhere  ",
        "Ünïcödé ströng wïth àccênts",
        "no-punctuation-here just words and 123 numbers",
        "The Quick Brown Fox Jumps Over The Lazy Dog",
    ];
    let mut v = Vec::with_capacity(base.len() * REPEAT);
    for _ in 0..REPEAT {
        for s in base {
            v.push(s.to_string());
        }
    }
    v
}

fn bench(name: &str, corpus: &[String], f: &dyn Fn(&str) -> String, total_bytes: usize) {
    let mut best = f64::INFINITY;
    for _ in 0..RUNS {
        let t = Instant::now();
        let mut acc = 0usize;
        for s in corpus {
            acc += black_box(f(s)).len();
        }
        black_box(acc);
        best = best.min(t.elapsed().as_secs_f64());
    }
    let rows = corpus.len();
    let ns_row = best * 1e9 / rows as f64;
    let mb_s = (total_bytes as f64 / 1e6) / best;
    println!("{name:<22} {ns_row:>8.1} ns/row   {mb_s:>8.1} MB/s");
}

fn main() {
    let c = corpus();
    let total_bytes: usize = c.iter().map(|s| s.len()).sum();
    println!(
        "corpus: {} rows, {:.1} MB, {} runs (min wall)\n",
        c.len(),
        total_bytes as f64 / 1e6,
        RUNS
    );
    let kernels: &[(&str, Box<dyn Fn(&str) -> String>)] = &[
        ("strip", Box::new(|s| text::strip(s).to_string())),
        ("lowercase", Box::new(|s| text::lowercase(s))),
        ("uppercase", Box::new(|s| text::uppercase(s))),
        ("title_case", Box::new(|s| text::title_case(s))),
        (
            "collapse_whitespace",
            Box::new(|s| text::collapse_whitespace(s)),
        ),
        ("normalize_quotes", Box::new(|s| text::normalize_quotes(s))),
        (
            "normalize_line_endings",
            Box::new(|s| text::normalize_line_endings(s)),
        ),
        (
            "normalize_unicode",
            Box::new(|s| text::normalize_unicode(s)),
        ),
        ("remove_html_tags", Box::new(|s| text::remove_html_tags(s))),
        ("remove_urls", Box::new(|s| text::remove_urls(s))),
        ("remove_digits", Box::new(|s| text::remove_digits(s))),
        (
            "remove_punctuation",
            Box::new(|s| text::remove_punctuation(s)),
        ),
        ("remove_emojis", Box::new(|s| text::remove_emojis(s))),
        ("extract_numbers", Box::new(|s| text::extract_numbers(s))),
        ("fix_mojibake", Box::new(|s| text::fix_mojibake(s))),
        ("email_normalize", Box::new(|s| email::email_normalize(s))),
        ("email_canonical", Box::new(|s| email::email_canonical(s))),
        (
            "name_transliterate",
            Box::new(|s| names::name_transliterate(s)),
        ),
        ("strip_titles", Box::new(|s| names::strip_titles(s))),
        ("strip_suffixes", Box::new(|s| names::strip_suffixes(s))),
        ("name_proper", Box::new(|s| names::name_proper(s))),
        (
            "nickname_standardize",
            Box::new(|s| names::nickname_standardize(s)),
        ),
        ("name_initials", Box::new(|s| names::name_initials(s))),
        ("strip_middle", Box::new(|s| names::strip_middle(s))),
    ];
    for (name, f) in kernels {
        bench(name, &c, f.as_ref(), total_bytes);
    }
}
