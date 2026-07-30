# goldenfuzz

Fast, **byte-identical-to-rapidfuzz** fuzzy-string scorers, plus a one-vs-many
`extract` / `cdist` / `BatchComparator` API. A thin PyO3 wheel over the pyo3-free
`goldenfuzz-core` Rust crate.

```python
import goldenfuzz as gf

gf.jaro_winkler("jonathan", "jonathon")      # -> 0.95...
gf.levenshtein("kitten", "sitting")          # normalized similarity in [0, 1]
gf.indel("fuzzy wuzzy", "wuzzy fuzzy")

# one-vs-many top-k (query bitmap built once)
gf.extract("jonathan smith",
           ["jon smith", "jane doe", "jonathan smith"],
           scorer="jaro_winkler", score_cutoff=0.7, limit=2)
# -> [(2, 1.0), (0, 0.9...)]

# reuse a prepared query across many choices
bc = gf.BatchComparator("acme corporation")
[bc.jaro_winkler(c) for c in choices]
```

Scorers: `jaro_winkler` | `levenshtein` | `indel`, each returning normalized
similarity in `[0, 1]`, byte-identical to the corresponding rapidfuzz metric
(proven by an oracle fuzz in `goldenfuzz-core`). On short strings (names,
addresses) goldenfuzz is faster than rapidfuzz; on documents it matches/beats on
jaro-winkler and levenshtein.

## `fuzz.*` composite scorers (drop-in for rapidfuzz `fuzz`)

The full weighted/token/partial family, each returning a score in `[0, 100]`:

```python
gf.ratio("fname", "first_name")              # normalized indel, x100
gf.partial_ratio("fname", "first_name")      # best alignment of the shorter in the longer
gf.token_sort_ratio("a b c", "c b a")        # -> 100.0
gf.token_set_ratio("fuzzy was a bear", "fuzzy fuzzy was a bear")  # -> 100.0
gf.WRatio("fname", "first_name")             # weighted composite (rapidfuzz's fuzz.WRatio)
gf.QRatio("this is a test", "this is a test!")
```

Also `token_ratio`, `partial_token_sort_ratio`, `partial_token_set_ratio`,
`partial_token_ratio`. Every one is verified byte-identical to `rapidfuzz.fuzz`
over a 6.5k-pair corpus (all 10 scorers, worst abs diff `0.0`), so it is a true
drop-in — the value is the same, computed by our own kernel with no rapidfuzz at
runtime.

Part of the [golden suite](https://github.com/benseverndev-oss/goldenmatch).
