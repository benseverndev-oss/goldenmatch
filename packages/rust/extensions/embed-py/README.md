# goldenmatch-embed

Local ONNX embedder for [GoldenMatch](https://github.com/benseverndev-oss/goldenmatch)
SQL UDFs, shipped as a standalone abi3 wheel.

A thin [PyO3](https://pyo3.rs) wrapper over the pyo3-free `goldenembed` Rust
crate. `GoldenEmbed.load(dir).embed([...])` runs the in-house char-n-gram
featurizer plus an ONNX projection with no torch and no network access. All of
the `ort` (onnxruntime) dependency is confined to this wheel so the rest of the
suite (the pgrx Postgres extension, the DataFusion FFI surface) can keep linking
`goldenembed` without embedding CPython.

```python
from goldenmatch_embed import GoldenEmbed

model = GoldenEmbed.load("/path/to/saved/model")  # config.json + model.onnx
vecs = model.embed(["acme corp", "acme corporation"])  # list[list[float]]
print(model.dim)
```

This package is normally imported by the `goldenmatch-duckdb` embed UDF rather
than used directly.
