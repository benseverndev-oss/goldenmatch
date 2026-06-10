# DataFusion Spine — Stage E: out-of-core spill bench (Implementation Plan)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether the DataFusion spine's relational stages SPILL and SURVIVE where the in-memory pipeline OOMs — the binding "out-of-core" proof — via a `workflow_dispatch` bench on `large-new-64GB`, and record the verdict (binding or honest-null) in the roadmap.

**Architecture:** A single-file driver `bench_datafusion_spine_spill.py` generates one shared person-shape dataset per scale, then runs three variants — `bucket` (in-memory production winner), `spine_nospill` (`run_spine`, no memory cap), `spine_spill` (`run_spine` with a low `fair_spill_pool` cap) — each in a SUBPROCESS so a Linux OOM-kill of one variant is recorded (non-zero exit) rather than crashing the bench. Each child self-reports peak RSS via `getrusage(RUSAGE_SELF).ru_maxrss`. The bench keeps scale below the ~50M-pair scipy/UF envelope (the UF break collects pairs to the driver — an in-memory island the spill pool does NOT cover; pushing past it would OOM at the UF boundary and falsely read as "spine doesn't survive"). A workflow on `large-new-64GB` builds the Stage-B FFI UDF wheel + installs `datafusion>=53`, runs the driver, uploads the JSON + posts a markdown table. The verdict (incl. the honest-null outcome where nothing OOMs at reachable scale) lands in the roadmap.

**Tech Stack:** Python 3.12, polars, `datafusion>=53,<54`, the `goldenmatch_datafusion_udf` FFI wheel, `goldenmatch._native` (bucket backend kernel), GitHub Actions `large-new-64GB` (16c/64GB).

---

## Critical context for the executor (read before starting)

