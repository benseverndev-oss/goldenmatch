// Head-to-head: goldenphonetic-core (soundex / nysiis) vs the Python `jellyfish`
// package, on an ASCII single-token name corpus (the overwhelming common case).
//
// Build+run:  cargo run --release --example bench_vs_jellyfish
//
// This example ONLY calls the public `soundex` / `nysiis`, so it compiles
// unchanged against both the pre- and post-fast-path lib. To get the before/after
// split with an identical harness:
//   cargo run --release --example bench_vs_jellyfish            # AFTER (fast path)
//   git stash && cargo run --release --example bench_vs_jellyfish && git stash pop  # BEFORE
//
// It also writes the exact corpus it timed to
// `target/bench_ascii_names.txt` so `scripts/bench_jellyfish.py` can time the SAME
// inputs with Python `jellyfish` and the ratios line up.
use std::path::Path;
use std::time::Instant;

use goldenphonetic_core::{nysiis, soundex};

fn next(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

// Real-ish given/surname fragments — same flavour as the parity corpus, all ASCII.
const FRAGS: &[&str] = &[
    "robert",
    "rupert",
    "ashcraft",
    "ashcroft",
    "tymczak",
    "pfister",
    "honeyman",
    "catherine",
    "kathryn",
    "smith",
    "smyth",
    "jonathan",
    "thompson",
    "byrne",
    "michael",
    "michelle",
    "gutierrez",
    "vandeusen",
    "vanderberg",
    "mcdonald",
    "macintosh",
    "mackenzie",
    "knight",
    "wright",
    "pneumonia",
    "phillip",
    "schwartz",
    "schmidt",
    "quentin",
    "xavier",
    "yolanda",
    "zachary",
    "elizabeth",
    "william",
    "andrew",
    "matthew",
    "christopher",
    "kristopher",
    "jennifer",
    "wojcik",
    "kowalski",
    "schneider",
    "fischer",
    "hoffmann",
    "richter",
    "schroeder",
    "neumann",
    "gennifer",
    "nathaniel",
    "maryanne",
];

// Build `n` single-token ASCII names: a fragment, sometimes concatenated with a
// second fragment (compound surname) to spread lengths realistically.
fn corpus(n: usize) -> Vec<String> {
    let mut rng: u64 = 0xC0FF_EE12_3456_789A;
    (0..n)
        .map(|_| {
            let a = FRAGS[(next(&mut rng) as usize) % FRAGS.len()];
            if next(&mut rng) & 3 == 0 {
                let b = FRAGS[(next(&mut rng) as usize) % FRAGS.len()];
                format!("{a}{b}")
            } else {
                a.to_string()
            }
        })
        .collect()
}

// ns/call: run the encoder over every name in `corpus`, `reps` times.
fn time<F: Fn(&str) -> String>(corpus: &[String], reps: usize, f: F) -> f64 {
    let t = Instant::now();
    let mut acc = 0usize;
    for _ in 0..reps {
        for s in corpus {
            acc += f(s).len();
        }
    }
    std::hint::black_box(acc);
    let calls = (reps * corpus.len()) as f64;
    t.elapsed().as_secs_f64() / calls * 1e9
}

fn main() {
    let names = corpus(50_000);
    let reps = 20; // 1,000,000 calls per encoder

    // Persist the exact corpus for the Python jellyfish side.
    let out = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("target")
        .join("bench_ascii_names.txt");
    if let Some(dir) = out.parent() {
        std::fs::create_dir_all(dir).ok();
    }
    std::fs::write(&out, names.join("\n")).expect("write corpus");

    // Warm up (fill caches / branch predictor) before timing.
    let _ = time(&names, 1, soundex);
    let _ = time(&names, 1, nysiis);

    let sx = time(&names, reps, soundex);
    let ny = time(&names, reps, nysiis);

    let total_ms = |ns: f64| ns * names.len() as f64 / 1e6;

    println!(
        "goldenphonetic-core ASCII name corpus: {} names x {} reps = {} calls/encoder",
        names.len(),
        reps,
        names.len() * reps
    );
    println!("corpus written to {}", out.display());
    println!();
    println!(
        "{:<10} {:>12} {:>16}",
        "encoder", "ns/call", "ms / corpus-pass"
    );
    println!("{}", "-".repeat(40));
    println!("{:<10} {:>10.1}ns {:>13.1}ms", "soundex", sx, total_ms(sx));
    println!("{:<10} {:>10.1}ns {:>13.1}ms", "nysiis", ny, total_ms(ny));
    println!();
    println!("ms / corpus-pass is directly comparable to scripts/bench_jellyfish.py");
}
