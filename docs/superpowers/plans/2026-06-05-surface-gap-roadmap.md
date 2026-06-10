# GoldenMatch Surface-Gap Roadmap (CLI / TUI / Web / API)

> **For agentic workers:** Each wave is independently shippable. Steps use checkbox (`- [ ]`) syntax. Use superpowers:executing-plans (or subagent-driven-development) per wave. `docs/superpowers/` is gitignored — do NOT `git add` this plan.

**Goal:** Close every gap surfaced in the 2026-06-05 four-surface audit (CLI, TUI, Web/HTTP, programmatic API) for the `goldenmatch` product, including full Python↔TypeScript parity.

**Source audit:** four-surface sweep, 2026-06-05. Version baseline: Python `goldenmatch` **1.25.0**, TS `goldenmatch` **0.13.0**.

**Ordering principle: RISK-FIRST.** Wave 0 fixes the one publicly-exposed security hole and the confirmed user-facing bugs. Then HTTP hardening, then orphaned-UI wiring, then library→CLI exposure, then TS parity (cheap surface-existing first, heavy net-new ports last), then polish.

**Scope decision (TS parity):** FULL parity in scope — every Python-only capability is treated as a TS gap. Exception: subsystems that are Python-only *by architecture* (native Rust kernel, Ray/Sail/DataFusion distributed backends, the React SPA) are NOT ported; Wave 6 records that decision explicitly rather than forcing an edge-hostile port.

**Branch/auth SOP:** feature branch per wave, squash-merge via PR, clean history. Python/TS packages use the work account; `packages/rust/extensions/` + `benseverndev-oss` use **personal** `benzsevern` (`gh auth switch --user benzsevern` before push, switch back after).

**Effort key:** S = <0.5 day · M = 0.5–2 days · L = 2–5 days · XL = >1 week.

---

# STATUS LEDGER (2026-06-05, end of execution session — supersedes the per-wave checkboxes below)

All waves executed in one session. 2 PRs merged, 10 open, all open PRs pairwise
merge-tree-verified conflict-free (any merge order works).