- **This box HANGS on `import goldenmatch`/`polars`/`datafusion`, and the bench scale OOMs Ben's Windows machine.** Do NOT run the driver locally at any non-trivial scale. Validate Python with `ruff check` + `python -m py_compile` ONLY. The smoke test runs in CI; the real bench runs on `large-new-64GB`. CI is the only verifier.
- **Branch off `origin/main`** (Stage D #702 is merged: `mode` field + `_validate_scale_mode_supported` are on `origin/main`). This plan's branch is `feat/datafusion-spine-stage-e` (already created off `origin/main`).
- **GitHub auth:** `GH_TOKEN=$(gh auth token --user benzsevern)` for every push/PR/merge/`gh run`/`gh workflow run`. NEVER `benzsevern-mjh`. Repo `benseverndev-oss/goldenmatch`.
- **`ruff check packages/python/goldenmatch` must exit 0** before EVERY commit (I001 import order enforced). Never pipe through `tail`.
- **`run_spine` requires `config.mode == "scale"`** (Stage D gate) — the driver MUST set `mode="scale"` on the spine config or the gate raises `ValueError`.
- **The bench is billable** (`large-new-64GB`, up to ~85 min). Land + smoke-green the harness in the PR FIRST, then dispatch the bench from the branch via `gh workflow run --ref feat/datafusion-spine-stage-e`, THEN commit the verdict to the same PR, THEN merge.
- **Honest-null is a valid outcome** (spec Risk: "Spill may still not bind"): if nothing OOMs at reachable sub-50M-pair scale on 64GB, record that — the spine's value is then engine-portability/Sail, not one-box survival. Do NOT manufacture an OOM by pushing past 50M pairs (that OOMs the UF island, not the relational stages — a false negative).

## Grounding references (all on `origin/main`)

- `backends/datafusion_spine.py::run_spine(blocked_candidates, config, *, memory_limit=None, target_partitions=None)` → `(golden_df, assignments, raw_pairs)`. `memory_limit` (bytes) sizes the `RuntimeEnvBuilder().with_disk_manager_os().with_fair_spill_pool(memory_limit)` so score+dedup spill. `None` = no pool cap.
- `tests/test_datafusion_spine_parity.py::_prepared_blocks(df, config)` — the canonical block-build for the spine (add `__row_id__`, `precompute_matchkey_transforms`, `build_blocks` over static blocking, returns LazyFrame blocks). Mirror it.
- `tests/fixtures/realistic_person.py::realistic_person_df(n, seed=42)` — deterministic person-shape data with real fuzzy dupes; ~5K+ distinct surnames, soundex-distributed blocks, dupe count scales with N.
- `scripts/bench_datafusion_vs_bucket.py` — the existing datafusion bench: `_make_config(backend)` (single-field weighted jaro_winkler on `last_name`, soundex static blocking), the JSON/markdown/decision-gate structure to mirror.
- `.github/workflows/bench-pipeline-complete-path.yml` — the subprocess-per-variant + "catch the child's non-zero exit and record OOM" pattern + `large-new-64GB` + `np` input + step-summary + artifact upload.
- `.github/workflows/bench-datafusion-vs-bucket.yml` — `large-new-64GB`, `uv sync`, `scripts/build_native.py`, JSON artifact, markdown step summary.
- FFI UDF build recipe (from `ci.yml:269-280`):
  ```
  uv pip install 'datafusion>=53,<54'
  uv run --with maturin maturin build --release \
    --manifest-path packages/rust/extensions/datafusion-udf/Cargo.toml --out dist-dfudf
  uv pip install dist-dfudf/*.whl
  ```
- Bucket = current in-memory winner: 25M rows → 57.7 GB peak RSS on `large-new-64GB` (near the 64 GB ceiling); ~8.3M pairs at 25M rows (well under the 50M UF envelope). So the OOM-seeking zone is ~25–40M records.

---

## File Structure

- **Create** `packages/python/goldenmatch/scripts/bench_datafusion_spine_spill.py` — the driver (parent orchestrator + `--worker` child entrypoint).
- **Create** `packages/python/goldenmatch/tests/test_bench_spine_spill_smoke.py` — a tiny-scale smoke test that runs in the goldenmatch CI lane (validates the driver end-to-end before the billable dispatch).
- **Create** `.github/workflows/bench-datafusion-spine-spill.yml` — `workflow_dispatch` bench on `large-new-64GB`.
- **Modify** `docs/superpowers/specs/2026-06-01-arrow-native-finish-line-design.md` — record the Stage E spill verdict (numbers table + binding/honest-null call). Commit with `git add -f` (tracked doc).

---

## Task 1: The spill-bench driver

**Files:**
- Create: `packages/python/goldenmatch/scripts/bench_datafusion_spine_spill.py`

- [ ] **Step 1: Write the driver.** Single file; parent orchestrates, `--worker` runs one variant in a child process. Peak RSS via the child's own `getrusage(RUSAGE_SELF).ru_maxrss` (Linux KB); OOM detected by the parent as a non-zero/killed child exit.

```python
"""Stage E: out-of-core spill bench for the DataFusion spine.

Measures whether the spine's RELATIONAL stages (score self-join + dedup
GROUP BY, inside one DataFusion ctx with a fair-spill pool) SPILL and
SURVIVE where the in-memory pipeline OOMs. Three variants per scale:

    bucket         -- dedupe_df(df, backend="bucket"): the in-memory
                      production winner (holds within-block score state
                      in RAM); the OOM comparand.
    spine_nospill  -- run_spine(blocks, cfg, memory_limit=None): the
                      spine with NO pool cap.
    spine_spill    -- run_spine(blocks, cfg, memory_limit=POOL): the
                      spine with a low fair-spill pool so score+dedup
                      spill to disk.

Each variant runs in a SUBPROCESS (this script re-invoked with --worker)
so a Linux OOM-kill of one variant is recorded as a non-zero exit, not a
bench crash (mirrors bench-pipeline-complete-path.yml). The child
self-reports peak RSS via getrusage(RUSAGE_SELF).ru_maxrss.

SCOPE (honest): the spill-survival claim is for the relational stages
ONLY. The spine's UF break (build_cluster_frames) collects raw pairs to
the driver -- an in-memory island the spill pool does NOT cover. Keep
scale below the ~50M-pair scipy/UF envelope (person-shape data yields
~8.3M pairs at 25M rows, so 25-40M rows is the OOM-seeking-yet-safe
zone). Pushing past 50M pairs OOMs the UF island, not the relational
stages -- a FALSE negative. If nothing OOMs at reachable scale, that's a
valid HONEST-NULL result (the spine's value is then engine portability).

Usage (CI / large-new-64GB only -- OOMs a laptop):
    python scripts/bench_datafusion_spine_spill.py \
        --rows 5000000,25000000 --pool-mb 8192 --out result.json
    python scripts/bench_datafusion_spine_spill.py --smoke   # tiny, CI
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

import polars as pl

# realistic_person_df lives under tests/fixtures (same sys.path dance as
# bench_datafusion_vs_bucket.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from fixtures.realistic_person import realistic_person_df  # noqa: E402
from goldenmatch import dedupe_df  # noqa: E402
from goldenmatch.config.schemas import (  # noqa: E402
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)

VARIANTS = ("bucket", "spine_nospill", "spine_spill")


def _spine_config() -> GoldenMatchConfig:
    """Scale-mode spine config: single-field weighted jaro_winkler on
    last_name, soundex static blocking -- the supported spine surface
    (mode='scale' is REQUIRED by the Stage D gate)."""
    return GoldenMatchConfig(
        mode="scale",
        matchkeys=[
            MatchkeyConfig(
                name="last_name_fuzzy",
                type="weighted",
                fields=[MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0)],
                threshold=0.85,
            )
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["last_name"], transforms=["soundex"])],
        ),
    )


def _bucket_config() -> GoldenMatchConfig:
    """Same matchkey/blocking as the spine, backend=bucket (standard
    mode) -- the in-memory comparand on an identical workload."""
    cfg = _spine_config()
    cfg.mode = "standard"
    cfg.backend = "bucket"
    return cfg


def _build_blocks(df: pl.DataFrame, config: GoldenMatchConfig):
    """Mirror tests/test_datafusion_spine_parity.py::_prepared_blocks."""
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.core.matchkey import precompute_matchkey_transforms

    with_ids = df.with_row_index("__row_id__")
    augmented = precompute_matchkey_transforms(with_ids, config.get_matchkeys())
    return build_blocks(augmented.lazy(), config.blocking)


def _worker(variant: str, data_path: str, pool_mb: int) -> int:
    """Run ONE variant in this (child) process. Print a JSON result line
    to stdout and exit 0; on OOM the OS kills us and the parent records
    it. Peak RSS is self-reported via getrusage."""
    df = pl.read_parquet(data_path)
    t0 = time.perf_counter()

    if variant == "bucket":
        result = dedupe_df(df, config=_bucket_config())
        pairs = result.dupes.height if result.dupes is not None else 0
        clusters = len(result.clusters) if result.clusters else 0
    else:
        from goldenmatch.backends.datafusion_spine import run_spine

        cfg = _spine_config()
        blocks = _build_blocks(df, cfg)
        pool = None if variant == "spine_nospill" else pool_mb * 1024 * 1024
        _golden, assign, raw_pairs = run_spine(blocks, cfg, memory_limit=pool)
        pairs = len(raw_pairs)
        clusters = assign["cluster_id"].n_unique() if assign is not None and assign.height else 0

    wall = time.perf_counter() - t0
    # ru_maxrss: Linux = KB, macOS = bytes. CI is Linux -> KB -> MB.
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    print(json.dumps({
        "variant": variant, "wall_s": wall, "peak_rss_mb": peak_mb,
        "pairs": int(pairs), "clusters": int(clusters), "status": "ok",
    }))
    return 0


def _run_variant_subprocess(variant: str, data_path: str, pool_mb: int) -> dict:
    """Spawn a child for one variant; capture its JSON or record OOM."""
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()),
         "--worker", variant, "--data", data_path, "--pool-mb", str(pool_mb)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        # 137 = 128+9 (SIGKILL, the OOM-killer signature).
        oom = proc.returncode in (137, -9)
        return {
            "variant": variant,
            "status": "OOM" if oom else "ERROR",
            "returncode": proc.returncode,
            "stderr_tail": proc.stderr[-2000:],
        }
    # The child may emit warnings before the JSON line; take the last
    # non-empty stdout line as the result.
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        return {"variant": variant, "status": "ERROR", "returncode": 0,
                "stderr_tail": "no JSON emitted; stderr: " + proc.stderr[-1500:]}
    return json.loads(lines[-1])


def _bench_scale(rows: int, pool_mb: int, seed: int, tmp: Path) -> dict:
    data_path = tmp / f"spine_spill_{rows}.parquet"
    df = realistic_person_df(rows, seed=seed)
    df.write_parquet(data_path)
    del df  # free the parent's copy before spawning children
    print(f"\n=== rows={rows:,} pool={pool_mb}MB (data={data_path.name}) ===", flush=True)
    out: dict[str, dict] = {}
    for variant in VARIANTS:
        r = _run_variant_subprocess(variant, str(data_path), pool_mb)
        out[variant] = r
        if r.get("status") == "ok":
            print(f"  {variant}: wall={r['wall_s']:.1f}s peak_rss={r['peak_rss_mb']:.0f}MB "
                  f"pairs={r['pairs']} clusters={r['clusters']}", flush=True)
        else:
            print(f"  {variant}: {r['status']} (rc={r.get('returncode')})", flush=True)
    try:
        data_path.unlink()
    except OSError:
        pass
    return {"rows": rows, "pool_mb": pool_mb, "results": out}


def _markdown(scales: list[dict]) -> str:
    lines = ["## bench-datafusion-spine-spill", "",
             "| rows | variant | wall_s | peak_rss_MB | pairs | status |",
             "|---|---|---|---|---|---|"]
    for s in scales:
        for variant in VARIANTS:
            r = s["results"][variant]
            if r.get("status") == "ok":
                lines.append(f"| {s['rows']:,} | {variant} | {r['wall_s']:.1f} | "
                             f"{r['peak_rss_mb']:.0f} | {r['pairs']} | ok |")
            else:
                lines.append(f"| {s['rows']:,} | {variant} | - | - | - | "
                             f"**{r['status']}** (rc={r.get('returncode')}) |")
    # Verdict line: binding iff at the largest scale bucket OOMs/errors
    # AND spine_spill is ok.
    top = scales[-1]["results"] if scales else {}
    bucket_dead = top.get("bucket", {}).get("status") in ("OOM", "ERROR")
    spill_ok = top.get("spine_spill", {}).get("status") == "ok"
    verdict = ("BINDING: in-memory (bucket) OOM/errored while spine_spill survived"
               if bucket_dead and spill_ok else
               "HONEST-NULL: nothing OOMed at this scale -- spine value is "
               "engine-portability, not one-box survival (push scale or lower pool "
               "to seek the binding point, staying < 50M pairs)")
    lines += ["", f"**Verdict:** {verdict}"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", choices=VARIANTS, help="internal: run one variant")
    ap.add_argument("--data", help="internal: parquet path for --worker")
    ap.add_argument("--rows", default="5000000,25000000",
                    help="comma-separated row counts (OOM-seeking; keep < 50M pairs)")
    ap.add_argument("--pool-mb", type=int, default=8192,
                    help="fair-spill pool size (MB) for spine_spill")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--smoke", action="store_true",
                    help="tiny scale for CI validation (rows=2000, pool=128MB)")
    ap.add_argument("--out", default=None, help="JSON output path")
    args = ap.parse_args()

    if args.worker:
        return _worker(args.worker, args.data, args.pool_mb)

    rows_list = [2000] if args.smoke else [int(x) for x in args.rows.split(",")]
    pool_mb = 128 if args.smoke else args.pool_mb
    tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "spine-spill-bench"
    tmp.mkdir(parents=True, exist_ok=True)

    scales = [_bench_scale(n, pool_mb, args.seed, tmp) for n in rows_list]
    payload = {"scales": scales, "pool_mb": pool_mb, "smoke": args.smoke}
    md = _markdown(scales)
    print("\n" + md)
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Static-validate.** `ruff check packages/python/goldenmatch/scripts/bench_datafusion_spine_spill.py` (exit 0); `python -m py_compile` it.

- [ ] **Step 3: Commit.**

```bash
git add packages/python/goldenmatch/scripts/bench_datafusion_spine_spill.py
git commit -m "feat(spine): Stage E out-of-core spill bench driver"
```

---

## Task 2: CI smoke test (validate the driver before the billable dispatch)

**Files:**
- Create: `packages/python/goldenmatch/tests/test_bench_spine_spill_smoke.py`

- [ ] **Step 1: Write the smoke test.** It imports the driver's `_worker`/`_build_blocks` and runs the `spine_spill` variant in-process at a trivial scale, asserting it produces pairs+clusters. Runs in the `python (goldenmatch)` lane (datafusion + FFI wheel present). Self-contained.

```python
"""Stage E smoke: the spill-bench driver runs end-to-end at a trivial
scale (validates the driver before the billable large-new-64GB dispatch).
Runs in the goldenmatch CI lane (datafusion + FFI UDF wheel present)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("datafusion")
pytest.importorskip("goldenmatch_datafusion_udf")

_DRIVER = (
    Path(__file__).resolve().parents[1] / "scripts" / "bench_datafusion_spine_spill.py"
)


def _load_driver():
    spec = importlib.util.spec_from_file_location("spine_spill_bench", _DRIVER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["spine_spill_bench"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_spine_spill_driver_runs_tiny():
    drv = _load_driver()
    import polars as pl

    df = drv.realistic_person_df(400, seed=7)
    cfg = drv._spine_config()
    blocks = drv._build_blocks(df, cfg)

    from goldenmatch.backends.datafusion_spine import run_spine

    # Low pool to exercise the spill runtime construction path on tiny data.
    _golden, assign, raw_pairs = run_spine(blocks, cfg, memory_limit=128 * 1024 * 1024)
    assert isinstance(raw_pairs, list)
    assert assign is not None
    # NOTE: build_blocks drops soundex-singleton surnames (blocks with < 2
    # records), so `assign` covers only the post-height>=2 block universe,
    # NOT all 400 source rows. Assert the path produced a non-empty
    # assignment with at least one cluster -- the end-to-end "it ran" check.
    assert assign.height >= 1
    assert assign["cluster_id"].n_unique() >= 1


def test_spine_spill_config_is_scale_mode():
    # The Stage D gate requires mode='scale'; a regression here would make
    # every spine variant ValueError at bench time.
    drv = _load_driver()
    assert drv._spine_config().mode == "scale"
    assert drv._bucket_config().backend == "bucket"
```

- [ ] **Step 2: Static-validate** + **Step 3: Commit.**

```bash
git add packages/python/goldenmatch/tests/test_bench_spine_spill_smoke.py
git commit -m "test(spine): Stage E spill-bench driver smoke (tiny-scale, CI lane)"
```

---

## Task 3: The workflow_dispatch bench on large-new-64GB

**Files:**
- Create: `.github/workflows/bench-datafusion-spine-spill.yml`

- [ ] **Step 1: Write the workflow.** Mirror `bench-datafusion-vs-bucket.yml` (large-new-64GB, native build) + the FFI UDF build recipe + `bench-pipeline-complete-path.yml` inputs/summary.

```yaml
# Stage E: out-of-core spill bench for the DataFusion spine
# (workflow_dispatch only). Runs scripts/bench_datafusion_spine_spill.py
# on large-new-64GB (16c/64GB) -- the same box the bucket 25M numbers
# were measured on. Builds the Stage-B FFI UDF wheel + installs
# datafusion>=53 (the spine's scorers), and the native ext (bucket's
# kernel). Uploads the JSON artifact and posts the markdown verdict to
# the step summary.
#
# Scope (honest): the spill-survival claim is for the relational stages
# only; keep `rows` below the ~50M-pair UF/scipy envelope (person-shape
# ~8.3M pairs at 25M rows). A HONEST-NULL verdict (nothing OOMs) is a
# valid outcome -- do NOT push past 50M pairs to manufacture an OOM.
#
# Spec: docs/superpowers/specs/2026-06-03-datafusion-spine-design.md (Stage E)
# Roadmap: docs/superpowers/specs/2026-06-01-arrow-native-finish-line-design.md

name: bench-datafusion-spine-spill

on:
  workflow_dispatch:
    inputs:
      rows:
        description: "Comma-separated row counts (OOM-seeking; keep < 50M pairs)"
        default: "5000000,25000000"
      pool_mb:
        description: "fair-spill pool size (MB) for the spine_spill variant"
        default: "8192"

permissions:
  contents: read

jobs:
  bench:
    runs-on: large-new-64GB
    timeout-minutes: 85
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5  # v4
      - uses: dtolnay/rust-toolchain@29eef336d9b2848a0b548edc03f92a220660cdb8  # stable
      - uses: astral-sh/setup-uv@caf0cab7a618c569241d31dcd442f54681755d39  # v3
        with:
          enable-cache: true
          cache-dependency-glob: |
            uv.lock
            **/pyproject.toml
      - run: uv sync --all-packages

      - name: Build + install the FFI UDF wheel (spine scorers) + datafusion runtime
        run: |
          uv pip install 'datafusion>=53,<54'
          uv run --with maturin maturin build --release \
            --manifest-path packages/rust/extensions/datafusion-udf/Cargo.toml --out dist-dfudf
          uv pip install dist-dfudf/*.whl

      - name: Build native extension (bucket backend kernel)
        run: uv run python scripts/build_native.py

      - name: Verify spine + bucket deps importable
        run: |
          uv run python -c "import datafusion, goldenmatch_datafusion_udf; import goldenmatch._native as n; assert hasattr(n,'score_block_pairs_arrow'); print('deps ok')"

      - name: Run spill bench
        run: |
          set -o pipefail
          mkdir -p .profile_tmp
          uv run python packages/python/goldenmatch/scripts/bench_datafusion_spine_spill.py \
            --rows "${{ inputs.rows }}" --pool-mb "${{ inputs.pool_mb }}" \
            --out .profile_tmp/spine_spill.json | tee /tmp/spine_spill.txt
          { echo '## bench-datafusion-spine-spill (raw stdout)'; echo '```'; cat /tmp/spine_spill.txt; echo '```'; } >> "$GITHUB_STEP_SUMMARY"

      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: spine-spill-bench
          path: .profile_tmp/spine_spill.json
          if-no-files-found: warn
          retention-days: 30
```

- [ ] **Step 2:** A workflow-file change to `ci.yml` is NOT needed (this is a standalone `workflow_dispatch` file). Confirm the path-filter `changes` job in `ci.yml` does not REQUIRE every new workflow to be registered (new bench workflows are added without a filter entry — verify by checking an existing `bench-*.yml` has no `ci.yml` filter entry). If the repo gates on workflow presence, no action; bench workflows are dispatch-only and unfiltered.

- [ ] **Step 3: Commit.**

```bash
git add .github/workflows/bench-datafusion-spine-spill.yml
git commit -m "ci(spine): Stage E spill-bench workflow (workflow_dispatch, large-new-64GB)"
```

---

## Task 4: Push, smoke-green the harness PR

- [ ] **Step 1: Push + open PR.**

```bash
GH_TOKEN=$(gh auth token --user benzsevern) git push -u origin feat/datafusion-spine-stage-e
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create --base main \
  --title "feat(spine): Stage E out-of-core spill bench (driver + workflow + smoke)" \
  --body "Stage E harness: the spill-bench driver, a tiny-scale CI smoke, and the large-new-64GB workflow_dispatch bench. The binding spill verdict is dispatched from this branch and committed here once measured. Spec: docs/superpowers/specs/2026-06-03-datafusion-spine-design.md (Stage E)."
```

- [ ] **Step 2: Watch the `python (goldenmatch)` lane go green** (it runs the smoke test). Poll: `while gh pr checks <N> | grep -qE "\bpending\b|in_progress"; do sleep 30; done`. If behind main, `gh pr update-branch <N>`. Confirm the pytest summary includes the 2 smoke tests passing (grep the raw log).

---

## Task 5: Dispatch the bench, record the verdict, merge

- [ ] **Step 1: Dispatch the bench from the branch** (reads the workflow from the branch ref):

```bash
GH_TOKEN=$(gh auth token --user benzsevern) gh workflow run bench-datafusion-spine-spill.yml \
  --ref feat/datafusion-spine-stage-e -f rows=5000000,25000000 -f pool_mb=8192
```

- [ ] **Step 2: Wait for it** (`large-new-64GB` provisioning can take minutes; the run up to ~85 min). Find the run id (`gh run list --workflow=bench-datafusion-spine-spill.yml --branch feat/datafusion-spine-stage-e -L 1`), then `gh run watch <id> --exit-status` or poll. Download the artifact + read the step summary for the verdict table.

  **Interpretation:**
  - **BINDING** if at the top scale `bucket` is OOM/ERROR (rc 137) AND `spine_spill` is `ok` → the spine survived where in-memory died.
  - **HONEST-NULL** if nothing OOMed → record it plainly; consider one re-dispatch with a larger `rows` (staying < 50M pairs — person-shape stays ~1/3 row-count in pairs, so ≤ ~40M rows) and/or a lower `pool_mb`. Do NOT exceed the 50M-pair envelope.
  - If `spine_spill` OOMs while `spine_nospill` doesn't, or pairs ≳ 50M, the UF island (not the relational stages) is the OOM — note it; lower the scale.

- [ ] **Step 3: Record the verdict in the roadmap.** Add a Stage E section to `docs/superpowers/specs/2026-06-01-arrow-native-finish-line-design.md` (near the scale-mode sign-off): the per-variant numbers table (rows / wall / peak RSS / pairs / status), the binding-or-honest-null call, the run id, and the runner. Keep ASCII (no em-dashes).

```bash
git add -f docs/superpowers/specs/2026-06-01-arrow-native-finish-line-design.md
git commit -m "docs(spine): record Stage E out-of-core spill verdict (run <id>)"
GH_TOKEN=$(gh auth token --user benzsevern) git push
```

- [ ] **Step 4: Re-green + merge.** Poll PR CI; `gh pr update-branch <N>` if behind; merge once green:

```bash
GH_TOKEN=$(gh auth token --user benzsevern) gh pr merge <N> --squash --delete-branch
```

- [ ] **Step 5: Do NOT flip the `mode` default.** Stage E's verdict (binding or honest-null) is recorded; the default-flip decision is a separate, explicit follow-up the human owns. Surface the recommendation in the PR/summary, do not implement it here.

---

## Definition of done

- Driver `bench_datafusion_spine_spill.py` runs the 3-variant subprocess bench, records OOM as a non-zero child exit, self-reports peak RSS, emits JSON + markdown verdict.
- Smoke test green in the `python (goldenmatch)` lane.
- `bench-datafusion-spine-spill.yml` dispatched on `large-new-64GB`; the spill verdict (binding or honest-null) recorded in the roadmap with the numbers, run id, and runner.
- PR merged. `mode` default NOT flipped (separate human decision).

## Out of scope

- Flipping `mode` to `"scale"` by default.
- Sail / distributed UF (removing the in-memory pair-collection island) — that's the tier that would let Stage E exceed 50M pairs.
- Fixing the pre-existing empty-input `SchemaError` in the frames-out tail (flagged in Stage D).
