/**
 * generate_warehouse_udfs.mjs — emit BigQuery JS-wasm UDF DDL from the committed
 * base64 wasm kernels.
 *
 * This is surface #5 of the cross-surface parity effort: the SAME Rust `*-core`
 * kernel that runs in Python (native wheel), edge TS/WASM, DuckDB and Postgres,
 * now packaged as a BigQuery `CREATE FUNCTION ... LANGUAGE js` that instantiates
 * the wasm inline (base64) and calls the kernel export per row. No porting — the
 * committed `*WasmBytes.ts` blob is embedded verbatim; the wasm-bindgen glue is
 * flattened into a self-contained UDF body (async/fetch path dropped, no ES
 * imports, `TextEncoder`/`TextDecoder` guarded with tiny UTF-8 polyfills so the
 * body runs in any V8 sandbox — BigQuery, Snowflake, plain Node).
 *
 * BigQuery reuses the JS context across rows within a worker, so the wasm is
 * instantiated once (cached on `globalThis`) and reused. The generated `.sql`
 * files under `warehouse/bigquery/` are the deliverable (copy-paste-deployable,
 * zero infra — no GCS bucket needed). A drift guard in CI regenerates and diffs.
 *
 * Run: node scripts/generate_warehouse_udfs.mjs
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const PKG = join(HERE, "..");
const WASM_DIR = join(PKG, "src", "core", "_wasm");
const OUT_DIR = join(PKG, "warehouse", "bigquery");

// ---------------------------------------------------------------------------
// Kernel manifest — one entry per warehouse UDF. Each names the committed
// wasm-bindgen bindings + base64 bytes, the exported kernel fn, and the SQL
// signature. Add a kernel here (+ a parity fixture) to grow the surface.
// ---------------------------------------------------------------------------
const KERNELS = [
  {
    udf: "goldenmatch_fingerprint",
    bindings: "fingerprintWasmBindings.js",
    bytesModule: "fingerprintWasmBytes.ts",
    bytesConst: "FINGERPRINT_WASM_BASE64",
    exportFn: "fingerprint_json",
    args: [{ name: "record_json", sqlType: "STRING" }],
    returns: "STRING",
    doc:
      "Canonical cross-surface record fingerprint: SHA-256 (64 lowercase hex) of\n" +
      "a JSON-object record string, via the shared `fingerprint-core` kernel —\n" +
      "byte-identical to the Python / DuckDB / Postgres / edge-TS surfaces.\n" +
      "Drops `__`-prefixed keys; type-tags values (int 1 != str \"1\" != bool).",
  },
];

// ---------------------------------------------------------------------------
// Read the base64 constant out of the committed `*WasmBytes.ts`.
// ---------------------------------------------------------------------------
function readBase64(bytesModule, bytesConst) {
  const src = readFileSync(join(WASM_DIR, bytesModule), "utf8");
  const m = src.match(new RegExp(`${bytesConst}\\s*=\\s*\\n?\\s*"([A-Za-z0-9+/=]+)"`));
  if (!m) throw new Error(`could not find ${bytesConst} in ${bytesModule}`);
  return m[1];
}

// ---------------------------------------------------------------------------
// Flatten the committed wasm-bindgen bindings into a self-contained UDF body.
//
// The committed bindings are an ES module (async `__wbg_init`/`__wbg_load` +
// `export`s). For a warehouse UDF body we keep only the SYNCHRONOUS init path
// (`initSync`: `new WebAssembly.Module` + `Instance`) plus the kernel export and
// its helpers, drop the ES `export`/`import` keywords and the fetch-based async
// functions, and guard the two host libs (`TextEncoder`/`TextDecoder`) with
// UTF-8 polyfills. The result closes over one `wasm` instance and is cached on
// `globalThis` so BigQuery instantiates once per worker.
// ---------------------------------------------------------------------------
function flattenBindings(bindingsSrc) {
  let s = bindingsSrc;

  // Drop the fetch/import.meta async init path (unused; would reference
  // fetch/Response/Request/URL that a warehouse sandbox may not provide).
  s = stripFunction(s, "async function __wbg_load");
  s = stripFunction(s, "async function __wbg_init");

  // Drop ES module syntax — the body runs as a plain function body.
  s = s.replace(/^export function /gm, "function ");
  s = s.replace(/^export \{[^}]*\};\s*$/gm, "");

  // Strip `console.*` statements — a warehouse sandbox may not expose `console`,
  // and the only survivor is a dead-path deprecation warning (we always pass the
  // `{ module }` form to initSync). Line-based: each such call sits on its own
  // line in the wasm-bindgen glue.
  s = s
    .split("\n")
    .filter((l) => !/^\s*console\.(?:warn|log|error)\(/.test(l))
    .join("\n");

  // Route the two host libs through guarded locals so the body is self-
  // contained even where BigQuery/Snowflake don't expose them.
  s = s.replace(/new TextEncoder\(\)/g, "new _GM_TextEncoder()");
  s = s.replace(/new TextDecoder\(/g, "new _GM_TextDecoder(");
  // The `'encodeInto' in cachedTextEncoder` feature-probe + patch (lines that
  // add encodeInto when absent) already handles our polyfill, which ships only
  // `.encode`. Nothing to change there.

  return s.trim();
}

/** Remove a top-level `function name(...) { ... }` by brace-matching. */
function stripFunction(src, header) {
  const start = src.indexOf(header);
  if (start === -1) return src;
  const braceOpen = src.indexOf("{", start);
  let depth = 0;
  let i = braceOpen;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") {
      depth--;
      if (depth === 0) { i++; break; }
    }
  }
  return src.slice(0, start) + src.slice(i);
}

