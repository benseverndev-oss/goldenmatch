# Generated API reference — design

**Status:** design, not yet implemented. Decision recorded in
`context-network/decisions/0063-generated-api-reference.md`.

## The gap

The doc-generation battery covers *capability* surfaces exhaustively — config
matrices, agent manifest + codemap, suite matrix, native pages, capability counts.
It covers the **API** not at all.

Measured on 2026-08-24:

| Surface | Symbols | Generated reference |
| --- | --- | --- |
| goldenmatch | 202 exports | none |
| goldencheck | 44 | none |
| goldenflow | 36 | none |
| goldenpipe | 20 | none |
| infermap | 20 | none |
| goldenanalysis | 8 | none |
| **total** | **330** | **none** |

`docs-site/reference/api-surface.mdx` is a *capability matrix* — per-package
versions and MCP tool counts — not a reference. Nothing anywhere renders what a
function does, what it takes, or what it returns.

Three packages ship FastAPI REST services (`goldenmatch/web/app.py`,
`goldenflow/api/server.py`, and the goldenpipe service) and **no OpenAPI spec is
committed**; `docs_render` runs `mint validate --disable-openapi`, so the docs site
has an OpenAPI capability that is switched off because there is nothing to feed it.

The only current gate on the API surface is `check_llms_counts`, which asserts that
a *number* stated in prose equals `len(__all__)`. That catches "the count moved". It
cannot catch a renamed parameter, a changed return type, or a symbol whose docstring
describes behaviour it no longer has.

## Constraints this repo has already paid for

These are not hypotheticals — each is a lesson already recorded elsewhere in the
battery, and a design that ignores one will fail the same way.

1. **Environment-stable, or it flaps.** `gen_suite_matrix` explicitly does *not*
   `import <pkg>.mcp.server`, because that needs the `[mcp]` extra and the output
   then differs between a dev box and CI. Any API-reference generator that
   **imports** the packages inherits that trap at 330-symbol scale, where optional
   extras (torch, aiohttp, ray, spark) decide whether a module is importable at all.
   → the generator must be **static-analysis**, not import-based.
2. **Byte-deterministic.** `regen_docs.py --check` compares bytes, so output must be
   ordered deterministically and written with `newline="\n"`, and the pages need
   `eol=lf` + `linguist-generated` in `.gitattributes` (Phase 2's lesson).
3. **One entry point.** It must be a `WRITE_STEPS` entry in `regen_docs.py`, with
   its outputs covered by `GENERATED_PATHS` — `test_regen_docs.py` now enforces
   both, so a generator added outside the entry point fails.
4. **The docs site must still render.** `docs_render` runs `mint validate` in strict
   mode over every page. Hundreds of new pages is a build-time and nav-integrity
   question, not just a generation question — and `check_docs_consistency`'s orphan
   detection requires every `.mdx` to be reachable from `docs.json`.
5. **Docstring quality is the real bottleneck.** A generated reference makes the
   *current* docstrings visible at scale. That is the point, but it means stage 1
   will surface a large prose backlog, and the gate must not be "all 330 symbols
   documented" on day one or it can never go green.

## Design

### Python — griffe, static

[griffe](https://mkdocstrings.github.io/griffe/) parses source with `ast` and never
imports the package, which satisfies constraint 1 directly. It resolves `__all__`,
signatures, type annotations, and docstrings (Google/NumPy/Sphinx styles).

- **Scope: `__all__` only.** Not every module member. `__all__` is already the
  package's declared public contract, it is already what `check_llms_counts` counts,
  and it keeps the reference to the 330 symbols that are actually promised.
- **Output: one page per package**, `docs-site/reference/python/<pkg>.mdx`, symbols
  in `__all__` order (deterministic, and it is the order the package author chose).
  One page per *module* is the obvious alternative and is rejected for now: it
  multiplies nav entries and orphan-detection surface for no reader benefit at this
  size. Revisit if goldenmatch's page becomes unwieldy.
- **Driven by the roster.** `config_matrix.roster.DOCUMENTED` decides which packages
  get a page — the same single edit that onboards a package everywhere else.
- **New dependency:** `griffe`, dev-only (workspace dev group, not a package
  runtime dep). This is the one genuinely new tool in the plan.

### REST — emit OpenAPI from the apps

FastAPI already builds the spec: `app.openapi()` returns it. Emit to
`docs-site/reference/openapi/<service>.json`, commit it, and drop
`--disable-openapi` from `docs_render` so Mintlify renders it.

This one **does** import — a FastAPI app cannot be constructed statically. It is
acceptable here and not in the Python case because the surface is three known
service modules rather than six package trees, and the import is exactly what the
service does at startup, so a failure is a real defect rather than a missing extra.

### TypeScript — deferred, explicitly

`typedoc` → MDX is the obvious path, but the TS packages' public surface is already
gated by `parity/<pkg>.yaml` + `api_parity`, which is the property that matters
cross-language. A TS reference is real work for a smaller readership. **Deferred**,
recorded here so it is a decision rather than an omission.

## Staging

Each stage lands green on its own and is independently revertable.

| Stage | Ships | Gate |
| --- | --- | --- |
| 1 | griffe generator + one page for the smallest package (goldenanalysis, 8 exports) | `regen_docs --check` covers it |
| 2 | All six packages; nav entries; `.gitattributes`; coverage *reported*, not gated | orphan + render gates |
| 3 | OpenAPI emission for the three services; drop `--disable-openapi` | `docs_render` |
| 4 | Ratchet: a *floor* on documented public symbols per package, raised as prose lands | new gate, floor-style like the coverage floors in `parity/` |

Stage 4 is the one that makes it stick, and it is deliberately last: a ratchet on a
backlog you have not measured yet is a gate that is red on arrival.

## Conformance to the two-engines frame (0047)

- **One authoritative semantic owner.** The reference is *derived from* the code, so
  it adds no second source of truth. It is a rendering of the existing owner.
- **Arrow at bulk boundaries.** Not applicable — this is build-time tooling, no
  runtime data path.
- **Compute vs control stay distinct.** Not applicable.
- **Kernelize on measurement.** Not applicable; no hot path.
- **Conformance defines correctness.** The reference does not become a parity
  surface: `parity/<pkg>.yaml` + `api_parity` remain the cross-language contract.
  This renders the Python side for humans and agents; it does not adjudicate parity.

## Honest flags

- **Page weight.** goldenmatch's page will be large (202 symbols). If `mint validate`
  slows materially, split by module — that is the escape hatch, taken on measurement
  rather than pre-emptively.
- **Dynamic exports.** Static analysis cannot resolve an `__all__` built at runtime.
  Any package doing that must be found in stage 1 and either made static or
  explicitly excluded with a reason (the deferral-map pattern used elsewhere).
- **This surfaces the docstring backlog.** Expect stage 2 to render a lot of thin
  docstrings. That is the tool working, not failing; stage 4's floor is how it gets
  paid down without blocking.
- **griffe is a new dependency.** It is the only one, it is dev-only, and it is the
  standard tool for exactly this job.
