/**
 * api/server.ts -- GoldenPipe REST API server.
 *
 * Node-only: uses node:http, node:path. NOT edge-safe.
 *
 * Port of goldenpipe/api/server.py (FastAPI). Mirrors the sibling GoldenFlow TS
 * API server for structure. Endpoints:
 *   GET  /health     - liveness probe
 *   GET  /stages     - list registered stages with produces/consumes
 *   POST /validate    - validate pipeline wiring ({ pipeline, stages })
 *   POST /run         - run a pipeline on a CSV ({ source })
 *
 * /stages and /validate delegate to the MCP tool handler; /run calls the
 * file-based `run` directly so the response can include per-stage timing
 * (parity with the Python REST endpoint).
 */

import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import { resolve, isAbsolute } from "node:path";
import { handleTool } from "../mcp/server.js";
import { run } from "../run.js";

const VERSION = "1.0.0";

/** Resolve a path relative to cwd and reject traversal outside it. */
function sanitizePath(raw: string): string {
  const resolved = isAbsolute(raw) ? resolve(raw) : resolve(process.cwd(), raw);
  const cwd = resolve(process.cwd());
  if (!resolved.startsWith(cwd)) {
    throw new Error(`Path '${raw}' is outside the working directory`);
  }
  return resolved;
}

function jsonResponse(res: ServerResponse, status: number, data: unknown): void {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(data));
}

async function readBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString("utf-8");
}

export function createApp(): ReturnType<typeof createServer> {
  return createServer((req, res) => {
    void (async () => {
      const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
      const pathname = url.pathname;
      const methodName = req.method ?? "GET";

      try {
        if (pathname === "/health" && methodName === "GET") {
          return jsonResponse(res, 200, { status: "ok", version: VERSION });
        }

        if (pathname === "/stages" && methodName === "GET") {
          return jsonResponse(res, 200, await handleTool("list_stages", {}));
        }

        if (pathname === "/validate" && methodName === "POST") {
          let data: { pipeline?: string; stages?: unknown[] };
          try {
            data = JSON.parse(await readBody(req));
          } catch {
            return jsonResponse(res, 400, { error: "Invalid JSON" });
          }
          const result = await handleTool("validate_pipeline", {
            pipeline: data.pipeline ?? "",
            stages: data.stages ?? [],
          });
          return jsonResponse(res, 200, result);
        }

        if (pathname === "/run" && methodName === "POST") {
          let data: { source?: string };
          try {
            data = JSON.parse(await readBody(req));
          } catch {
            return jsonResponse(res, 400, { error: "Invalid JSON" });
          }
          if (!data.source) {
            return jsonResponse(res, 400, { error: "'source' is required" });
          }
          const result = await run(sanitizePath(data.source));
          return jsonResponse(res, 200, {
            status: result.status,
            source: result.source,
            input_rows: result.inputRows,
            errors: result.errors,
            skipped: result.skipped,
            timing: result.timing,
          });
        }

        jsonResponse(res, 404, { error: "Not found" });
      } catch (err) {
        // Log server-side; return a generic message (CodeQL js/stack-trace-exposure).
        console.error("[goldenpipe-api] request error:", err);
        jsonResponse(res, 500, { error: "internal server error" });
      }
    })();
  });
}

export function runServer(port = 8000, host = "0.0.0.0"): ReturnType<typeof createServer> {
  const app = createApp();
  app.listen(port, host, () => {
    console.log(`GoldenPipe API server running at http://${host}:${port}`);
  });
  return app;
}
