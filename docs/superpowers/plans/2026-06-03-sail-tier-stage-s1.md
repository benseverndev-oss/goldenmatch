# Sail Tier — Stage S1: harness + scorer UDF + score/dedup (Implementation Plan)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up `goldenmatch.sail` on Sail (Spark Connect): connect to a local Sail server, score a block self-join with a rapidfuzz Arrow/pandas UDF, dedup with a distributed GROUP BY, and prove the emitted pair SET matches a python-rapidfuzz reference on a fixture — the first Sail-native gate.

**Architecture:** A new optional `goldenmatch[sail]` extra (`pysail` + `pyspark[connect]`). A pytest fixture starts an in-process Sail Spark Connect server (`pysail.spark.SparkConnectServer`) and hands tests a `SparkSession`. `sail/scoring.py` builds the block self-join, applies a rapidfuzz `pandas_udf` scorer, threshold-filters, and dedups via `groupBy(a,b).max(score)`. Parity is gated against a python-rapidfuzz brute-force comparand (NOT the datafusion one-box spine — that keeps the Sail CI lane free of datafusion/native; both sides use rapidfuzz, so parity is exact). A new path-filtered `sail` CI job runs it.

**Tech Stack:** Python 3.12, `pysail>=0.6`, `pyspark[connect]`, rapidfuzz, polars, pytest. Sail = Spark Connect (PySpark DataFrame/SQL), NOT datafusion-python.

---

## Critical context for the executor (read before starting)

