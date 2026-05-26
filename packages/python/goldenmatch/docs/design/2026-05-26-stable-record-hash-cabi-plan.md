# Stable record hash + identity ID: canonicalization spec & C-ABI plan

Date: 2026-05-26
Status: proposed (the "C-ABI plan" the decision matrix gates the hashing kernel on)
Companions: `2026-05-25-native-acceleration-decision-matrix.md` (row: "Stable
record hashing / ID generation — Rust, High, exempt from the gates"),
`2026-05-25-rust-acceleration-roadmap.md`.

## TL;DR

- The decision matrix lists stable record hashing as the one **High** native
  item that is **exempt from the perf gates** — the rationale is *correctness +
  cross-surface determinism*, not speed. It also says: do it "on portability
  grounds, when a second surface needs it (**gated on a C ABI plan, not
  before**)." This doc is that plan.
- There is **no single record hash today** — ~12 unrelated hashing schemes are
  scattered across the package (different algorithms, truncations, and
  canonicalizations). Most are single-surface and fine as-is.
- Exactly **one** of them crosses surfaces *and* determines durable identity:
  `identity/resolve.py::_hash_payload` → `derive_record_id`, which mints a
  record's `{source}:hash:{12}` ID from `sha256(json.dumps(payload,
  sort_keys=True, default=str))`. Python's `json.dumps` byte formatting is **not
  reproducible** in Rust / DuckDB / Node, so any non-Python surface that derives
  the same record's ID will silently mint a *different* ID and break identity
  resolution across surfaces.
- Plan: pin a **language-agnostic canonicalization spec**, ship a Rust kernel
  that reproduces today's Python bytes **exactly** (parity-gated, default-off),
  switch Python to it behind the existing `native_enabled(...)` gate, and only
  then export a **C ABI** when the second surface (pgrx / DuckDB / Node SDK)
  actually adopts it. Speed is explicitly *not* the justification.

## 1. The hashing landscape today (survey)

Grep for `hashlib.` across `packages/python/goldenmatch/goldenmatch` returns
these distinct schemes. Categorized by whether identity/determinism crosses a
process or language boundary:

### Cross-surface + identity-bearing (the target)
| Site | Scheme | Used for |
|---|---|---|
| `identity/resolve.py::_hash_payload` / `derive_record_id` | `sha256(json.dumps(payload, sort_keys=True, default=str))`, `[:12]` for the ID | **Durable record→entity ID** when no natural PK. Served by Python CLI/REST/MCP **and** the pgrx/DuckDB identity functions. |

This is the only hash whose *output is a persisted identifier* that more than
one language surface can compute for the same input. It is the whole reason the
matrix calls hashing "exempt from the gates."

### Single-surface / internal (leave alone)
| Site | Scheme | Why it does NOT need unifying |
|---|---|---|
| `core/memory/corrections.py::compute_record_hash` / `compute_field_hash` | `sha256("\|".join(str(v)))[:16]` | Memory staleness check; written + read by the same Python process. Never recomputed by another language. |
| `distributed/record_store.py::_sanitize_signature` | `sha256(sig)[:16]` | DuckDB *table-name* sanitization. Internal, ephemeral. |
| `core/boost.py::_column_hash` | `md5(sorted cols)[:12]` | Cache key for a HF reranker; Python-only, heavy path. |
| `db/metadata.py::config_hash` | `md5(...)[:16]` | Config-change detection in the local job DB. |
| `core/autoconfig_memory.py` | `sha256(repr(key))[:16]` | Cross-run config cache key; `repr()` is Python-only by design. |
| `core/matchkey.py` blocking-key | `blake2b(...)` | Block-key column; already chosen over salted `hash()` for cross-process stability *within Python*. Polars-vectorized. |
| `embeddings/*`, `utils/transforms.py` PPRL | `sha256` / `blake2b` / HMAC | Embedding cache keys + PPRL Bloom hashing — domain-specific, parity defined by their own specs. |

**Non-goal:** do not consolidate the single-surface hashes into the canonical
kernel. They carry no cross-language determinism requirement, several encode
Python-only inputs (`repr()`), and forcing them through one kernel adds churn
and risk for zero correctness benefit. The matrix's "one canonical impl" is
about the *identity* hash, not every `hashlib` call.

## 2. The actual problem: canonicalization drift

`_hash_payload` is:

```python
def _hash_payload(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
```

The `sha256` is trivially portable. The **`json.dumps(...)` byte string is
not.** CPython's encoder has behavior other languages do not match by default:

- Item/key separators default to `", "` and `": "` (a space after each). Rust
  `serde_json` emits no spaces; DuckDB's `to_json` differs again.
- Non-ASCII escapes to `\uXXXX` (`ensure_ascii=True`); most other encoders emit
  raw UTF-8.
- Floats format via Python `repr` (shortest round-trip). Rust/Node differ on
  edge cases (`1.0` vs `1`, exponent form, `-0.0`).
- `default=str` stringifies non-JSON values (datetimes, Decimals, UUIDs) using
  **Python's** `str()`, which other languages cannot reproduce without the same
  rules.
- Key ordering is `sort_keys=True` — the one part that *is* portable.

So a Postgres or DuckDB identity function deriving the ID for the same row will
compute different bytes → different `sha256` → a different `{source}:hash:{12}`
ID, and the cross-surface identity graph splits the same record into two
entities. This is a correctness bug waiting for the first non-Python writer, not
a perf concern — which is exactly why it's gate-exempt.

## 3. Decision

Define a **canonical record fingerprint** as an explicit, language-agnostic byte
spec, implement it once in Rust, expose it to Python first (PyO3, behind the
existing `native_enabled` gate) and to other surfaces later (C ABI). The hash
algorithm stays **sha256** (ubiquitous, fast everywhere — a hand-rolled hash
would *violate* Gate 1). The kernel's value is the **canonicalization**, not the
digest.

### 3.1 Canonicalization spec (v1)

Given a record as an ordered set of `(field_name, value)` entries:

1. **Field selection:** drop any field whose name starts with `__` (matches
   `_row_to_payload` today).
2. **Key order:** sort field names by Unicode code point ascending.
3. **Value normalization** (the cross-language-critical part) — render each
   value to a canonical UTF-8 string:
   - null/None → the literal empty token (define one sentinel, e.g. `\x00`
     marker, never a printable that could collide with a real value).
   - bool → `true` / `false`.
   - integer → base-10, no leading zeros, leading `-` for negatives.
   - float → shortest round-trip decimal with a single fixed rule (specify:
     Ryū/Grisu; `NaN`/`Inf` rejected or mapped to fixed tokens; `-0.0`→`0`).
   - string → raw UTF-8 bytes (NFC-normalized — pin the Unicode form so
     accented data hashes equally everywhere).
   - bytes → raw bytes.
   - datetime/UUID/Decimal → an explicit ISO-8601 / canonical-string rule (NOT
     Python `str()`); this is where `default=str` is replaced by a spec.
4. **Framing:** join as `field \x1f value \x1e` (unit/record separators) so a
   value containing the separator cannot forge a different field boundary
   (the current `"|".join` is ambiguous — `a|b` over `["a","b"]` vs `["a|b"]`).
5. **Digest:** `sha256` over the framed UTF-8 bytes; lowercase hex.
6. **ID form:** `{source}:hash:{first 12 hex}` (unchanged), full digest stored.

v1 must produce bytes that let us *migrate*: see §5.

### 3.2 Surface API

- Rust: `fn record_fingerprint(fields: &[(&str, Value)]) -> String` in a new
  `hash.rs` in `packages/rust/extensions/native`, registered in `lib.rs`.
- Python: `native_module().record_fingerprint(...)` behind
  `native_enabled("hashing")`; `identity/resolve.py::_hash_payload` calls it when
  enabled, else the current pure-Python path. Add `"hashing"` to `_GATED_ON`
  **only after** parity + a migration decision (§5).
- C ABI: a `#[no_mangle] extern "C"` thin wrapper (`gm_record_fingerprint(const
  char* json_utf8, char* out64)`) added **only** when pgrx / DuckDB / a Node
  SDK actually wires it. Not in this phase.

## 4. Gates (this kernel still has gates — different ones)

The perf gates don't apply (it's not a hot loop), but correctness gates do:

1. **Byte-parity, then a deliberate cutover.** First ship the Rust kernel under
   a *new* spec name and prove it deterministic + identical to itself across a
   fixture battery (unicode, floats, nulls, datetimes, nested-as-string,
   separator-injection adversarial rows). The Rust output will **not** byte-match
   today's `json.dumps` blob (that's the point — the spec fixes the
   non-portable formatting), so this is a *format change*, governed by §5, not a
   silent swap.
2. **No silent ID churn.** Because the canonical bytes differ from today's
   `json.dumps` bytes, turning this on **re-mints every payload-derived
   record ID**. That is a migration event, not a parity flip.
3. **Determinism battery in CI**, run from at least two languages once the C ABI
   lands (Python vs Rust vs, eventually, a DuckDB SQL call) asserting identical
   hex on the same fixtures.

## 5. Migration: the real cost

Existing identity stores hold `{source}:hash:{12}` IDs minted by the current
`json.dumps` scheme. Switching canonicalization changes those IDs. Options,
to decide before flipping `_GATED_ON`:

- **A. New records only, versioned prefix.** Mint new IDs as
  `{source}:h1:{12}` (version the scheme in the ID). Old `:hash:` IDs keep
  resolving via the legacy path; the resolver tries v1 then legacy. No rewrite
  of stored data; two schemes coexist. **Recommended** — lowest risk.
- **B. One-time re-key migration.** Recompute every record ID under v1 and
  rewrite alias/edge tables in a migration script. Clean end-state, but a
  breaking data migration for anyone with a populated store.
- **C. Freeze legacy, only adopt v1 on a second surface.** Don't touch the
  Python path at all; pgrx/DuckDB get v1 and Python keeps legacy — **rejected**,
  this *guarantees* the cross-surface split we're trying to prevent.

## 6. Phasing

1. **Spec + Rust kernel + Python binding, default-off.** Land `hash.rs`,
   PyO3-register, add the fixture/determinism battery. `native_enabled("hashing")`
   exists but `"hashing"` stays out of `_GATED_ON`. No behavior change.
2. **Migration decision (§5) + versioned IDs.** Implement option A (versioned
   `:h1:` prefix + legacy-fallback resolver) in `identity/resolve.py`. Now
   turning the gate on is safe (old IDs still resolve).
3. **C ABI + second surface.** Export `gm_record_fingerprint`; wire the pgrx +
   DuckDB identity functions to call it instead of embedding CPython for this
   one operation; add the cross-language determinism CI lane. This is the first
   concrete step of the roadmap's Phase 4 "decouple SQL extensions from embedded
   CPython," scoped to one well-defined function.
4. **(Optional) Node/C# SDK** consumes the same C ABI when those land.

## 7. Why not just do the perf items instead

For the record, the other roadmap leftovers were checked and are *not* clean
"just implement" work right now:

- **Arrow bridge (roadmap Phase 3):** the correctness motivation (lossy `str()`
  on tuple-keyed `pair_scores`) is **already fixed** — `api.rs` now serves
  clusters/pairs as structured `Vec<ClusterMember>`/`Vec<ScoredPair>` and
  `convert.rs` already has Arrow IPC helpers. What remains is a low-priority,
  cross-surface perf swap (golden/matched DataFrame returns → Arrow IPC) that
  also touches the CI-only pgrx crate. Not banked here.
- **MST / `transitivity_rate` (Phase 1 tail):** the matrix measured clustering
  at ~on-par with Python; the split-loop hang is fixed structurally (work
  budget, PR #516), so there is no perf or correctness forcing function.
- **Pair canonicalize / dedup:** matrix verdict is measure-first / likely no
  kernel (Polars `group_by` already does it; 50-100M cost is Ray shuffle).
- **Block scoring 5M confirmation:** Python-only baseline OOMs/times out, so the
  native path being the *only* one that completes 5M single-node is itself the
  result; nothing to write.

So the hashing kernel is the one remaining item with a real, non-speculative
justification — and it is gated on this plan, which is now written.
