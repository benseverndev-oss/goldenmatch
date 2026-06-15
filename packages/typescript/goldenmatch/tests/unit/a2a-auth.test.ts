import { describe, it, expect } from "vitest";
import { isAuthorized, startA2aServer } from "../../src/node/a2a/server.js";

describe("A2A bearer auth — isAuthorized", () => {
  const TOKEN = "s3cret-token";

  it("public paths pass without a token (auth disabled)", () => {
    expect(isAuthorized("/health", undefined, undefined)).toBe(true);
    expect(isAuthorized("/.well-known/agent.json", undefined, undefined)).toBe(
      true,
    );
  });

  it("public paths pass even when a token is set, regardless of header", () => {
    expect(isAuthorized("/health", undefined, TOKEN)).toBe(true);
    expect(isAuthorized("/health", "Bearer wrong", TOKEN)).toBe(true);
    expect(
      isAuthorized("/.well-known/agent.json", undefined, TOKEN),
    ).toBe(true);
  });

  it("non-public paths pass without a token when auth is disabled", () => {
    expect(isAuthorized("/tasks", undefined, undefined)).toBe(true);
    expect(isAuthorized("/tasks", "Bearer anything", undefined)).toBe(true);
    expect(isAuthorized("/tasks/abc", undefined, "")).toBe(true);
  });

  it("non-public path requires the matching Bearer token when a token is set", () => {
    expect(isAuthorized("/tasks", `Bearer ${TOKEN}`, TOKEN)).toBe(true);
    expect(isAuthorized("/tasks/abc/cancel", `Bearer ${TOKEN}`, TOKEN)).toBe(
      true,
    );
  });

  it("non-public path is rejected on missing / wrong / malformed header", () => {
    expect(isAuthorized("/tasks", undefined, TOKEN)).toBe(false);
    expect(isAuthorized("/tasks", "", TOKEN)).toBe(false);
    expect(isAuthorized("/tasks", "Bearer wrong-token", TOKEN)).toBe(false);
    expect(isAuthorized("/tasks", TOKEN, TOKEN)).toBe(false); // no "Bearer " prefix
    expect(isAuthorized("/tasks", `bearer ${TOKEN}`, TOKEN)).toBe(false); // case-sensitive
  });
});

describe("A2A bearer auth — startup guard (fail-closed)", () => {
  it("throws when binding a non-loopback host without a token", () => {
    const prev = process.env["GOLDENMATCH_AGENT_TOKEN"];
    delete process.env["GOLDENMATCH_AGENT_TOKEN"];
    try {
      expect(() => startA2aServer({ port: 0, host: "0.0.0.0" })).toThrow(
        /Refusing to start an unauthenticated A2A server/,
      );
    } finally {
      if (prev !== undefined) process.env["GOLDENMATCH_AGENT_TOKEN"] = prev;
    }
  });

  it("loopback host without a token starts fine", () => {
    const prev = process.env["GOLDENMATCH_AGENT_TOKEN"];
    delete process.env["GOLDENMATCH_AGENT_TOKEN"];
    let server: ReturnType<typeof startA2aServer> | undefined;
    try {
      expect(() => {
        server = startA2aServer({ port: 0, host: "127.0.0.1" });
      }).not.toThrow();
    } finally {
      server?.close();
      if (prev !== undefined) process.env["GOLDENMATCH_AGENT_TOKEN"] = prev;
    }
  });

  it("non-loopback host WITH a token starts fine", () => {
    const prev = process.env["GOLDENMATCH_AGENT_TOKEN"];
    process.env["GOLDENMATCH_AGENT_TOKEN"] = "s3cret-token";
    let server: ReturnType<typeof startA2aServer> | undefined;
    try {
      expect(() => {
        server = startA2aServer({ port: 0, host: "0.0.0.0" });
      }).not.toThrow();
    } finally {
      server?.close();
      if (prev !== undefined) process.env["GOLDENMATCH_AGENT_TOKEN"] = prev;
      else delete process.env["GOLDENMATCH_AGENT_TOKEN"];
    }
  });
});

describe("A2A bearer auth — live 401", () => {
  it("rejects a non-public path without the token and allows /health", async () => {
    const prev = process.env["GOLDENMATCH_AGENT_TOKEN"];
    process.env["GOLDENMATCH_AGENT_TOKEN"] = "live-token";
    const server = startA2aServer({ port: 0, host: "127.0.0.1" });
    try {
      await new Promise<void>((resolveFn) => {
        if (server.listening) resolveFn();
        else server.once("listening", () => resolveFn());
      });
      const addr = server.address();
      const port =
        typeof addr === "object" && addr !== null && "port" in addr
          ? addr.port
          : 8200;
      const base = `http://127.0.0.1:${port}`;

      // /health is public -> 200 even without the token.
      const health = await fetch(base + "/health");
      expect(health.status).toBe(200);

      // /tasks without token -> 401.
      const noAuth = await fetch(base + "/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skill: "list_scorers", input: {} }),
      });
      expect(noAuth.status).toBe(401);
      const errBody = (await noAuth.json()) as { error: string };
      expect(errBody.error).toBe("Unauthorized");

      // /tasks with the right token -> 200.
      const withAuth = await fetch(base + "/tasks", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer live-token",
        },
        body: JSON.stringify({ skill: "list_scorers", input: {} }),
      });
      expect(withAuth.status).toBe(200);
    } finally {
      await new Promise<void>((resolveFn) => server.close(() => resolveFn()));
      if (prev !== undefined) process.env["GOLDENMATCH_AGENT_TOKEN"] = prev;
      else delete process.env["GOLDENMATCH_AGENT_TOKEN"];
    }
  });
});
