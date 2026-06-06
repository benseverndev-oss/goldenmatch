# Surface hardening + parity — the 2026-06-05 four-surface arc

A risk-first sweep of goldenmatch's four user surfaces (CLI, TUI, web/HTTP,
programmatic API) driven by a same-day audit. Detailed working ledger:
`docs/superpowers/plans/2026-06-05-surface-gap-roadmap.md` (gitignored,
local-only — the STATUS LEDGER section there is the fine-grained source of
truth; this node carries the durable shape).

## What the audit found
- Four unauthenticated HTTP servers, one publicly deployed (the Railway MCP).
- Two confirmed CLI bugs: a `--preview` flag collision and a documented-but-
  unregistered `goldenmatch review` command (`unmerge` was also a no-op stub).
- Three fully-built-but-never-mounted TUI components.
- Broad Python→TS drift, partly real gaps, partly already declared
  Python-only by design (TS CLAUDE.md).

## Shipped (merged)
- **Wave 0 (#766)** — fail-closed bearer auth on the MCP HTTP + A2A servers
  (`GOLDENMATCH_MCP_TOKEN` / `GOLDENMATCH_AGENT_TOKEN`; non-loopback binds
  refuse to start without a token), `--merge-preview` split, real `review`
  command, real `unmerge`.
- **Wave 1 (#767)** — web/REST fail-closed auth (`GOLDENMATCH_WEB_TOKEN` /
  `GOLDENMATCH_API_TOKEN`), CORS allowlist (`GOLDENMATCH_API_CORS_ORIGINS`,
  no more `*`), SPA hard-refresh fallback, readiness-grade health checks,
  A2A card now honestly advertises `streaming: false`.
- **Wave 2.1 (#769)** — the three orphaned TUI components wired (progress
  overlay, threshold slider, auto-config review screen).

## In flight (PRs open, pairwise conflict-verified — any merge order)
\#771 explain/lineage/anomalies CLI · #773 match zero-config · #774 in-TUI
triage loop · #775 REST shatter/unmerge · #776 goldenpipe TUI · #777 TS-TUI
boost/export · #779 TS evaluate CLI · #780 TS resolveClusters + identity
helpers · #781 TS config optimizer · #782 TS PPRL faithful port.

## OPEN ACTION
The Railway `goldenmatch-mcp` service must get `GOLDENMATCH_MCP_TOKEN` set
before its next deploy, or it crash-loops — that is the fail-closed guard
working as intended.

## The parity-fixture methodology (durable, reuse for future ports)
Every heavy TS port ships with a Python-emitted fixture
(`packages/python/goldenmatch/scripts/emit_*_fixture.py` →
`packages/typescript/goldenmatch/tests/parity/fixtures/*.json`):
- UUID-bearing outputs → compare **structure** (summary counts,
  record→entity groupings), never literal ids.
- Float-boundary decisions → the **emitter asserts margins** (e.g. every
  pair score ≥0.10 from every swept threshold; ≥1e-3 for f32-vs-f64) so
  cross-language scorer tolerance cannot flip an outcome.
- Track record: caught two real divergences pre-merge (pydantic
  revalidation on blocking-key removal; a fragile borderline pair).

## What remains
- **AgentSession + 13 agent MCP tools** — the last heavy TS port; own session.
- Small: `DOMAIN_EXTRACTED_COLS` 3→12, TS sensitivity/compare-clusters CLI,
  optimizer `confidence` objective (needs a zero-label profile port).
- TS `0.14.0` release cut (changelog + wave-history row) once the queue merges.

---
**Classification:** planning/workstream • **Last updated:** 2026-06-05
