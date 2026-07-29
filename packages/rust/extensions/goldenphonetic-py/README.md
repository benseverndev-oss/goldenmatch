# goldenphonetic

Fast, **byte-identical-to-jellyfish** phonetic encoders. A thin PyO3 wheel over
the pyo3-free `goldenphonetic-core` Rust crate, with **zero runtime dependencies**.

```python
import goldenphonetic as gp

gp.soundex("Robert")               # -> 'R163'   (American Soundex)
gp.metaphone("Thompson")           # -> '0MPSN'  (original Metaphone, Philips 1990)
gp.nysiis("Catherine")             # -> 'CATARAN'
gp.match_rating_codex("Byrne")     # -> 'BYRN'   (Match Rating Approach codex)
gp.match_rating_comparison("Byrne", "Boern")   # -> True
```

Every function is a byte-for-byte drop-in for the corresponding function in the
Python [`jellyfish`](https://pypi.org/project/jellyfish/) package (>= 1.0),
including jellyfish's exact uppercasing and Unicode (NFKD / grapheme) handling —
verified against `jellyfish` over a 6,000-input + 2,500-pair fuzz corpus
(`goldenphonetic-core/scripts/check_phonetic_parity.py`), with no `jellyfish` at
runtime.

## Functions

| Function | Returns | Notes |
|---|---|---|
| `soundex(s)` | `str` | American Soundex |
| `metaphone(s)` | `str` | Original Metaphone (Lawrence Philips, 1990) — **not** double-metaphone |
| `nysiis(s)` | `str` | NYSIIS |
| `match_rating_codex(s)` | `str` | Match Rating Approach codex |
| `match_rating_comparison(s1, s2)` | `bool` or `None` | MRA comparison |

## Error semantics (mirrors jellyfish exactly)

- `match_rating_codex` **raises `ValueError`** when the input contains any
  non-alphabetic, non-space character (`"O'Brien"`, `"Smith-Jones"`, `"123"`).
- `match_rating_comparison` **never raises** — it returns `None` when the two
  codices can't be compared: a codex-length difference of 3 or more
  (`match_rating_comparison("Tim", "Timothy") is None`), or either input rejected
  by the codex (non-alphabetic).

## Unicode

Inputs are uppercased with the same algorithm jellyfish uses, then
`soundex`/`metaphone` apply full NFKD decomposition (vendored as generated static
data — no `unicode-normalization` dependency) and `nysiis` segments grapheme
clusters (UAX-29 GB9 over a vendored combining-mark table). Accented names match
in both precomposed and decomposed form.

Part of the [golden suite](https://github.com/benseverndev-oss/goldenmatch).
