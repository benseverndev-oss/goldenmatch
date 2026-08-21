# 0057 — Ontology layer: live-catalog write-back (RDF → file / SPARQL Graph Store)

**Status:** accepted (2026-08-13, Ben) • **Shipped:** `goldenmatch.semantic.{write_ontology_catalog, write_resolved_identity_graph}` + `goldenmatch ontology discover --endpoint/--graph-iri/--mode` • **Builds on:** [0053–0056 (ontology layer + front door)](0053-ontology-layer-rdf-owl-provider.md) • **Planning:** [../planning/semantic-layer-interchange.md](../planning/semantic-layer-interchange.md)

## Context
The last deferred piece of the ontology arc. The emitters (`emit_sameas_graph`,
`discover_ontology`, `emit_ontology_shapes`, `emit_golden_triples`) produce RDF as
a *string*; the semantic layer already persists its YAML declaration to a catalog
file (`write_resolved_catalog`). This adds the ontology analogue and the "live"
half — writing the RDF into a running triple store — so "resolve once, every SPARQL
query inherits correct identity" lands in the catalog, not just a return value.

## Decision
1. **`write_ontology_catalog(rdf, dest=None, *, endpoint=None, …)`** — one function,
   two destinations, exactly one required:
   - `dest`: write the RDF to a **file** (refuses to clobber unless `overwrite`);
   - `endpoint`: a **SPARQL 1.1 Graph Store HTTP Protocol** URL — `mode="replace"`
     → HTTP **PUT** (replace the named graph), `mode="merge"` → **POST** (add
     triples); `graph_iri` selects the named graph (`?default` otherwise). Content-Type
     is derived from the RDF `fmt`.
   Returns a small status dict. HTTP/URL errors are wrapped in a clear `RuntimeError`.
2. **`write_resolved_identity_graph(crosswalk, …)`** — the convenience wrapper:
   emit a `ResolvedCrosswalk`'s `owl:sameAs`/PROV-O graph and write it in one call.
3. **CLI write-back.** `goldenmatch ontology discover` gains `--endpoint`
   / `--graph-iri` / `--mode` so a discovered ontology can be pushed to a triple
   store from the shell. A new flag on an existing command — **not** a new
   parity surface (no new command/tool); the CLI-options docs regenerate.
4. **Stdlib-only, no new dependency.** The write path uses `urllib` (mirroring
   `client.py`); persisting an already-serialized string needs **no rdflib**, so
   `write_ontology_catalog` runs on a plain install and in the normal test lane.
   Only *producing* the RDF (`write_resolved_identity_graph` → `emit_sameas_graph`)
   needs the `goldenmatch[ontology]` extra.

## Conformance to the two-engines frame (0047)
- **One authoritative owner** ✅ — a thin persistence adapter over the emitters +
  the control plane's resolved ids. It **conforms to** the SPARQL Graph Store
  protocol; it does not implement a triple store (the replaceable-backend rule —
  Fuseki / GraphDB / Neptune are the backends).
- **Conformance defines correctness** ✅ — the endpoint path speaks the W3C Graph
  Store protocol (PUT/POST, `?graph=`), verified by request-construction tests.

## Consequences / honest flags
- **No live triple store in CI.** The endpoint path is proven by mocking `urllib`
  (method / URL / Content-Type / body); an integration test against a real Fuseki
  is a possible follow-on. The file path is tested directly.
- **Auth is caller's responsibility for now.** The endpoint call sends no
  credentials; a triple store behind auth needs a follow-on (a header/token
  parameter). Kept out of v1 to avoid a half-built auth surface.
- **`mode="replace"` PUT replaces the whole named graph** (Graph Store semantics) —
  intended for a GoldenMatch-owned identity graph, not co-mingled with other data;
  use a dedicated `graph_iri` and `merge` when appending.
- **Ontology arc fully complete.** With 0057 the whole arc — v1 (0053), consume/audit
  (0054), produce/discover (0055), CLI+MCP front door (0056), live-catalog
  write-back (0057) — is landed; no deferred items remain for the ontology layer.

---
**Classification:** decision/accepted • **Last updated:** 2026-08-13
