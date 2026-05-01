/**
 * 02 — Vercel Edge / Cloudflare Workers / Deno deployment.
 *
 * The `goldenmatch/core` entrypoint is edge-safe (no `node:*` imports) so
 * it runs in V8 isolates. This example is a Vercel Edge route that takes
 * a JSON array of records and returns deduped clusters.
 *
 * Vercel deployment:
 *   - Save as `api/dedupe.ts` (or `app/api/dedupe/route.ts` in App Router)
 *   - Add `export const runtime = "edge"`
 *   - vercel deploy
 *
 * Run locally:
 *     npm install goldenmatch
 *     npx tsx 02-edge-runtime.ts
 *     # or Vercel CLI: vercel dev
 */
import { dedupe } from "goldenmatch/core";

export const runtime = "edge";

export async function POST(request: Request): Promise<Response> {
  let rows: Array<Record<string, unknown>>;
  try {
    rows = await request.json();
  } catch {
    return new Response("invalid JSON", { status: 400 });
  }

  if (!Array.isArray(rows) || rows.length === 0) {
    return new Response("body must be a non-empty array", { status: 400 });
  }

  const url = new URL(request.url);
  const threshold = Number(url.searchParams.get("threshold") ?? "0.85");

  const result = dedupe(rows, {
    exact: ["email"],
    fuzzy: { name: 0.85 },
    threshold,
  });

  return Response.json({
    stats: result.stats,
    clusters: Array.from(result.clusters, ([id, c]) => ({
      id,
      members: c.members,
      golden: c.golden,
    })),
  });
}

// NOTE: the local-self-check that used `process.argv` lived here previously,
// but `process` is a Node-only global and this file is meant to be edge-safe
// (importing only from `goldenmatch/core`). Anyone copying the bottom of this
// file into an actual edge route would hit a build error.
//
// To run this handler locally during development, point a tool that gives you
// a Request-able runtime at it — e.g. `npx tsx --eval` with a small driver
// script that imports POST and constructs a Request, or `vercel dev` against
// the route after dropping this file at app/api/dedupe/route.ts.
