// 06 — Next.js Route Handler with edge runtime.
//
// This is a reference snippet showing how to use infermap inside a Next.js
// App Router Route Handler. Drop this in your Next.js project as:
//
//     app/api/infer-mapping/route.ts
//
// Then POST to it with a JSON body of { sourceCsv, targetCsv }.
//
// The default `infermap` entrypoint has zero Node built-ins, so it runs on
// the Edge Runtime with no special config. For Node file system or database
// access, switch to `infermap/node` and remove the `runtime = "edge"` line.

import { map, mapResultToReport } from "infermap";

// Next.js edge runtime directive — remove if you need Node APIs.
export const runtime = "edge";

interface RequestBody {
  sourceCsv: string;
  targetCsv: string;
  aliases?: Record<string, string[]>;
  minConfidence?: number;
}

export async function POST(req: Request): Promise<Response> {
  let body: RequestBody;
  try {
    body = (await req.json()) as RequestBody;
  } catch {
    return Response.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  if (!body.sourceCsv || !body.targetCsv) {
    return Response.json(
      { error: "Both sourceCsv and targetCsv are required" },
      { status: 400 }
    );
  }

  try {
    const result = map(
      { csvText: body.sourceCsv, sourceName: "source" },
      { csvText: body.targetCsv, sourceName: "target" },
      {
        config: body.aliases ? { aliases: body.aliases } : undefined,
        engineOptions: body.minConfidence
          ? { minConfidence: body.minConfidence }
          : undefined,
      }
    );

    // mapResultToReport rounds confidences and strips internal bookkeeping
    // — perfect shape for a JSON API response.
    return Response.json({
      report: mapResultToReport(result),
      metadata: result.metadata,
    });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : String(e) },
      { status: 500 }
    );
  }
}

// For reference: call it like this from the browser
//
// const res = await fetch("/api/infer-mapping", {
//   method: "POST",
//   headers: { "content-type": "application/json" },
//   body: JSON.stringify({
//     sourceCsv: "fname,lname,email_addr\nAlice,Smith,a@b.co\n",
//     targetCsv: "first_name,last_name,email\n,,\n",
//   }),
// });
// const { report } = await res.json();
