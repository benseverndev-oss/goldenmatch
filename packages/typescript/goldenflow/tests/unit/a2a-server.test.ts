/**
 * Tests for the GoldenFlow A2A server.
 * Mirrors goldenmatch's tests/unit/a2a-server.test.ts and the Python
 * packages/python/goldenflow/tests/test_a2a.py cases.
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import type { Server } from "node:http";
import { writeFileSync, rmSync, mkdtempSync } from "node:fs";
import { join, relative } from "node:path";
import { startA2aServer, AGENT_CARD } from "../../src/node/a2a/server.js";

let server: Server;
let baseUrl: string;
let csvRelPath: string;
let tmpDir: string;

beforeAll(async () => {
  // MCP sanitizePath only allows files under cwd, so write the fixture there.
  tmpDir = mkdtempSync(join(process.cwd(), "a2a-fixture-"));
  const csvPath = join(tmpDir, "people.csv");
  writeFileSync(
    csvPath,
    "name,email,phone\n  John Smith  ,JOHN@EXAMPLE.COM,(555) 123-4567\nJane Doe,jane@test.com,555.987.6543\n",
  );
  csvRelPath = relative(process.cwd(), csvPath);

  server = startA2aServer({ port: 0, host: "127.0.0.1" });
  await new Promise<void>((resolveFn) => {
    if (server.listening) {
      resolveFn();
      return;
    }
    server.once("listening", () => resolveFn());
  });
  const addr = server.address();
  const port = typeof addr === "object" && addr !== null && "port" in addr ? addr.port : 8150;
  baseUrl = `http://127.0.0.1:${port}`;
});

afterAll(async () => {
  if (server) {
    await new Promise<void>((resolveFn, rejectFn) => {
      server.close((err) => (err ? rejectFn(err) : resolveFn()));
    });
  }
  if (tmpDir) rmSync(tmpDir, { recursive: true, force: true });
});

describe("A2A agent card (exported constant)", () => {
  it("has name, description, version, provider, skills", () => {
    expect(AGENT_CARD.name).toBe("GoldenFlow");
    expect(typeof AGENT_CARD.description).toBe("string");
    expect(typeof AGENT_CARD.version).toBe("string");
    expect(typeof AGENT_CARD.provider.organization).toBe("string");
    expect(Array.isArray(AGENT_CARD.skills)).toBe(true);
  });

  it("has exactly 6 skills (parity with Python)", () => {
    expect(AGENT_CARD.skills.length).toBe(6);
    const ids = AGENT_CARD.skills.map((s) => s.id);
    expect(ids).toContain("transform-data");
    expect(ids).toContain("map-schemas");
    expect(ids).toContain("discover");
    expect(ids).toContain("diff-results");
    expect(ids).toContain("configure");
    expect(ids).toContain("handoff");
  });

  it("every skill has id, name, description, inputModes, outputModes", () => {
    for (const skill of AGENT_CARD.skills) {
      expect(typeof skill.id).toBe("string");
      expect(skill.id.length).toBeGreaterThan(0);
      expect(typeof skill.name).toBe("string");
      expect(typeof skill.description).toBe("string");
      expect(skill.inputModes.length).toBeGreaterThan(0);
      expect(skill.outputModes.length).toBeGreaterThan(0);
    }
  });
});

describe("A2A server HTTP endpoints", () => {
  it("GET /.well-known/agent.json returns the AgentCard", async () => {
    const res = await fetch(baseUrl + "/.well-known/agent.json");
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      name: string;
      skills: Array<{ id: string }>;
    };
    expect(body.name).toBe("GoldenFlow");
    expect(body.skills.length).toBe(6);
  });

  it("GET /health returns ok", async () => {
    const res = await fetch(baseUrl + "/health");
    expect(res.status).toBe(200);
    const body = (await res.json()) as { status: string };
    expect(body.status).toBe("ok");
  });

  it("POST /tasks discover returns transforms and domains", async () => {
    const res = await fetch(baseUrl + "/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: "t1", skill: "discover", params: {} }),
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      id: string;
      status: string;
      result: { transforms: unknown; domains: { domains: string[] } };
    };
    expect(body.status).toBe("completed");
    expect(body.result.transforms).toBeDefined();
    expect(body.result.domains).toBeDefined();
    expect(body.result.domains.domains).toContain("carceral");
  });

  it("POST /tasks transform-data profiles then transforms a CSV", async () => {
    const res = await fetch(baseUrl + "/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill: "transform-data", params: { path: csvRelPath } }),
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      status: string;
      result: Array<{ step: string; result: unknown }>;
    };
    expect(body.status).toBe("completed");
    expect(body.result.length).toBe(2);
    expect(body.result[0]!.step).toBe("profile");
    expect(body.result[1]!.step).toBe("transform");
  });

  it("POST /tasks configure profiles then learns a config", async () => {
    const res = await fetch(baseUrl + "/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill: "configure", params: { path: csvRelPath } }),
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      status: string;
      result: Array<{ step: string }>;
    };
    expect(body.status).toBe("completed");
    expect(body.result.map((p) => p.step)).toEqual(["profile", "config"]);
  });

  it("POST /tasks handoff maps findings to transforms", async () => {
    const res = await fetch(baseUrl + "/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        skill: "handoff",
        params: { findings: [{ check: "whitespace_issues", column: "name" }] },
      }),
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { status: string; result: unknown };
    expect(body.status).toBe("completed");
    expect(body.result).toBeDefined();
  });

  it("GET /tasks/{id} returns task status after creation", async () => {
    const postRes = await fetch(baseUrl + "/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill: "discover", params: {} }),
    });
    const postBody = (await postRes.json()) as { id: string };
    expect(typeof postBody.id).toBe("string");

    const getRes = await fetch(baseUrl + "/tasks/" + postBody.id);
    expect(getRes.status).toBe(200);
    const getBody = (await getRes.json()) as { id: string; skill: string; status: string };
    expect(getBody.id).toBe(postBody.id);
    expect(getBody.skill).toBe("discover");
    expect(["completed", "running", "pending", "failed"]).toContain(getBody.status);
  });

  it("GET /tasks/nonexistent returns 404", async () => {
    const res = await fetch(baseUrl + "/tasks/does-not-exist-xyz");
    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: string };
    expect(typeof body.error).toBe("string");
  });

  it("POST /tasks with unknown skill returns completed with error result", async () => {
    const res = await fetch(baseUrl + "/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill: "not_a_real_skill", params: {} }),
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { result?: { error?: string } };
    expect(body.result?.error).toContain("Unknown skill");
  });

  it("POST /tasks without skill returns 400", async () => {
    const res = await fetch(baseUrl + "/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ params: {} }),
    });
    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: string };
    expect(typeof body.error).toBe("string");
  });
});
