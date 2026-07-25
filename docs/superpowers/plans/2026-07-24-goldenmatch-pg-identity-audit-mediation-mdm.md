# goldenmatch-pg: identity audit + mediation + MDM SQL surface (post-#1913)

**Status:** planned (not started). **Depends on:** #1913 (closed) — the in-DB
identity write path, GUC/DSN plumbing, dual `db_path`/in-DB store selection, and
the `rust_pgrx` CI smoke it shipped are the foundation this reuses wholesale.

## 1. Context — what's already on the SQL surface vs. what's missing

`#1913` (design: `docs/superpowers/specs/2026-07-19-goldenmatch-pg-in-db-identity-write-path-design.md`)
brought the Postgres extension from read-only identity to a stateful in-DB
engine. As-built on `origin/main` (pgrx **0.15.0**), the identity SQL surface is:

| Present today | Kind | Bridge fn | Python entrypoint |
|---|---|---|---|
| `goldenmatch_identity_resolve` | read | `identity_resolve` | `find_by_record` |
| `goldenmatch_identity_view` | read | `identity_view` | `get_entity` |
| `goldenmatch_identity_history` | read | `identity_history` | event log |
| `goldenmatch_identity_conflicts` | read | `identity_conflicts` | conflicts |
| `goldenmatch_identity_list` | read | `identity_list` | list |
| `gm_resolve` | write | `resolve_identities` | `resolve_clusters` |
| `gm_identity_merge` | write | `identity_merge` | `manual_merge` |
| `gm_identity_split` | write | `identity_split` | `manual_split` |

The Python identity API (`goldenmatch.identity`) — and the MCP/CLI/REST surfaces —
expose **three families that never reached SQL**. These are NOT unfinished #1913
work (they were explicit non-goals of that design); they're the clean next
extension that brings the SQL surface to identity parity with the other surfaces
(every-capability-on-every-surface, the North Star commitment).

## 2. The gap — 8 functions, all class-A embedded-Python wrapping

Each is a thin `#[pg_extern]` wrapper → `goldenmatch_bridge::api::*` → embedded
CPython calling an existing `goldenmatch.identity` entrypoint. No new resolution
logic, no schema DDL (the Python store owns identity schema — §5 of the #1913
design), no Python-side changes. Same two templates already in the tree:

- **Read template** — `quick.rs::goldenmatch_identity_resolve` + `api::identity_resolve`.
  Wrapper takes `db_path: String`, routes through `identity_store_ref(db_path)`
  (non-empty = SQLite path or explicit DSN; empty = in-DB via the
  `goldenmatch.identity_dsn` GUC / env). Bridge opens the store, calls the
  Python fn, closes on all paths, `json.dumps(..., default=str)`.
- **Write template** — `quick.rs::gm_identity_merge` + `api::identity_merge`.
  DSN-required (empty rejected with the "set `goldenmatch.identity_dsn`" error);
  otherwise identical.

### 2a. Audit chain (tamper-evident log)

| New SQL fn | Kind | Python call | Notes |
|---|---|---|---|
| `gm_identity_audit(dataset, db_path)` | read | `store.export_audit_log(dataset)` | the raw event log; `dataset`/`db_path` empty = all / in-DB |
| `gm_identity_audit_verify(dataset, db_path)` | read | `verify_audit_chain(store, dataset=…)` | returns an `AuditVerification` — content + chain integrity |
| `gm_identity_audit_seal(dataset)` | write | `seal_audit_log(store, dataset=…, actor=…)` | DSN-required; **returns `None` when nothing new to seal** → emit `{"sealed": false}` |

Cross-language note: the audit hashing is already proven byte-identical
Python↔TS (the `audit.ts` port + conformance harness, ADR 0046). This surface
just exposes the *same* Python chain through SQL — no new hashing.

### 2b. Mediation (steward conflict resolution)

| New SQL fn | Kind | Python call | Notes |
|---|---|---|---|
| `gm_identity_resolve_conflict(dataset, record_a, record_b, resolution, steward, reason)` | write | `mediate_conflict(store, record_a, record_b, resolution, *, steward, reason, dataset)` | `resolution` ∈ `same`/`distinct`/`defer`; DSN-required |
| `gm_identity_claim(dataset, record_id, entity_id, actor, trust, reason)` | write | `claim_record(store, …)` | steward assertion; DSN-required. **Confirm exact `claim_record` arg list at impl time** |

`mediate_conflict` has a wide kwarg surface (`apply`, `config`, `actor`,
`trust`); v1 exposes the core (`record_a`, `record_b`, `resolution`, `steward`,
`reason`, `dataset`) and lets the rest default, matching how the MCP
`identity_resolve_conflict` tool calls it. Widen later if a caller needs it.

### 2c. MDM reads (steward/operator views)

| New SQL fn | Kind | Python call | Notes |
|---|---|---|---|
| `gm_identity_profile(entity_id, db_path)` | read | `entity_profile(store, entity_id)` | `None` when entity absent → `{"found": false}` |
| `gm_identity_stats(dataset, db_path)` | read | `identity_summary_stats(store, dataset=…)` | graph-level health summary |
| `gm_identity_worklist(dataset, db_path)` | read | `steward_worklist(store, dataset=…)` | v1 uses default `weak_confidence=0.6`, `limit=50`; add as SQL args only if asked |

