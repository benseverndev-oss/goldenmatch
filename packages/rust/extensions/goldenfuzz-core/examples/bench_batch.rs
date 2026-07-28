// One-vs-many (process.cdist) head-to-head: goldenfuzz BatchComparator (query
// peq built once) vs per-pair (peq rebuilt each choice) vs rapidfuzz's
// BatchComparator, on document-length inputs where the peq-build amortisation
// dominates. Build+run: cargo run --release --example bench_batch
use std::time::Instant;

use goldenfuzz_core::{indel_ratio, jaro_winkler, levenshtein_normalized_similarity, BatchComparator, Scorer};
use rapidfuzz::distance::{jaro_winkler as rf_jw, levenshtein as rf_lev};
use rapidfuzz::fuzz as rf_fuzz;

fn next(s: &mut u64) -> u64 {
    *s = s.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *s;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

fn doc(rng: &mut u64, len: usize) -> String {
    const AB: &[u8] = b"abcdefghijklmnop qrstuvwxyz";
    (0..len).map(|_| AB[(next(rng) as usize) % AB.len()] as char).collect()
}

fn main() {
    let mut rng = 0xBA7C_4u64;
    for &(class, qlen, n) in &[("name", 13usize, 5000usize), ("document", 600, 2000)] {
        let query = doc(&mut rng, qlen);
        let choices: Vec<String> = (0..n).map(|_| doc(&mut rng, qlen)).collect();
        let cref: Vec<&str> = choices.iter().map(String::as_str).collect();
        println!("\n== {class} (query {qlen} chars vs {n} choices), ns/choice ==");
        println!("{:<14} {:>12} {:>12} {:>12}", "scorer", "per-pair", "gf-batch", "rf-batch");

        // jaro-winkler
        let t = Instant::now(); let mut acc = 0.0;
        for c in &cref { acc += jaro_winkler(&query, c); }
        let pp = t.elapsed().as_secs_f64() / n as f64 * 1e9;
        let t = Instant::now(); let bc = BatchComparator::new(&query);
        for c in &cref { acc += bc.jaro_winkler(c); }
        let gb = t.elapsed().as_secs_f64() / n as f64 * 1e9;
        let t = Instant::now(); let rb = rf_jw::BatchComparator::new(query.chars());
        for c in &cref { acc += rb.normalized_similarity(c.chars()); }
        let rbn = t.elapsed().as_secs_f64() / n as f64 * 1e9;
        println!("{:<14} {:>10.1}ns {:>10.1}ns {:>10.1}ns  (gf-batch/rf {:.2}x)", "jaro_winkler", pp, gb, rbn, gb / rbn);

        // levenshtein
        let t = Instant::now();
        for c in &cref { acc += levenshtein_normalized_similarity(&query, c); }
        let pp = t.elapsed().as_secs_f64() / n as f64 * 1e9;
        let t = Instant::now(); let bc = BatchComparator::new(&query);
        for c in &cref { acc += bc.levenshtein(c); }
        let gb = t.elapsed().as_secs_f64() / n as f64 * 1e9;
        let t = Instant::now(); let rb = rf_lev::BatchComparator::new(query.chars());
        for c in &cref { acc += rb.normalized_similarity(c.chars()); }
        let rbn = t.elapsed().as_secs_f64() / n as f64 * 1e9;
        println!("{:<14} {:>10.1}ns {:>10.1}ns {:>10.1}ns  (gf-batch/rf {:.2}x)", "levenshtein", pp, gb, rbn, gb / rbn);

        // indel
        let t = Instant::now();
        for c in &cref { acc += indel_ratio(&query, c); }
        let pp = t.elapsed().as_secs_f64() / n as f64 * 1e9;
        let t = Instant::now(); let bc = BatchComparator::new(&query);
        for c in &cref { acc += bc.indel(c); }
        let gb = t.elapsed().as_secs_f64() / n as f64 * 1e9;
        let t = Instant::now(); let rb = rf_fuzz::RatioBatchComparator::new(query.chars());
        for c in &cref { acc += rb.similarity(c.chars()); }
        let rbn = t.elapsed().as_secs_f64() / n as f64 * 1e9;
        println!("{:<14} {:>10.1}ns {:>10.1}ns {:>10.1}ns  (gf-batch/rf {:.2}x)", "indel/ratio", pp, gb, rbn, gb / rbn);

        std::hint::black_box(acc);
    }
    println!("\nper-pair rebuilds the query peq every choice; gf-batch / rf-batch build it once.");
}
