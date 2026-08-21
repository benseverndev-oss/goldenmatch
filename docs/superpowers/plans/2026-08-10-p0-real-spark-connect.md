# P0 — Prove the tier on real Spark Connect: Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish, as fact rather than inference, whether the existing Spark tier runs against **Apache Spark Connect** — and enumerate any incompatibilities.

**Architecture:** The 9 sail test files each duplicate a `spark` fixture hardcoding `pysail.spark.SparkConnectServer`. Extract one shared, backend-agnostic fixture driven by an env var, keep Sail as its default so nothing changes today, then add a second CI lane that points the same tests at real Spark. The tests are the experiment; no engine code changes.

**Tech Stack:** pytest fixtures, `pyspark[connect]`, Apache Spark 4 local Connect server (`builder.remote("local[*]")`), GitHub Actions.

---

## Why this is P0

Spec: `docs/superpowers/specs/2026-08-10-spark-native-execution-design.md`.

Every later phase assumes the tier speaks generic Spark Connect. The evidence for that is *"`session.py` is `builder.remote()` and a grep finds no Sail-specific calls"* — strong, but inference. If Spark Connect's coverage differs from Sail's anywhere the tier touches, P1–P6 reshape. This is the cheapest possible test of the load-bearing assumption.

**A red result here is a success.** The deliverable is a fact plus an enumerated gap list, not a green tick.

### Scope

P0 only. P1–P6 each get their own plan. This plan changes **no engine code** — if you find yourself editing `goldenmatch/sail/*.py`, stop: that is a P0 *finding*, to be recorded and specced, not fixed here.

---

## File Structure

| File | Responsibility |
|---|---|
| `packages/python/goldenmatch/tests/conftest_spark.py` | CREATE — the single backend-agnostic `spark` fixture |
| `packages/python/goldenmatch/tests/test_sail_*.py` (9 files) | MODIFY — delete the local fixture, import the shared one |
| `.github/workflows/ci.yml` | MODIFY — add the `spark_connect` lane |
| `docs/superpowers/specs/2026-08-10-spark-native-execution-design.md` | MODIFY — record the P0 result |

The fixture goes in its own module rather than the root `tests/conftest.py`: the root conftest is imported by the whole suite, and it must not grow a pyspark import that every unrelated test pays for. `test_quality_no_polars.py` already documents the cost of a conftest importing heavy deps.

---

## Task 1: Extract one backend-agnostic spark fixture

**Files:**
- Create: `packages/python/goldenmatch/tests/conftest_spark.py`
- Test: existing `tests/test_sail_connectivity.py` is the check

- [ ] **Step 1: Write the shared fixture**

```python
"""One `spark` fixture for every sail/spark test.

Backend-agnostic ON PURPOSE (P0). `GOLDENMATCH_SPARK_REMOTE` selects the server:

  unset            -> spawn a local pysail SparkConnectServer (today's behaviour)
  "local[*]"       -> Apache Spark's own local Connect server (needs pyspark >= 4)
  "sc://host:port" -> an already-running Connect endpoint

The 9 test files each carried a copy of this hardcoding
`pysail.spark.SparkConnectServer`, which is why the tier had never been run
against real Spark: the tests could not express it.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("pyspark")


@pytest.fixture(scope="module")
def spark():
    from pyspark.sql import SparkSession

    remote = os.environ.get("GOLDENMATCH_SPARK_REMOTE")

    if not remote:
        # Default: pysail in-process (unchanged from the per-file fixtures).
        from pysail.spark import SparkConnectServer

        server = SparkConnectServer()
        server.start()
        _, port = server.listening_address
        sess = SparkSession.builder.remote(f"sc://localhost:{port}").getOrCreate()
        yield sess
        sess.stop()
        server.stop()
        return

    # Real Spark: `local[*]` spawns Spark's own Connect server in-process;
    # an sc:// URL attaches to a running one.
    sess = SparkSession.builder.remote(remote).getOrCreate()
    yield sess
    sess.stop()
```

- [ ] **Step 2: Verify the default path is unchanged**

