# goldenprofile

Edge-safe TypeScript surface for the **GoldenProfile Virtual Fingerprint engine** — cross-document entity resolution. It resolves a set of profile "mentions" (a name + category + anchor + defining attribute per element) into cross-document entities, with a full per-pair score breakdown behind every merge.

The resolver is the **same pyo3-free Rust kernel** (`goldenprofile-core`) used by the Python (`goldenprofile_native`) and C bindings, surfaced here through **opt-in WebAssembly**. Resolutions are byte-identical across surfaces by construction — one kernel, no hand-maintained parallel implementation.

## Design: pure-by-default, kernel-on-opt-in

The base `goldenprofile` import is **pure types + the resolver + a registry** — it pulls **zero wasm bytes**, so it stays edge-safe (browsers, Workers, edge runtimes; no `node:*`). The kernel lives behind the `goldenprofile/wasm` subpath. Until you enable it, `resolveProfiles()` throws an actionable error (the exact analog of the Python surface requiring its native wheel).

```ts
import { resolveProfiles } from "goldenprofile";
import { enableGoldenprofileWasm } from "goldenprofile/wasm";

enableGoldenprofileWasm(); // loads the wasm kernel once, registers the backend

const result = resolveProfiles({
  profiles: [
    { kind: "node", name: "Acme Inc", category: "Company", anchor: "UNKNOWN", attribute: "Anvils" },
    { kind: "node", name: "Acme",     category: "Company", anchor: "UNKNOWN", attribute: "Founded 1900" },
  ],
});

result.clusters; // [[0, 1]] — both mentions are the same cross-document entity
result.edges;    // the scored merge(s) that justify the cluster
```

`clusters` partitions **every** profile index (singletons included). `node` and `edge` kinds never cross-merge.

## API

- `resolveProfiles(request): Resolution` — resolve; throws if the wasm backend isn't enabled.
- `enableGoldenprofileWasm()` (from `goldenprofile/wasm`) — load the kernel + register the backend.
- `isGoldenprofileWasmEnabled()`, `disableGoldenprofileWasm()` — backend state (test isolation / opt-out).

## Regenerating the wasm artifact

The committed `src/core/_wasm/*` is built from the Rust kernel by `scripts/build_goldenprofile_wasm.mjs` (needs `wasm-pack` + the `wasm32-unknown-unknown` target). Re-run it whenever the kernel changes; CI guards the committed artifact against drift.

## License

MIT
