# GoldenFlow owned-kernel boundary (W6: honest handling of structural holes)

**Status:** accepted · **Date:** 2026-07-06

This closes the owned-kernel + cross-surface program (Waves 0–5). It states,
honestly, **what the owned-kernel model covers and what it deliberately does
not** — so no future contributor tries to byte-port a transform that
structurally can't be, and no capability silently falls out of parity.

Enforced by `packages/python/goldenflow/tests/transforms/test_owned_kernel_boundary.py`:
every registered transform must be classified into exactly one bucket below, or
that test fails.

## The model

`goldenflow-core` (pyo3-free Rust) is the **reference implementation** of a
transform's logic. Every other surface — the Python fallback, the
`goldenflow-native` wheel, the TS pure port + `goldenflow-wasm`, the compiled
`goldenflow-duckdb` extension, and Postgres — reproduces the reference
**byte-for-byte**, proven by a shared corpus
(`tests/parity/identifiers_corpus.jsonl`, oracle = goldenflow-core).

As of W5: **113 registered transforms**, of which **81 are corpus-owned** and
**16 more are owned via pinned-vector kernel tests** — 97 owned in total. The
remaining 16 are the structural holes documented here.

## What qualifies as an owned kernel

A transform is an owned byte-parity kernel when it is:

1. **Single-value (or fixed-arity)** — `str -> scalar`, `str -> (str, str)`,
   `(str, str) -> str`, etc. The value is a pure function of its own inputs.
2. **Deterministic** — same input, same bytes out, on every surface and run.
3. **Byte-portable** — expressible with hand-rolled ASCII / explicit
   code-point tables in Rust, Python, and TS with **no divergence** (no
   language-specific regex `\b`/greedy semantics, no `round()` half-even vs
   half-away, no locale/Unicode-DB reliance). Where a real library is the
   reference (rapidfuzz, phonenumbers), the port is validated byte-identical
   over a corpus, or the kernel is NANP/DE-IT–gated to the region where the
   port and the library agree exactly.
4. **Data-light** — any lookup table is a small curated constant compiled into
   every surface, not a runtime-loaded dataset or a full Unicode database.

## The structural holes (and the honest verdict for each)

### 1. Dates — structurally non-portable (13 transforms)

`date_iso8601`, `date_us`, `date_eu`, `date_parse`, `date_shift`,
`date_validate`, `datetime_iso8601`, `age_from_dob`, `extract_year`,
`extract_month`, `extract_day`, `extract_quarter`, `extract_day_of_week`.

**Verdict: stay Python-reference; not an owned kernel.** Date parsing depends on
`dateutil`'s **fuzzy** parser and **non-deterministic partial-date resolution**
(a bare `"93"` or `"March"` resolves against the current date / heuristics).
This was **measured** — see the memory `reference_dates_chrono_dateutil_parity`:
chrono (`%Y` greedily accepts 2-digit years) and dateutil disagree, and the
fuzzy path has no fixed byte-for-byte contract. Forcing a port would either
change results or reintroduce the 2-digit-year hazard. There is also **no perf
motive** — Polars already vectorizes the common ISO/US/EU paths; the per-row
`dateutil` reference only settles the residual. Owned kernels **exclude dates by
design.**

### 2. Data-dependent categorical mapping (2 transforms)

`category_standardize`, `category_from_file`.

**Verdict: the logic is owned; the data is not.** These apply a
**caller-supplied** variant→canonical mapping (a function argument, or a
CSV/YAML loaded at runtime). That mapping is **runtime data, not logic**, so
goldenflow-core does not own a dict-lookup kernel for it. What *is* owned is the
shared **key-derivation** step both use before their lookup —
`category_normalize_key` (trim + lowercase) — which is a corpus-owned kernel.
Only the lookup-with-fallback loop stays in the host, operating on host-supplied
data.

### 3. Product-spec fallback (1 transform)

`phone_validate`.

**Verdict: deliberately pure-Python reference.** Its only native symbol
(`phone_valid_arrow`) implements `is_valid`, but the product spec chose
`is_possible`. Rather than ship a native path that diverges from the spec, it is
intentionally unwired (`_FALLBACK_ONLY` in `_native_loader.py`). The other three
phone encoders (`phone_e164`/`phone_national`/`phone_country_code`) ARE owned —
NANP-gated to the region where the Rust port is byte-identical to `phonenumbers`.

### 4. Fixed-arity / parameterized / whole-column kernels — owned, pinned (16)

`split_name`, `split_name_reverse`, `split_address`, `merge_name`, `truncate`,
`pad_left`, `pad_right`, `round`, `clamp`, `abs_value`, `fill_zero`,
`phone_e164`, `phone_national`, `phone_country_code`, `category_auto_correct`,
`initial_expand`.

