# 0063 — Generated API reference (Python static, REST OpenAPI, TypeScript deferred)

**Status:** accepted (design); implementation staged, not yet started.
**Design:** `docs/superpowers/specs/2026-08-24-generated-api-reference-design.md`
**Related:** 0058 (single docs-regen command and gate), 0047 (one product, two engines)

## Context

The derived-docs battery covers capability surfaces exhaustively and the **API**
not at all. Measured 2026-08-24: **330 exported symbols** across the six documented
packages (202 of them goldenmatch's) with no generated reference of any kind.
`docs-site/reference/api-surface.mdx` is a capability matrix — versions and MCP tool
counts — not a reference.

Three packages ship FastAPI REST services and no OpenAPI spec is committed;
`docs_render` runs `mint validate --disable-openapi`, so a docs-site capability is
switched off because nothing feeds it.

The only gate on this surface is `check_llms_counts`, which asserts a number in
prose equals `len(__all__)`. It catches "the count moved" and cannot catch a renamed
parameter, a changed return type, or a docstring describing behaviour the function
no longer has.

## Decision

1. **Python: generate statically with `griffe`.** No importing. The repo has already
   paid for this lesson — `gen_suite_matrix` deliberately avoids importing
   `<pkg>.mcp.server` because it needs the `[mcp]` extra and flaps between a dev box
   and CI. At 330 symbols across trees with optional torch / aiohttp / ray / spark
   dependencies, an import-based generator (sphinx-autodoc, pdoc) would inherit that
   trap everywhere. griffe parses with `ast`.
2. **Scope to `__all__`**, one page per package, driven by
   `config_matrix.roster.DOCUMENTED`, wired as a `WRITE_STEPS` entry in
   `regen_docs.py` with outputs in `GENERATED_PATHS`.
3. **REST: emit OpenAPI from the FastAPI apps**, commit the specs, drop
   `--disable-openapi`. This one imports, which is acceptable for three known
   service modules — constructing the app is what the service does at startup, so a
   failure there is a real defect, not a missing extra.
4. **TypeScript: deferred, explicitly.** The TS public surface is already gated
   cross-language by `parity/<pkg>.yaml` + `api_parity`, which is the property that
   matters. Recorded as a decision rather than left as an omission.
5. **Ship in four stages**, each green on its own; the documented-symbol *floor*
   (ratchet) lands last, because a ratchet on an unmeasured backlog is red on
   arrival.

## Conformance to the two-engines frame (0047)

- **One authoritative semantic owner** — the reference is derived from the code and
  adds no second source of truth; it renders the existing owner.
- **Conformance defines correctness** — this does *not* become a parity surface.
  `parity/<pkg>.yaml` + `api_parity` remain the cross-language contract; this renders
  the Python side for humans and agents and does not adjudicate parity.
- **Arrow at bulk boundaries / compute-vs-control / kernelize on measurement** — not
  applicable; build-time tooling with no runtime data path.

## Consequences / honest flags

- **Adds one dev dependency** (`griffe`). It is the only new tool in the plan and is
  the standard one for this job.
- **Surfaces the docstring backlog.** Stage 2 will render a lot of thin docstrings.
  That is the tool working; stage 4's floor is how it gets paid down without
  blocking a green build.
- **Page weight is a measurement question.** goldenmatch's 202-symbol page may slow
  `mint validate`; splitting by module is the escape hatch, taken on measurement.
- **Static analysis cannot resolve a runtime-built `__all__`.** Any package doing
  that must be found in stage 1 and either made static or excluded with a stated
  reason, using the deferral-map pattern already established in
  `config_matrix/roster.py`.
- **Not doing this leaves the largest doc surface ungated** — every other surface in
  the battery is generated and drift-checked; the API is the one that is neither.
