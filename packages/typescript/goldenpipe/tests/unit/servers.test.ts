/**
 * Tests for the GoldenPipe A2A + REST API servers.
 * Mirrors the sibling GoldenFlow / GoldenMatch server tests and the Python
 * packages/python/goldenpipe/tests cases. Both servers delegate to the same 4
 * MCP tools, so these tests focus on the HTTP wiring + skill/endpoint dispatch.
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import type { Server } from "node:http";
import { startA2aServer, AGENT_CARD } from "../../src/node/a2a/server.js";
import { createApp } from "../../src/node/api/server.js";

function portOf(server: Server, fallback: number): number {
  const addr = server.address();
  return typeof addr === "object" && addr !== null && "port" in addr ? addr.port : fallback;
}

async function listen(server: Server): Promise<void> {
  await new Promise<void>((res) => {
    if (server.listening) return res();
    server.once("listening", () => res());
  });
}

async function close(server: Server): Promise<void> {
  await new Promise<void>((res, rej) => server.close((e) => (e ? rej(e) : res())));
}

describe("A2A agent card", () => {
  it("has name GoldenPipe and the 4 parity skills", () => {
    expect(AGENT_CARD.name).toBe("GoldenPipe");
    const ids = AGENT_CARD.skills.map((s) => s.id);
    expect(ids).toEqual(["run-pipeline", "validate-pipeline", "list-stages", "explain-pipeline"]);
    for (const skill of AGENT_CARD.skills) {
      expect(skill.id.length).toBeGreaterThan(0);
      expect(typeof skill.name).toBe("string");
      expect(Array.isArray(skill.inputModes)).toBe(true);
      expect(Array.isArray(skill.outputModes)).toBe(true);
    }
  });
});

describe("A2A server HTTP endpoints", () => {
  let server: Server;
  let baseUrl: string;

  beforeAll(async () => {
    server = startA2aServer({ port: 0, host: "127.0.0.1" });
    await listen(server);
    baseUrl = `http://127.0.0.1:${portOf(server, 8250)}`;
  });
  afterAll(async () => close(server));

  it("GET /.well-known/agent.json returns the card", async () => {
    const res = await fetch(baseUrl + "/.well-known/agent.json");
    expect(res.status).toBe(200);
    const body = (await res.json()) as { name: string; skills: unknown[] };
    expect(body.name).toBe("GoldenPipe");
    expect(body.skills.length).toBe(4);
  });

  it("GET /health returns ok", async () => {
    const res = await fetch(baseUrl + "/health");
    const body = (await res.json()) as { status: string };
    expect(body.status).toBe("ok");
  });

  it("POST /tasks list-stages returns the registered stages", async () => {
    const res = await fetch(baseUrl + "/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: "t1", skill: "list-stages", params: {} }),
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { status: string; result: Record<string, unknown> };
    expect(body.status).toBe("completed");
    expect(Object.keys(body.result)).toContain("goldenmatch.dedupe");
  });

  it("POST /tasks validate-pipeline returns valid:true", async () => {
    const res = await fetch(baseUrl + "/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        skill: "validate-pipeline",
        params: { pipeline: "demo", stages: ["goldencheck.scan", "goldenflow.transform"] },
      }),
    });
    const body = (await res.json()) as { result: { valid: boolean } };
    expect(body.result.valid).toBe(true);
  });

  it("POST /tasks rejects an unknown skill", async () => {
    const res = await fetch(baseUrl + "/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill: "nope", params: {} }),
    });
    const body = (await res.json()) as { result: { error?: string } };
    expect(body.result.error).toContain("Unknown skill");
  });
});

describe("REST API server HTTP endpoints", () => {
  let server: Server;
  let baseUrl: string;

  beforeAll(async () => {
    server = createApp();
    server.listen(0, "127.0.0.1");
    await listen(server);
    baseUrl = `http://127.0.0.1:${portOf(server, 8000)}`;
  });
  afterAll(async () => close(server));

  it("GET /health returns ok", async () => {
    const res = await fetch(baseUrl + "/health");
    const body = (await res.json()) as { status: string };
    expect(body.status).toBe("ok");
  });

  it("GET /stages lists the suite stages", async () => {
    const res = await fetch(baseUrl + "/stages");
    const body = (await res.json()) as Record<string, unknown>;
    expect(Object.keys(body)).toContain("goldencheck.scan");
  });

  it("POST /validate returns valid:true for a well-wired chain", async () => {
    const res = await fetch(baseUrl + "/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pipeline: "demo", stages: ["goldencheck.scan"] }),
    });
    const body = (await res.json()) as { valid: boolean };
    expect(body.valid).toBe(true);
  });

  it("POST /run requires a source", async () => {
    const res = await fetch(baseUrl + "/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    expect(res.status).toBe(400);
  });

  it("POST /run on a missing file returns a failed status with timing", async () => {
    const res = await fetch(baseUrl + "/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: "missing-pipe-input.csv" }),
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { status: string; input_rows: number; timing: unknown };
    expect(body.status).toBe("failed");
    expect(body.input_rows).toBe(0);
    expect(body.timing).toBeDefined();
  });
});
