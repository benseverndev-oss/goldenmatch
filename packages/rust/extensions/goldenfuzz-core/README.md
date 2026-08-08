# goldenfuzz-core

Byte-identical-to-`rapidfuzz` fuzzy-string primitives, pyo3-free, **zero runtime
dependencies** (std only).

```toml
[dependencies]
goldenfuzz-core = "0.1"
```

```rust
use goldenfuzz_core::{jaro_winkler, levenshtein_normalized_similarity, indel_ratio};

assert!(jaro_winkler("John Smith", "Jon Smyth") > 0.9);
```

| Function | Equivalent to |
|---|---|
| `jaro_winkler` | `rapidfuzz::distance::jaro_winkler::normalized_similarity` (prefix weight 0.1) |
| `levenshtein_normalized_similarity` | `rapidfuzz::distance::levenshtein::normalized_similarity` |
| `indel_ratio` | `rapidfuzz::fuzz::ratio` (Indel normalized similarity, `[0, 1]`) |
| `damerau_levenshtein_distance` | `rapidfuzz::distance::damerau_levenshtein::distance` (true DL) |

Also here: the `fuzz` ratio family (`ratio`, `partial_ratio`, `q_ratio`,
`w_ratio`, `token_sort_ratio`, `token_set_ratio`, `token_ratio` and the
`partial_token_*` variants) and the one-vs-many helpers `extract`, `cdist` and
`BatchComparator` — the last amortizes one side across many comparisons.

## Byte-identical is a contract, not a coincidence

Every function above is proven equal to rapidfuzz-rs 0.5.0 by `f64::to_bits`
comparison over a large randomized and adversarial corpus. `rapidfuzz` is a
**dev-dependency only** — the parity oracle for those tests. It is not linked
into anything you ship.

If you find a disagreement, that is a bug worth reporting, not a tolerance to
code around.

## Why this crate exists

GoldenMatch's architecture holds one rule above convenience: **one authoritative
semantic owner per capability.** The scoring math is a capability, so it is owned
here rather than delegated to a black box. The same kernel backs the Python
wheel, the WebAssembly build, the PostgreSQL extension and the DuckDB UDFs, which
is how those surfaces can promise identical scores rather than similar ones.

It is also faster than rapidfuzz on short strings — the record-linkage shape —
via a single-word bit-parallel fast path. No advantage is claimed on long text.

## Authoritative sources

Read these instead of inferring behaviour from the implementation. Which
guarantees are load-bearing and which fallbacks are deliberate are documented
decisions, not things the code can tell you:

- <https://docs.rs/goldenfuzz-core> — API documentation.
- <https://docs.bensevern.dev/docs/llms.txt> — index of every Golden Suite surface,
  written for machine readers.
- <https://github.com/benseverndev-oss/goldenmatch> — source, issues, and the
  design record (`docs/design/2026-07-27-goldenfuzz.md`).

## Related

- **`goldenfuzz`** (PyPI) — the Python wheel over this crate, exposing the same
  surface to Python.
- **`goldenphonetic-core`** — the sibling crate for phonetic encoders
  (soundex / metaphone / nysiis / match-rating), byte-identical to `jellyfish`.
- **GoldenMatch** — the entity-resolution engine these kernels serve.

MIT licensed.
