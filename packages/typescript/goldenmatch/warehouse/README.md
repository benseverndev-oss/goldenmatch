# GoldenMatch warehouse UDFs (BigQuery)

Cross-surface parity, surface #5: the same Rust `*-core` kernels that run in the
Python native wheel, the edge TS/WASM build, DuckDB, and Postgres — packaged as
**BigQuery JavaScript UDFs** that instantiate the wasm inline and call the kernel
per row. No re-implementation: each UDF embeds the committed `*WasmBytes.ts` blob
and runs the exact same bytes.

## What's here

| UDF | Signature | Kernel | Parity |
|-----|-----------|--------|--------|
| `goldenmatch_fingerprint` | `(record_json STRING) → STRING` | `fingerprint-core::fingerprint_json` | SHA-256 record id, byte-identical to every other surface |

Generated files live in `bigquery/*.sql`. They are **copy-paste deployable** — no
GCS bucket, no external library reference; the wasm travels base64-inline in the
function body.

## Deploy

1. Open `bigquery/goldenmatch_fingerprint.sql`.
2. Replace `` `YOUR_DATASET` `` with your dataset (e.g. `` `myproj.util` ``).
3. Run it in the BigQuery console or `bq query --use_legacy_sql=false < file.sql`.

```sql
SELECT
  id,
  `myproj.util`.goldenmatch_fingerprint(TO_JSON_STRING(STRUCT(name, city))) AS fp
FROM `myproj.dataset.contacts`;
```

The wasm is instantiated **once per worker** (cached on `globalThis`) and reused
across rows — BigQuery keeps the JS context alive between invocations, so the
per-row cost is just the kernel call.

## How it works

`scripts/generate_warehouse_udfs.mjs` reads the committed base64 wasm + its
wasm-bindgen glue and flattens them into a self-contained UDF body:

- the async/`fetch`/`import.meta` init path is dropped (only the synchronous
  `new WebAssembly.Module` + `Instance` path remains);
- ES `import`/`export` are stripped (a UDF body is a plain function body);
- `TextEncoder`/`TextDecoder` are routed through tiny UTF-8 polyfills used only
  when the host sandbox doesn't provide them;
- `console.*` is stripped (a warehouse sandbox may not expose it).

Regenerate after any kernel change:

```bash
node scripts/generate_warehouse_udfs.mjs
```

CI regenerates and `git diff --exit-code`s the output, so a stale committed
`.sql` fails the build (same drift-guard posture as the committed wasm blobs).

## Parity guarantee & validation scope

`tests/parity/warehouse-bigquery.parity.test.ts` extracts each UDF's JS body
verbatim from the shipped `.sql`, runs it in a fresh V8 realm (`node:vm`) that
mimics a warehouse sandbox — **no Node globals** — and asserts it reproduces the
**shared golden oracle** (`tests/parity/fixtures/fingerprint/fingerprint_golden.json`,
the same file `fingerprint-core/tests/golden.rs` and the Python surface check)
byte-for-byte. It runs both with host text codecs and with them deleted (forcing
the polyfills), so the UDF is proven correct either way.

**Scope of that guarantee:** this is a Node/V8 **simulation**. It validates the
wasm + glue logic against the cross-surface oracle in the same engine family
BigQuery uses (V8), but it does not exercise BigQuery's specific sandbox. Running
Rust→wasm inside a BigQuery JS UDF is well-established, but before advertising a
UDF for production, run a one-off smoke query in your own project to confirm your
BigQuery edition accepts the inlined module. The parity of the *bytes* is
guaranteed by construction (same committed blob as every other surface); only the
host-acceptance step is environment-specific.

## Snowflake

Snowflake JS UDFs can run wasm too, but the source-size cap (~100 KB) means only
the smaller kernels fit inline; `fingerprint` (156 KB base64) exceeds it. A
Snowflake target for the sub-cap kernels is a planned follow-up.