Run: `.venv/bin/python -m pytest packages/python/goldenmatch/tests/test_sail_connectivity.py -v --timeout=300`
Expected: PASS, identical to before (the fixture is not yet wired in, so this is the baseline).

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/tests/conftest_spark.py
git commit -m "test(spark): one backend-agnostic spark fixture (P0 precondition)"
```

---

## Task 2: Point the 9 test files at the shared fixture

Do them **one file per commit**. A per-file commit keeps the blast radius readable if one test turns out to depend on module-scoped server state.

**Files (each: delete the local `spark` fixture, add the import):**

`test_sail_clustering_checkpoint.py` · `test_sail_clustering_parity.py` · `test_sail_connectivity.py` · `test_sail_determinism.py` · `test_sail_golden_parity.py` · `test_sail_identity_incremental.py` · `test_sail_identity_parity.py` · `test_sail_pipeline.py` · `test_sail_score_parity.py`

- [ ] **Step 1: In one file, replace the local fixture with the import**

```python
from conftest_spark import spark  # noqa: F401  -- pytest fixture by import
```

Delete the file's own `@pytest.fixture def spark(): …` block and its now-unused `SparkConnectServer` / `SparkSession` imports.

- [ ] **Step 2: Run that file**

Run: `.venv/bin/python -m pytest packages/python/goldenmatch/tests/<file> -v --timeout=300`
Expected: PASS, same count as before.

- [ ] **Step 3: Commit, then repeat for the next file**

- [ ] **Step 4: After all 9, run the whole sail suite**

Run: `.venv/bin/python -m pytest packages/python/goldenmatch/tests/test_sail_*.py -v --timeout=600`
Expected: same pass/skip counts as the pre-change baseline. **Record both numbers** — this is the control for Task 4.

`test_sail_scorer_native_parity.py` and `test_sail_r3_feature_gate.py` have no `spark` fixture; leave them alone.

---

## Task 3: Add the `spark_connect` CI lane

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add the job**

Model it on the existing `sail` job (~line 2938). Two deliberate differences: it installs **`pyspark[connect]>=4` and NOT `pysail`**, and it sets the env var.

```yaml
  # P0 (spec 2026-08-10-spark-native-execution-design): the same sail tests
  # against APACHE SPARK's own Connect server, not Sail's. The tier is believed
  # backend-agnostic (session.py is builder.remote(); no Sail-specific calls),
  # and this lane is what turns that belief into a fact.
  #
  # NOT ci-required while P0 runs -- a red here is a FINDING to enumerate, not a
  # merge blocker. Promote to required once green (P2).
  spark_connect:
    needs: changes
    if: needs.changes.outputs.sail == 'true' || needs.changes.outputs.force_all == 'true'
    runs-on: ubuntu-latest
    timeout-minutes: 30
    continue-on-error: true
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10  # v6
      - uses: astral-sh/setup-uv@caf0cab7a618c569241d31dcd442f54681755d39  # v3
        with:
          enable-cache: true
          cache-dependency-glob: uv.lock
      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: '17'
      - run: uv sync --all-packages --no-install-package goldenmatch-native --no-install-package goldenflow-native
      # NO pysail. Real Spark's own Connect server, via pyspark 4.
      # (goldenmatch[sail] pins pyspark<4 because of pysail -- install directly.)
      - name: Install pyspark connect (no Sail)
        run: uv pip install 'pyspark[connect]>=4'
      - name: Confirm Sail is absent
        run: |
          .venv/bin/python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('pysail') is None else 'pysail present; this lane must test real Spark')"
      - name: Sail tests against Apache Spark Connect
        env:
          GOLDENMATCH_SPARK_REMOTE: "local[*]"
        run: |
          .venv/bin/python -c "import pyspark; print('pyspark', pyspark.__version__)"
          .venv/bin/python -m pytest packages/python/goldenmatch/tests/test_sail_*.py -v --timeout=600 \
            --junitxml=spark-connect-results.xml
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v4
        if: always()
        with:
          name: spark-connect-p0-results
          path: spark-connect-results.xml
          if-no-files-found: warn
```

- [ ] **Step 2: Do NOT add it to `ci-required`**

While P0 runs, a red is data. Adding a new job means adding its filter entry and `if:` gate — the `sail` filter already covers these paths, so reuse it rather than inventing one.

- [ ] **Step 3: Commit and push; let the lane run**

The `pysail`-absent assertion is the important one. Without it a lane that silently picked up pysail from the lockfile would prove nothing while looking green.

---

## Task 4: Record the result

**Files:**
- Modify: `docs/superpowers/specs/2026-08-10-spark-native-execution-design.md`

- [ ] **Step 1: Compare against the Task 2 Step 4 baseline**

Per test: same pass / newly failing / newly skipped.

- [ ] **Step 2: Write the verdict into §2 of the spec**

For each failure, record the test, the Spark Connect error, and whether it's:
- **(a)** a Spark Connect API gap → constrains the design, feeds P4/P5
- **(b)** a Sail-ism the tier accidentally depends on → a P2 fix
- **(c)** a test-harness assumption (module-scoped session reuse, port binding) → fix in the test

Classification matters more than the count. (a) reshapes the spec; (c) is noise.

- [ ] **Step 3: Commit the finding**

```bash
git commit -m "docs(spec): record the P0 real-Spark-Connect result"
```

---

## Definition of done

- [ ] One `spark` fixture; 9 files import it; no `SparkConnectServer` outside `conftest_spark.py`
- [ ] Default (pysail) path byte-identical in pass/skip counts to the pre-change baseline
- [ ] `spark_connect` lane runs, with pysail proven absent
- [ ] Every deviation classified (a)/(b)/(c) and written into the spec
- [ ] **No engine code changed**

## Notes

- **Do not run this locally.** pyspark + a JVM will OOM the dev box; see `feedback_offload_heavy_installs_and_runs`. Task 1–2 verification runs in CI via the existing `sail` lane, which those commits already trigger.
- `pyspark[connect]>=4` in the P0 lane is deliberately *outside* the `[sail]` extra's `<4` pin. Do not relax the extra's pin here — that is P2, and it should happen when pysail leaves, not before.
- If `builder.remote("local[*]")` turns out not to spawn a server on the installed pyspark, fall back to `sbin/start-connect-server.sh` from an `apache/spark` service container. Record which was used.
