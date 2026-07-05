#!/usr/bin/env node
// Emit a package's real TS operation surface as JSON: {package, mcp_tools, cli_commands}.
// CI-only (the box OOMs TS builds). Imports the built (dist/) package registries — no server
// boot (each cli.ts is made import-safe via a main-module guard; the MCP TOOLS lists are
// module-level exports).
//
// Must be run from the repo root: paths are resolved against process.cwd() as
// packages/typescript/<pkg>. The gate (check_api_parity.py) and the CI job both invoke this
// from the repo root, so that assumption holds.
//
// Per-package variance is captured in REGISTRY:
//   mcp.symbol : the combined tools export ("TOOLS" or "TOOL_DEFINITIONS"); mcp:null => the
//                package ships no TS MCP server (goldenanalysis) -> emits [].
//   cli.kind   : "program"      -> import { program } (commander), read program.commands
//                "buildProgram" -> import { buildProgram }, call it, read .commands (goldenanalysis)
//                "commands"     -> import { COMMANDS } (a plain string[] the CLI dispatches on; infermap)
import path from "node:path";
import { pathToFileURL } from "node:url";

const REGISTRY = {
  goldenmatch:    { mcp: { from: "dist/node/mcp/server.js", symbol: "TOOLS" },            cli: { from: "dist/cli.js", kind: "program" } },
  goldencheck:    { mcp: { from: "dist/node/mcp/server.js", symbol: "TOOL_DEFINITIONS" }, cli: { from: "dist/cli.js", kind: "program" } },
  goldenflow:     { mcp: { from: "dist/node/mcp/server.js", symbol: "TOOL_DEFINITIONS" }, cli: { from: "dist/cli.js", kind: "program" } },
  goldenpipe:     { mcp: { from: "dist/node/mcp/server.js", symbol: "TOOLS" },            cli: { from: "dist/cli.js", kind: "program" } },
  goldenanalysis: { mcp: null,                                                            cli: { from: "dist/cli.js", kind: "buildProgram" } },
  infermap:       { mcp: { from: "dist/node/mcp/server.js", symbol: "TOOLS" },            cli: { from: "dist/cli.js", kind: "commands" } },
};

async function emit(pkg) {
  const spec = REGISTRY[pkg];
  if (!spec) throw new Error(`no parity registry entry for '${pkg}'`);
  const base = path.resolve(process.cwd(), "packages/typescript", pkg);
  const load = async (rel) => import(pathToFileURL(path.join(base, rel)).href);

  let mcp_tools = [];
  if (spec.mcp) {
    const m = await load(spec.mcp.from);
    const tools = m[spec.mcp.symbol];
    if (!tools) throw new Error(`${pkg}: expected export '${spec.mcp.symbol}' in ${spec.mcp.from}`);
    mcp_tools = [...tools].map((t) => t.name).sort();
  }

  const c = await load(spec.cli.from);
  let cmds;
  if (spec.cli.kind === "program") cmds = c.program.commands.map((x) => x.name());
  else if (spec.cli.kind === "buildProgram") cmds = c.buildProgram().commands.map((x) => x.name());
  else if (spec.cli.kind === "commands") cmds = [...c.COMMANDS];
  else throw new Error(`${pkg}: unknown cli.kind '${spec.cli.kind}'`);
  const cli_commands = cmds.sort();

  return { package: pkg, mcp_tools, cli_commands };
}

const pkg = process.argv[2];
if (!pkg) { console.error("usage: emit_ts_surface.mjs <package>"); process.exit(2); }
emit(pkg).then((d) => console.log(JSON.stringify(d))).catch((e) => {
  console.error(e?.stack || String(e)); process.exit(2);
});
