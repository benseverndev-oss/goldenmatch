# GoldenFlow Arrow-native kernel (vectorized fast paths + optional Rust phone kernel)

GoldenFlow's performance arc, same Arrow-native theme as the rest of the suite:
the two transforms that dominated a run — date and phone normalization — are
re-expressed as vectorized Polars fast paths with a per-row fallback, plus an
optional compiled `goldenflow-native` kernel for the phone residual. The
GoldenFlow sibling of `goldenmatch-native`.

**Status:** SHIPPED (2026-06-07, branch `claude/review-technical-work-en9SG`).
**Decision:** [../decisions/0006-goldenflow-native-nanp-gating.md](../decisions/0006-goldenflow-native-nanp-gating.md).

> **Wave 0 update (2026-07, ADR [../decisions/0031-goldenflow-reference-mode-identifiers-wasm.md](../decisions/0031-goldenflow-reference-mode-identifiers-wasm.md)):**
> the kernels now live in a pyo3-free `goldenflow-core` crate; `native-flow` is a
> thin PyO3 shim over it and a new `goldenflow-wasm` crate surfaces the identifier
> kernels to the edge. The loader moved to reference-mode (`_has_symbol` +
> `_COMPONENT_SYMBOLS` + `_FALLBACK_ONLY`) — `GOLDENFLOW_NATIVE=auto` now runs
> native wherever a component's kernel symbol exists (phone + the checksummed
> identifiers cc/iban/isbn/ean/vat), and `_GATED_ON` below is retained only as
> documentation of the byte-exact phone sign-off. `phone_validate` is now
> `_FALLBACK_ONLY`. The phone-kernel content below is still accurate for that
> component.
**Code-level notes:** `packages/python/goldenflow/CLAUDE.md` (Performance section),
`packages/rust/extensions/native-flow/README.md`. **Docs-site:** `goldenflow/performance.mdx`.

## The measurement that framed it
On a realistic messy 1M-row frame, `date_iso8601` (27.6 s) and `phone_e164`
(16.5 s) were **~92 % of a ~48 s run** — both because they called a Python
library (`dateutil` / `phonenumbers`) once per row via `map_elements`
(~0.04–0.06 M rows/s). Everything else was already sub-second. This is the
"measure wall-clock before designing" lesson in action: the win concentrates in
two transforms, not the library.

## Tier 1 — vectorized Polars fast paths (no Rust)
`transforms/_fastpath.py::apply_with_residual` is a three-tier resolver: a
vectorized Polars `fast_expr` resolves the well-formed common case in Rust,
leaving uncertain rows null; an optional native kernel handles the residual; the
per-row `dateutil`/`phonenumbers` reference settles the rest. **Parity-safe by
construction** — each tier only claims rows it resolves identically to the
reference, asserted over a random corpus (`tests/transforms/test_fastpath_parity.py`).

Load-bearing guards (each caught a real divergence in testing):
- **Dates** require a 4-digit year — chrono's `%Y` greedily eats 2-digit years
  (`02/02/93` → 0093) but dateutil maps them to 1993.
- **E.164** only claims NANP `^[2-9]\d{9}$` / `^1[2-9]\d{9}$`, **no letters, no
  `+`** — a leading 1 is the country code to phonenumbers, and an int'l `+CC`
  number can strip to exactly 10 digits (German `+4930123456` → `4930123456`,
  else mis-NANP'd to `+14930123456`).

Measured @ 1M: `date_iso8601` **76×**, `phone_e164` **19×**, `phone_digits`
**4.9×**; **~14× end-to-end**, output unchanged.

## Tier 2 — `goldenflow-native` (optional Rust/PyO3 kernel)
A separate maturin/PyO3 **abi3** crate at `packages/rust/extensions/native-flow/`
(mirrors `goldenmatch-native`; pymodule `_native`), shipped as the
`goldenflow-native` wheel (`pip install goldenflow[native]`). Arrow zero-copy
phone kernels (`phone_e164/national/country_code/valid_arrow`) over the Rust
`phonenumber` crate (a libphonenumber port). Loader
`goldenflow/core/_native_loader.py` discovery: `goldenflow._native` (in-tree
`scripts/build_native.py`) → `goldenflow_native._native` (wheel) → pure Python.
Wired as the tier-2 `native_fn` of `apply_with_residual`, so native only touches
the residual the Polars fast path couldn't normalize.

**Why phone-only:** dates are already vectorized by Polars (a per-row chrono
parser would be slower and reintroduce the 2-digit hazard); the phone residual
(alpha, extensions, `+1` forms, international) is where a compiled parser helps.

## The gate: NANP-only, parity-safe, on by default
See the decision for the full reasoning. Short version: the Rust `phonenumber`
port is **not** byte-identical to the Python `phonenumbers` library — it
diverges on (a) `+CC` int'l numbers parsed with the mismatched `"US"` default
region whose national number starts with `1` (`+33142685300` → `+3342685300`),
and (b) ambiguous leading-`1` inputs (`1234567890`). Two gates confine native to
where it's proven identical: the kernel's `nanp_only` mode (emit only
country-code-1, else null → Python) **and** a canonical-NANP `^\+1[2-9]\d{9}$`
acceptance check in `_native.py`. So `phone` is in `_GATED_ON` and runs under
`GOLDENFLOW_NATIVE=auto`; it resolves the canonical-NANP residual ~**4.3×**
faster and defers all international/ambiguous to Python. `phone_country_code` is
also gated (the code agrees on all NANP); `phone_national`/`phone_validate` stay
pure Python (no cheap canonical check).

`GOLDENFLOW_NATIVE`: `0` force Python · `auto`/unset gated native (default) ·
`1` native-everywhere bench/parity lane (no nanp_only — **can** diverge on int'l).

## Distribution + CI
- **Publish:** `.github/workflows/publish-goldenflow-native.yml` (mirrors
  `publish-goldenmatch-native.yml`) — abi3 wheels for linux x86_64/aarch64,
  windows x64, macOS x86_64+aarch64 (both on `macos-14`), + sdist, on a
  `goldenflow-native-v*` release tag. Version lives in THREE lockstep spots
  (Cargo.toml, pyproject.toml, the `__init__.py` fallback — maturin reads
  pyproject).
- **CI:** two lanes gated by a `native_flow` paths-filter (mirror
  goldenmatch's `native`/`native_wheel`): `native_flow` builds the in-tree
  `.so` and runs the parity suite with the ext **present** (so the gated native
  path executes and is asserted byte-identical to phonenumbers); `native_flow_wheel`
  builds the abi3 wheel, installs it without the in-tree `.so`, and smoke-tests
  the loader-discovers-the-wheel split + the `nanp_only` kernel.

## Verification
- Parity asserted byte-identical to the pure references over corpora: a random
  date/phone corpus (`test_fastpath_parity.py`) and a 60k mixed phone corpus
  including 24 countries × 3 formats (`test_native_parity.py`) — 0 mismatches.
- `GOLDENFLOW_NATIVE` 0/auto/1 lanes all green; `cargo fmt`/`clippy -D warnings`
  + `ruff` clean.

## Relationship to existing code
- **Parallels** `goldenmatch-native` (the in-tree-`.so` → wheel → pure-Python
  loader split, abi3, the three-spot version lockstep).
- Native is **opt-in and additive** — the pure-Python `goldenflow` wheel is
  unchanged; turning the kernel on/off only changes speed, never cleaned values.

---
**Classification:** architecture/shipped • **Last updated:** 2026-06-07
