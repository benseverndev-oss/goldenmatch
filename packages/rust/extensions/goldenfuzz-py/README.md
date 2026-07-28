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

Part of the [golden suite](https://github.com/benseverndev-oss/goldenmatch).
