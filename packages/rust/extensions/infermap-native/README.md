# infermap-native

Optional native (Rust/PyO3) acceleration kernels for
[`infermap`](https://github.com/benseverndev-oss/goldenmatch/tree/main/packages/python/infermap).

`infermap` stays a pure-Python wheel; `pip install infermap[native]` pulls this
separate abi3 package, and `infermap._native_loader` discovers it and dispatches to the
compiled kernels (thin shims over the pyo3-free `infermap-core`). Without it, `infermap`
uses its byte-identical pure-Python paths.

Not imported directly. See the package's `_native_loader` and the `INFERMAP_NATIVE`
environment gate.