## Shipped / merged
| Item | PR | Notes |
|------|----|-------|
| Wave 0 — fail-closed MCP/A2A auth, `--preview` collision, `review` cmd, `unmerge` fix | #766 ✅ merged | **ACTION STILL OPEN: set `GOLDENMATCH_MCP_TOKEN` on the Railway `goldenmatch-mcp` service or the next deploy crash-loops** (fail-closed working as intended). CI lesson: never assert substrings of Rich-rendered `--help` (narrow CI terminal wraps tokens) — introspect click params. |
| Wave 1 — web/REST auth + CORS allowlist, SPA fallback, real health, A2A streaming:false | #767 ✅ merged | New env tokens: `GOLDENMATCH_WEB_TOKEN`, `GOLDENMATCH_API_TOKEN`, `GOLDENMATCH_API_CORS_ORIGINS`. |
| Wave 2.1 — wire 3 orphaned TUI widgets (progress overlay, threshold slider, autoconfig screen) | #769 ✅ merged | |
| (user's parallel PR) safe_path validation | #768 ✅ merged | Not from this roadmap; verified non-conflicting. |

## Open PRs (the queue)
| Item | PR |
|------|----|
| Wave 3.1 — `explain` / `lineage` / `anomalies` CLI commands | #771 |
| Wave 3.2 — `match` zero-config + `--backend` help | #773 |
| Wave 2.4 — in-TUI guided triage loop (Ctrl+T → TriageScreen) | #774 |
| Wave 3.3 — REST `/shatter` + `/unmerge` (cluster surgery) | #775 |
| Wave 2.2 — goldenpipe TUI stub → real (4 tabs wired to PipeResult) | #776 |
| Wave 2.3 — TS-TUI boost write-back + real export (`writeExports`) | #777 |
| Wave 4 — TS `evaluate` CLI + hardcoded-version fix | #779 |
| Wave 4 — TS `resolveClusters` port + identity `findConflicts`/`history` helpers | #780 |
| Wave 4 — TS config optimizer (ConfigEdit vocabulary + grid/coordinate loop) | #781 |
| Wave 4 — TS PPRL faithful port (CLK protocol + bitwise scoring) | #782 |

## Audit corrections discovered during execution (don't re-flag these)
- `analyze-blocking` NameError: **false positive** (module-level `console` resolves at call time).
- Web shatter: **already covered** via `POST /runs/{name}/unmerge` `mode="cluster"`.
- Web evaluation GT upload: **already covered** — GT derives from browser-writable steward labels, not a server path.
- TS identity `findConflicts`/`history`: not "un-exported" — they were **not implemented** as module helpers (store methods existed).
- Python `link_smc` is itself a **simulated** secret-sharing structure (real mp-spdz = future enhancement) — "port the MPC crypto" meant "match what Python actually does."

## Deferred with rationale (not silently dropped)
- **3.2 `schedule list/status/cancel`** — needs a job store + PID/signal layer (`ScheduledJob.start()` is a foreground blocking loop); a feature, not a consistency fix.
- **3.2 port unification** — Typer already shows defaults; changing `serve`'s 8080 breaks scripts.
- **3.3 REST sensitivity** — duplicates web `POST /api/v1/sensitivity`; low marginal value.
- **TS optimizer `confidence` objective** — needs a zero-label ComplexityProfile port first (`optimizeConfig` throws with guidance; `groundTruth`/`scoreFn` work).
- **TS `LLMProposer` / LLMRefitPolicy / llm_label_pairs / llm_extract_features** — env-gated LLM territory; custom `Proposer`/hooks accepted instead.
- **TS resolveClusters extras** — postgres bulk path, `cluster_frames` path, legacy hash-migration candidate, controllerSnapshot, batch-fingerprint (documented in TS CLAUDE.md).
- **`DOMAIN_EXTRACTED_COLS` 3→12** — requires the TS extractors to *produce* 9 more columns, not just list them.
- **TS `sensitivity` / `compare-clusters` CLI** — need cluster-file loader / sweep plumbing.
- **AgentSession + 13 agent MCP tools** — the last heavy port; deterministic and fixture-able but the widest surface. Own session.
- **Wave 6 polish** — SCORERS/TRANSFORMS codegen sync, docs-vs-registered-CLI test: still open.

## Declared Python-only by design (per TS CLAUDE.md — do not port)
Distributed/Ray/GPU, REST API + React web UI, native Rust kernel, Polars-only `bucket` backend.

## Methodology that worked (reuse for AgentSession + future ports)
Every heavy TS port shipped with a **Python-emitted parity fixture** (`packages/python/goldenmatch/scripts/emit_*_fixture.py` → `tests/parity/fixtures/*.json`):
- UUID outputs → compare **structure** (summary counts, record→entity groupings), not literal ids (resolveClusters).
- Float-boundary decisions → the emitter **asserts margins** (every score ≥0.10 from every threshold for the optimizer; ≥1e-3 for PPRL f32-vs-f64) so scorer tolerance can't flip an outcome.
- The fixtures caught two real divergences pre-merge: pydantic revalidation on blocking-key removal (config-edits) and a fragile borderline pair (optimizer emitter assertion).

---

## Wave 0 — Risk & confirmed bugs (ship first)

The only items that are either actively exposed or break documented workflows.

### 0.1 — Auth on the deployed MCP HTTP server `[M]` `[SECURITY]`
The Railway-hosted MCP endpoint `goldenmatch-mcp-production.up.railway.app/mcp/` is publicly reachable with **no token check** — any caller can invoke all 43 tools incl. file-writing ones (`export_results`, `create_domain`, `pprl_link`).

**Files:** `goldenmatch/mcp/server.py:1264` (`run_server_http`), `goldenmatch/mcp/server.py:1299` (`/mcp` route), `railway.json`.

- [ ] Add a bearer-token Starlette middleware gated on `GOLDENMATCH_MCP_TOKEN`; reject `/mcp` without it (401). Keep `/.well-known/mcp/server-card.json` public for healthcheck.
- [ ] Default-DENY when the env var is **unset in a server (HTTP) context** — fail closed, log a clear startup error. (stdio transport stays unauthenticated — local only.)
- [ ] Set `GOLDENMATCH_MCP_TOKEN` on the Railway service; document in `packages/python/goldenmatch/CLAUDE.md` under the Railway section.
- [ ] Mirror the same gate in TS `node/mcp` HTTP path if/when it serves over HTTP.
- **Acceptance:** `curl .../mcp/` without a token → 401; with token → tool list. Railway redeploy verified live.

### 0.2 — `dedupe --preview` option collision `[S]` `[BUG]`
`dedupe.py:72` (`preview`) and `dedupe.py:93` (`merge_preview`) both register the option string `--preview`. One shadows the other; `merge_preview` is unreachable.

**Files:** `goldenmatch/cli/dedupe.py:72,93`.

- [ ] Rename the merge-preview flag to `--merge-preview` (keep `--preview` = sample-without-writing).
- [ ] Add a regression test that both flags parse independently (`CliRunner`).
- **Acceptance:** `goldenmatch dedupe --help` shows both flags; each toggles its own behavior.

### 0.3 — Implement the phantom `review` command `[M]` `[BUG]`
`goldenmatch review` is documented in README:446, 3 wiki docs, and `docs/learning-memory.md` as the Learning-Memory quickstart, but is **not registered** — users hit "No such command." Machinery exists in `core/review_queue.py` (`ReviewQueue`, `gate_pairs()`).

**Files:** new `goldenmatch/cli/review.py`; register in `goldenmatch/cli/main.py` (~line 116, near `label`).

- [ ] Build `review_cmd`: load config, run/restore a run, gate borderline pairs, walk them interactively (reuse the GoldenCheck `action_guided_review` pattern), write decisions to MemoryStore/labels.
- [ ] Wire `app.command("review", ...)(review_cmd)`.
- [ ] Reconcile docs ↔ implementation (flags must match README example `goldenmatch review --config goldenmatch.yml`).
- **Acceptance:** the README Learning-Memory quickstart runs end-to-end.

### 0.4 — Make `unmerge` actually unmerge `[S]` `[BUG]`
`rollback.py:63` reads the CSV and logs what it *would* do but never calls `unmerge_record()`/`unmerge_cluster()`.

**Files:** `goldenmatch/cli/rollback.py:63-113`.

- [ ] Call `core.cluster.unmerge_record` / `unmerge_cluster` on the parsed input; honor `--shatter`.
- [ ] Remove the "use the Python API directly" dead-end message.
- **Acceptance:** `goldenmatch unmerge <record_id> --clusters out.csv` mutates the cluster file; round-trip test passes.

### 0.5 — A2A auth posture decision `[S]` `[SECURITY]`
`a2a/server.py:327` enforces bearer only if `GOLDENMATCH_AGENT_TOKEN` is set; unset = fully open.

- [ ] Decide + implement: fail-closed in HTTP/server mode (match 0.1), or document the open-by-default as intentional for local discovery. Recommend fail-closed for parity with 0.1.
- **Acceptance:** documented + enforced consistently with the MCP server.

---

## Wave 1 — HTTP hardening & observability

Bring the remaining 4 servers up to the Wave-0 bar before any of them get deployed.

### 1.1 — Auth on Web UI API + REST matching API `[M]` `[SECURITY]`
40+ `/api/v1/` routes (incl. `POST /run`, `/identities/{id}/merge|split`, `/rules/save`) and the stdlib REST server are both fully open; REST also sets `Access-Control-Allow-Origin: *` on every response.

**Files:** `goldenmatch/web/app.py`, `goldenmatch/api/server.py:458`.

- [ ] Optional bearer middleware (env-gated) on both, off by default for the localhost dev-tool use, **required** when bind host != `127.0.0.1`. Refuse to start on `0.0.0.0` without a token.
- [ ] Tighten REST CORS: reflect an allowlist env var instead of `*`.
- **Acceptance:** binding to `0.0.0.0` without a token aborts with a clear message.

### 1.2 — SPA catch-all fallback `[S]`
`web/app.py:61` mounts StaticFiles at `/` for exact `/` only; hard-refresh/shared URL to `/workbench`, `/runs/foo` → 404.

- [ ] Add a catch-all route returning `index.html` before the StaticFiles mount (CLAUDE.md already prescribes this).
- **Acceptance:** hard refresh on every client route serves the SPA.

### 1.3 — Real health checks `[S]`
All `/health(z)` endpoints return `{"status":"ok"}` unconditionally.

**Files:** `web/app.py:17`, `api/server.py:358`; add `/health` to A2A (`a2a/server.py` — currently none).

- [ ] Probe data.csv presence, memory-store reachability, project-root usability; return 503 + reasons on failure.
- [ ] Add a health endpoint to the A2A server.

### 1.4 — A2A streaming: implement or stop advertising `[M]`
Agent card claims `"streaming": true` (`a2a/server.py:182`) but `_handle_send_task` is synchronous — streaming clients hang on long skills.

- [ ] Either implement SSE/chunked task streaming, or set the capability to `false`. Recommend `false` now, real streaming as a follow-up.

---

## Wave 2 — Orphaned & half-wired UI

Fully-built components one wiring away from working.

### 2.1 — Wire the 3 orphaned Textual widgets `[M]`
- [ ] `tui/widgets/progress_overlay.py` — mount in `GoldenMatchApp`; drive from the `@work(thread=True)` match jobs (8-stage progress instead of a toast).
- [ ] `tui/widgets/threshold_slider.py` — add to Matches/Config tab; bind to `engine.recluster_at_threshold()` (already tested) for live re-cluster.
- [ ] `tui/screens/autoconfig_screen.py` — push from the dedupe auto-launch path, pre-populated with detected config.
- **Acceptance:** each widget visibly functions in `goldenmatch tui`; add pilot tests to `tests/test_tui.py`.

### 2.2 — goldenpipe TUI: stub → real `[M]`
`goldenpipe/tui/app.py` is 4 `Static` placeholders. The orchestrator package has the most to show and the least TUI.

- [ ] Wire Pipeline tab to `Pipeline.run()` stage timings; Config tab to `PipelineConfig`; Results tab to final golden records; Log tab to `PipeResult.reasoning`.
- [ ] Add a `goldenpipe interactive` launch command.

### 2.3 — TS TUI: persist boost labels + real export `[M]` `[PARITY]`
- [ ] Boost tab (`node/tui/app.ts:430`) — call the TS memory `addCorrection()` (exists) instead of dropping labels in local state.
- [ ] Export tab (`node/tui/app.ts:545`) — call the real file connector/CSV writer instead of the `setTimeout` simulation.

### 2.4 — Review-queue triage loop in the goldenmatch TUI `[M]`
No interactive borderline-pair triage (GoldenCheck has guided review; goldenmatch doesn't). Overlaps with 0.3 — share the walk-one-at-a-time component between CLI `review` and the TUI.

---

## Wave 3 — Library→CLI/HTTP exposure (Python)

Capabilities that exist in the API with no front door.

### 3.1 — New CLI commands for existing library features `[L]`
- [ ] `goldenmatch explain` → `core/explain.py` (`explain_pair_nl`/`explain_cluster_nl`).
- [ ] `goldenmatch lineage` → `core/lineage.py` (`build_lineage`/`save_lineage`).
- [ ] `goldenmatch graph-er` → `core/graph_er.py` (multi-table ER).
- [ ] `goldenmatch anomalies` standalone → `core/anomaly.py` (currently only a `dedupe --anomalies` flag).
- [ ] `goldenmatch domain` CRUD (list/create/test/save) → `core/domain_registry.py` (exists as MCP tools, no CLI).
- **Acceptance:** each command has `--help` + a smoke test.

### 3.2 — Fill CLI inconsistencies `[M]`
- [ ] `schedule` subcommands: `list` / `status` / `cancel` (`schedule.py` — currently start-only, no introspection).
- [ ] Unify "serve" port defaults (today: `serve`=8080, `mcp-serve`=8200, TS=8000). Pick a documented scheme.
- [ ] Let `match` run zero-config like `dedupe` (`match.py:22` currently requires `--config`).
- [ ] `dedupe --backend` help text: list all valid values (`bucket`, `chunked`, `ray`, `duckdb`).

### 3.3 — HTTP exposure gaps `[M]`
- [ ] Expose `shatter_cluster` over the Web UI API + REST (MCP has it; HTTP doesn't).
- [ ] Sensitivity endpoint on the REST matching server (only the Web UI API has it).
- [ ] Ground-truth upload for `/api/v1/runs/{run}/evaluation` (today requires a server-local path — blocks browser-only eval).

---

## Wave 4 — TS parity: surface existing library (cheap)

Close gaps where the TS library function **already ships** and just lacks a CLI/export.

### 4.1 — TS CLI commands over existing functions `[M]` `[PARITY]`
- [ ] `evaluate` → `core/evaluate.ts` (functions exist).
- [ ] `sensitivity` → `core/sensitivity.ts`.
- [ ] `compare-clusters` → `core/compare-clusters.ts`.
- [ ] `memory add` (TS memory group has stats/learn/export/import/show but no `add`).

### 4.2 — Python CLI parity with TS `[S]` `[PARITY]`
- [ ] `goldenmatch score <a> <b>` (TS has it; Python has `score_strings()` lib but no CLI).
- [ ] `goldenmatch info` (list scorers/strategies/transforms/blocking; TS has it, Python doesn't).

### 4.3 — Identity graph: self-population + re-exports `[M]` `[PARITY]`
- [ ] Wire `resolveClusters` in the TS pipeline so the TS identity graph self-populates (today read/query/manual-merge only; nothing fills it). Mirrors Python `identity/resolve.py:resolve_clusters`.
- [ ] Re-export `findConflicts` + `history` from `core/identity/index.ts` (they exist in `query.ts` but aren't surfaced).
- [ ] Add `identity resolve` to the TS CLI (deferred per `cli.ts:627`).

### 4.4 — Cheap TS correctness fixes `[S]` `[PARITY]`
- [ ] `cli.ts:221` — read version from `package.json` instead of the hardcoded `v0.1.0`.
- [ ] `domain.ts` — expand `DOMAIN_EXTRACTED_COLS` from 3 → 12 to match Python (flagged in TS CLAUDE.md).
- [ ] Document `recordFingerprint` sync(Py)/async(TS) divergence at both call sites, or add a sync TS path.

---

## Wave 5 — TS parity: net-new algorithm ports (heavy)

Real porting work where no TS implementation exists. Each is independently shippable.

### 5.1 — Config optimizer stack `[L]` `[PARITY]`
Port `optimize_config`, `GridProposer`/`LLMProposer`/`CoordinateDescentProposer`, the 6 config-edit types (`ThresholdShift` etc.), `suggest_threshold` (Otsu).

### 5.2 — PPRL real crypto `[L]` `[PARITY]`
TS `linkTrustedThirdParty`/`linkSMC` (`pprl/protocol.ts:180,201`) are self-labeled "API-parity stubs" over a simplified bloom approximation. Port the real CLK bloom-filter + MPC path; add `auto_configure_pprl_llm`, `compute_bloom_filters`.

### 5.3 — LLM subsystem parity `[M]` `[PARITY]`
Port `llm_label_pairs`, `llm_extract_features`, `LLMRefitPolicy` to TS.

### 5.4 — Data-ops parity `[M]` `[PARITY]`
Port `detect_anomalies`, `auto_map_columns` (schema match), `generate_diff`, `rollback_run`, `boost_accuracy`, `run_stream`, `save_lineage_streaming`/`load_lineage`.

### 5.5 — AgentSession + agent tools `[L]` `[PARITY]`
Port `core/agent.py:AgentSession` and the 13 MCP agent tools (`mcp/agent_tools.py`) to the TS Node surface.

### 5.6 — TS blocker + plugin completeness `[M]` `[PARITY]`
- [ ] Implement `ann` / `ann_pairs` / `canopy` / `learned` blocking (today `blocker.ts:593` throws at runtime — `ANNBlocker`/`HNSWANNBlocker` exist to build on).
- [ ] Open the `registerScorer`/`registerTransform`/`registerConnector` plugin slots (today only `golden_strategy` works).
- [ ] Decide: real embedder vs the hash-placeholder fallback (`scorer.ts:46`). At minimum make embedding-scorer calls **error loudly** when no real embedder is registered instead of silently producing placeholder vectors.

### 5.7 — TS memory/connector parity `[M]` `[PARITY]`
- [ ] `MemoryLearner.fieldWeights` (`learner.ts:76`) — implement or document the permanent null.
- [ ] TS Postgres memory backend (today in-memory + SQLite only).
- [ ] Expand TS connectors (5 → match Python's 12) as demand dictates — track per-connector, don't block the wave.

---

## Wave 6 — Polish, sync, and architecture decisions

### 6.1 — Frontend/config drift `[S]`
- [ ] Codegen `SCORERS`/`TRANSFORMS` in `web/frontend/src/lib/types.ts` from `config/schemas.py::VALID_SCORERS/VALID_SIMPLE_TRANSFORMS` (today hand-synced — new scorers silently miss the dropdowns).

### 6.2 — Doc reconciliation `[S]`
- [ ] Sweep README/wiki for other commands-that-don't-exist (the `review` class of bug); add a CLI-inventory test that asserts every documented command is registered.

### 6.3 — Record intentional Python-only boundaries (decision, not port) `[S]`
These are Python-only **by architecture**; the roadmap's job is to document the decision, not force an edge-hostile port:
- [ ] Native Rust/PyO3 kernel (`native.py`) — TS is edge-safe by design.
- [ ] Ray / Sail / DataFusion distributed backends — server-side only.
- [ ] React SPA (`web/`) — single-tenant local tool.
- [ ] `snowflake/udfs.py` Phase-2 stored procs + `identity/store.py` SQLite bulk-write stubs — confirm these are still wanted; either schedule or delete.
- **Acceptance:** a "TS parity: intentional exclusions" section in `packages/typescript/goldenmatch/CLAUDE.md` so future audits don't re-flag them.

---

## Suggested PR sequencing

| PR | Wave items | Why first |
|----|-----------|-----------|
| 1 | 0.1, 0.5 | Live security exposure |
| 2 | 0.2, 0.3, 0.4 | Confirmed bugs / broken docs |
| 3 | 1.1–1.4 | HTTP hardening before any new deploy |
| 4 | 2.1, 2.4 | High-value/low-cost orphaned UI |
| 5 | 2.2, 2.3 | Remaining UI wiring |
| 6 | 3.1, 3.2, 3.3 | Python feature exposure |
| 7 | 4.1–4.4 | Cheap TS parity (lib exists) |
| 8+ | 5.1–5.7 | Heavy TS ports, one PR each |
| last | 6.1–6.3 | Polish + record decisions |

**Cross-cutting gates:** every wave adds tests; CI parity gates (backend + cross-language) must stay green; per `feedback_verify_perf_not_just_ship`, any perf-touching change verifies wall-clock on the failing env, not just that it shipped.