**Verdict: these ARE owned** — their byte-parity is proven by **pinned-vector
kernel tests** (`test_{name,address,text,numeric,autocorrect}_kernels.py`,
`test_native_parity.py`) rather than the string-keyed corpus, because their
shape doesn't fit a `str -> scalar` corpus row: multi-output splits, a
two-input merge, per-column-constant params (truncate/pad width, round places),
numeric-**array** ops (numeric input, not a string), the NANP-gated phone
encoders, the whole-column fuzzy autocorrect, and the flag-wrapper
`initial_expand` (value passthrough; flags via the corpus-owned `has_initial`).
They are listed in the `_OWNED_PINNED` bucket, not because they're second-class,
but because the corpus harness is string-keyed.

## The auto-detect profiling surface (owned, but NOT a registered transform)

**Status:** added 2026-07-13 (goldenflow 2.1.0).

Zero-config's **type-inference / profiling decision** is now an owned
`goldenflow_core::profile` kernel, distinct from the transform registry above.
It is *not* a registered transform, so it does **not** appear in the
`test_owned_kernel_boundary.py` buckets — those enumerate `registry()`, which
holds only `@register_transform` entries. The profiler is a separate owned
**surface**: the decision "what type is this column?" that drives which
transforms zero-config selects.

- **`infer_type(values, hint) -> String`** is the owned decision on **every
  surface** — the Polars columnar path (`profile_dataframe` →
  `_profile_column`), the Polars-free list/dict path (`profile_columns` →
  `_infer_type_list`), the `goldenflow-native` wheel, and `goldenflow-wasm` / the
  TS `inferType`. The pure-Python `_infer_type`/`_infer_type_list` and pure-TS
  ports are byte-matched fallbacks.
- **`profile_column(values, hint) -> ColumnProfileOut`** is the fused columnar
  wrapper (Path 1): one FFI call returns `inferred_type` + null/unique/samples,
  Polars-free.
- Cross-surface byte-parity is proven by
  `tests/parity/profile_corpus.jsonl` (oracle = `goldenflow-core`), enforced the
  same way as the identifier corpus.

### Known edge (accepted, corpus-unexercised follow-up)

The pure-TS profiler builds its `≤100`-value sample as **strip-then-slice**,
while Python/Rust do **slice-then-strip** (take the first 100 non-null values,
*then* strip and drop empties). So on a column with **>100 non-null values that
contains empty strings among the first 100**, the surfaces can pick a different
100-value window and, in principle, infer a different type. This is a
pre-existing, corpus-unexercised edge — treated as a documented reference-mode
lossy fallback (the pure-TS surface may diverge here), tracked as a follow-up to
align the TS sampling order. It does not affect the native/Polars/list Python
paths, which all slice-then-strip identically.

A second pre-existing pure-TS divergence, same reference-mode status: on a
**mixed number+string column** (e.g. `[1, "1"]`), the pure-TS `inferType`
returns `"numeric"` via its `hasNumber && !hasBoolean` early-return, while
Python/Rust/wasm return `"string"` (they only short-circuit to `numeric` when
**all** non-null values are numeric; a mixed column falls through to the regex
path — corpus row `[1,"1"]` pins `"string"`). Both TS edges are the pure-TS
fallback's own quirks, not the owned kernel's, and are tracked together as the
TS-profiler-alignment follow-up.

## Boundaries with sibling packages (not GoldenFlow's job)

- **PII detection / redaction in free text** is **GoldenCheck's** job (scanning
  + profiling). GoldenFlow owns deterministic *format* masks over a
  known-shaped field (`email_mask`, `ssn_mask`, `cc_mask`) — not "find the SSNs
  inside a paragraph."
- **Cross-record / multi-column entity logic** (dedupe, survivorship, blocking)
  is **GoldenMatch's** job. GoldenFlow's owned kernels are per-value; the
  fixed-arity multi-column ones it *does* own (`split_*`, `merge_name`) are
  still pure functions of one record's fields, not cross-record.
- **Full i18n** (transliteration for every script, locale-specific formatting,
  a complete Unicode normalization DB) is **data-heavy and deferred.** Bounded,
  curated subsets ARE owned — `name_transliterate` (an explicit Latin-diacritic
  fold map, not NFD), `name_script` (code-point ranges), `normalize_unicode` (a
  generated 413-entry map, not a runtime Unicode DB). Extending coverage means
  extending the curated tables, deliberately, on every surface — never pulling a
  runtime locale database into the edge/WASM/DuckDB surfaces.

## Enforcement

`test_owned_kernel_boundary.py` asserts:
- every registered transform is classified (no silent gaps);
- no bucket has stale entries (renamed/removed transforms);
- the buckets are disjoint from the corpus and from each other.

Adding a transform therefore forces an explicit decision: put it in the corpus
(if it's an owned single-value kernel), a pinned-vector test (owned, other
shape), or a documented hole bucket here — with the rationale.
