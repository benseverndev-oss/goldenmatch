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

## Gating: NANP-only (parity-safe, on by default)

`phone` **is** in `goldenflow.core._native_loader._GATED_ON`, so it runs under
`GOLDENFLOW_NATIVE=auto` (the default) — but only in a provably parity-safe
mode.

Characterization across 20 country metadata sets showed the Rust `phonenumber`
port is byte-identical to the Python `phonenumbers` library **except** when a
`+CC` international number is parsed with a mismatched default region (the
transforms pass `"US"`) and its national number starts with `1` (e.g.
`+33142685300` → native `+3342685300` — the port mis-applies US national-prefix
stripping). It also diverges on ambiguous leading-`1` inputs (`1234567890` →
native `+1234567890` vs python `+11234567890`).

Two gates make native authoritative only where it's proven correct:

1. **Kernel `nanp_only` mode** — emits a result only for country-code-1 numbers;
   everything else returns null and falls back to Python.
2. **Canonical-NANP acceptance** (in `transforms/_native.py`) — keeps only
   `^\+1[2-9]\d{9}$` outputs, dropping the malformed 9-digit results the
   leading-`1` ambiguity produces.

So native resolves the canonical-NANP residual the Polars fast path can't reach
(alpha like `1-800-FLOWERS`, extensions, `+1`-prefixed forms) ~4-5× faster, and
everything international/ambiguous defers to Python. `phone_country_code` is also
gated (country code agrees on all NANP); `phone_national`/`phone_validate` stay
pure Python (no cheap canonical check). Verified byte-identical to
`phonenumbers` over a 60k mixed corpus in
`packages/python/goldenflow/tests/transforms/test_native_parity.py`.

`GOLDENFLOW_NATIVE=0` forces pure Python; `=1` enables native for all components
(bench/parity lane, no nanp_only restriction — will diverge on intl).

## Build

```bash
# In-tree dev build (drops goldenflow/_native.abi3.so):
python packages/python/goldenflow/scripts/build_native.py

# Distributable abi3 wheel:
cd packages/rust/extensions/native-flow && maturin build --release
```
