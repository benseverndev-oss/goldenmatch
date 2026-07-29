// HNSW build/query/recall bench on synthetic unit-vector corpora at the two
// GoldenMatch embedding dims (32, 128). Measures BUILD wall, per-query median
// latency, and recall@10 vs a brute-force exact top-k ground truth. The
// measure-first baseline for any perf change to `dist` / `search_layer` — run
// before and after and compare (recall MUST NOT regress).
//
//   cargo run --release --example bench_hnsw
//   cargo run --release --example bench_hnsw -- 100000 32   (rows, dim override)
use std::time::Instant;

use goldenhnsw::{HnswIndex, HnswParams};

fn next(s: &mut u64) -> u64 {
    *s = s.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *s;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

fn unit(s: &mut u64) -> f32 {
    (next(s) >> 40) as f32 / (1u64 << 24) as f32
}

/// `n` random L2-normalized `dim`-vectors, flat row-major (like the wheel).
fn corpus(n: usize, dim: usize, seed: u64) -> Vec<f32> {
    let mut s = seed;
    let mut out = vec![0.0f32; n * dim];
    for row in out.chunks_exact_mut(dim) {
        for x in row.iter_mut() {
            *x = unit(&mut s) * 2.0 - 1.0;
        }
        let norm = row.iter().map(|v| v * v).sum::<f32>().sqrt().max(1e-12);
        for x in row.iter_mut() {
            *x /= norm;
        }
    }
    out
}

/// Exact top-k ids by descending inner product (tie-break id asc), matching the
/// `Item` ordering flip the index uses.
fn brute_topk(corpus: &[f32], dim: usize, q: &[f32], k: usize) -> Vec<u32> {
    let mut scored: Vec<(f32, u32)> = corpus
        .chunks_exact(dim)
        .enumerate()
        .map(|(i, x)| {
            let ip: f32 = q.iter().zip(x).map(|(a, b)| a * b).sum();
            (ip, i as u32)
        })
        .collect();
    scored.sort_by(|a, b| b.0.total_cmp(&a.0).then_with(|| a.1.cmp(&b.1)));
    scored.into_iter().take(k).map(|(_, i)| i).collect()
}

fn run(n: usize, dim: usize) {
    let params = HnswParams {
        m: 16,
        ef_construction: 200,
        ef_search: 64,
        seed: 123,
    };
    let k = 10;
    let n_queries = 1000usize;

    let data = corpus(n, dim, 0xC0FFEE ^ dim as u64);

    // BUILD.
    let t = Instant::now();
    let mut idx = HnswIndex::new(dim, params);
    for row in data.chunks_exact(dim) {
        idx.add(row);
    }
    let build = t.elapsed().as_secs_f64();
    assert_eq!(idx.len(), n);

    // Query set: evenly-spaced corpus points (self is the guaranteed top hit).
    let step = (n / n_queries).max(1);
    let q_ids: Vec<usize> = (0..n_queries).map(|i| (i * step) % n).collect();

    // QUERY latency: median over per-query wall.
    let mut lat_ns: Vec<f64> = Vec::with_capacity(q_ids.len());
    let mut sink = 0u64;
    for &qi in &q_ids {
        let q = &data[qi * dim..qi * dim + dim];
        let t = Instant::now();
        let res = idx.search(q, k);
        lat_ns.push(t.elapsed().as_secs_f64() * 1e9);
        sink = sink.wrapping_add(res.len() as u64);
    }
    std::hint::black_box(sink);
    lat_ns.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let median = lat_ns[lat_ns.len() / 2];
    let p95 = lat_ns[(lat_ns.len() * 95) / 100];

    // RECALL@k vs brute force.
    let mut hits = 0usize;
    let mut total = 0usize;
    for &qi in &q_ids {
        let q = &data[qi * dim..qi * dim + dim];
        let got: std::collections::HashSet<u32> =
            idx.search(q, k).into_iter().map(|(i, _)| i).collect();
        let want = brute_topk(&data, dim, q, k);
        for w in &want {
            total += 1;
            if got.contains(w) {
                hits += 1;
            }
        }
    }
    let recall = hits as f64 / total as f64;

    println!(
        "n={n:<7} dim={dim:<4} build={build:>7.3}s  q_median={median:>8.0}ns  q_p95={p95:>8.0}ns  recall@{k}={recall:.4}"
    );
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() >= 3 {
        let n: usize = args[1].parse().expect("rows");
        let dim: usize = args[2].parse().expect("dim");
        run(n, dim);
        return;
    }
    println!("HNSW bench (M=16, ef_construction=200, ef_search=64, k=10, 1000 queries)");
    for &dim in &[32usize, 128] {
        for &n in &[50_000usize, 200_000] {
            run(n, dim);
        }
    }
}
