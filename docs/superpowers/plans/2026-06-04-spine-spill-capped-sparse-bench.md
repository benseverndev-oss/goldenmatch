# DataFusion Spine — capped sparse-block spill bench (Implementation Plan)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Stage E HONEST-NULL into a real measurement: under a fixed per-process cgroup memory cap, on a LARGE-but-SPARSE block, show that the in-memory `bucket` backend (which materializes an M×M `rapidfuzz.cdist` score matrix) OOMs while the spilling DataFusion spine (which STREAMS the self-join through a threshold filter) survives — the binding spill-survival proof the dense soundex workload precluded.

**Architecture:** Extend the existing `bench_datafusion_spine_spill.py` with (1) a sparse-block dataset (one block of N distinct, mutually-dissimilar surnames → most pairs below threshold), and (2) a per-variant **cgroup cap** via `sudo systemd-run --scope -p MemoryMax=.. -p MemorySwapMax=0` so a variant that exceeds the cap is **cgroup-OOM-killed (SIGKILL, rc 137) cleanly and in isolation** — the driver records it; the other variants + the runner survive (fixing the exit-143-kills-the-whole-job flaw). A new `workflow_dispatch` on **`ubuntu-latest`** (the cap bounds RSS → no billable large runner) runs it.

**Tech Stack:** Python 3.12, polars, numpy, `datafusion>=53`, the FFI UDF wheel, `goldenmatch._native` (bucket kernel), `systemd-run` (cgroup v2 on the GH runner), pytest.

---

## Critical context for the executor
- **Box HANGS on imports; the bench OOMs a laptop.** Validate Python with `ruff check` +
  `python -m py_compile` ONLY. The bench runs in CI (the new workflow). CI is the verifier.
- **Branch off `origin/main`**. Branch `feat/spine-spill-capped-bench`.
- `ruff check packages/python/goldenmatch` exit 0 before EVERY commit. Never pipe through `tail`.
- GitHub auth: `GH_TOKEN=$(gh auth token --user benzsevern)`. Push may hit a cosmetic
  `.git/config` permission error — re-run `git push`, verify `git ls-remote` HEAD == local.
- **`run_spine` requires `config.mode == "scale"`** (Stage D gate) — the sparse spine config MUST set it.
- The original `bench_datafusion_spine_spill.py` + `bench-datafusion-spine-spill.yml` stay as-is (the
  unbounded version). This plan ADDS a capped+sparse path + a NEW workflow.

## Grounding references
- `scripts/bench_datafusion_spine_spill.py` — the existing driver: `_worker(variant, data_path, pool_mb)`,
  `_run_variant_subprocess`, `_bench_scale`, `_spine_config`/`_bucket_config` (soundex-on-last_name),
  `VARIANTS=("bucket","spine_nospill","spine_spill")`, the `_markdown` verdict. Extend it.
- `.github/workflows/bench-datafusion-spine-spill.yml` — the existing workflow to mirror (FFI wheel +
  datafusion + native build steps), but target `ubuntu-latest` + add the cap smoke.
- Stage E verdict (`docs/superpowers/specs/2026-06-01-arrow-native-finish-line-design.md`) named these
  two follow-ups: cgroup-capped relational-only bench + large-but-sparse-block workload. This is both.
- Why it can bind: `bucket` uses `rapidfuzz.process.cdist` → an M×M matrix in RAM (CLAUDE.md:
  "Fuzzy matching uses rapidfuzz.process.cdist for vectorized NxN scoring"). The spine's
  `_score_and_dedup` self-join streams + `collect()`s only the FILTERED (small) above-threshold set.

## Parameters (defaults; tunable via workflow inputs)
| Knob | Default | Rationale |
|---|---|---|
| `--sparse-rows` N | 30000 | bucket float32 matrix = 30000² × 4B ≈ 3.6 GB |
| `--mem-cap-mb` | 2560 | 3.6 GB matrix > 2.5 GB → bucket OOMs; spine streamed set < 2.5 GB → survives |
| `--pool-mb` (spine_spill) | 1024 | fair-spill pool < cap → score+dedup spill |
| runner | `ubuntu-latest` | cap bounds RSS; ~450M Rust UDF evals (FFI) ≈ tens of sec |

---

## Task 1: sparse-block dataset + config

**Files:** Modify `packages/python/goldenmatch/scripts/bench_datafusion_spine_spill.py`

- [ ] **Step 1: Add the sparse dataset + sparse config builders.** A block of N distinct random
  8-char lowercase surnames (pairwise jaro_winkler well below 0.85 → sparse) all forced into ONE
  block via a constant `block` column.

