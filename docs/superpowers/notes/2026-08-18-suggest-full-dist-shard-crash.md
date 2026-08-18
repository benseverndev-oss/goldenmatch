# `test_suggest_full_dist` was OOM-killed under `-n auto`

**Status: root-caused and fixed.** The file is `--ignore`d from the parallel
`python_goldenmatch` shards and runs SERIALLY in the same job (shard 1 only),
which keeps native available. No coverage is lost.

## Symptom

```
[gw0] node down: Not properly terminated
worker 'gw0' crashed while running
  'tests/test_suggest_full_dist.py::test_full_dist_on_lowers_to_high_side_valley_when_threshold_far_above'
```

A hard worker death with no assertion and no `out of memory` / `SIGKILL` /
`SIGSEGV` / `panicked` string anywhere in the job log.

## How it was identified

Re-enabled the test on a debug branch forked from PR #2664's head with
`PYTHONFAULTHANDLER=1` and `PYTHONUNBUFFERED=1`. The env vars are confirmed set
in the job log, the crash reproduced, and **faulthandler produced no dump at
all**.

faulthandler installs handlers for SIGSEGV, SIGABRT, SIGFPE, SIGBUS and SIGILL
-- every fatal signal except **SIGKILL**, which is uncatchable by design. A
fatal-signal death with faulthandler armed and silent therefore leaves SIGKILL
as the only candidate, and SIGKILL arriving mid-test on a CI runner is the
kernel OOM killer. The absence of a log message is explained by the same fact:
the OOM killer writes to dmesg, not to the job's stdout.

`ci.yml` already documents this exact signature for the heavy lane: those files
"OOM-kill xdist workers under `-n auto` + coverage on a 2-core runner (silent
'node down', no traceback)".

## Why it appeared on PR #2664

That PR adds four test files. `ci.yml` notes that "any new test file reshuffles
pytest-split groups" (bit W2a PR #1632) -- the reshuffle changed which
memory-heavy tests share an xdist worker, and the new grouping exceeds the
runner. Consistent with:

* all three python shards GREEN on the parent PR #2657;
* deterministic reproduction on #2664 (3 runs, 3 crashes) -- same files, same
  split, same co-tenancy.

## Ruled out: the PR's own logic

The obvious theory is that a PR changing auto-config blocking changed this
test's inputs. It does not:

* the committed config for the ncvr corpus is byte-identical on `main` and on
  the branch (`multi_pass`, 2 passes, `birth_year` + `zip_code`,
  `max_block_size=5000`), checked by running
  `scripts.suggest_quality.oracle._auto_configure_no_rerank` under both;
* the full-frame blocking measurement the branch adds costs **5 ms / 4 MB** on
  that 7,500-row frame, and both passes vectorize (no exact fallback);
* it passes locally standalone with the native kernel (34.5 s) and alongside
  all four new files under `-n 2` (21 passed).

## Why the serial step lives in `python_goldenmatch`, not the heavy lane

`python_goldenmatch_heavy` is where OOM-prone files normally go, but it syncs
`--no-install-package goldenmatch-native` while the parallel shards BUILD
native (`scripts/build_native.py`, "native is the test path, not an opt-in
lane"). `test_suggest_full_dist` skips without the native `suggest_config`
kernel, so relocating it there would have converted an OOM into a silent skip.
Running it serially inside the job that already has native keeps the assertion
alive: it is the only ACTIVE end-to-end check that the right-anchored dip fix
survives the full marshaling path (diagnostic run -> Arrow batches -> native
`suggest_config` -> `Suggestion`).
