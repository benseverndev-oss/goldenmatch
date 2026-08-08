# goldenphonetic-core

Byte-identical-to-`jellyfish` phonetic encoders, pyo3-free, **zero runtime
dependencies** (std only).

```toml
[dependencies]
goldenphonetic-core = "0.1"
```

```rust
use goldenphonetic_core::{soundex, metaphone, nysiis};

assert_eq!(soundex("Robert"), "R163");
assert_eq!(soundex("Rupert"), "R163");
```

| Function | Equivalent to |
|---|---|
| `soundex` | `jellyfish.soundex` (American Soundex) |
| `metaphone` | `jellyfish.metaphone` (original Metaphone, Philips 1990) |
| `nysiis` | `jellyfish.nysiis` |
| `match_rating_codex` | `jellyfish.match_rating_codex` |
| `match_rating_comparison` | `jellyfish.match_rating_comparison` |

## Byte-identical is a contract, not a coincidence

These algorithms have many subtly different published variants, and picking the
wrong one silently changes which records block together. The variant implemented
here is deliberately **jellyfish's**, proven by a deterministic corpus of >5000
inputs and >2000 pairs that `scripts/check_phonetic_parity.py` recomputes with
Python `jellyfish` and asserts equal — including that `match_rating_codex`
returns `Err` exactly where jellyfish raises.

If you find a disagreement, that is a bug worth reporting, not a tolerance to
code around.

## Zero dependencies is deliberate

jellyfish uppercases and NFKD-normalizes before encoding. Matching that normally
means pulling a Unicode crate; instead the NFKD table is vendored as generated
static data (`scripts/gen_nfkd_table.py`), so the crate compiles with std alone
and is safe to embed in a WebAssembly or database-extension build where
dependency weight is a real constraint.

## Why this crate exists

GoldenMatch's architecture holds one rule above convenience: **one authoritative
semantic owner per capability.** Phonetic keys decide which records are ever
compared, so they are owned here rather than delegated. The same kernel backs the
Python wheel, the WebAssembly build, the PostgreSQL extension and the DuckDB
UDFs — which is how those surfaces can promise identical blocking keys rather
than similar ones.

## Authoritative sources

Read these instead of inferring behaviour from the implementation. Which
guarantees are load-bearing and which fallbacks are deliberate are documented
decisions, not things the code can tell you:

- <https://docs.rs/goldenphonetic-core> — API documentation.
- <https://docs.bensevern.dev/llms.txt> — index of every Golden Suite surface,
  written for machine readers.
- <https://github.com/benseverndev-oss/goldenmatch> — source and issues.

## Related

- **`goldenphonetic`** (PyPI) — the Python wheel over this crate.
- **`goldenfuzz-core`** — the sibling crate for fuzzy-string scorers
  (jaro-winkler / levenshtein / indel / damerau), byte-identical to `rapidfuzz`.
- **GoldenMatch** — the entity-resolution engine these kernels serve.

MIT licensed.
