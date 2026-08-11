# P1 — Executor dependency delivery: Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** get `goldenmatch` onto the Spark executors' Python workers **from the client, at session time**, with no cluster-side install — and prove it loaded rather than inferring it.

**Architecture:** A packed relocatable venv is shipped via Spark Connect's `addArtifact(..., archive=True)` and the UDF worker is pointed at its interpreter with `spark.sql.execution.pyspark.python`. Ship it as a supported helper (`goldenmatch.sail.deps`) rather than a docs snippet, because every user needs the identical four lines.

**Tech Stack:** `venv-pack`, Spark Connect artifact transfer, `pyspark[connect]>=4`.

---

## Why this is P1, and why it is a blocker

P0 (run [31496638072](https://github.com/benseverndev-oss/goldenmatch/actions/runs/31496638072)) returned **20 failed / 36 passed**, and every failure was one cause:

```
ModuleNotFoundError: No module named 'goldenmatch'   (73x)
ModuleNotFoundError: No module named 'pandas'        (13x)
```

raised inside the Python UDF worker. No Spark Connect API gaps were found. The
tier is compatible; its **dependencies are not delivered**.

P0 also established *why this was never seen*: Sail's Connect server runs
in-process, so its worker shares the client interpreter. Real Spark forks a
separate worker. Anything that only ever ran on pysail structurally could not
surface this.

**Exit for P1 is the `spark_connect` lane going green** — and, separately, proof
that the native kernel is *present* on the worker (using it is P3).

---

## The mechanism (verified against upstream docs)

```python
import venv_pack
venv_pack.pack(output="gm_env.tar.gz")

spark.addArtifact("gm_env.tar.gz#environment", archive=True)
spark.conf.set("spark.sql.execution.pyspark.python", "environment/bin/python")
```

`addArtifact`/`addArtifacts` is **Spark Connect-only** (it raises on a classic
session), archives are unpacked executor-side automatically, and session-based
dependency management has existed since Spark 3.5.0. This is exactly the
capability that makes the zero-install cutover story true rather than aspirational.

---

## File Structure

| File | Responsibility |
|---|---|
| `packages/python/goldenmatch/goldenmatch/sail/deps.py` | CREATE — `ship_python_environment()` + `executor_probe()` |
| `packages/python/goldenmatch/tests/conftest.py` | MODIFY — fixture ships the env when told to |
| `packages/python/goldenmatch/tests/test_spark_executor_deps.py` | CREATE — the proof tests |
| `packages/python/goldenmatch/pyproject.toml` | MODIFY — `venv-pack` into the sail extra |
| `.github/workflows/ci.yml` | MODIFY — pack the venv, point the lane at it |

---

## Task 1: `ship_python_environment` + an executor probe

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/sail/deps.py`

- [ ] **Step 1: Write the failing test first**

Create `tests/test_spark_executor_deps.py`:

```python
"""P1: the executor's Python worker can import goldenmatch, because the client
shipped it. Without this every pandas_udf dies with ModuleNotFoundError -- which
is exactly what P0 measured (20 failures, one cause)."""
from __future__ import annotations

import pytest

pytest.importorskip("pyspark")


def test_executor_can_import_goldenmatch(spark):
    """The proof. Runs ON THE EXECUTOR, not the driver."""
    from goldenmatch.sail.deps import executor_probe

    report = executor_probe(spark)
    assert report["goldenmatch"] is True, (
        f"goldenmatch is not importable on the executor: {report}"
    )
    assert report["rapidfuzz"] is True, f"rapidfuzz missing on the executor: {report}"


def test_probe_runs_on_the_executor_not_the_driver(spark):
    """Guard on the guard: a probe that accidentally reports the DRIVER's
    interpreter would pass while proving nothing."""
    from goldenmatch.sail.deps import executor_probe

    report = executor_probe(spark)
    assert report["ran_on"] == "executor", report
```

- [ ] **Step 2: Run it, watch it fail**

In CI (not locally — pyspark + JVM OOMs the dev box). Expected: `ModuleNotFoundError`, or an ImportError for `goldenmatch.sail.deps`.

- [ ] **Step 3: Write `deps.py`**

```python
"""Ship the client's Python environment to Spark executors (P1).

Spark Connect's ``addArtifact(..., archive=True)`` uploads an archive and
unpacks it executor-side; ``spark.sql.execution.pyspark.python`` then points the
UDF worker at that interpreter. Together they put ``goldenmatch`` on the
executors WITHOUT a cluster-side install -- which is the whole zero-friction
claim for a Splink-on-Spark cutover.

This is Spark Connect ONLY. ``addArtifact`` raises on a classic session.
"""
from __future__ import annotations

from typing import Any

_ENV_NAME = "environment"


def ship_python_environment(spark: Any, archive: str, env_name: str = _ENV_NAME) -> None:
    """Upload ``archive`` (a relocatable venv, e.g. from ``venv-pack``) and point
    the UDF workers at its interpreter.

    ``archive`` must be built for the EXECUTOR platform (manylinux), not the
    client's. A venv packed on macOS/Windows will unpack but its interpreter will
    not run on Linux executors -- and the scorer's pure fallback means that
    failure is SILENT (correct results, none of the speed). Build it in CI.
    """
    spark.addArtifact(f"{archive}#{env_name}", archive=True)
    spark.conf.set("spark.sql.execution.pyspark.python", f"./{env_name}/bin/python")


def executor_probe(spark: Any) -> dict[str, Any]:
    """What is importable ON THE EXECUTOR. Returns a dict, never raises.

    Deliberately a UDF: asking the driver proves nothing, because the driver is
    where the client venv already lives.
    """
    from pyspark.sql.functions import udf
    from pyspark.sql.types import StringType

    @udf(returnType=StringType())
    def _probe(_ignored: str) -> str:
        import json
        import os

        def _importable(name: str) -> bool:
            import importlib.util

            try:
                return importlib.util.find_spec(name) is not None
            except Exception:
                return False

        native = False
        try:
            from goldenmatch.core._native_loader import native_module

            native = native_module() is not None
        except Exception:
            native = False

        return json.dumps(
            {
                # A UDF body only runs in a Python worker, so reaching here at
                # all means we are off the driver.
                "ran_on": "executor",
                "goldenmatch": _importable("goldenmatch"),
                "rapidfuzz": _importable("rapidfuzz"),
                "pandas": _importable("pandas"),
                "pyarrow": _importable("pyarrow"),
                "native_kernel": native,
                "executable": os.environ.get("PYSPARK_PYTHON", "?"),
            }
        )

    import json

    row = spark.range(1).selectExpr("cast(id as string) as s").select(_probe("s")).collect()[0]
    return json.loads(row[0])
```

- [ ] **Step 4: Run in CI, watch it pass** (after Task 3 wires the lane)

- [ ] **Step 5: Commit**

---

## Task 2: Fixture ships the env when told to

**Files:**
- Modify: `packages/python/goldenmatch/tests/conftest.py`

- [ ] **Step 1: Extend the `spark` fixture**

After the session is built on the real-Spark branch, honour a second env var:

```python
    archive = os.environ.get("GOLDENMATCH_SPARK_PYENV")
    if archive:
        from goldenmatch.sail.deps import ship_python_environment

        ship_python_environment(sess, archive)
```

Unset (the pysail default) changes nothing, so the `sail` lane is untouched.

- [ ] **Step 2: Verify the `sail` lane still passes.** It is the control; if it moves, stop.

---

## Task 3: Pack the venv in CI and point the lane at it

**Files:**
- Modify: `.github/workflows/ci.yml` (the `spark_connect` job)
- Modify: `packages/python/goldenmatch/pyproject.toml`

- [ ] **Step 1: Add `venv-pack` to the sail extra**

- [ ] **Step 2: Pack, then run**

```yaml
      - name: Pack the client venv for the executors
        run: |
          uv pip install venv-pack
          .venv/bin/python -m venv_pack -o gm_env.tar.gz --force
          ls -lh gm_env.tar.gz
      - name: Sail tests against Apache Spark Connect
        env:
          GOLDENMATCH_SPARK_REMOTE: "local[*]"
          GOLDENMATCH_SPARK_PYENV: "gm_env.tar.gz"
```

CI is Linux, so the packed venv matches the executor platform by construction —
the trap named in `ship_python_environment`'s docstring is structurally avoided
here, and must be called out in the user docs where it is not.

- [ ] **Step 3: Read the lane.** Exit is the P0 20 failures going green.

---

## Task 4: Record the result

- [ ] Update the spec's §2a with the P1 outcome and the `native_kernel` value from
      the probe. If `native_kernel` is `false` while `goldenmatch` is `true`, that
      is expected and is **P3's** problem (`sail_scoring` is `_FALLBACK_ONLY`), not
      a P1 failure — say so explicitly so it is not misread as a regression.

---

## Definition of done

- [ ] `executor_probe` reports `goldenmatch: true` **on the executor**
- [ ] The 20 P0 failures pass
- [ ] The `sail` (pysail) control lane is unchanged
- [ ] `spark_connect` is green — and can now be proposed for `ci-required` at P2
- [ ] The platform trap (client-packed venv ≠ executor platform) is documented

## Notes

- **Do not run locally.** pyspark + JVM OOMs the box.
- **CONFIRMED (run 31509239089):** `venv-pack` cannot pack a `uv`-created venv.
  It fails with `VenvPackError: Current environment is not a virtual environment`
  before any test runs. The lane now builds a **purpose-built stdlib venv**
  (`python -m venv`), installs only `goldenmatch` into it, and packs that via
  `venv-pack -p`. That is the better shape anyway: what a user ships is
  goldenmatch plus its RUNTIME deps, not a dev environment carrying pytest and
  the whole workspace. **This changes the user-facing instructions** — the docs
  must not tell people to pack their working venv if they use uv.
- Do **not** "fix" the lane by setting `spark.sql.execution.pyspark.python` to the
  client's `.venv` interpreter directly. It would go green in `local[*]` and prove
  nothing, because a real cluster has no such path. The archive is the point.
