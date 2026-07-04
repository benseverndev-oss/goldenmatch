/**
 * Cross-surface parity for the BigQuery JS-wasm UDFs (surface #5).
 *
 * The generated `warehouse/bigquery/*.sql` embed the SAME committed wasm kernel
 * the edge-TS / Python / DuckDB / Postgres surfaces run. This test extracts each
 * UDF's JS body verbatim from the shipped `.sql`, runs it inside a fresh V8 realm
 * (node:vm) that mimics a warehouse sandbox — no Node globals — and asserts it
 * reproduces the shared golden oracle byte-for-byte.
 *
 * It runs the body TWICE: once with host `TextEncoder`/`TextDecoder` present, and
 * once with them ABSENT (forcing the generated UTF-8 polyfills). Both must match
 * the golden, so the UDF is proven correct whether or not BigQuery's sandbox
 * exposes those libs. This is a Node/V8 simulation — it validates the wasm + glue
 * logic, not BigQuery's specific engine; see warehouse/README.md.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const HERE = dirname(fileURLToPath(import.meta.url));
const PKG = join(HERE, "..", "..");

/** Pull the JS body out of a `... AS r"""<body>""";` BigQuery UDF. */
function extractBody(sqlPath: string): string {
  const sql = readFileSync(sqlPath, "utf8");
  const m = sql.match(/AS r"""\n([\s\S]*?)\n""";/);
  if (!m) throw new Error(`no UDF body found in ${sqlPath}`);
  return m[1]!;
}

/** Build the UDF as a callable inside a fresh V8 realm.
 *
 * A `vm` context IS a full realm with its own intrinsics (WebAssembly, Object,
 * Uint8Array, …) and, on Node, TextEncoder/TextDecoder — a single-realm sandbox
 * like BigQuery's. We must NOT inject foreign intrinsics (a host `Object` would
 * make the realm's object-literal prototype `!== Object.prototype`, breaking
 * `initSync`'s `{module}` unwrap). When `hostCodecs` is false we DELETE the
 * text codecs to force the generated UTF-8 polyfills. */
function makeUdf(
  body: string,
  argNames: string[],
  hostCodecs: boolean,
): (...args: unknown[]) => unknown {
  const ctx: Record<string, unknown> = {};
  vm.createContext(ctx);
  if (!hostCodecs) {
    vm.runInContext(
      "delete globalThis.TextEncoder; delete globalThis.TextDecoder;",
      ctx,
    );
  }
  const factory = `(function(${argNames.join(", ")}){\n${body}\n})`;
  return vm.runInContext(factory, ctx) as (...a: unknown[]) => unknown;
}

type FingerprintCase = { name: string; json: string; hash: string };

describe("BigQuery goldenmatch_fingerprint — cross-surface golden parity", () => {
  const body = extractBody(
    join(PKG, "warehouse", "bigquery", "goldenmatch_fingerprint.sql"),
  );
  const golden = JSON.parse(
    readFileSync(
      join(PKG, "tests", "parity", "fixtures", "fingerprint", "fingerprint_golden.json"),
      "utf8",
    ),
  ) as FingerprintCase[];

  it("has a non-trivial fixture", () => {
    expect(golden.length).toBeGreaterThanOrEqual(13);
  });

  // Host-lib path (TextEncoder/TextDecoder present) and polyfill path (absent).
  for (const [label, hostCodecs] of [
    ["host TextEncoder/TextDecoder", true],
    ["UTF-8 polyfill (no host libs)", false],
  ] as const) {
    describe(label, () => {
      const udf = makeUdf(body, ["record_json"], hostCodecs);
      for (const c of golden) {
        it(`${c.name}: fingerprint_json matches the shared golden hash`, () => {
          expect(udf(c.json)).toBe(c.hash);
        });
      }
      it("unicode + surrogate record hashes stably", () => {
        const rec = JSON.stringify({ name: "café 日本語 😀", city: "münchen" });
        expect(udf(rec)).toMatch(/^[0-9a-f]{64}$/);
      });
    });
  }

  it("host and polyfill paths agree on a unicode record (encode parity)", () => {
    const rec = JSON.stringify({ name: "café 日本語 😀", city: "münchen" });
    const host = makeUdf(body, ["record_json"], true)(rec);
    const poly = makeUdf(body, ["record_json"], false)(rec);
    expect(host).toBe(poly);
  });
});
