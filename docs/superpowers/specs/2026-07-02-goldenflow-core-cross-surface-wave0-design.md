# GoldenFlow owned-kernel core + cross-surface Wave 0 design

Date: 2026-07-02
Status: design approved (brainstorm), spec under review
Program: "GoldenFlow → owned Rust kernels, Rust-reference, cross-surface"
Scope of THIS doc: Wave 0 (foundation + identifier pilot). Later waves sketched in the appendix.

## Context

The suite decided 2026-07-01 that the **Rust kernel is the reference implementation** of each
primitive; pure-Python/TS are non-authoritative lossy fallbacks
(`docs/design/2026-07-01-rust-is-the-reference-roadmap.md`). goldenmatch, goldencheck, and
goldenanalysis have been flipped to the `_has_symbol` reference-mode loader. **goldenflow is the
last package still on the pre-inversion loader**: its `core/_native_loader.py` `auto` branch
returns `_native is not None and component in _GATED_ON` with `_GATED_ON = frozenset({"phone"})`
— a family allow-list, not the reference-mode symbol probe.

goldenflow also never got the `-core`/`-native`/`-wasm` crate split every other package has. Its
only Rust lives in `packages/rust/extensions/native-flow/` — a direct PyO3 `#[pymodule] _native`
crate wrapping the `phonenumber` crate. There is no pyo3-free core, so nothing can surface to
WASM/TS or SQL. Of goldenflow's 76 transforms, only the phone family has a Rust kernel; everything
else is pure-Polars expr or per-row Python.

