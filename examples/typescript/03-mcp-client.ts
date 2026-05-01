/**
 * 03 — Connect to goldensuite-mcp from a TypeScript MCP client.
 *
 * Spin up the server first:
 *     docker run -p 8300:8300 ghcr.io/benzsevern/goldensuite-mcp:latest
 *
 * Then:
 *     npm install @modelcontextprotocol/sdk
 *     npx tsx 03-mcp-client.ts
 */
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const SERVER_URL = "http://localhost:8300/mcp";

async function main() {
  const transport = new StreamableHTTPClientTransport(new URL(SERVER_URL));
  const client = new Client({ name: "goldensuite-demo", version: "0.0.0" });
  await client.connect(transport);

  const { tools } = await client.listTools();
  console.log(`connected to ${SERVER_URL}`);
  console.log(`got ${tools.length} tools from goldensuite-mcp`);

  // Sample a few tool names so you can see the suite-wide surface.
  console.log("\nsample tools:");
  for (const tool of tools.slice(0, 8)) {
    console.log(`  ${tool.name.padEnd(30)} — ${tool.description?.slice(0, 80) ?? ""}`);
  }

  // Call a known tool. `list_domains` exists in goldenmatch and should not collide.
  if (tools.find((t) => t.name === "list_domains")) {
    const result = await client.callTool({ name: "list_domains", arguments: {} });
    const text = result.content[0]?.type === "text" ? result.content[0].text : "(non-text)";
    console.log(`\nlist_domains → ${text.slice(0, 200)}${text.length > 200 ? "..." : ""}`);
  }

  await client.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
