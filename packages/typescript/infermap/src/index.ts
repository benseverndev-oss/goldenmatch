// infermap — TypeScript port of the Python infermap library.
// This is the default entrypoint. It re-exports the edge-safe core surface.
// Node-only features (DB providers, CLI) live under `infermap/node`.
export * from "./core/index.js";