```python
def _sparse_block_df(rows: int, seed: int) -> "pl.DataFrame":
    """One large block of distinct, mutually-DISSIMILAR surnames (constant
    block key). bucket materializes the rows x rows cdist matrix (OOM under a
    tight cap); the spine streams the self-join + threshold-filter (few pairs
    pass -> survives). first_name/email carried so golden has fields."""
    import numpy as np
    import polars as pl

    rng = np.random.default_rng(seed)
    alphabet = np.array(list("abcdefghijklmnopqrstuvwxyz"))
    last = ["".join(r) for r in alphabet[rng.integers(0, 26, size=(rows, 8))]]
    first = ["".join(r) for r in alphabet[rng.integers(0, 26, size=(rows, 5))]]
    return pl.DataFrame({
        "last_name": last,
        "first_name": first,
        "email": [f"{i}@x.com" for i in range(rows)],
        "block": ["B0"] * rows,  # one forced block
    })
```

  Then add sparse config builders (block on the constant `block` column, NO transform; score
  `last_name` with jaro_winkler, threshold 0.85). Parameterize the existing `_spine_config` /
  `_bucket_config` OR add `_sparse_spine_config()` / `_sparse_bucket_config()`:

```python
def _sparse_spine_config():
    from goldenmatch.config.schemas import (
        BlockingConfig, BlockingKeyConfig, GoldenMatchConfig,
        MatchkeyConfig, MatchkeyField,
    )
    return GoldenMatchConfig(
        mode="scale",
        matchkeys=[MatchkeyConfig(
            name="last_name_fuzzy", type="weighted",
            fields=[MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0)],
            threshold=0.85,
        )],
        blocking=BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["block"])],
        ),
    )


def _sparse_bucket_config():
    cfg = _sparse_spine_config()
    cfg.mode = "standard"
    cfg.backend = "bucket"
    return cfg
```

  Wire a `--sparse` flag + a `--mem-cap-mb` arg into `main`'s argparse and thread them: in `--sparse`
  mode, `_bench_scale` generates via `_sparse_block_df` and the worker uses the sparse configs +
  `block_col="block"`. The `_worker` needs to know the mode (pass `--sparse` to the child too).

- [ ] **Step 2: Static-validate + commit.**

```bash
git add packages/python/goldenmatch/scripts/bench_datafusion_spine_spill.py
git commit -m "feat(bench): sparse-block workload for the spill bench (large block, dissimilar)"
```

---

## Task 2: the cgroup cap in the variant runner

**Files:** Modify `bench_datafusion_spine_spill.py` (`_run_variant_subprocess`)

- [ ] **Step 1: Wrap the child in a cgroup cap** when `mem_cap_mb > 0`. A cgroup-OOM SIGKILLs the
  child (rc 137) cleanly, isolated to its scope — the parent + sibling variants + runner survive.

```python
def _run_variant_subprocess(variant, data_path, pool_mb, *, mem_cap_mb=0, sparse=False):
    child = [sys.executable, str(Path(__file__).resolve()),
             "--worker", variant, "--data", data_path, "--pool-mb", str(pool_mb)]
    if sparse:
        child.append("--sparse")
    if mem_cap_mb:
        # cgroup v2 cap: a transient scope, swap off so it OOM-kills (not swaps).
        # --scope runs synchronously and propagates the child's exit/signal.
        cmd = ["sudo", "-n", "systemd-run", "--scope", "--quiet",
               "-p", f"MemoryMax={mem_cap_mb}M", "-p", "MemorySwapMax=0",
               "--"] + child
    else:
        cmd = child
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        oom = proc.returncode in (137, -9)  # SIGKILL == cgroup OOM-kill
        return {"variant": variant,
                "status": "OOM" if oom else "ERROR",
                "returncode": proc.returncode,
                "stderr_tail": proc.stderr[-2000:]}
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        return {"variant": variant, "status": "ERROR", "returncode": 0,
                "stderr_tail": "no JSON; stderr: " + proc.stderr[-1500:]}
    return json.loads(lines[-1])
```

  Thread `mem_cap_mb` + `sparse` from `_bench_scale` (which gets them from `main`'s args). The
  worker self-reports peak RSS via `getrusage` as today.

- [ ] **Step 2: Update the verdict** in `_markdown` for the capped sparse run: BINDING iff under the
  cap `bucket` is OOM AND `spine_spill` is ok (the spilling spine survives where in-memory OOMs).
  (Keep the existing honest-null branch for the uncapped path.)

- [ ] **Step 3: Static-validate + commit.**

```bash
git add packages/python/goldenmatch/scripts/bench_datafusion_spine_spill.py
git commit -m "feat(bench): per-variant cgroup MemoryMax cap (clean isolated OOM, not exit-143)"
```

---

## Task 3: the capped-bench workflow (ubuntu-latest)

**Files:** Create `.github/workflows/bench-spine-spill-capped.yml`

- [ ] **Step 1: Write the workflow.** Mirror `bench-datafusion-spine-spill.yml`'s build steps (FFI
  wheel + datafusion + native) but on `ubuntu-latest`, add a **cgroup-cap smoke** (de-risk the
  load-bearing harness mechanism before the real run), then run the sparse+capped bench.

