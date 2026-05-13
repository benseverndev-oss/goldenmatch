import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { resolve, isAbsolute } from "node:path";
import { readFile } from "../connectors/file.js";
import { TransformEngine } from "../../core/engine/transformer.js";
import { listTransforms } from "../../core/transforms/index.js";

/** Validate a file path to prevent path traversal. Resolves relative to cwd. */
function sanitizePath(raw: string): string {
  const resolved = isAbsolute(raw) ? resolve(raw) : resolve(process.cwd(), raw);
  const cwd = resolve(process.cwd());
  if (!resolved.startsWith(cwd)) {
    throw new Error(`Path '${raw}' is outside the working directory`);
  }
  return resolved;
}

const VERSION = "0.1.0";

/** Strip stack / errno / syscall fields from objects + Error instances so
 *  unauthenticated callers don't see internal paths / library versions. */
function sanitiseForWire(data: unknown): unknown {
  if (data instanceof Error) {
    return { error: data.message };
  }
  if (data && typeof data === "object" && !Array.isArray(data)) {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(data)) {
      if (k === "stack" || k === "errno" || k === "syscall") continue;
      out[k] = v;
    }
    return out;
  }
  return data;
}

function jsonResponse(res: ServerResponse, status: number, data: unknown): void {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(sanitiseForWire(data)));
}

async function readBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString("utf-8");
}

export function createApp() {
  return createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", `http://${req.headers.host}`);

    if (url.pathname === "/health" && req.method === "GET") {
      return jsonResponse(res, 200, { status: "ok", version: VERSION });
    }

    if (url.pathname === "/transforms" && req.method === "GET") {
      const transforms = listTransforms().map(t => ({
        name: t.name,
        input_types: [...t.inputTypes],
        auto_apply: t.autoApply,
        priority: t.priority,
        mode: t.mode,
      }));
      return jsonResponse(res, 200, transforms);
    }

    if (url.pathname === "/transform" && req.method === "POST") {
      try {
        const body = await readBody(req);
        let data: { rows?: unknown[]; path?: string };
        try { data = JSON.parse(body); } catch { return jsonResponse(res, 400, { error: "Invalid JSON" }); }

        let rows: Record<string, unknown>[];
        if (data.path) {
          rows = readFile(sanitizePath(data.path));
        } else if (Array.isArray(data.rows)) {
          rows = data.rows as Record<string, unknown>[];
        } else {
          return jsonResponse(res, 400, { error: "Provide 'path' or 'rows'" });
        }

        const engine = new TransformEngine();
        const result = engine.transformDf(rows);
        return jsonResponse(res, 200, {
          rows: result.rows,
          manifest: {
            records: result.manifest.records,
            errors: result.manifest.errors,
          },
        });
      } catch (e) {
        return jsonResponse(res, 500, { error: e instanceof Error ? e.message : String(e) });
      }
    }

    jsonResponse(res, 404, { error: "Not found" });
  });
}

export function runServer(port = 8000, host = "0.0.0.0"): void {
  const app = createApp();
  app.listen(port, host, () => {
    console.log(`GoldenFlow API server running at http://${host}:${port}`);
  });
}
