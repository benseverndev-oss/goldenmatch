# Codebase Audit Programme — Phase A: Dead and Unused Surface

**Status:** design approved, not yet planned
**Date:** 2026-09-01
**Phase:** A of a three-phase programme (A → B → C)

## Why

The codebase is ~974k LOC across four languages:

| language | code | files |
| --- | ---: | ---: |
| Python | 471,918 | 3,167 |
| TypeScript | 114,579 | 955 |
| Rust | 58,756 | 291 |
| SQL | 10,928 | 53 |

Two production bugs shipped on 2026-09-01, both from lookalike code that had
drifted from the real implementation: `MatchEngine` kept a parallel copy of the
dedupe pipeline, and `score_buckets` inverted `blocker.py`'s block-key dispatch.
Both produced silent wrong answers — zero pairs, no error — for real users. That
is the cost this programme exists to reduce.

## The programme

Three phases, sequenced. Each gets its own spec and implementation plan.

**A — Dead and unused surface (this spec).** Mechanical, reversible, lowest
risk. Shrinks what B and C have to reason about.

**B — Duplication and drift.** The proven bug source. Its hard problem is
distinguishing deliberate parity — the Python / TypeScript / Rust cross-surface
contracts, which must survive — from accidental drift. Includes resolving the
118 `*_py` reference functions (~1,680 lines; 108 in goldenflow, 10 in
goldenmatch) now that the native kernels are base dependencies.

**C — Cohesion and module splitting.** Highest risk and effort:
`autoconfig.py` 7,334 lines, `pipeline.py` 6,481, `probabilistic.py` 5,808,
`mcp/server.py` 2,968, `scorer.py` 2,953, `score_buckets.py` 2,921,
`identity/store.py` 2,822, `config/schemas.py` 2,670.

**Ordering rationale.** A shrinks the surface B and C must analyse. B precedes C
because splitting a module that has an undiscovered twin carries the drift into
two files instead of one.

## Decisions taken

**Public API is out of scope for deletion.** These packages have real consumers:
goldenmatch 3.17.1 is published, five packages are listed in the MCP registry
with download badges tracked, and `api_parity` runs across six packages — so a
public symbol is a cross-surface contract, not a single deletion. Phase A still
*inventories* unused public exports and hands the list on; a later effort can
decide about deprecation, where that cost is the point rather than a
side-effect.

**Nothing is deleted on static analysis alone.** See the evidence model.

## Architecture

### Evidence model

A module or symbol is a deletion candidate only when ALL of these hold:

1. **S1 static** — no reference outside its own definition.
2. **S2 runtime** — zero coverage under the union of the full pytest suite, the
   CLI sweep (66 registered commands) and the MCP sweep (97 tools).
3. **Not allowlisted** — see below.
4. **Not public API** — per the decision above.

### Registry-aware liveness (the load-bearing decision)

The codebase has 1,089 `getattr(` call sites. A static reference scan will
confidently condemn code that is reached dynamically, so liveness is not
inferred from references. The registries are resolved directly, and everything
they can dispatch to is marked live **by construction**:

| registry | live set |
| --- | ---: |
| transform registry | 113 registered transforms |
| MCP `TOOLS` | 97 dispatchable tools |
| typer command tree | 36 commands |
| `entry_points` | 2 |

This inverts the usual method: enumerate what is provably alive first, then
treat the remainder as candidates. Dynamic reachability becomes a first-class
input rather than an edge case the allowlist is expected to catch.

### Coverage union (new infrastructure)

The CI `coverage combine` step currently takes `coverage_shard1..3` and
`coverage_heavy_1..3` only. The CLI and MCP sweeps run as separate jobs and
contribute no coverage, so every code path reached only by invoking a command or
a tool is invisible to S2 today. Phase A wires those two runs into the combine.

This is independently useful: it makes the existing coverage baseline reflect
the command and tool surfaces, which it currently does not.

### Untested is not unused

Of the 487 measured modules, 16 report 0% coverage and 7 more are under 15%.
Several are live integrations that need external services and cannot run in CI:

```
goldenmatch/identity/mongo_backend.py     156 stmts
goldenmatch/core/vertex_embedder.py       122 stmts
goldenmatch/connectors/mongo.py            95 stmts
goldenmatch/connectors/hubspot.py          46 stmts
goldenmatch/connectors/bigquery.py         40 stmts
```

These are allowlist entries, not deletions. An audit that conflates the two
removes working integrations.

## Components

| component | responsibility |
| --- | --- |
| `scripts/dead_code/liveness.py` | Resolve the four registries into the live symbol set |
| `scripts/dead_code/static.py` | Per-language candidates: `check_dead_code.build_graph_ast`'s AST import graph (Python modules), `ts-prune` (TypeScript), `cargo-machete` plus the `check_native_symbols` unwired list (Rust) |
| CI coverage change | Run the CLI and MCP sweeps under coverage; add both to `coverage combine` |
| `parity/dead_code/*.yaml` | Allowlist, one reason per entry, the pre-existing per-package classification maps `check_dead_code.py`'s `dead_code_deferred` mechanism already consumed |
| `scripts/dead_code/report.py` | Intersect the signals; emit the candidate list with the evidence for each item; and separately emit the unused-public-export inventory, which is reported but never actioned in this phase |
| `scripts/test_dead_code_detector.py` | Tests for the detector itself |
| `scripts/test_no_new_dead_code.py` | Regrowth ratchet, `KNOWN_DEAD: set[str] = set()`, matching the `KNOWN_POLARS_BOUND` pattern |

