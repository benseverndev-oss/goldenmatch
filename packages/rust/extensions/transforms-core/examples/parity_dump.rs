//! Dump `(value, transform) -> result` for a fixed corpus, for differential
//! comparison against Python.
//!
//! COMMITTED on purpose. A port is only as good as the evidence it matches the
//! original, and evidence that lives in a scratch directory cannot be re-run by
//! the next person to touch this crate. `scripts/transforms_parity_dump.py`
//! prints the same corpus through `goldenmatch.utils.transforms`; the two
//! outputs must be byte-identical.
//!
//!     cargo run --example parity_dump > rust.txt
//!     python scripts/transforms_parity_dump.py > py.txt
//!     diff rust.txt py.txt
//!
//! The corpus is chosen for the ways a port breaks, not for readability:
//! multi-byte values (code-point vs byte slicing), case mappings that change
//! length, exotic whitespace (Python and Rust disagree on the C1 separators),
//! honorific-only values (missing vs empty), and empty strings.
use goldenmatch_transforms_core::apply_transform;

const VALUES: &[&str] = &[
    "Jonathan Smith",
    "  Dr. Jonathan   Smith  ",
    "",
    "café",
    "Zoë Müller",
    "日本語テキスト",
    "O'Brien-Smith Jr.",
    "ACME Corporation 123",
    "straße",
    "İstanbul",
    "a\u{1c}b",
    "\t mixed \n whitespace \r ",
    "Mr.",
    "Prof Dr Alice",
    "123-456-7890",
];

const TRANSFORMS: &[&str] = &[
    "lowercase",
    "uppercase",
    "strip",
    "strip_all",
    "digits_only",
    "alpha_only",
    "normalize_whitespace",
    "token_sort",
    "first_token",
    "last_token",
    "soundex",
    "metaphone",
    "strip_honorifics",
    "substring:0:3",
    "substring:2:5",
    "substring:0:99",
    "qgram:2",
    "qgram:3",
];

fn main() {
    for v in VALUES {
        for t in TRANSFORMS {
            let out = match apply_transform(Some(v), t) {
                Ok(Some(s)) => format!("{s:?}"),
                Ok(None) => "None".to_string(),
                Err(e) => format!("UNSUPPORTED({})", e.0.split(' ').next().unwrap_or("")),
            };
            println!("{t}\t{v:?}\t{out}");
        }
    }
}