```yaml
name: bench-spine-spill-capped
on:
  workflow_dispatch:
    inputs:
      sparse_rows: { description: "rows in the one sparse block", default: "30000" }
      mem_cap_mb:  { description: "per-variant cgroup MemoryMax (MB)", default: "2560" }
      pool_mb:     { description: "spine_spill fair-spill pool (MB)", default: "1024" }
permissions:
  contents: read
jobs:
  bench:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5  # v4
      - uses: dtolnay/rust-toolchain@29eef336d9b2848a0b548edc03f92a220660cdb8  # stable
      - uses: astral-sh/setup-uv@caf0cab7a618c569241d31dcd442f54681755d39  # v3
      - run: uv sync --all-packages
      - name: Build + install the FFI UDF wheel + datafusion runtime
        run: |
          uv pip install 'datafusion>=53,<54'
          uv run --with maturin maturin build --release \
            --manifest-path packages/rust/extensions/datafusion-udf/Cargo.toml --out dist-dfudf
          uv pip install dist-dfudf/*.whl
      - name: Build native extension (bucket kernel)
        run: uv run python scripts/build_native.py
      # DE-RISK: confirm the cgroup cap actually OOM-kills (the harness premise).
      - name: cgroup cap smoke (must OOM-kill -> rc 137)
        run: |
          set +e
          sudo -n systemd-run --scope --quiet -p MemoryMax=128M -p MemorySwapMax=0 -- \
            python3 -c "x=bytearray(512*1024*1024)"
          rc=$?
          echo "cap smoke rc=$rc (expect 137)"
          [ "$rc" = "137" ] || { echo '::error::cgroup cap did not OOM-kill; harness premise broken'; exit 1; }
      - name: Run capped sparse spill bench
        run: |
          set -o pipefail
          mkdir -p .profile_tmp
          uv run python packages/python/goldenmatch/scripts/bench_datafusion_spine_spill.py \
            --sparse --sparse-rows "${{ inputs.sparse_rows }}" \
            --mem-cap-mb "${{ inputs.mem_cap_mb }}" --pool-mb "${{ inputs.pool_mb }}" \
            --out .profile_tmp/spine_spill_capped.json | tee /tmp/capped.txt
          { echo '## bench-spine-spill-capped'; echo '```'; cat /tmp/capped.txt; echo '```'; } >> "$GITHUB_STEP_SUMMARY"
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: spine-spill-capped
          path: .profile_tmp/spine_spill_capped.json
          if-no-files-found: warn
```

- [ ] **Step 2: Validate** (`yaml.safe_load`) **+ commit.**

```bash
git add .github/workflows/bench-spine-spill-capped.yml
git commit -m "ci(bench): capped sparse-block spill bench (ubuntu-latest, cgroup cap)"
```

---

## Task 4: push, merge the harness, dispatch the bench, record the verdict

- [ ] **Step 1: Push + open the PR + green the goldenmatch lane** (the driver is a script; the
  `python (goldenmatch)` lane doesn't run it, but ruff + the workflow-file change run). Merge once
  green (the new workflow must be on `main` to be dispatchable — the workflow-trigger-ordering gotcha).
- [ ] **Step 2: Dispatch** `gh workflow run bench-spine-spill-capped.yml --ref main` (defaults), watch it.
- [ ] **Step 3: Read the verdict.** BINDING if `bucket`=OOM (rc 137) and `spine_spill`=ok under the
  cap. **If inconclusive:** if bucket did NOT OOM, re-dispatch with larger `sparse_rows` or smaller
  `mem_cap_mb`; if spine_spill ALSO OOMed, that's a cleaner honest-null (the spine's self-join
  materializes rather than streams — record it, it's a real finding about the engine). The capped run
  is cheap → iterate.
- [ ] **Step 4: Record the verdict** in the roadmap (update the Stage E section) + the context network
  `architecture/datafusion-spine.md` / memory. Keep ASCII.

---

## Definition of done
- The cap smoke proves cgroup OOM-kill works on the runner (rc 137).
- The capped sparse bench runs on `ubuntu-latest` and records a clean 3-way result.
- A recorded verdict: BINDING (bucket OOMs, spine_spill survives under the same cap) or a cleaner
  honest-null (both OOM → the spine's join materializes). Either is a real measured outcome.

## Out of scope
- The full pipeline / golden / identity (this bench is score+dedup spill-survival).
- The unbounded large-new-64GB bench (stays as-is). Flipping the `mode` default (separate decision).
