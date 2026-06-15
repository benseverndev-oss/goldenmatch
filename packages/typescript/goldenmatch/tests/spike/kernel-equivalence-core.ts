/**
 * kernel-equivalence-core.ts — runtime-agnostic core of the cross-JS-target WASM
 * equivalence check (R1 Workstream A, kill-criterion 2).
 *
 * The SAME assertion as the Node spike (`kernel-equivalence.test.ts`): the
 * `score-wasm` kernel reproduces the pure-TS scorers to 4 decimals. Factored out
 * so EVERY JS target — Node (vitest), Deno (`deno test`), browser (Playwright),
 * Cloudflare Workers (workerd/vitest-pool-workers) — runs byte-for-byte the same
 * check with no per-target scoring code. Each target only has to (a) hand us the
 * raw `.wasm` bytes however its runtime gets them and (b) call `runEquivalence`.
 *
 * The pure-TS reference is FROZEN in `fixtures/pure-ts-reference.json` (generated
 * in Node from the real pure-TS scorers). Comparing the kernel against that
 * frozen reference — rather than re-importing the full `scorer.ts` into Workers /
 * Deno — keeps each harness self-contained and avoids bundling the 1000-line
 * scorer into the edge runtimes. The Node spike still compares against the LIVE
 * pure-TS scorer, so the fixture itself stays honest (regenerate it if the
 * pure-TS scorers change; the Node spike would catch a drift).
 *
 * Edge-safe: no `node:*`, no `import.meta.url` asset resolution. Bytes come in as
 * a `Uint8Array`; instantiation uses the wasm-bindgen `--target web` glue, which
 * accepts the bytes directly.
 */

// The wasm-bindgen --target web glue shape (default async init + score_matrix).
// `module_or_path` accepts raw bytes (Node/browser/Deno) OR a precompiled
// `WebAssembly.Module` (the Cloudflare Workers path — workerd bans runtime
// WebAssembly.instantiate-from-bytes, so Workers MUST hand a Module compiled at
// deploy time via a static `.wasm` import).
export interface ScoreWasmGlue {
  default: (input: { module_or_path: Uint8Array | WebAssembly.Module }) => Promise<unknown>;
  score_matrix: (values: string, sep: string, scorerId: number) => Float64Array;
}

import reference from "./fixtures/pure-ts-reference.json" with { type: "json" };

const SCORER_ID: Readonly<Record<string, number>> = {
  jaro_winkler: 0,
  levenshtein: 1,
  token_sort: 2,
  exact: 3,
};
const SEP = "\x1e";
const TOL = 1e-4; // project-wide 4-decimal scorer parity contract

export interface EquivResult {
  readonly ok: boolean;
  readonly maxDiff: number;
  readonly worst: string;
  readonly comparisons: number;
}

/**
 * Instantiate the kernel from raw bytes via the provided glue, score the frozen
 * corpus under every covered scorer, and assert the kernel == the frozen pure-TS
 * reference to 4dp. Returns the worst absolute diff + a pass/fail. Throws only on
 * a structural failure (instantiation / shape); a numeric divergence is reported
 * as `ok: false` so the caller can surface a useful message.
 */
export async function runEquivalence(
  glue: ScoreWasmGlue,
  bytesOrModule: Uint8Array | WebAssembly.Module,
): Promise<EquivResult> {
  await glue.default({ module_or_path: bytesOrModule });

  const corpus = reference.corpus as readonly string[];
  const scorers = reference.scorers as Record<string, number[][]>;
  const joined = corpus.join(SEP);

  let maxDiff = 0;
  let worst = "";
  let comparisons = 0;

  for (const [name, id] of Object.entries(SCORER_ID)) {
    const refMatrix = scorers[name];
    if (refMatrix === undefined) throw new Error(`reference missing scorer ${name}`);
    const flat = glue.score_matrix(joined, SEP, id);
    const n = corpus.length;
    if (flat.length !== n * n) {
      throw new Error(`${name}: kernel returned ${flat.length}, expected ${n * n}`);
    }
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const pure = refMatrix[i]![j]!;
        const kern = flat[i * n + j]!;
        const diff = Math.abs(pure - kern);
        comparisons++;
        if (diff > maxDiff) {
          maxDiff = diff;
          worst = `${name}: "${corpus[i]}" vs "${corpus[j]}" pure=${pure} kernel=${kern}`;
        }
      }
    }
  }
  return { ok: maxDiff <= TOL, maxDiff, worst, comparisons };
}
