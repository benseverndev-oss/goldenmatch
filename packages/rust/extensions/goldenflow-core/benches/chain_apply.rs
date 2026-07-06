//! Fused columnar apply bench (Pillar-1 of the Rust cutover).
//!
//! A realistic multi-op cleanup chain (`strip → lowercase → collapse_whitespace →
//! remove_punctuation`) over a 1M-row LargeUtf8 column (the shape Polars exports),
//! two ways:
//!   - SEQUENTIAL: each kernel produces a fresh array from the previous — the
//!     Rust-side analog of the per-transform path (N array allocations + N full
//!     column iterations).
//!   - FUSED: one `apply_chain` pass (two reused scratch buffers, one output array).
//!
//! This measures only the IN-RUST component. The dominant host-side win — the
//! per-transform path crosses the Python/Polars/Arrow boundary N times and rebuilds
//! the Polars column N times, the fused path once — is not visible here and is
//! measured by the host-side microbench. Even so, the array-rebuild + repeated-scan
//! savings show up. Asserts fused == sequential (parity) before timing.
//!
//! Run: `cargo bench --bench chain_apply --features arrow`.

use std::hint::black_box;
use std::time::Instant;

use arrow_array::{Array, LargeStringArray};
use goldenflow_core::chain::{apply_chain, Kernel};
use goldenflow_core::text;

const N: usize = 1_000_000;
const RUNS: usize = 5;

const CHAIN: [Kernel; 4] = [
    Kernel::Strip,
    Kernel::Lowercase,
    Kernel::CollapseWhitespace,
    Kernel::RemovePunctuation,
];

/// The per-transform shape: one owned kernel, one fresh array, per step.
fn one_kernel(arr: &LargeStringArray, k: Kernel) -> LargeStringArray {
    arr.iter()
        .map(|v| {
            v.map(|s| match k {
                Kernel::Strip => text::strip(s).to_string(),
                Kernel::Lowercase => text::lowercase(s),
                Kernel::CollapseWhitespace => text::collapse_whitespace(s),
                Kernel::RemovePunctuation => text::remove_punctuation(s),
                _ => unreachable!("bench chain is fixed"),
            })
        })
        .collect()
}

fn sequential(arr: &LargeStringArray) -> LargeStringArray {
    let mut cur = arr.clone();
    for k in CHAIN {
        cur = one_kernel(&cur, k);
    }
    cur
}

fn bench<T, F: Fn() -> T>(f: F) -> (f64, T) {
    let out = f();
    let mut times = Vec::with_capacity(RUNS);
    for _ in 0..RUNS {
        let t = Instant::now();
        let r = f();
        times.push(t.elapsed().as_secs_f64() * 1000.0);
        black_box(&r);
    }
    times.sort_by(|a, b| a.partial_cmp(b).unwrap());
    (times[RUNS / 2], out)
}

fn build_data() -> LargeStringArray {
    let pool = [
        "  John Smith!  ",
        "MARY-JONES  ",
        "o'Brien, Jr.",
        "  Van   Der  Berg ",
        "de la CRUZ #3",
        "JACKSON... ",
        "Rue\u{e9}  ",
        "  a  b   c  ",
    ];
    (0..N)
        .map(|i| {
            if i % 100 == 0 {
                None
            } else {
                Some(format!("{}{}", pool[i % pool.len()], i % 97))
            }
        })
        .collect()
}

fn main() {
    let mrows = N as f64 / 1e6;
    let tp = |m: f64| mrows / (m / 1000.0);
    println!(
        "Fused columnar apply -- {N} rows, chain of {} kernels, {RUNS}-run median wall\n",
        CHAIN.len()
    );
    let arr = build_data();

    let (s_seq, o_seq) = bench(|| sequential(&arr));
    let (s_fused, o_fused) = bench(|| apply_chain(&arr, &CHAIN).array);
    // Parity: fused must byte-match the sequential per-transform result.
    assert_eq!(o_seq.len(), o_fused.len());
    for i in 0..o_seq.len() {
        assert_eq!(o_seq.is_null(i), o_fused.is_null(i), "null mismatch @ {i}");
        if !o_seq.is_null(i) {
            assert_eq!(o_seq.value(i), o_fused.value(i), "value mismatch @ {i}");
        }
    }

    println!("in-Rust component (array-rebuild + scans; host boundary savings NOT shown):");
    println!(
        "  sequential (per-transform) {s_seq:8.2} ms ({:6.1} M/s)  1.00x",
        tp(s_seq)
    );
    println!(
        "  fused (one pass)           {s_fused:8.2} ms ({:6.1} M/s)  {:.2}x",
        tp(s_fused),
        s_seq / s_fused
    );
    println!(
        "\nThe host-side win is larger: the per-transform path also pays {}x\n\
         (Series<->Arrow export/import + Polars with_columns + a full-column\n\
         affected-count scan) that the fused path pays once.",
        CHAIN.len()
    );
}
