# goldenanalysis-native

Optional native (Rust/PyO3) acceleration kernels for
[`goldenanalysis`](https://github.com/benseverndev-oss/goldenmatch/tree/main/packages/python/goldenanalysis).

You don't import this directly. `goldenanalysis` stays a pure-Python wheel;
`pip install goldenanalysis[native]` pulls this compiled abi3 wheel, and
`goldenanalysis.core._native_loader` discovers it (falling back to the pure-Python
path when it isn't present).

The kernel mirrors the pure-Python aggregation loops in
`goldenanalysis/core/aggregate.py` (`histogram`, `quantile`) value-for-value, reading
input as a Float64 Arrow array (zero-copy, C Data Interface). A primitive is only
used under `GOLDENANALYSIS_NATIVE=auto` once it has cleared
`_native_loader._GATED_ON` — proven byte-identical **and** measured to move the wall
on a real shape. The two-crate split (pyo3-free `analysis-core` + this abi3 shim)
mirrors `goldencheck-core`/`goldencheck-native`.

Build in-tree for local dev (drops `goldenanalysis/_native.abi3.so`):

```bash
uv run python scripts/build_analysis_native.py
```

MIT.
