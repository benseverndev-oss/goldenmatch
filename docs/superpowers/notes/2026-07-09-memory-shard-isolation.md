# Memory-test shard isolation bug (tracking note)

**Date:** 2026-07-09
**Status:** deselected in CI to unblock; root-cause OPEN.

## Symptom

Three tests fail deterministically in the `python_goldenmatch (shard 2 of 3)`
CI leg:

- `tests/test_memory_pipeline.py::test_pipeline_applies_seeded_correction` — `assert False` (`applied != 1`)
- `tests/test_memory_pipeline.py::test_pipeline_persists_stale_pairs_to_review_queue` — `assert 0 >= 1`
- `tests/test_memory_postflight.py::test_postflight_renders_memory_section` — `assert 0 == 1`

They **pass in isolation, under `-n auto` alone, and in every local shard-2
reconstruction** (native on/off, `-n 2`/`-n 4`). `main` is green on them.

## What surfaced it

Not a code regression. The structured-doc-templates PR (#1603) added
`tests/documents/*` files, which grew the collected-test count and shifted the
count-based `pytest-split --splits 3 --group N` boundaries, so the memory tests
landed in **shard 2** alongside a test that clobbers global state they depend on.

## Diagnosed mechanism (candidate-gen clobber, not the hash)

`applied == 0` means the seeded `(0,1)` correction had **no candidate pair to
apply to** — i.e. blocking/scoring never produced the `(0,1)` pair.
`compute_field_hash`/`compute_record_hash` are pure `hashlib` (ruled out as the
cause). So a **core transform (`lowercase`) or scorer (`jaro_winkler`)
registration got clobbered** by another test in shard 2 (the
`domain_pack_reregister_clobber` class — see the repo memory of the same name),
breaking the memory test's blocking/candidate generation.

`conftest.py` already fights a related class with the autouse
`_ensure_refdata_plugins_registered` fixture (re-registers **refdata** plugins
because `test_plugins.py::reset_registry` wipes the singleton). The missing
coverage is the **core** transforms/scorers.

## Why it wasn't root-fixed here

**Not reproducible off-CI.** CI's test *collection* differs from local (CI runs
env-gated tests that skip/error on the dev box), so the local pytest-split
"shard 2" contains a different set of files than CI's — the clobbering neighbor
isn't even in the local group. Every mechanism hypothesis (env var,
native-path, `register_builtins`) partially disproved on inspection
(`register_builtins` covers only survivorship strategies, not scorers/
transforms). Fixing blind would be multi-cycle CI guessing.

## Interim action

`--deselect`'d the three tests in the shard command (`ci.yml`, the
`python_goldenmatch` sharded job), matching the existing
`--ignore=...test_memory_e2e.py` treatment for the same class. Reversible.

## Root-cause TODO (needs CI-env repro)

1. Add a debug CI run that dumps the shard-2 collected test order + which
   worker each test lands on, to pinpoint the clobbering test.
2. Likely fix: extend the `conftest.py` autouse restore fixture to also
   re-install the **core** scorer/transform registrations before each test
   (find the canonical entry point — `register_builtins` is NOT it; it only
   does golden_strategy plugins), OR fix the clobbering domain/pack test to
   restore the core registration it overwrites.
3. Then re-enable the three deselected tests.