// UTF-8 polyfills + a base64 decoder, prepended to every body. `_GM_TextEncoder`
// / `_GM_TextDecoder` fall through to the host implementations when present.
const PRELUDE = `// --- self-contained prelude (base64 + UTF-8, no host libs required) ---
const _GM_TextEncoder = (typeof TextEncoder !== "undefined") ? TextEncoder : (function () {
  function E() {}
  E.prototype.encode = function (str) {
    str = String(str); const out = [];
    for (let i = 0; i < str.length; i++) {
      let c = str.charCodeAt(i);
      if (c >= 0xd800 && c <= 0xdbff && i + 1 < str.length) {
        const c2 = str.charCodeAt(i + 1);
        if (c2 >= 0xdc00 && c2 <= 0xdfff) { c = 0x10000 + ((c - 0xd800) << 10) + (c2 - 0xdc00); i++; }
      }
      if (c < 0x80) out.push(c);
      else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 0x3f));
      else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f));
      else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 0x3f), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f));
    }
    return new Uint8Array(out);
  };
  return E;
})();
const _GM_TextDecoder = (typeof TextDecoder !== "undefined") ? TextDecoder : (function () {
  function D() {}
  D.prototype.decode = function (bytes) {
    if (!bytes) return "";
    const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    let out = "", i = 0;
    while (i < b.length) {
      let c = b[i++];
      if (c > 0x7f) {
        if (c >> 5 === 0x6) c = ((c & 0x1f) << 6) | (b[i++] & 0x3f);
        else if (c >> 4 === 0xe) c = ((c & 0xf) << 12) | ((b[i++] & 0x3f) << 6) | (b[i++] & 0x3f);
        else c = ((c & 0x7) << 18) | ((b[i++] & 0x3f) << 12) | ((b[i++] & 0x3f) << 6) | (b[i++] & 0x3f);
      }
      if (c > 0xffff) { c -= 0x10000; out += String.fromCharCode(0xd800 + (c >> 10), 0xdc00 + (c & 0x3ff)); }
      else out += String.fromCharCode(c);
    }
    return out;
  };
  return D;
})();
function _gmB64ToBytes(b64) {
  const A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  const lut = _gmB64ToBytes._lut || (_gmB64ToBytes._lut = (function () {
    const t = new Int16Array(256).fill(-1);
    for (let i = 0; i < A.length; i++) t[A.charCodeAt(i)] = i;
    return t;
  })());
  let len = b64.length;
  while (len > 0 && b64[len - 1] === "=") len--;
  const outLen = (len * 3) >> 2;
  const out = new Uint8Array(outLen);
  let o = 0, acc = 0, bits = 0;
  for (let i = 0; i < len; i++) {
    const v = lut[b64.charCodeAt(i)];
    if (v < 0) continue;
    acc = (acc << 6) | v; bits += 6;
    if (bits >= 8) { bits -= 8; out[o++] = (acc >> bits) & 0xff; }
  }
  return out;
}`;

function buildBody(kernel, base64) {
  const glue = flattenBindings(readFileSync(join(WASM_DIR, kernel.bindings), "utf8"));
  const argNames = kernel.args.map((a) => a.name).join(", ");
  const cacheKey = `__gm_${kernel.exportFn}__`;
  return `${PRELUDE}
const _WASM_B64 = "${base64}";
// Instantiate once per worker; BigQuery keeps globalThis alive across rows.
let _gm = globalThis.${cacheKey};
if (!_gm) {
${indent(glue, 2)}
  initSync({ module: _gmB64ToBytes(_WASM_B64) });
  _gm = globalThis.${cacheKey} = { ${kernel.exportFn} };
}
return _gm.${kernel.exportFn}(${argNames});`;
}

function indent(src, n) {
  const pad = " ".repeat(n);
  return src.split("\n").map((l) => (l ? pad + l : l)).join("\n");
}

function buildSql(kernel, body) {
  const sig = kernel.args.map((a) => `${a.name} ${a.sqlType}`).join(", ");
  const docLines = kernel.doc.split("\n").map((l) => `-- ${l}`).join("\n");
  return `-- AUTO-GENERATED by scripts/generate_warehouse_udfs.mjs — DO NOT EDIT.
-- BigQuery JS-wasm UDF. Embeds the committed ${kernel.bytesConst} wasm inline;
-- runs the shared Rust kernel per row. Replace \`YOUR_DATASET\` with your dataset.
--
${docLines}
CREATE OR REPLACE FUNCTION \`YOUR_DATASET\`.${kernel.udf}(${sig})
RETURNS ${kernel.returns}
DETERMINISTIC
LANGUAGE js
AS r"""
${indent(body, 2)}
""";
`;
}

mkdirSync(OUT_DIR, { recursive: true });
for (const kernel of KERNELS) {
  const base64 = readBase64(kernel.bytesModule, kernel.bytesConst);
  const body = buildBody(kernel, base64);
  const sql = buildSql(kernel, body);
  const outPath = join(OUT_DIR, `${kernel.udf}.sql`);
  writeFileSync(outPath, sql);
  // eslint-disable-next-line no-console
  console.log(`wrote ${outPath} (${sql.length} bytes, kernel=${kernel.exportFn})`);
}
