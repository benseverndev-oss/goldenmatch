//! Byte-parity gate: `float_to_polars_string` must reproduce Polars' `cast(Utf8)`
//! / `write_csv` float formatting exactly. The fixture is `(f64 little-endian bits
//! hex, TAB, polars string)` generated from Polars 1.40 across every magnitude
//! band (fixed, both scientific tails, the fixed↔sci boundaries, 0/-0, NaN/±inf).

use goldenflow_core::float_fmt::float_to_polars_string;

fn from_hex_le(h: &str) -> f64 {
    let mut b = [0u8; 8];
    for (i, byte) in b.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&h[i * 2..i * 2 + 2], 16).unwrap();
    }
    f64::from_le_bytes(b)
}

#[test]
fn matches_polars_float_formatting() {
    let data = include_str!("fixtures/polars_float_fmt.tsv");
    let mut mismatches = Vec::new();
    let mut n = 0;
    for line in data.lines().filter(|l| !l.is_empty()) {
        let (h, want) = line.split_once('\t').unwrap();
        let x = from_hex_le(h);
        let got = float_to_polars_string(x);
        n += 1;
        if got != want {
            mismatches.push(format!("{x:e}: want {want:?}, got {got:?}"));
        }
    }
    assert!(n > 4000, "fixture unexpectedly small ({n} rows)");
    assert!(
        mismatches.is_empty(),
        "{}/{} float formats diverge from Polars:\n{}",
        mismatches.len(),
        n,
        mismatches
            .iter()
            .take(20)
            .cloned()
            .collect::<Vec<_>>()
            .join("\n")
    );
}
