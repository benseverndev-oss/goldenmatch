// Head-to-head: goldenfuzz (score-core::strsim, bit-parallel) vs the rapidfuzz
// crate 0.5, per-pair, across name / address / document length classes.
// Build+run: cargo run --release --example bench_vs_rapidfuzz
//
// This measures the SCALAR per-pair kernel (the thing #2159 made bit-parallel).
// rapidfuzz's Python `process.cdist` SIMD-batch advantage is a separate axis and
// is characterised in the design doc, not here.
use std::time::Instant;

use goldenmatch_score_core::strsim;
use rapidfuzz::distance::{jaro_winkler as rf_jw, levenshtein as rf_lev};
use rapidfuzz::fuzz as rf_fuzz;

fn next(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

fn rand_str(rng: &mut u64, len: usize) -> String {
    const AB: &[u8] = b"abcdefghijklmnopqrstuvwxyz ";
    (0..len).map(|_| AB[(next(rng) as usize) % AB.len()] as char).collect()
}

// Build `n` (a, b) pairs where b is a lightly-corrupted a (realistic match input).
fn corpus(rng: &mut u64, len: usize, n: usize) -> Vec<(String, String)> {
    (0..n)
        .map(|_| {
            let a = rand_str(rng, len);
            let mut b: Vec<char> = a.chars().collect();
            for _ in 0..(len / 8).max(1) {
                let i = (next(rng) as usize) % b.len();
                b[i] = (b"abcdefghijklmnopqrstuvwxyz "[(next(rng) as usize) % 27]) as char;
            }
            (a, b.into_iter().collect())
        })
        .collect()
}

fn time<F: FnMut() -> f64>(iters: usize, mut f: F) -> f64 {
    let t = Instant::now();
    let mut acc = 0.0f64;
    for _ in 0..iters {
        acc += f();
    }
    std::hint::black_box(acc);
    t.elapsed().as_secs_f64() / iters as f64 * 1e9 // ns/call
}

fn main() {
    let mut rng: u64 = 0xC0FFEE_1234_5678;
    let classes = [("name", 13usize, 200_000usize), ("address", 35, 100_000), ("document", 600, 4_000)];
    println!("{:<10} {:>6} {:>12} {:>12} {:>8}", "class", "len", "goldenfuzz", "rapidfuzz", "ratio");
    println!("{}", "-".repeat(52));
    for (name, len, n) in classes {
        let pairs = corpus(&mut rng, len, n);
        for (label, ours, theirs) in [
            (
                "jaro_winkler",
                Box::new(|p: &[(String, String)]| time(p.len(), {
                    let mut i = 0;
                    move || { let (a, b) = &p[i % p.len()]; i += 1; strsim::jaro_winkler(a, b) }
                })) as Box<dyn Fn(&[(String, String)]) -> f64>,
                Box::new(|p: &[(String, String)]| time(p.len(), {
                    let mut i = 0;
                    move || { let (a, b) = &p[i % p.len()]; i += 1;
                        rf_jw::normalized_similarity(a.chars(), b.chars()) }
                })) as Box<dyn Fn(&[(String, String)]) -> f64>,
            ),
            (
                "levenshtein",
                Box::new(|p: &[(String, String)]| time(p.len(), {
                    let mut i = 0;
                    move || { let (a, b) = &p[i % p.len()]; i += 1;
                        strsim::levenshtein_normalized_similarity(a, b) }
                })) as Box<dyn Fn(&[(String, String)]) -> f64>,
                Box::new(|p: &[(String, String)]| time(p.len(), {
                    let mut i = 0;
                    move || { let (a, b) = &p[i % p.len()]; i += 1;
                        rf_lev::normalized_similarity(a.chars(), b.chars()) }
                })) as Box<dyn Fn(&[(String, String)]) -> f64>,
            ),
            (
                "indel/ratio",
                Box::new(|p: &[(String, String)]| time(p.len(), {
                    let mut i = 0;
                    move || { let (a, b) = &p[i % p.len()]; i += 1; strsim::indel_ratio(a, b) }
                })) as Box<dyn Fn(&[(String, String)]) -> f64>,
                Box::new(|p: &[(String, String)]| time(p.len(), {
                    let mut i = 0;
                    move || { let (a, b) = &p[i % p.len()]; i += 1;
                        rf_fuzz::ratio(a.chars(), b.chars()) }
                })) as Box<dyn Fn(&[(String, String)]) -> f64>,
            ),
        ] {
            let g = ours(&pairs);
            let r = theirs(&pairs);
            println!("{:<10} {:>6} {:>10.1}ns {:>10.1}ns {:>7.2}x",
                     format!("{name}/{label}"), len, g, r, g / r);
        }
    }
    println!("\nratio = goldenfuzz / rapidfuzz  (1.0 = parity, <1 faster, >1 slower)");
}
