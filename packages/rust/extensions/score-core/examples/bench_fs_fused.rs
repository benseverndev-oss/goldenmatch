// Go/no-go spike (NOT shipped, NOT a test): does OWNING the string-sim
// primitives in Rust + FUSING the FS-block glue (score -> level -> weight ->
// normalize -> emit) into ONE pass beat the current Python numpy-per-field
// path END-TO-END on a realistic historical_50k-shaped tiny-block workload?
//
// This isolates exactly the back-half the fork would replace: shared prep
// (transforms, EM training) is identical either way and excluded. The Rust
// side GENERATES the block set deterministically and DUMPS it to a TSV so the
// Python counterpart (bench_fs_fused.py) scores byte-identical input; both
// print a checksum so we can confirm parity, and a wall time.
//
// Build: cargo build --release --example bench_fs_fused
// Run:   ./target/release/examples/bench_fs_fused <dump_path>
use std::fs::File;
use std::io::{BufWriter, Write};
use std::time::Instant;

use goldenmatch_score_core::{
    jaro_winkler_similarity, levenshtein_similarity, token_sort_ratio,
};

fn next(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

const FIRST: &[&str] = &[
    "jonathan", "katherine", "michael", "elizabeth", "christopher", "alexandra",
    "william", "margaret", "benjamin", "victoria", "nicholas", "gabriela",
    "samuel", "patricia", "theodore", "rosalind", "dominic", "florence",
];
const LAST: &[&str] = &[
    "richardson", "montgomery", "fitzgerald", "cunningham", "abernathy",
    "castellanos", "vasquez", "okonkwo", "petrov", "nakamura", "andersen",
    "bianchi", "delacroix", "hawthorne", "kowalski", "underwood",
];

// corrupt one alpha char ~1/3 of the time (models FS error-heavy PII)
fn corrupt(rng: &mut u64, s: &str) -> String {
    if !next(rng).is_multiple_of(3) {
        return s.to_string();
    }
    let mut b = s.as_bytes().to_vec();
    let i = (next(rng) as usize) % b.len();
    if b[i].is_ascii_alphabetic() {
        b[i] = b[i].wrapping_add(1);
    }
    String::from_utf8_lossy(&b).into_owned()
}

// A row = (given, family, dob, address). 4 FS comparison fields.
type Row = (String, String, String, String);

fn gen_row(rng: &mut u64) -> Row {
    let f = FIRST[(next(rng) as usize) % FIRST.len()];
    let l = LAST[(next(rng) as usize) % LAST.len()];
    let year = 1940 + (next(rng) as usize) % 70;
    let month = 1 + (next(rng) as usize) % 12;
    let day = 1 + (next(rng) as usize) % 28;
    let dob = format!("{year:04}-{month:02}-{day:02}");
    let num = 1 + (next(rng) as usize) % 9999;
    let street = LAST[(next(rng) as usize) % LAST.len()];
    let addr = format!("{num} {street} st");
    (
        corrupt(rng, f),
        corrupt(rng, l),
        corrupt(rng, &dob),
        corrupt(rng, &addr),
    )
}

// historical_50k FS shape: ~31.7k blocks, ~79% <= 8 rows, long thin tail.
// Draw sizes so the total lands ~50k rows across ~31k blocks.
fn gen_blocks(rng: &mut u64) -> Vec<Vec<Row>> {
    let mut blocks = Vec::new();
    let mut total = 0usize;
    while total < 50_000 {
        let r = next(rng) % 100;
        let size = if r < 55 {
            2 + (next(rng) as usize) % 3 // 2-4  (majority tiny)
        } else if r < 79 {
            5 + (next(rng) as usize) % 4 // 5-8
        } else if r < 96 {
            9 + (next(rng) as usize) % 16 // 9-24
        } else {
            25 + (next(rng) as usize) % 40 // 25-64 (thin tail)
        };
        let block: Vec<Row> = (0..size).map(|_| gen_row(rng)).collect();
        total += size;
        blocks.push(block);
    }
    blocks
}

// 3 levels per field: disagree / partial / agree. Log2-bit match weights
// (disagree negative, agree strongly positive) -- representative FS m/u.
const WEIGHTS: [[f64; 3]; 4] = [
    [-2.5, 0.4, 4.2], // given  (jw)
    [-3.0, 0.6, 5.1], // family (jw)
    [-1.8, 0.9, 3.7], // dob    (lev)
    [-1.2, 0.3, 2.4], // address(token_sort)
];
const PARTIAL: f64 = 0.7; // partial-level threshold
const AGREE: f64 = 0.92; // agree-level threshold

#[inline]
fn level(sim: f64) -> usize {
    if sim >= AGREE {
        2
    } else if sim >= PARTIAL {
        1
    } else {
        0
    }
}

#[inline]
fn score_field(field: usize, a: &str, b: &str) -> f64 {
    match field {
        0 | 1 => jaro_winkler_similarity(a, b),
        2 => levenshtein_similarity(a, b),
        _ => token_sort_ratio(a, b) / 100.0,
    }
}

fn main() {
    let dump = std::env::args().nth(1).unwrap_or_else(|| "/tmp/fs_blocks.tsv".into());
    let mut rng: u64 = 0x0BAD_F00D_DEAD_BEEF;
    let blocks = gen_blocks(&mut rng);
    let nrows: usize = blocks.iter().map(|b| b.len()).sum();
    let npairs: usize = blocks.iter().map(|b| b.len() * (b.len() - 1) / 2).sum();
    eprintln!(
        "blocks={} rows={} within-block pairs={}",
        blocks.len(),
        nrows,
        npairs
    );

    // Dump for the Python counterpart (identical input).
    {
        let f = File::create(&dump).expect("create dump");
        let mut w = BufWriter::new(f);
        for (bi, block) in blocks.iter().enumerate() {
            for (given, family, dob, addr) in block {
                writeln!(w, "{bi}\t{given}\t{family}\t{dob}\t{addr}").unwrap();
            }
        }
    }

    // Precompute per-field normalization envelope (min/max total weight).
    let wmin: f64 = WEIGHTS.iter().map(|w| w[0]).sum();
    let wmax: f64 = WEIGHTS.iter().map(|w| w[2]).sum();
    let span = wmax - wmin;
    let thresh = 0.5f64;

    // Warm + timed: the FUSED pass. One walk over upper-triangle pairs; every
    // field scored + leveled + accumulated inline; no per-field NxN matrix
    // allocated, no FFI, no numpy intermediate.
    let mut best_wall = f64::MAX;
    let mut checksum = 0.0f64;
    let mut emitted = 0usize;
    for it in 0..5 {
        let t0 = Instant::now();
        let mut cs = 0.0f64;
        let mut em = 0usize;
        for block in &blocks {
            let n = block.len();
            for i in 0..n {
                for j in (i + 1)..n {
                    let ri = &block[i];
                    let rj = &block[j];
                    let cols_i = [&ri.0, &ri.1, &ri.2, &ri.3];
                    let cols_j = [&rj.0, &rj.1, &rj.2, &rj.3];
                    let mut total = 0.0f64;
                    for field in 0..4 {
                        let sim = score_field(field, cols_i[field], cols_j[field]);
                        total += WEIGHTS[field][level(sim)];
                    }
                    let norm = ((total - wmin) / span).clamp(0.0, 1.0);
                    if norm >= thresh {
                        cs += norm;
                        em += 1;
                    }
                }
            }
        }
        let wall = t0.elapsed().as_secs_f64();
        if wall < best_wall {
            best_wall = wall;
        }
        checksum = cs;
        emitted = em;
        eprintln!("iter {it}: {:.4}s", wall);
    }
    println!(
        "RUST_FUSED wall={:.4}s emitted={} checksum={:.3}",
        best_wall, emitted, checksum
    );
}
