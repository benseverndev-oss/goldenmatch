//! Dump `(values, strategy) -> survivor` for a fixed corpus, for differential
//! comparison against Python's `merge_field`.
//!
//! COMMITTED, like the transforms one, because a port is only as good as the
//! evidence it matches the original -- and that harness caught two real bugs
//! (a scorer bound to the wrong crate, and honorifics stripped from the wrong
//! end) that review had passed.
//!
//!     cargo run --example parity_dump > rust.txt
//!     python scripts/survivorship_parity_dump.py > py.txt
//!     diff rust.txt py.txt
//!
//! The corpus targets TIE-BREAKS, because that is where a port silently
//! diverges: Python's `Counter.most_common` keeps insertion order and `max()`
//! keeps the first maximum, while Rust's `max_by_key` keeps the LAST.
use goldenmatch_survivorship_core::{merge_field, SUPPORTED};

const CASES: &[&[Option<&str>]] = &[
    &[Some("a"), Some("b")],
    &[Some("b"), Some("a"), Some("a")],
    &[Some("a"), Some("a"), Some("b"), Some("b")],
    &[None, Some("x"), Some("y")],
    &[None, None],
    &[Some("a"), None, Some("a")],
    &[Some("ab"), Some("abcd")],
    &[Some("ab"), Some("cd")],
    &[Some("café"), Some("abcde")],
    &[Some(""), Some("x")],
    &[Some("same"), Some("same"), Some("same")],
    &[Some("日本語"), Some("ab")],
];

fn main() {
    for case in CASES {
        for s in SUPPORTED {
            let got = match merge_field(case, s) {
                Ok(Some(v)) => format!("{v:?}"),
                Ok(None) => "None".to_string(),
                Err(_) => "REFUSED".to_string(),
            };
            println!("{s}\t{case:?}\t{got}");
        }
    }
}
