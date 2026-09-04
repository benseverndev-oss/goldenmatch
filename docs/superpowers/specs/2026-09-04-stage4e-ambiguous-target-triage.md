# Phase C, Stage 4e — Triage of the 8 Ambiguous-Target Claims

**Status:** complete — all 8 individually triaged, zero real gaps found
**Date:** 2026-09-04
**Prior:** `docs/superpowers/specs/2026-09-04-stage4d-test-writing-results.md`,
PR #2863 (the resolver fix that first surfaced these 8 as a distinct bucket
instead of letting them masquerade as ordinary findings)

## Why this document exists

PR #2863 stopped 8 claims from silently resolving to a bare word's first
same-named declaration (`score`, `__init__`, `find_conflicts`, ...) and
routed them to their own `unenforced_ambiguous_target` bucket instead. That
fix deliberately did not attempt to guess the RIGHT target — it just
stopped the tool from confidently reporting a wrong one. This document does
the guessing, by hand: for each of the 8, read the claimant's full
docstring (not just the ambiguous bare word) to find what the claim is
actually about, same method already proven 5 times over in Stage 4c and 4d.

## Result: 8 claims, zero real gaps

| claim | real target | outcome |
| --- | --- | --- |
| `backends/score_buckets.py:_ensure_alias_tables_installed` | `core/scorer.py:_alias_match_single` (native id 8) | **ALREADY TESTED** — `tests/test_native_alias_parity.py` |
| `core/scorer.py:_iso_date_digits` | `score-core`'s private (non-`pub`, no pyo3 binding) `iso_date_digits` | **CROSS-LANGUAGE, genuinely not testable** — no Python-callable symbol exists |
| `core/vector_store.py:prep_rows` | `core/vector_index.py:VectorIndex._prep_frame` | **TEST WRITTEN, PASSES** |
| `identity/store.py:edges_by_kind` | `identity/store.py:find_conflicts` (same file) | **TEST WRITTEN, PASSES** |
| `mcp/server.py:_tool_score_pair` | TypeScript SDK `score_pair` (cross-language) + `core.scorer.score_pair` (direct delegation) | **PATTERN CLAIM + CROSS-LANGUAGE** — same two-part shape as the already-fixed `_tool_score_strings` |
| `spark/identity.py:build_incremental_nodes` | `identity/store.py:IdentityStore.retire_identity` | **TEST WRITTEN, UNVERIFIED (pyspark not available locally)** |
| `tui/engine.py:from_dataframe` | `MatchEngine.__init__` (same class, self-referential) | **TEST WRITTEN, PASSES (3 tests)** |
| `utils/transforms.py:canonical_soundex` | `score-core`'s `soundex` (already-known cross-language case) | **CROSS-LANGUAGE, ALREADY VERIFIED** — `tests/test_native_soundex_parity.py` |

**Every one of the 8 resolved cleanly** — no claim in this bucket turned
out to be a real, currently-unverified gap once correctly retargeted. 5 are
TRUE POSITIVE (2 already covered, 3 newly tested, 1 of those 3 not locally
executable due to this repo's own pyspark-is-CI-only policy), 2 are
genuinely cross-language and cannot be tested from Python, 1 is a mix of
cross-language and direct-delegation pattern claim.

## What "ambiguous" actually meant in practice

None of the 8 needed a coin-flip between two genuinely plausible candidates.
In every case, the docstring's own fuller text already named the real
target unambiguously — the bare-word resolver's ambiguity was a symptom of
looking at too little context (one word), not evidence of a genuinely
unclear claim. Three recurring shapes:

- **The full docstring already spells it out.** `edges_by_kind`'s window
  literally says "`find_conflicts` (which is `edges_by_kind('conflicts_with')`)"
  — the claim defines its own target precisely; only the bare-word search
  was ambiguous.
- **The bare word matched a real but wrong symbol elsewhere.** "score"
  matched dozens of unrelated declarations across the codebase in three of
  these claims, none of which were what any of the three docstrings meant.
- **Self-reference collapsed the ambiguity to zero.** `from_dataframe`
  "mirrors `__init__`" sounds like it could point at any of 79 same-named
  methods repo-wide — it means its OWN class's `__init__`, which needs no
  disambiguation once you notice it's the same class.

This suggests the resolver fix (PR #2863) is doing the right job for the
right reason: it is not that these 8 claims are unusually hard to verify —
it's that they were unusually easy for a bare-word match to get
confidently wrong, exactly the failure mode the fix targets.

## Being wrong about this document

All 8 were read individually and in full. The one residual uncertainty:
`build_incremental_nodes`'s new test could not be executed locally
(`pyspark` unavailable, per this repo's own policy) — it collects cleanly
and follows the file's existing fixture conventions, but has not actually
run anywhere yet. Everything else in this document rests on a test that
was run and passed, or a cross-language claim confirmed by reading the
non-Python source directly.
