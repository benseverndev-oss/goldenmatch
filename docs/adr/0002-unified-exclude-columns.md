# ADR-0002: Unified `exclude_columns` API surface across the suite

**Status:** Accepted
**Date:** 2026-05-21 (PRs #405, #406, #407)

## Context

GoldenCheck, GoldenFlow, and GoldenMatch each had their own (or no) notion of "skip this column." GoldenCheck has `Finding` objects with rule-specific severities. GoldenFlow's `apply_transforms` touched every column it had a rule for. GoldenMatch's auto-config picked matchkeys from every column it profiled.

Three problems:
1. A `record_hash` column legitimately needed to be matched on (PPRL bloom-filter use case) but NOT transformed by GoldenFlow. No way to express that.
2. Auto-config detector exclusions (#404 — audit, sentinel, foreign-id, etc.) needed to be paired with a user-override mechanism. Env vars covered ad-hoc cases; the YAML config had nowhere to put a user list.
3. Different surfaces (CLI / REST / MCP / A2A / Python) had to surface the same parameter consistently.

## Decision

Single `GoldenMatchConfig.exclude_columns: list[str]` field — the canonical source of truth. Layered ADDITIVELY with detector-derived exclusions (#404) and env-var overrides (#404 V1 path). `QualityConfig.autoconfig_force_include` wins on conflict (rescue beats every opt-out path).

Three surface layers:
- **YAML** (`exclude_columns: [created_at, external_id]`).
- **Python kwarg** (`dedupe_df(df, exclude_columns=[...])`).
- **CLI flag** (`--exclude-columns col1,col2,col3`).

REST / MCP / A2A surfaces share one `EXCLUDE_COLUMNS_SCHEMA` JSON Schema fragment (in `goldenmatch/_exclusions_schema.py`) so the parameter shape stays in lockstep across servers. Parity test diff-checks them byte-equal.

Rejected alternatives:
- **Per-tool exclusions** (`flow_exclude` / `match_exclude`). Premature; the single "skip across the suite" sledgehammer covers 95% of real cases. Per-tool granularity is a V2 if a user files for it.
- **Pattern/glob exclusions** (`exclude_columns: ["audit_*"]`). Defer; concrete column lists work today.
- **Replace detector exclusions with user list only.** No — detectors catch what users don't realize is bad (sentinel-polluted phone columns, foreign IDs disguised as identity claims). Layered is the right composition.

## Consequences

Positive:
- One mental model: "want to skip a column? `exclude_columns`." Works the same way across every entry point.
- GoldenFlow's strip-and-reattach pattern (column passes through unchanged) preserves the column in golden record output without applying transforms.
- MCP / A2A schemas can't drift — parity test enforces.

Negative:
- The runtime ContextVar (`_RUNTIME_EXCLUDE_COLUMNS`) is process-wide within a pytest worker. Tests that set it must reset it (autouse fixture in `tests/conftest.py`). Caught a real cross-test leak in CI; documented as a goldenmatch-specific gotcha.
- The "additive layering" means there's no clean way to express "ignore detectors entirely, use ONLY my list." That's documented in #404's spec as an explicit non-goal — if a user filed for it, we'd add a `disable_autoconfig_exclusions: bool` flag.

Cross-references:
- Specs: `docs/superpowers/specs/2026-05-21-unified-column-exclusions-design.md` + `2026-05-21-exclude-columns-surfaces-design.md`
- PRs: #404 (issue), #405 (detectors), #406 (API + YAML), #407 (CLI / REST / MCP / A2A surfaces)