- **This box HANGS on `import goldenmatch`/`polars`, and Sail/pyspark are NOT installed locally.** Do NOT run anything locally. Validate Python with `ruff check` + `python -m py_compile` ONLY. **The new `sail` CI lane is the only verifier.** Every "run test" step below means: push, read the `sail` lane result.
- **Sail is brand-new to this repo's CI.** The dep stack (pysail + pyspark[connect] + Spark Connect handshake + pandas_udf on Sail) is UNPROVEN here. **Task 1 is a pure connectivity de-risk** — prove the harness runs a trivial query before building the pipeline on it (mirrors how the spine de-risked the FFI boundary at Stage A). If Task 1's CI run fails on dep/version compat, pin `pyspark` to the version `pysail` requires (`pip show pysail` / pysail PyPI metadata) before proceeding.
- **Branch off `origin/main`** (the Sail spec is merged there). Branch `feat/sail-tier-s1`.
- **`ruff check packages/python/goldenmatch` must exit 0** before EVERY commit (I001). Never pipe through `tail`.
- **GitHub auth:** `GH_TOKEN=$(gh auth token --user benzsevern)`. NEVER `benzsevern-mjh`. Repo `benseverndev-oss/goldenmatch`.
- pyright slice does NOT cover `goldenmatch/sail/` or `tests/` — diagnostics there don't gate CI.
- Scope: S1 uses the **pure-Python rapidfuzz** scorer UDF (the spec's decision-2 floor + parity reference). The native-kernel Arrow UDF is a later perf task — NOT in S1. Keep the fixture small (≤ a few hundred rows); S1 proves correctness + the harness, not scale.

## Grounding references
- Spec: `docs/superpowers/specs/2026-06-03-sail-tier-design.md` (S1 = "Sail harness + score/dedup").
- The one-box parity shape to mirror: `tests/test_datafusion_spine_parity.py::_fixture_df` (5-dense / 2-member / 3-chain / 2-singleton on `last_name`, block on `zip`) + `_inmemory_comparand` (python `rapidfuzz` `JaroWinkler.normalized_similarity` brute-force within block, canonical `(min,max)` pairs, MAX dedup). S1 re-implements that comparand inline (self-contained; do NOT import the datafusion test module).
- CI job template: `.github/workflows/ci.yml::distributed` (lines ~838-890) — an optional-extra goldenmatch lane: `uv sync` → `uv pip install <extra>` → `.venv/bin/python -m pytest` (NOT `uv run` — server-subprocess/venv mismatch risk, same lesson as ray).
- pysail API (verified): `from pysail.spark import SparkConnectServer; s=SparkConnectServer(); s.start(); _,port=s.listening_address; SparkSession.builder.remote(f"sc://localhost:{port}").getOrCreate()`. `start()` is background by default; `s.stop()` to tear down.

## File Structure
- **Create** `packages/python/goldenmatch/goldenmatch/sail/__init__.py`
- **Create** `packages/python/goldenmatch/goldenmatch/sail/session.py` — connect helper.
- **Create** `packages/python/goldenmatch/goldenmatch/sail/scorers.py` — rapidfuzz `pandas_udf` by scorer name.
- **Create** `packages/python/goldenmatch/goldenmatch/sail/scoring.py` — block self-join → score → dedup.
- **Create** `packages/python/goldenmatch/tests/test_sail_connectivity.py` — Task 1 de-risk.
- **Create** `packages/python/goldenmatch/tests/test_sail_score_parity.py` — Task 4 gate (+ the shared server fixture).
- **Modify** `packages/python/goldenmatch/pyproject.toml` — add the `[sail]` extra.
- **Modify** `.github/workflows/ci.yml` — `sail` path filter + job + `ci-required` wiring.

---

## Task 0: Branch + the `[sail]` optional extra

**Files:** `pyproject.toml`

- [ ] **Step 1:** `git fetch origin && git checkout -b feat/sail-tier-s1 origin/main`.

- [ ] **Step 2:** In `packages/python/goldenmatch/pyproject.toml`, under `[project.optional-dependencies]`, add:

```toml
sail = [
  "pysail>=0.6",
  "pyspark[connect]>=3.5",
]
```

  (If CI Task 1 reveals a version mismatch, pin `pyspark` to the version `pysail` declares — pysail bundles/targets a specific Spark Connect protocol version. The connectivity test is where this is nailed down.)

- [ ] **Step 3:** Commit.

```bash
git add packages/python/goldenmatch/pyproject.toml
git commit -m "build(sail): add goldenmatch[sail] optional extra (pysail + pyspark connect)"
```

---

## Task 1: Sail connectivity de-risk (prove the harness before building on it)

**Files:**
- Create: `goldenmatch/sail/__init__.py` (empty / docstring), `goldenmatch/sail/session.py`
- Test: `tests/test_sail_connectivity.py`
- Modify: `.github/workflows/ci.yml` (the `sail` job — so this can RUN)

- [ ] **Step 1: Write `sail/session.py`** — the connect helper used everywhere.

```python
"""Sail (Spark Connect) session helpers. Sail is programmed via PySpark /
Spark Connect, NOT the datafusion Python API -- this is a re-expression of
the one-box spine's algorithm, not a port."""
from __future__ import annotations

import os
from typing import Any


def connect(remote: str | None = None) -> Any:
    """Return a SparkSession connected to a Sail server.

    ``remote`` (or ``SAIL_REMOTE``) is an ``sc://host:port`` URL. Raises if
    neither is set -- S1 has no implicit cluster bootstrap (BYO)."""
    from pyspark.sql import SparkSession

    url = remote or os.environ.get("SAIL_REMOTE")
    if not url:
        raise RuntimeError(
            "No Sail remote: pass remote='sc://host:port' or set SAIL_REMOTE."
        )
    return SparkSession.builder.remote(url).getOrCreate()
```

- [ ] **Step 2: Write the connectivity test** `tests/test_sail_connectivity.py` — starts an in-process Sail server and runs a trivial query. This is the infra gate.

```python
"""S1 de-risk: prove the Sail (Spark Connect) harness runs in CI before any
pipeline is built on it. Skips where the sail extra is absent."""
from __future__ import annotations

import pytest

pytest.importorskip("pysail")
pytest.importorskip("pyspark")


def test_sail_local_server_runs_trivial_query():
    from pyspark.sql import SparkSession
    from pysail.spark import SparkConnectServer

    server = SparkConnectServer()
    server.start()  # background
    try:
        _, port = server.listening_address
        spark = SparkSession.builder.remote(f"sc://localhost:{port}").getOrCreate()
        rows = spark.sql("SELECT 1 + 1 AS two").collect()
        assert rows[0]["two"] == 2
        spark.stop()
    finally:
        server.stop()
```

- [ ] **Step 3: Add the `sail` CI lane** so Task 1 can run. In `.github/workflows/ci.yml`:
  - **In the `changes` job's `outputs:` map (lines ~20-36), add** (REQUIRED — without this, `needs.changes.outputs.sail` resolves to empty and the lane only ever runs via the `ci_workflow` fallback, silently skipping the gate on a sail-only change in later S-stages; every other area here has this line):
    ```yaml
          sail: ${{ steps.filter.outputs.sail }}
    ```
  - In the `changes` job's `filters:` block (mirror the `distributed` filter), add:
    ```yaml
            sail:
              - 'packages/python/goldenmatch/goldenmatch/sail/**'
              - 'packages/python/goldenmatch/tests/test_sail_*.py'
    ```
  - Add a `sail` job (mirror `distributed`, lines ~838-890):
    ```yaml
      sail:
        needs: changes
        if: needs.changes.outputs.sail == 'true' || needs.changes.outputs.ci_workflow == 'true'
        runs-on: ubuntu-latest
        timeout-minutes: 20
        steps:
          - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5  # v4
          - uses: astral-sh/setup-uv@caf0cab7a618c569241d31dcd442f54681755d39  # v3
            with:
              enable-cache: true
              cache-dependency-glob: |
                uv.lock
                **/pyproject.toml
          - run: uv sync --all-packages
          # Sail (Spark Connect) optional stack. NO Java: pyspark[connect] is a
          # pure-gRPC client (no py4j/JVM launch) and the Sail server is Rust.
          # Install VIA THE EXTRA so the pyproject [sail] entry is exercised.
          - name: Install sail extra
            run: uv pip install -e 'packages/python/goldenmatch[sail]'
          # Run with the venv python directly (NOT `uv run`): the Sail server
          # spawns in-process; mirror the ray lane's venv-python discipline.
          - name: Sail connectivity gate (blocking)
            run: |
              .venv/bin/python -c "import pysail, pyspark; print('pyspark', pyspark.__version__)"
              .venv/bin/python -m pytest packages/python/goldenmatch/tests/test_sail_connectivity.py -v --timeout=300
    ```
  - Register `sail` in the `ci-required` job's `needs:` list (find the `ci-required` aggregator job and add `sail` so the gate waits on it). If `ci-required` uses a result-checking step, add `sail` there too.
  - **Note:** a change to `ci.yml` forces all jobs (incl. `sail`) to run via the `ci_workflow` filter — expected (this is also why the missing `outputs:` line would stay hidden in S1; fix it now).

- [ ] **Step 4: Static-validate + push the connectivity slice EARLY.** `ruff check` + `py_compile` the new py files; `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"` to confirm the YAML parses. Commit:

```bash
git add packages/python/goldenmatch/goldenmatch/sail/__init__.py \
        packages/python/goldenmatch/goldenmatch/sail/session.py \
        packages/python/goldenmatch/tests/test_sail_connectivity.py \
        .github/workflows/ci.yml
git commit -m "feat(sail): session connect helper + Spark Connect connectivity gate + CI lane"
```

- [ ] **Step 5: Push, open the PR, and CONFIRM the `sail` lane goes green on connectivity** before building further. This isolates the novel-infra risk. **If it fails on the Spark Connect handshake (a gRPC/protobuf version error won't name the fix):** Sail v0.6.x targets a SPECIFIC Spark Connect protocol version, so read pysail's declared `pyspark` constraint FIRST — `uv pip show pysail` (Requires) or the PyPI metadata — and pin `pyspark` in the `[sail]` extra to that compatible version, rather than guessing. Then re-push. Do NOT proceed to Task 2 until the connectivity gate is green.

---

## Task 2: rapidfuzz scorer as a pandas UDF

**Files:**
- Create: `goldenmatch/sail/scorers.py`
- Test: add `test_sail_scorer_udf_matches_rapidfuzz` to `tests/test_sail_score_parity.py` (create the file + the shared server fixture here).

- [ ] **Step 1: Write the shared server fixture + scorer.** First `sail/scorers.py`:

```python
"""Scorers as Spark pandas UDFs (S1: pure-Python rapidfuzz -- the floor +
parity reference; the native-kernel Arrow UDF is a later perf task)."""
from __future__ import annotations

from typing import Any

# SQL-callable names (match the matchkey scorer names the spine supports).
_SUPPORTED = ("jaro_winkler", "levenshtein", "token_sort")


def make_scorer_udf(scorer_name: str) -> Any:
    """Return a Spark ``pandas_udf`` (double) scoring two string columns in
    [0,1] via rapidfuzz -- the same library the one-box spine's FFI scorer
    delegates to (rapidfuzz==rust-rapidfuzz at 1e-9), so this is the exact
    parity reference."""
    if scorer_name not in _SUPPORTED:
        raise NotImplementedError(
            f"Sail S1 supports scorers {_SUPPORTED}; got {scorer_name!r}."
        )
    from pyspark.sql.functions import pandas_udf

    @pandas_udf("double")
    def _udf(a, b):  # a, b: pandas Series[str]
        import pandas as pd
        from rapidfuzz.distance import JaroWinkler, Levenshtein

        def score(x: str, y: str) -> float:
            x = x or ""
            y = y or ""
            if scorer_name == "jaro_winkler":
                return JaroWinkler.normalized_similarity(x, y)
            if scorer_name == "levenshtein":
                return Levenshtein.normalized_similarity(x, y)
            # token_sort: rapidfuzz fuzz.token_sort_ratio / 100
            from rapidfuzz import fuzz
            return fuzz.token_sort_ratio(x, y) / 100.0

        return pd.Series([score(x, y) for x, y in zip(a, b)])

    return _udf
```

- [ ] **Step 2: Write the fixture + scorer test** in `tests/test_sail_score_parity.py`:

```python
"""S1 gate: the Sail score/dedup pipeline's emitted pair SET matches a
python-rapidfuzz reference. Self-contained (no datafusion). Skips where the
sail extra is absent; runs in the `sail` CI lane."""
from __future__ import annotations

import pytest

pytest.importorskip("pysail")
pytest.importorskip("pyspark")
pytest.importorskip("polars")


@pytest.fixture(scope="module")
def spark():
    from pyspark.sql import SparkSession
    from pysail.spark import SparkConnectServer

    server = SparkConnectServer()
    server.start()
    _, port = server.listening_address
    sess = SparkSession.builder.remote(f"sc://localhost:{port}").getOrCreate()
    yield sess
    sess.stop()
    server.stop()


def _fixture_rows():
    """Mirror the one-box parity fixture: dense / 2-member / 3-chain /
    singletons on last_name, block on zip. Returns list of (row_id, last, zip)."""
    last = (["Aaaa"] * 5 + ["Brown", "Brown"]
            + ["Carter", "Carter", "Carter"] + ["Dixon", "Ellis"])
    zips = (["10001"] * 5 + ["20002"] * 2 + ["30003"] * 3 + ["40004", "50005"])
    return [(i, last[i], zips[i]) for i in range(len(last))]


def test_sail_scorer_udf_matches_rapidfuzz(spark):
    from goldenmatch.sail.scorers import make_scorer_udf
    from rapidfuzz.distance import JaroWinkler

    df = spark.createDataFrame([("Aaaa", "Aaaa"), ("Brown", "Browne")], ["a", "b"])
    udf = make_scorer_udf("jaro_winkler")
    got = {(r["a"], r["b"]): r["s"] for r in df.select("a", "b", udf("a", "b").alias("s")).collect()}
    for (a, b), s in got.items():
        assert abs(s - JaroWinkler.normalized_similarity(a, b)) < 1e-9
```

- [ ] **Step 3: Static-validate + commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/sail/scorers.py \
        packages/python/goldenmatch/tests/test_sail_score_parity.py
git commit -m "feat(sail): rapidfuzz scorer pandas UDF + parity-fixture scaffold"
```

---

## Task 3: block self-join → score → dedup

**Files:**
- Create: `goldenmatch/sail/scoring.py`

- [ ] **Step 1: Write `sail/scoring.py`** — the relational score+dedup, the S1 core.

```python
"""S1 score+dedup on Sail (Spark Connect): a block self-join scored by a
rapidfuzz pandas UDF, threshold-filtered, then deduped via GROUP BY max.
Returns the RAW above-threshold canonical (a<b) pair set."""
from __future__ import annotations

from typing import Any


def score_and_dedup(
    df: Any,
    *,
    block_col: str,
    value_col: str,
    id_col: str,
    scorer_name: str,
    threshold: float,
):
    """``df`` is a Spark DataFrame with ``id_col`` (int), ``value_col`` (the
    scored field) and ``block_col`` (the blocking key). Returns a Spark
    DataFrame of ``(a, b, score)`` with ``a < b``, score >= threshold,
    deduped to max(score) per pair. The self-join + UDF + GROUP BY are all
    Spark ops Sail distributes."""
    from pyspark.sql import functions as F

    from goldenmatch.sail.scorers import make_scorer_udf

    udf = make_scorer_udf(scorer_name)
    a = df.alias("a")
    b = df.alias("b")
    pairs = (
        a.join(
            b,
            (F.col(f"a.{block_col}") == F.col(f"b.{block_col}"))
            & (F.col(f"a.{id_col}") < F.col(f"b.{id_col}")),
        )
        .select(
            F.col(f"a.{id_col}").alias("a"),
            F.col(f"b.{id_col}").alias("b"),
            udf(F.col(f"a.{value_col}"), F.col(f"b.{value_col}")).alias("score"),
        )
        .where(F.col("score") >= F.lit(threshold))
    )
    # Dedup: max(score) per canonical (a,b) -- the scale-mode MAX contract.
    return pairs.groupBy("a", "b").agg(F.max("score").alias("score"))
```

- [ ] **Step 2: Static-validate + commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/sail/scoring.py
git commit -m "feat(sail): block self-join score + GROUP BY max dedup (score_and_dedup)"
```

---

## Task 4: the S1 parity gate

**Files:**
- Modify: `tests/test_sail_score_parity.py` (add the gate test).

- [ ] **Step 1: Add the parity gate** — Sail pipeline's pair SET == python-rapidfuzz brute-force reference.

```python
def _reference_pairs(rows, threshold):
    """python-rapidfuzz brute-force within block (mirror _inmemory_comparand):
    canonical (min,max) above-threshold pairs. The SAME rapidfuzz the Sail
    UDF uses -> exact set parity is the gate."""
    from collections import defaultdict

    from rapidfuzz.distance import JaroWinkler

    by_block = defaultdict(list)
    for rid, last, z in rows:
        by_block[z].append((rid, last))
    out = set()
    for members in by_block.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                (ida, va), (idb, vb) = members[i], members[j]
                if JaroWinkler.normalized_similarity(va, vb) >= threshold:
                    out.add((min(ida, idb), max(ida, idb)))
    return out


def test_sail_score_dedup_pair_set_parity(spark):
    from goldenmatch.sail.scoring import score_and_dedup

    rows = _fixture_rows()
    threshold = 0.85
    sdf = spark.createDataFrame(rows, ["__row_id__", "last_name", "zip"])
    out = score_and_dedup(
        sdf, block_col="zip", value_col="last_name", id_col="__row_id__",
        scorer_name="jaro_winkler", threshold=threshold,
    )
    sail_pairs = {(min(r["a"], r["b"]), max(r["a"], r["b"])) for r in out.collect()}
    assert sail_pairs == _reference_pairs(rows, threshold)
```

- [ ] **Step 2: Add the parity test to the `sail` CI lane.** In the `sail` job, extend the blocking step (or add one) to run the parity file:

```yaml
          - name: Sail score/dedup parity gate (blocking)
            run: |
              .venv/bin/python -m pytest packages/python/goldenmatch/tests/test_sail_score_parity.py -v --timeout=300
```

- [ ] **Step 3: Static-validate + commit.**

```bash
git add packages/python/goldenmatch/tests/test_sail_score_parity.py .github/workflows/ci.yml
git commit -m "test(sail): S1 score/dedup pair-set parity gate vs rapidfuzz reference"
```

---

## Task 5: push, green the `sail` lane, merge

- [ ] **Step 1: Push** (PR already opened in Task 1; otherwise open it). Body: "Sail tier Stage S1 — harness + rapidfuzz scorer UDF + score/dedup, parity-gated vs a python-rapidfuzz reference on the spine fixture. First Sail-native gate. Spec: docs/superpowers/specs/2026-06-03-sail-tier-design.md (S1)."

- [ ] **Step 2: Watch the `sail` lane** (connectivity gate + parity gate). Poll `while gh pr checks <N> | grep -qE "\bpending\b|in_progress"; do sleep 30; done`. If behind main, `gh pr update-branch <N>`. Grep the raw log for the pytest summary (`passed`/`failed,`) — `continue-on-error` lies in per-step JSON.

- [ ] **Step 3: Merge** once the `sail` lane + `ci-required` are green: `gh pr merge <N> --squash --delete-branch`.

---

## Definition of done
- `goldenmatch[sail]` extra installs; the `sail` CI lane connects to a local Sail Spark Connect server and runs a trivial query (connectivity gate green).
- `score_and_dedup` on Sail produces a pair SET byte-identical to the python-rapidfuzz brute-force reference on the spine fixture (parity gate green).
- The `sail` lane is wired into `ci-required`. PR merged.

## Out of scope (later S-stages)
- WCC on Sail (S2 — the gate that follows). Golden + identity (S3). The binding 100M+ multi-node bench + Ray retirement (S4).
- The native-kernel Arrow UDF (a perf task; S1 uses pure-Python rapidfuzz).
- Multi-field weighted matchkeys, soundex/other blocking transforms (S1 blocks on a raw column to match the parity fixture).
