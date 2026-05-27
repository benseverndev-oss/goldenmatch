# goldenmatch-native

Optional native (Rust/PyO3) acceleration kernels for
[goldenmatch](https://github.com/benseverndev-oss/goldenmatch).

This is **not** a standalone package — it ships only the compiled `abi3`
extension that `goldenmatch` loads when present. Mirrors the
`polars` / `polars-runtime` split: the `goldenmatch` frontend stays a
pure-Python wheel, and the compiled runtime is distributed separately.

## Install

```bash
pip install "goldenmatch[native]"   # frontend + this runtime
```

Installing `goldenmatch` alone keeps the pure-Python paths; adding the
`native` extra pulls this package in and `goldenmatch` picks it up
automatically — no code change required. With the runtime present, the
auto-config planner routes simple/fast-box plans through the native Arrow
block-scorer (measured 1.7–3.7x faster at 1k–60k rows, identical clusters).

Set `GOLDENMATCH_PLANNER_BUCKET=0` to force the pure-Python scoring path even
with the runtime installed.

## What's inside

A single abi3 extension (`goldenmatch_native._native`) covering the gated
kernels: record fingerprinting, block scoring, pair generation, featurize, and
connected-components clustering. CPython 3.11+ on a per-platform wheel.

## License

MIT.
