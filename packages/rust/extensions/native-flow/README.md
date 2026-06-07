# goldenflow-native

Optional native (Rust/PyO3) acceleration kernels for
[GoldenFlow](https://github.com/benseverndev-oss/goldenmatch). The sibling of
`goldenmatch-native`; same abi3 / maturin / Arrow-zero-copy posture.

GoldenFlow stays a pure-Python wheel. `pip install goldenflow[native]` pulls
this compiled runtime, and `goldenflow.core._native_loader` discovers it (or
falls back to pure Python when it's absent). It is **off by default at runtime**
even when installed — see *Gating*.

## What it accelerates

The international **phone** family. GoldenFlow's pure-Python transforms already
resolve the common (NANP) case with vectorized Polars expressions; these kernels
accelerate the *residual* — international numbers that would otherwise hit the
`phonenumbers` library one row at a time. Kernels:

| function | returns |
|---|---|
| `phone_e164_arrow(array, region)` | Utf8 E.164 (null on parse failure) |
| `phone_national_arrow(array, region)` | Utf8 national format |
| `phone_country_code_arrow(array, region)` | Int64 country calling code |
| `phone_valid_arrow(array, region)` | Boolean validity |

Each takes an Arrow `Utf8`/`LargeUtf8` array (zero-copy in), returns an Arrow
array (zero-copy out), and releases the GIL around the compute loop. Any row it
can't parse comes back null so the Python reference settles it — the native path
is never *worse* than pure Python.

Dates are intentionally **not** a native kernel: the Polars `str.to_date`
coalesce fast path already resolves the 4-digit-year formats in vectorized Rust,
and a per-row `chrono` parser would be slower while reintroducing a 2-digit-year
parity hazard. The ragged date tail stays on `dateutil`.

## Gating & the open parity gap

`goldenflow.core._native_loader._GATED_ON` is **empty** by design. Measured
parity against the installed `phonenumbers` library is **not** byte-identical:
the Rust `phonenumber` port formats some international national numbers
differently (e.g. `+33 1 42 68 53 00` → native `+3342685300` vs python
`+33142685300` — it drops the national leading digit). Until that is reconciled
(metadata-version alignment, or restricting native acceptance to a proven
parity-safe subset such as NANP), `phone` stays out of `_GATED_ON`, so
`GOLDENFLOW_NATIVE=auto` (the default) never uses it.

- `GOLDENFLOW_NATIVE=1` enables it anyway (benchmark / parity lane).
- The parity test `packages/python/goldenflow/tests/transforms/test_native_parity.py`
  asserts native == `phonenumbers` on the NANP subset and pins the international
  divergence so a future fix flips the gate deliberately.

## Build

```bash
# In-tree dev build (drops goldenflow/_native.abi3.so):
python packages/python/goldenflow/scripts/build_native.py

# Distributable abi3 wheel:
cd packages/rust/extensions/native-flow && maturin build --release
```
