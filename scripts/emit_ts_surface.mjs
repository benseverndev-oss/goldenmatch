#!/usr/bin/env node
// Emit goldenmatch's real TS operation surface as JSON: {package, mcp_tools, cli_commands}.
// CI-only (the box OOMs TS builds). Imports the built package registries — no server boot
// (Task 1 made src/cli.ts import-safe; node/mcp/server.ts's TOOLS is a module-level export).
//
// Must be run from the repo root: paths are resolved against process.cwd() as
// packages/typescript/<pkg>. The gate (check_api_parity.py) and the CI job both
// invoke this from the repo root, so that assumption holds.
import path from "node:path";
import { pathToFileURL } from "node:url";

const REGISTRY = {
  goldenmatch: {
    mcpFrom: "dist/node/mcp/server.js",  // export const TOOLS
    cliFrom: "dist/cli.js",              // export const program (Task 1)
  },
};

async function emit(pkg) {
  const spec = REGISTRY[pkg];
  if (!spec) throw new Error(`no parity registry entry for '${pkg}'`);
  const base = path.resolve(process.cwd(), "packages/typescript", pkg);
  const mcpMod = await import(pathToFileURL(path.join(base, spec.mcpFrom)).href);
  const cliMod = await import(pathToFileURL(path.join(base, spec.cliFrom)).href);
  const mcp_tools = [...mcpMod.TOOLS].map((t) => t.name).sort();
  const cli_commands = cliMod.program.commands.map((c) => c.name()).sort();
  return { package: pkg, mcp_tools, cli_commands };
}

const pkg = process.argv[2];
if (!pkg) { console.error("usage: emit_ts_surface.mjs <package>"); process.exit(2); }
emit(pkg).then((d) => console.log(JSON.stringify(d))).catch((e) => {
  console.error(e?.stack || String(e)); process.exit(2);
});
