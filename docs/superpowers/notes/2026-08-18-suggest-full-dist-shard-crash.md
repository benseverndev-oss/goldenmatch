# `test_suggest_full_dist` crashes its xdist worker after a shard reshuffle

**Status:** unresolved. Deselected in `python_goldenmatch` (parallel shards) so
PR #2664 can land; the test still runs nowhere in CI.

## Symptom

```
worker 'gw0' crashed while running
  'tests/test_suggest_full_dist.py::test_full_dist_on_lowers_to_high_side_valley_when_threshold_far_above'
FAILED ... - worker 'gw0' crashed while running ...
===== 1 failed, 4449 passed, 83 skipped ... =====
```

A hard worker death, not an assertion. No `out of memory`, `SIGKILL`,
`SIGSEGV`, or Rust `panicked` string appears anywhere in the job log.

## What is established

* **Green before the reshuffle.** All three `python_goldenmatch` shards passed
  on PR #2657, which merged.
* **Deterministic after it.** Two runs on PR #2664 (heads `5225a158` and
  `38086235`), two crashes, same test.
* **Not caused by #2664's logic.** That PR changes auto-config blocking, so the
  obvious theory is that it changed this test's inputs. It does not:
  * the committed config for the ncvr corpus is byte-identical on `main` and on
    the branch (`multi_pass`, 2 passes, `birth_year` + `zip_code`,
    `max_block_size=5000`) -- checked by running
    `scripts.suggest_quality.oracle._auto_configure_no_rerank` under both;
  * the full-frame blocking measurement the branch adds costs **5 ms / 4 MB**
    on that 7,500-row frame, and both passes vectorize (no exact fallback).
* **Not reproducible off CI.** Passes standalone with the native kernel
  (34.5 s), and passes alongside all four of #2664's new test files under
  `-n 2` (21 passed). Local is Windows; CI is Linux.

The remaining difference is co-tenancy: #2664 adds four test files, and
`ci.yml` already documents that "any new test file reshuffles pytest-split
groups and re-exposes it (bit W2a PR #1632)" for the
`test_memory_pipeline` / `test_memory_postflight` trio. This is the same class,
except it manifests as a crash rather than a wrong value.

## Why it was deselected rather than moved

`python_goldenmatch_heavy` is where OOM-prone files go, but it syncs
`--no-install-package goldenmatch-native` while the parallel shards **build**
native (`scripts/build_native.py`, "native is the test path, not an opt-in
lane"). `test_suggest_full_dist` skips without the native `suggest_config`
kernel, so relocating it would convert a crash into a silent skip -- the check
that does not fire.

## What the deselect costs

The test is the only ACTIVE end-to-end assertion that the right-anchored dip
fix survives the full marshaling path (diagnostic run -> Arrow batches ->
native `suggest_config` -> `Suggestion`). Its sibling
`..._does_not_collapse...` is explicitly a regression guard whose loop body is
vacuous on this corpus, so it does not cover the same ground. The Rust unit
tests cover the dip logic but not the boundary.

## Next steps for whoever picks this up

1. Reproduce on Linux with the shard's actual membership:
   `pytest packages/python/goldenmatch --splits 3 --group 3 -n auto` with
   native built, and bisect the co-tenant by `--deselect`ing halves.
2. If a co-tenant is implicated, look for global-state mutation (env vars,
   transform/scorer registration) that changes the config handed to
   `suggest_config` -- a different kernel input is the only route from
   co-tenancy to a segfault.
3. `faulthandler` is worth enabling in that job (`PYTHONFAULTHANDLER=1`); the
   absence of any signal string in the log is itself suspicious and may just be
   xdist swallowing the child's stderr.
