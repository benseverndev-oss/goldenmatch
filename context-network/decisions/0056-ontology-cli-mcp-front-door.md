# 0056 — Ontology layer: CLI + MCP front door

**Status:** accepted (2026-08-13, Ben) • **Shipped:** `goldenmatch ontology {certify,discover}` CLI subgroup + MCP tools `ontology_certify` / `ontology_discover` • **Builds on:** [0053–0055 (ontology layer)](0053-ontology-layer-rdf-owl-provider.md) • **Planning:** [../planning/semantic-layer-interchange.md](../planning/semantic-layer-interchange.md)

## Context
The ontology layer (0053–0055) shipped library-only; a CLI/MCP front door was
deferred across the whole semantic arc as a parity-surface decision. The semantic
layer already has one (`certify-keys` / `discover-model` CLI, `certify_semantic_model`
/ `discover_semantic_model` MCP). This gives the ontology layer the same reach, so
an agent or a shell user — not just a Python caller — can certify and discover.

## Decision
1. **CLI `ontology` subgroup** (`cli/ontology.py`, registered like `identity`):
   `ontology certify <ontology.ttl> --data Class=path…` (certify the declared
   `owl:hasKey`/IFP keys against instance data, `--fail-untrustworthy` for a CI
   gate) and `ontology discover --data Class=path… [-o out.ttl]` (discover a draft
   OWL ontology, keys pre-graded). Mirrors `certify-keys` / `discover-model`
   (data as repeatable `Name=path`, `--json`, Rich table). Advisory only.
2. **MCP tools `ontology_certify` / `ontology_discover`** (`mcp/server.py`),
   returning `OntologyCertification.to_dict()` / `DiscoveredOntology.to_dict()`
   (+ the Turtle) — the same wire shapes the library and (future) REST emit. A
   shared `_load_class_frames` loader mirrors `_tool_discover_semantic_model`.
3. **Parity-gated, declared `python_only`.** Both surfaces are added to
   `parity/goldenmatch.yaml` (`cli_commands.python_only: ontology`,
   `mcp_tools.python_only: ontology_certify/ontology_discover`) — like
   `discover_semantic_model` / `discover-model`, no TS port (the ontology layer is
   rdflib-backed, outside the edge/WASM surface). Tool-count assertions updated
   (`_BASE_TOOLS` 46→48, `TOOLS` 95→97); the generated api-surface + config-matrix
   docs regenerated.
4. **`rdflib`-optional, fail-clean.** The handlers lazy-import the ontology
   functions; without the `goldenmatch[ontology]` extra they return / print the
   `_require_rdflib` install hint rather than crashing the CLI or MCP server.

## Conformance to the two-engines frame (0047)
- **One authoritative owner** ✅ — the front door is a thin adapter over
  `certify_ontology` / `discover_ontology` (→ the single key-integrity certifier);
  no new logic.
- **Conformance defines correctness** ✅ — the parity gate now covers these
  surfaces; the emitted shapes match the library's `to_dict()`.

## Consequences / honest flags
- **CLI covers certify + discover, not reconcile/emit.** Those need a resolved
  `ResolvedCrosswalk` (an ER run) rather than just files; they stay library-reachable.
  A `reconcile`/`emit` subcommand is a possible follow-on.
- **No REST endpoint yet** — the semantic layer has `POST /semantic/discover`; an
  ontology REST route is a follow-on (the MCP + CLI cover the agent + shell paths).
- **Still deferred:** live-catalog write-back (ADR 0057, next) — writing the
  emitted RDF to a triple store.

---
**Classification:** decision/accepted • **Last updated:** 2026-08-13
