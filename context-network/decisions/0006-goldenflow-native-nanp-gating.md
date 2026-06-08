# 0006 — GoldenFlow: vectorize first, gate the native phone kernel to NANP-only

**Status:** accepted (2026-06-07, Ben) • **Shipped:** branch `claude/review-technical-work-en9SG` • **Architecture:** [../architecture/goldenflow-native-kernel.md](../architecture/goldenflow-native-kernel.md)

## Context
GoldenFlow's date/phone transforms called a Python library per row and were ~92 %
of a 1M-row run. The ask was an "Arrow-native / Rust kernel" like
`goldenmatch-native`. Measuring first (the repo's standing lesson) reframed it:
the win is two transforms, and most of it is reachable with **vectorized Polars**
alone — a per-row compiled parser is not the whole answer. The hard part was
parity: validating the Rust `phonenumber` port against the installed Python
`phonenumbers` library turned up a ~6 % divergence on international/ambiguous
numbers, which means a native phone accelerator **cannot be a drop-in residual
replacement** without changing cleaned values.

## Decision
1. **Vectorize in Polars first; native owns only the residual it's byte-identical
   on.** Tier 1 (Polars fast paths) does the bulk of the lift (76× date, 19×
   phone) with zero build complexity; the `goldenflow-native` kernel is tier 2,
   scoped to **phone only** (dates are already vectorized — a per-row chrono
   parser would be slower).
2. **NANP-only gating, parity-safe by construction.** The kernel runs in a
   `nanp_only` mode (emit only country-code-1, else null → Python) **and** the
   Python bridge accepts only canonical-NANP `^\+1[2-9]\d{9}$` output. So `phone`
   joins `_GATED_ON` and runs by default under `GOLDENFLOW_NATIVE=auto`, but only
   where native == phonenumbers is proven; international + ambiguous defer to
   Python. Turning native on/off changes speed, never values.
3. **Keep the per-row reference as the ultimate backstop.** `apply_with_residual`
   is three-tier (Polars → native → Python); each tier must agree with the
   reference on rows it resolves, asserted over corpora.
4. **Ship the kernel infrastructure in full** (separate abi3 wheel, loader,
   publish workflow, two CI lanes) mirroring `goldenmatch-native`, even though
   native's *net* value here is the modest phone residual — the infra is the
   reusable part and keeps the suite consistent.

## Consequences / honest flags
- **Native's measured lift is modest** (~4.3× on the canonical-NANP residual the
  Polars fast path can't reach) — because Tier-1 Polars already grabbed the clean
  case and the international residual (where native would help most) is exactly
  where parity fails. The headline wins are Tier-1's, not the kernel's.
- **`phone_national` / `phone_validate` stay pure Python** — no cheap canonical
  check to gate their output, and the leading-1 ambiguity affects the national
  number. `phone_country_code` IS gated (the code agrees on all NANP).
- **The divergence is a port bug, not metadata skew** — native matches Python for
  all 20 countries tested when given the right region; it only mis-strips a
  national leading `1` under the mismatched `"US"` default region. A future
  reconciliation could widen the gate beyond NANP.
- **CI builds the kernel** in a dedicated lane (the pure-Python matrix skips the
  native tests), so the parity gate is enforced, not just asserted locally.
- **`goldenflow[native]` is NOT in `[all]`** until the first wheel is on PyPI (an
  extra pointing at a non-PyPI package breaks `uv sync --all-packages`); uv
  resolves it locally via a path source.

## Alternatives not taken
- **Pure Rust kernel replacing the whole transform** (declined — Polars already
  vectorizes the common case faster than per-row Rust; and full int'l parity with
  the Python library isn't there).
- **Enable native on the full residual** (declined — diverges from phonenumbers
  on ~6 % of international/ambiguous numbers; would change cleaned values).
- **A native date kernel** (declined — Polars `str.to_date` coalesce is faster
  than per-row chrono and avoids the 2-digit-year parity hazard).
- **Leave `_GATED_ON` empty / native off by default** (the earlier interim
  state — declined once NANP-only gating made `auto` provably safe).

---
**Classification:** decision/accepted • **Last updated:** 2026-06-07