**Goal (owner's framing):** the kernels should be *owned Rust that goldenflow controls*, not
"Polars expr counts as Rust." Correctness is proven by **byte-output comparison** against the
owned kernel as the oracle. Wave 0 establishes the foundation and proves it end-to-end across
**Python-native + WASM/TS** on a new, greenfield capability (checksummed identifiers) plus the
migrated phone kernel. SQL is a later wave.

## Wave 0 goals

1. Create `goldenflow-core` (pyo3-free) as the single home of the reference kernels.
2. Re-home the existing phone logic into `goldenflow-core`; `native-flow` becomes a thin PyO3 shim
   over it (output-identical — pure refactor).
3. Add a new owned-kernel capability: **checksummed identifiers** — payment card (Luhn), IBAN,
   ISBN, EAN/UPC, and EU VAT — as native-first transforms with no Polars authority tier.
4. Surface the identifier kernels to **WASM/TS** via a new `goldenflow-wasm` crate, reusing the
   `goldenmatch-wasm-runtime` substrate.
5. Stand up a **byte-output parity harness**: one checked-in corpus, `goldenflow-core` defines the
   expected output (oracle), and native / WASM-TS / Python-fallback are each asserted byte-identical
   against it in CI.
6. Flip goldenflow's loader to the reference-mode `_has_symbol` semantics, completing the suite-wide
   Rust-reference inversion for the last package.

### Non-goals (Wave 0)

- SQL surface (DataFusion/pgrx) — deferred to a later wave.
- i18n names, i18n addresses, validation-kernel hardening — later waves (appendix).
- Migrating the trivial Polars-expr / per-row-Python transforms to owned kernels — Wave D, last.
- National-ID checksums beyond EU VAT — later.
- Any change to phone semantics. Phone migration is a pure refactor; `phone_validate` stays
  pure-Python and `phone_digits` stays pure Polars (unchanged product decisions).

## Architecture

### Crate layout

```
packages/rust/extensions/
  goldenflow-core/        NEW — pyo3-free, the reference kernels (owned; the only place answers are computed)
    Cargo.toml            no pyo3, no arrow; std + tiny deps only (keeps it wasm32-clean)
    src/lib.rs            public fns operating on &str -> String / bool / Option<String>
    src/identifiers/
      mod.rs
      luhn.rs             card validate/format/mask
      iban.rs             mod-97 validate + grouped/electronic format
      isbn.rs             isbn10/13 checksum + normalize-to-13
      ean.rs              ean-8/13 + upc-a check digit
      vat.rs              per-country structural + checksum where a public algorithm exists
    src/phone.rs          MOVED from native-flow (logic unchanged)
    src/util.rs           MOVED from native-flow

  native-flow/            PyO3 abi3 shim — now depends on goldenflow-core
    Cargo.toml            adds `goldenflow-core = { path = "../goldenflow-core" }`
    src/lib.rs            #[pymodule] _native; Arrow-array in/out, GIL released; calls goldenflow-core

  goldenflow-wasm/        NEW — wasm-bindgen surface over goldenflow-core (identifiers only in Wave 0)
    Cargo.toml            wasm-bindgen; depends on goldenflow-core
    src/lib.rs            #[wasm_bindgen] thin exports; string in/out

packages/typescript/
  goldenmatch-wasm-runtime/   REUSED — zero-dep byte-loader / env-detect / registry substrate
  goldenflow/
    src/core/wasm/{backend.ts, loader.ts, index.ts}   NEW — enableWasm(), refuses-when-unenabled
    src/core/transforms/identifiers.ts                 NEW/extended — pure-TS fallback + wasm dispatch
```

### Layering rule

`goldenflow-core` is the only crate that *computes* an identifier or phone answer. `native-flow`
and `goldenflow-wasm` are marshaling shims (bytes in, call core, bytes out). The Python and TS
pure implementations are fallbacks that MUST reproduce core's bytes (enforced by the harness) and
are otherwise allowed to be lossy only where explicitly documented (none in Wave 0 — identifiers
are exactly reproducible in every language).

### Why identifiers are the right pilot

Pure functions over ASCII/Unicode strings, deterministic, no data files, no float, no HashMap-seed
nondeterminism. Output is a canonical string or a bool, so **byte comparison is exact and total** —
the ideal shape for the parity harness. They are also net-new capability (goldenflow ships only
`ssn_*`/`ein_*` today), so there is no Polars incumbent to fight; the pattern is proven on
greenfield before Wave D takes on incumbents.

## Kernel surface (Wave 0)

Each family gets a validate predicate and a canonicalizer; registration via `@register_transform`
in `transforms/identifiers.py`, mode `series` (native over the whole Arrow column).

| Transform          | Signature (core)                    | Behavior |
|--------------------|-------------------------------------|----------|
| `cc_validate`      | `&str -> bool`                      | strip separators, Luhn + length/brand table |
| `cc_format`        | `&str -> Option<String>`            | group by brand (e.g. 4-4-4-4); null if invalid |
| `cc_mask`          | `&str -> Option<String>`            | keep last 4, mask rest; null if not card-shaped |
| `iban_validate`    | `&str -> bool`                      | length-by-country + mod-97 == 1 |
| `iban_format`      | `&str -> Option<String>`            | uppercase, grouped in 4s; null if invalid |
| `isbn_validate`    | `&str -> bool`                      | ISBN-10 (mod 11) or ISBN-13 (mod 10) |
| `isbn_normalize`   | `&str -> Option<String>`            | canonical ISBN-13, no separators; null if invalid |
| `ean_validate`     | `&str -> bool`                      | EAN-8/13, UPC-A check digit |
| `vat_validate`     | `&str -> bool`                      | country prefix + structural length; checksum where a public algorithm exists |
| `vat_format`       | `&str -> Option<String>`            | canonical uppercase, no spaces/dots; null if structurally invalid |

**VAT scope note (bounds the pilot):** `vat_validate` does full checksum validation only for the
member states with a documented public algorithm (e.g. DE mod-11, NL, ES, IT, FR mod-97 variants).
For the rest it validates the country prefix + structural pattern (length + charset) and returns
true on structural match. The exact per-country checksum coverage list lives in the plan; the
design commitment is: **structural for all supported prefixes, checksum where the algorithm is
public.** Unsupported prefixes → false (documented).

Non-goals inside identifiers for Wave 0: national ID numbers (SSN beyond the existing formatter,
NINO, etc.), routing/SWIFT, IMEI. Later waves.

## Data flow (native-first, no Polars authority tier)

```
transform(series: pl.Series) ->            # mode="series"
  if native available AND _has_symbol(component):
      arrow = series.to_arrow()
      out   = _native.<fn>(arrow)          # goldenflow-core over the whole array, GIL released
      return pl.from_arrow(out)
  else:                                     # no wheel -> lossy fallback (byte-exact here)
      return series.map_elements(py_reference_fn)
```

No `apply_with_residual` three-tier path for the new kernels — there is no Polars fast expr in the
authority path, by design. Phone keeps its existing `apply_with_residual` (Polars fast-path →
native → Python), now with the native tier calling `goldenflow-core`.

## Loader / gating (the reference-mode flip)

`core/_native_loader.py` changes to match the rest of the suite:

```python
# reference-mode: Rust is the reference; native runs wherever a kernel symbol exists.
def _use_native(component: str) -> bool:
    if _MODE == 0:      # force fallback
        return False
    if _MODE == 1:      # require native
        return True
    return _native is not None and _has_symbol(component)   # auto (default)
```

- `_GATED_ON` the allow-list is replaced by `_has_symbol` (floor-symbol probe, wheel-skew safe —
  same pattern as goldenmatch #1343).
- Phone stays correct because its symbols exist and its NANP canonical-acceptance guard in
  `_native.py` is unchanged — the guard, not the allow-list, is what keeps phone byte-faithful.
- `phone_validate` / `phone_digits` have no native symbol, so `_has_symbol` returns false and they
  stay Python/Polars automatically — the documented `_NO_KERNEL` category, not a special-case.
- `GOLDENFLOW_NATIVE` env unchanged: `0` force-fallback, `1` require-native, `auto`/unset =
  reference-mode.

## Byte-output parity harness

Single source of truth corpus: `packages/python/goldenflow/tests/parity/identifiers_corpus.jsonl`
(also symlinked/copied into the TS package's test fixtures via a small sync check, or read from a
shared path — plan decides). Each line: `{"transform": "...", "input": "...", "expected": "..."}`
where `expected` is a string, `"true"`/`"false"`, or `null`.

**`goldenflow-core` defines `expected`.** The corpus is generated by running the core kernels and
checked in; a `--regenerate` guard in CI fails if the checked-in corpus is stale vs the kernels
(so the oracle can't silently drift).

Three consumers assert byte-identical against the *same* corpus:

1. **Rust** — `goldenflow-core` unit tests over the corpus (the oracle self-check + fixtures).
2. **Python** — a parametrized test runs BOTH the native path and the pure-Python fallback over the
   corpus and asserts each equals `expected` byte-for-byte. Native is the primary assertion; the
   fallback is tested as "must match the oracle" (Rust-reference test orientation).
3. **TS/WASM** — a vitest parity test runs the WASM path (and the pure-TS fallback) over the corpus
   and asserts byte-identical.

CI: a `flow-parity` job builds the native wheel + the wasm bundle and runs all three. The native
wheel becomes a **required** build artifact for goldenflow CI (the Rust-reference CI inversion,
applied here), with a separate `GOLDENFLOW_NATIVE=0` fallback lane asserting the pure-Python path.

## Error handling

- Kernels never panic on user input: malformed input → `false` (validate) or `None` → null
  (canonicalize). No exceptions cross the FFI/WASM boundary.
- Null input passes through as null on every surface (Polars null, TS undefined/null).
- The shim marshals a whole Arrow array; a per-element error is represented as null in the output
  array, never a raised error, so a single bad row can't fail a batch.
- WASM: `enableWasm()` is async and returns `false` (stays pure-TS) on any load failure — the
  runtime substrate's existing contract.

## Testing

- TDD: kernel unit tests in Rust first (each family: valid, invalid-checksum, separators,
  case, null/empty, boundary lengths).
- Python: the parity test above + registry/selector tests for the new transforms (auto_apply
  posture: identifiers are `auto_apply=False` — they validate/normalize on request, not
  zero-config, matching `ssn_format`).
- TS: parity test + unit tests for the pure-TS fallback and the wasm dispatch.
- Regression pins: card/IBAN/ISBN/EAN/VAT known-good vectors from public test-vector sets.

## Versioning / rollout

- New transforms are additive → **minor** version bump for `goldenflow` (Python) and `goldenflow`
  (npm). Phone migration is output-identical → no behavior change, no major.
- `goldenflow-core` is internal (not separately published); `goldenflow-native` wheel version bumps
  in lockstep across its three version spots (Cargo.toml, pyproject.toml, `__init__.py` fallback) —
  the documented native-wheel lockstep rule.
- `goldenflow-wasm` `.wasm` is built in CI, never committed (matches the suite wasm gitignore
  pattern); default TS users load zero wasm bytes.
- Docs sweep via the `rollout-docs-sweep` skill at the end: goldenflow CLAUDE.md (new crate split,
  reference-mode loader, identifier transforms), transform count source-of-truth, README, tuning
  docs, TS README, context-network ADR for the goldenflow reference-mode flip + the owned-kernel
  program.

## Risks / mitigations

- **VAT sprawl.** Per-country rules are a rabbit hole. Mitigation: the "structural for all,
  checksum where public" bound above; the supported-prefix list is explicit and tested; unsupported
  prefixes return false and are documented, not silently true.
- **Local build friction on Windows.** goldenflow-native builds fine via `pip install maturin` +
  `maturin build --release` (the `scripts/build_native.py` `.so`-hardcoded path is the only thing
  that fails on Windows). The wasm build needs the wasm32 toolchain + wasm-pack per the suite build
  notes. Both are CI-validated regardless.
- **Corpus drift.** The `--regenerate` staleness guard in CI prevents the oracle and the checked-in
  corpus from diverging.
- **Reference-mode loader flip regressing phone.** Covered by the existing phone parity corpus +
  the NANP canonical guard (unchanged); the flip only changes *which* components are eligible, and
  phone's eligibility is unchanged because its symbols already exist.

## Appendix — later waves (recorded, not designed here)

- **Wave A — validation kernels.** Harden email/URL (and fold phone validation posture) as
  authoritative owned kernels across native + WASM. Add national-ID / SWIFT / routing identifiers.
- **Wave B — i18n names.** Unicode-aware parse/normalize (script detection, transliteration,
  particle/suffix handling). Moderate data (transliteration tables); WASM-viable.
- **Wave C — i18n addresses.** libpostal-class parse/normalize. Heavy data → **native + SQL only,
  not WASM**; likely an owned lighter kernel rather than shipping libpostal to the edge.
- **Wave D — broad Polars/per-row-Python → owned-kernel sweep.** The largest wave, done last: move
  the remaining transform families into `goldenflow-core`, guided entirely by the byte-parity
  harness. This is where "kernels I control, not Polars" is fully realized for the incumbents.
- **SQL surface.** DataFusion UDF + pgrx over `goldenflow-core`; identifiers/validation are ideal
  first UDFs. Can slot in parallel once the core exists.
```