`docs/agent-codemap.json` is the static backbone for module-level work elsewhere
in the repo: it already records `defines` and `imports` per module across six
packages and is regenerated in CI. It does not record symbol-level references,
which is why module-level and symbol-level work are staged separately. It is
not, however, this detector's source for the import graph — see below.

### Convergence with the pre-existing detector

A dead-code detector already existed in this repo before this phase started:
`scripts/check_dead_code.py`, spec'd at
`docs/superpowers/specs/2026-07-22-arch-aware-dead-code-detection.md` and shipped
via PRs #2020 and #2027. Phase A's `scripts/dead_code/*` began as a second,
independent detector; a later change in this phase converged it onto the
existing one's analysis rather than shipping a competing implementation.

Each side contributed a different half. The prior work brought the AST import
graph (`check_dead_code.build_graph_ast`) and the curated per-package
classification maps (`parity/dead_code/*.yaml`, consumed by its
`dead_code_deferred` mechanism). This phase brought the tests neither had, the
runtime coverage signal, the CI wiring, the regrowth ratchet, and the
TypeScript/Rust candidate sources.

The AST graph is the correct source, not `docs/agent-codemap.json`, because the
codemap under-records `from <pkg> import <submodule>` edges: measured, 42 of the
176 static candidates the codemap-backed version reported were modules that ARE
imported (e.g. `goldenmatch.core.strsim`, `goldenmatch.mcp._ingest`,
`goldenanalysis.mcp.server`, `goldenmatch.core.refit`) — false candidates from
the source alone, before any liveness or allowlist reasoning ran.

## Delivery stages

**A0 — Detector, report-only.** Build `liveness.py`, `static.py` and
`report.py`; wire sweep coverage into the combine; land the detector as a
reporting CI job that does not gate. No deletions. Exit criterion: the report
runs in CI and its output is reviewable.

**A1 — Module-level deletions.** Triage the first report at module granularity,
populate `parity/dead_code.allow` with a reason per entry, delete in per-package
pull requests. Exit criterion: every module-level candidate is either deleted or
allowlisted with a reason.

"Module-level" means a different unit per language, and A1 covers all three:

| language | unit | evidence source |
| --- | --- | --- |
| Python | a module file nothing imports and no registry reaches | codemap `imports` graph + registry resolution + coverage union |
| TypeScript | an exported module with no importer | `ts-prune` + coverage where the TS suite covers it |
| Rust | an unused crate dependency, or an exported symbol with no host reference | `cargo-machete` + `check_native_symbols` unwired list |

Rust symbol removal is limited to exports the unwired list already flags; Rust
internals are out of scope for A1 because `cargo-machete` reasons about
dependencies rather than functions.

**A2 — Symbol-level.** Requires a real reference scan and carries most of the
false-positive risk. Deferred to its own spec, written once A1 has shown whether
the remaining surface justifies it.

**A3 — Ratchet on.** Flip `test_no_new_dead_code.py` from reporting to gating
with `KNOWN_DEAD` empty, so new dead code fails CI.

## Being wrong

The failure that matters is deleting something live. Defences, in order:

1. **Registry resolution** — dynamic reachability is computed, not guessed.
2. **Two independent signals** — a false positive needs both the static scan and
   the coverage union to be wrong about the same thing.
3. **Reasoned allowlist** — every entry states why, so a future reader can tell
   a live-but-untestable integration from a stale entry.
4. **Small per-package PRs** — a bad deletion reverts cleanly.
5. **Full suite per deletion PR** — the existing gates run on every one.

## Testing

The detector is tested like a product component, because a detector that
silently measures nothing is the failure mode this repository keeps hitting.
Three separate instances on 2026-09-01 alone: a coverage floor gate that
evaluated nothing while printing "all met", a polars sweep that recognised only
raised exceptions and so scored two broken tools as `ok`, and a CI monitor that
reported every running workflow as failing.

Required tests:

- a deliberately dead fixture module IS reported;
- a fixture reached only through the transform registry is NOT reported;
- a fixture reached only through the MCP `TOOLS` list is NOT reported;
- a fixture reached only through a typer command is NOT reported;
- an allowlist entry naming a module that no longer exists FAILS, so the
  allowlist cannot rot into a silent pass.

Each is verified by sabotage — reverting the detector and confirming the test
fails — not by observing it pass.

## Success criteria

- The detector runs in CI and reports candidates with per-item evidence.
- Sweep coverage is included in the combined coverage artifact.
- Every module-level candidate is resolved: deleted, or allowlisted with a
  reason.
- An inventory of unused public exports exists for a later phase to act on.
- `test_no_new_dead_code.py` gates at zero.
- No live code is removed: the full suite and all existing gates stay green
  across every deletion PR.

## Out of scope

- Deleting or deprecating published public API.
- Symbol-level deletion — phase A2, separate spec.
- Duplication and drift — phase B.
- Module splitting — phase C.
- `_archive/`, which is retained pre-fold history rather than live code.
