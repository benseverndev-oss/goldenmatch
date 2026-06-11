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

### Fixtures rot silently — the #856/#857 lesson
The fixtures are the only invariant the TS lane checks, and **nothing kept
them fresh**: the `typescript` CI job runs only on TS path changes, so a
pure-Python behaviour change leaves the committed vectors stale and the TS
parity test green against a fixture that no longer reflects Python (#856).
The 2026-06-11 audit found real drift sitting in `main` this way. Two
durable guards came out of fixing it (#857):
- **Bundle data via a generator + drift-guard test, never hand-copied.**
  When a TS port needs Python-side reference data (the refdata tables),
  generate the TS module from the Python source of truth
  (`scripts/sync_ts_refdata.mjs`) and add a test that deep-equals the
  generated const against that source. A Python-side data change then fails
  CI until re-synced — the table can't drift silently.
- **Pin numeric parity to Python-computed ground truth, not a self-mirror.**
  `tests/parity/scorer-ground-truth.test.ts` carries hardcoded
  Python-`score_pair` values at 4-decimal tolerance. A unit test that only
  checks "TS scorer == a TS re-implementation of its own rule" passes even
  if both diverge from Python; the ground-truth file is what actually binds
  the cross-language number.

### #857 — refdata name scorers + autoconfig blocking parity (TS, merged)
Closed the controller-stoppoint drift the #856 audit surfaced. Ported the
two refdata name scorers to the edge-safe TS core — `given_name_aliased_jw`
(alias-aware JW) and `name_freq_weighted_jw` (Census surname-IDF-weighted
JW) — plus the first/last-name auto-config refine (`refineNameScorer`,
last-before-first like Python; `multi_name` left unrefined) and a faithful
port of `build_blocking`'s selection (exact-eligibility gated at
`cardinality_ratio ≤ 0.5`, gates on the exact pool only, secondary-name
multi-pass passes). Scope grew twice from the original one-scorer ask: the
regen forced porting the surname scorer (its 186KB table) too, then the
remaining red turned out to be a separate blocking-evolution gap that also
got ported. Deferred (out of scope): the refdata transform packs
(`legal_form_strip`/`address_normalize`/`naics_normalize`) and the geo/date
blocking branches. Follow-up #860: TS `buildWeightedMatchkey` still drops
`nullRate>0.5` name columns while the blocking path now keeps them — a
matchkey-null-gate divergence (the reason `sparse_people` stays loose-shape).

## What remains
- **AgentSession + 13 agent MCP tools** — the last heavy TS port; own session.
- Small: `DOMAIN_EXTRACTED_COLS` 3→12, TS sensitivity/compare-clusters CLI,
  optimizer `confidence` objective (needs a zero-label profile port).
- TS `0.14.0` release cut (changelog + wave-history row) once the queue merges.

---
**Classification:** planning/workstream • **Last updated:** 2026-06-11
