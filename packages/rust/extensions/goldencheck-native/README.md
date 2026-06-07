# goldencheck-native

Optional native (Rust/PyO3) acceleration kernels for
[GoldenCheck](https://github.com/benseverndev-oss/goldenmatch/tree/main/packages/python/goldencheck).

`goldencheck` is a pure-Python wheel; this package ships the compiled abi3
`_native` extension that accelerates GoldenCheck's CPU-bound deep-profiling work
(Benford conformance, composite-key and functional-dependency mining). It is
pulled in via:

```bash
pip install goldencheck[native]
```

You never import this directly -- `goldencheck.core._native_loader` discovers it
and falls back to the pure-Python paths when it isn't installed. Behaviour is
identical either way; the native path only changes wall-clock time.

The kernels live in the pyo3-free `goldencheck-core` crate; this crate is the
thin Arrow-reading PyO3 shim over it.