## 3. Naming decision

Existing surface has two prefixes: original reads are `goldenmatch_identity_*`;
the #1913 write path is `gm_*`/`gm_identity_*`. **Use `gm_identity_*` for all 8
new functions** — they're the newer identity generation, and `gm_identity_*`
reads cleanly for both reads and writes. (Do not retro-rename the five
`goldenmatch_identity_*` reads — back-compat; that's an unrelated churn.)

## 4. Serialization (the one impl subtlety)

The read bridge fns return dataclasses (`AuditVerification`, `EntityProfile`,
`IdentitySummary`, `list[WorklistItem]`), which are **not** JSON-serializable by
`json.dumps(default=str)` alone (that only rescues leaf values, not the dataclass
container). Confirm at impl time whether each type exposes `.to_dict()` (the
existing read fns use `view.to_dict()`) or needs `dataclasses.asdict`. Mirror
exactly what the MCP tool layer (`mcp/identity-tools`) already does for the same
types — those tools serialize these today, so a serialization path exists;
reuse it so SQL output matches MCP/CLI byte-for-byte.

## 5. Versioning / packaging (per-PR tax, do it once)

pgrx **0.15.0 → 0.16.0**. Adding `#[pg_extern]`s means (the `pgrx_sql_sync` gate
enforces Rust→SQL presence, so all of these are mandatory in the same PR):

1. `sql/goldenmatch_pg--0.16.0.sql` — new base (regenerate via `cargo pgrx schema`, or hand-add the 8 `CREATE FUNCTION`s mirroring the 0.15.0 base).
2. `sql/goldenmatch_pg--0.15.0--0.16.0.sql` — migration adding the 8 functions.
3. `.control` `default_version = '0.16.0'` + `Cargo.toml` `version = "0.16.0"` (lockstep).
4. `cp sql/goldenmatch_pg--0.16.0.sql …` line in **both** `ci.yml` (`rust_pgrx` lane) and `publish-goldenmatch-pg.yml`.

No `api_parity` manifest change: the SQL surface is **not** in `parity/goldenmatch.yaml`
(only MCP tools / CLI / a2a_skills are gated). The MCP `identity_*` tools already
exist, so no MCP-side change either.

## 6. CI

Extend the existing `rust_pgrx` smoke (`.github/workflows/ci.yml`, ci-required,
PG 15/16/17) which already does create → absorb → read → split → merge. Add, in
the same in-DB dataset: `gm_identity_audit_seal` → `gm_identity_audit_verify`
(assert `verified: true`) → `gm_identity_profile`/`_stats`/`_worklist` return
non-error JSON → a `gm_identity_resolve_conflict('distinct', …)` on a seeded
conflict pair. Reads that take `db_path` also get a SQLite-path smoke to prove
the dual path (mirror how the existing read smoke passes a temp SQLite file).

## 7. Phasing — recommend ONE PR

The #1913 design phased P1–P4 across PRs because P1 carried real design surface
(GUC, DSN, connection model, schema ownership). **That infra now exists**, so
these 8 are pure mechanical additions on one shared pattern. One PR = one version
bump, one migration file, one smoke extension — cheaper than three stacked pgrx
version bumps (which conflict on `.control`/SQL and each need the `cp` lines).

Fallback split if review is heavy: (A) MDM reads + audit-read/verify (all reads,
lowest risk, carries the version bump), (B) writes (audit_seal, resolve_conflict,
claim). Reads-first means the seal/verify pair lands together (verify is only
useful once you can seal), so if splitting, keep `audit_seal` with `audit_verify`
in PR B rather than straddling.

## 8. Risks

- **Writes commit outside the caller's SQL transaction** (the #1913 §3.1
  property — the store uses its own libpq connection). Same as `gm_identity_merge/split`;
  idempotent replay is the safety net. Document on the new write fns identically.
- **`claim_record` arg list unconfirmed** — the one signature not yet read.
  Confirm before writing its wrapper (§2b).
- **`seal_audit_log` returns `None`** (nothing new to seal) — the wrapper must
  not blow up on a `None` (emit `{"sealed": false}`), unlike merge/split which
  always return a dict.
- **DuckDB parity: deferred** (same as #1913 — DuckDB has no durable
  multi-connection server store). Note it in the extensions "deferred by design"
  table.

## 9. Task checklist (TDD-shaped, one PR)

- [ ] Confirm `claim_record` signature + the five dataclass `.to_dict()` paths (read, don't guess).
- [ ] `api.rs`: 8 bridge fns (5 read via `open_identity_store`, 3 write DSN-required), mirroring `identity_resolve`/`identity_merge`.
- [ ] `quick.rs`: 8 `#[pg_extern]` wrappers (reads via `identity_store_ref`, writes DSN-checked).
- [ ] Version bump 0.15.0→0.16.0: `.control`, `Cargo.toml`, base SQL, migration SQL.
- [ ] `cp` lines in `ci.yml` + `publish-goldenmatch-pg.yml`.
- [ ] Extend `rust_pgrx` smoke (§6); confirm `pgrx_sql_sync` gate green.
- [ ] `cargo fmt` + `cargo clippy -D warnings` on the touched crates (native-ext CI is `-D warnings`).
- [ ] Docs: add the 8 fns to `docs-site/extensions/sql.mdx`; note DuckDB-deferred.
