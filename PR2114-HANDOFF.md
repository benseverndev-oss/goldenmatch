# Handoff — PR #2114 (goldenmatch)

> Scratch handoff note for resuming work in a fresh session. **Delete this file
> before flipping the PR to ready / merging** — it should not land on `main`.

**PR:** https://github.com/benseverndev-oss/goldenmatch/pull/2114 (draft)
**Branch:** `claude/emit-singletons-distributed-reach-r3n2q8`
**Head commit:** `1e824d0`

## What the PR does
Follow-up to #2105 (closed by #2111). Closes the two soft sub-asks left after the
bounded/batched SQLite resolve landed:

1. **`emit_singletons` SQLite ceiling advisory** — new
   `_maybe_warn_singleton_sqlite_ceiling()` in `identity/resolve.py`: one-time,
   downgrade-safe (`try/except`, never raises/gates) warning when
   `emit_singletons=True` on SQLite over >=100k rows, pointing at
   `emit_singletons=false` / Postgres. Documented in
   `docs-site/goldenmatch/identity-graph.mdx`.
2. **Single-node distributed reachability** — doc note in the same mdx: the
   per-partition resolver needs Ray **and** `backend=postgres`; a single-node
   SQLite run's only levers are `emit_singletons=false` and (past the low
   millions) Postgres.

## Commits on the branch
- `13e43e3` — feature (`identity/resolve.py` + mdx +
  `tests/identity/test_resolve_scaling.py`, 4 new tests)
- `1e824d0` — regenerated `docs/agent-codemap.json` (the `config_matrix`
  freshness gate flagged it stale after the new function)

## Local verification (all green)
- `test_resolve_scaling.py` -> 14 passed (4 new)
- 4-file identity suite (`test_resolve.py test_resolve_scaling.py
  test_incremental_resolve.py test_conflict_detection.py`) -> 43 passed
- `ruff check` clean on changed files

## Outstanding — what to do first
1. **Check CI on head `1e824d0`.** Failed jobs on run `30160091932` were
   re-run. Confirm `ci-required` is green.
   - The one red lane was `python_goldenmatch (3)` ->
     `test_weighted_null_renormalization::test_genuine_disagreement_still_does_not_match[polars-direct]`.
     **This is a flake, unrelated to the diff** (weighted-scoring path; the diff
     only touches identity resolve + docs + codemap). Passes locally (all 11 in
     that file). If it fails *again deterministically* on the re-run, treat it as
     real and investigate; otherwise it's noise.
2. **Nothing else pending.** `config_matrix` is fixed; the code-quality bot's
   "unused global" comment was a false positive and is already answered on the
   thread (the `global` latch is read + written correctly).

## Notes / gotchas
- **`uv.lock` churn:** every `uv run` in the sandbox rewrites `uv.lock`
  (byte-identical size, no real change). It is **not** part of the PR —
  `git checkout -- uv.lock` if the stop-hook flags it.
- PR is still a **draft.** Flip to ready once CI is green, then merge via the
  queue per repo SOP (`gh pr merge 2114 --auto --squash`, or "Merge when ready").
- Delete this `HANDOFF-2114.md` in the same commit that readies the PR.
