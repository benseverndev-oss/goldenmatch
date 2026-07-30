// Ad-hoc perf harness (NOT shipped, NOT a test): drives the block-scoring hot
// path (`score_one`) across the pure scorers over a realistic corpus of
// name-like pairs, to measure LLVM codegen changes (thin-vs-fat LTO,
// codegen-units) on the exact scoring path FS block scoring exercises.
// Build: `cargo build --release --example bench_score`.
use std::time::Instant;

use goldenmatch_score_core::score_one;

// Deterministic SplitMix64 so the corpus is identical across builds.
fn next(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

fn gen_name(rng: &mut u64) -> String {
    const FIRST: &[&str] = &[
        "jonathan", "katherine", "michael", "elizabeth", "christopher", "alexandra",
        "william", "margaret", "benjamin", "victoria", "nicholas", "gabriela",
    ];
    const LAST: &[&str] = &[
        "richardson", "montgomery", "fitzgerald", "cunningham", "abernathy",
        "castellanos", "vasquez", "okonkwo", "petrov", "nakamura", "andersen", "bianchi",
    ];
    let f = FIRST[(next(rng) as usize) % FIRST.len()];
    let l = LAST[(next(rng) as usize) % LAST.len()];
    let mut s = format!("{f} {l}");
    if next(rng).is_multiple_of(3) {
        let mut bytes = s.into_bytes();
        let i = (next(rng) as usize) % bytes.len();
        if bytes[i].is_ascii_alphabetic() {
            bytes[i] = bytes[i].wrapping_add(1);
        }
        s = String::from_utf8_lossy(&bytes).into_owned();
    }
    s
}

fn main() {
    let n_pairs: usize = std::env::var("BENCH_PAIRS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(50_000);
    let iters: usize = std::env::var("BENCH_ITERS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(40);

    let mut rng: u64 = 0x1234_5678_9ABC_DEF0;
    let pairs: Vec<(String, String)> = (0..n_pairs)
        .map(|_| (gen_name(&mut rng), gen_name(&mut rng)))
        .collect();

    // The pure, table-free scorers routed by the bucket block path.
    // 0=jaro_winkler 1=levenshtein 2=token_sort 5=qgram 9=dice 10=jaccard
    let scorers: [u8; 6] = [0, 1, 2, 5, 9, 10];

    let mut acc = 0.0f64;
    for (a, b) in pairs.iter().take(1000) {
        for &s in &scorers {
            acc += score_one(s, a, b);
        }
    }

    let mut best = f64::INFINITY;
    let total_calls = n_pairs * scorers.len();
    for _ in 0..iters {
        let start = Instant::now();
        for (a, b) in &pairs {
            for &s in &scorers {
                acc += score_one(s, a, b);
            }
        }
        let el = start.elapsed().as_secs_f64();
        if el < best {
            best = el;
        }
    }

    let ns_per_call = best * 1e9 / total_calls as f64;
    println!(
        "best={:.6}s  calls={}  ns/call={:.2}  (checksum={:.3})",
        best, total_calls, ns_per_call, acc
    );
}
