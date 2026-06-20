# Throughput Benchmark + CI Perf Gate Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Defend the #1083 throughput-tier claim with (a) a head-to-head docs/sec + MB/sec benchmark vs datatrove on a public corpus slice, and (b) a deterministic per-PR CI perf gate that fails on regression.

**Architecture:** A dedicated `scripts/bench_corpus_dedup/` harness mirroring the proven `scripts/bench_er_headtohead/` patterns (streaming corpus, one loud-failing runner per engine, subprocess-per-datapoint OOM-tolerant orchestrator, dispatch 64 GB lane) — purpose-built for whole-document near-dup. The engine-agnostic accuracy evaluator (`bench_er_headtohead/evaluate.py`) is **reused as-is** (its `{record_id, pred_cluster_id}` contract maps `record_id := doc_id`). A separate `throughput_perf_gate.py` runs the tier on a vendored offline corpus at fixed size+seed and gates on machine-independent cost (`scored_pair_count` from `bench_capture()`, derived `reduction_ratio`, measured recall on injected dups) vs a committed baseline JSON.

**Tech Stack:** Python 3.12, Polars, PyArrow, DuckDB (eval), `datatrove` (competitor, CI-installed not a repo dep), HuggingFace `datasets` (corpus streaming), GitHub Actions (`workflow_dispatch` headline + `dorny/paths-filter`-gated `ci.yml` job). Spec: `docs/superpowers/specs/2026-06-20-throughput-bench-ci-gate-design.md`.

---

## Sequencing precondition (read first)

This harness imports the throughput tier from **#1083 (PR #1129)**, which is **not on `main`** at plan time (open, BLOCKED, auto-merge armed). The work branch `feat/1086-throughput-bench` (worktree `.worktrees/1086-throughput-bench`) is cut from `origin/main`.

**Before Phase 1**, ensure the tier is importable in the worktree:
- If #1129 has merged to main: `git -C .worktrees/1086-throughput-bench fetch origin && git -C .worktrees/1086-throughput-bench rebase origin/main`.
- If not yet merged: rebase the work branch onto the tier branch — `git -C .worktrees/1086-throughput-bench rebase origin/feat/1083-throughput-plan` — and plan to re-rebase onto main once #1129 lands.
- **Do not open/merge the #1086 PR until #1129 is on main.**

Per `reference_py_worktree_test_native_skew`: run worktree Python via the main checkout's `.venv` with `PYTHONPATH` shadowing, and set `GOLDENMATCH_NATIVE=0` + `POLARS_SKIP_CPU_CHECK=1` for local controller/dedupe runs unless a fresh native build is done in the worktree. Local test invocations in this plan assume that shadow.

---

## File structure

```
scripts/bench_corpus_dedup/
  __init__.py
  corpora.py                 # load_corpus(name, n_docs, seed) -> Iterator[(doc_id, text)]
  inject_dups.py             # seeded near-dup injector -> corpus.parquet + truth.parquet
  run_goldenmatch.py         # one datapoint: throughput tier, fails loud if tier not engaged
  run_datatrove.py           # one datapoint: datatrove MinHash dedup pipeline
  orchestrate.py             # subprocess-per-(corpus,scale,engine), OOM-tolerant, summary.md
  throughput_perf_gate.py    # deterministic per-PR cost gate vs committed baseline
  perf_gate_baseline.json    # committed baseline (snapshot-test style)
  data/offline_corpus.jsonl  # vendored public-domain slice (gate fixture, network-free)
  README.md
  test_corpora.py            # offline determinism; HF adapters skip-if-offline
  test_inject_dups.py        # truth correctness + determinism
  test_perf_gate.py          # tolerance logic + --update-baseline round-trip
  test_smoke.py              # tiny end-to-end offline orchestrate -> summary
.github/workflows/bench-corpus-dedup.yml   # dispatch headline bench (64 GB)   [new]
.github/workflows/ci.yml                    # + throughput-gate job             [modified]
```

The evaluator is **not** recreated here — `orchestrate.py` invokes the existing
`scripts/bench_er_headtohead/evaluate.py` by path (same parquet contract).

---

## Phase 0: Scaffold + validate the tier's surfaces

### Task 0.1: Package scaffold

**Files:**
- Create: `scripts/bench_corpus_dedup/__init__.py` (empty)

- [ ] **Step 1: Create the package marker**

```bash
mkdir -p scripts/bench_corpus_dedup/data scripts/bench_corpus_dedup
: > scripts/bench_corpus_dedup/__init__.py
```

- [ ] **Step 2: Commit**

```bash
git add scripts/bench_corpus_dedup/__init__.py
git commit -m "chore(bench): scaffold bench_corpus_dedup package (#1086)"
```

### Task 0.2: Spike — confirm the throughput tier exposes what the gate needs

This validates the spec's load-bearing assumptions empirically **before** building on them:
that `dedupe_df(df, throughput=…)` runs under `bench_capture()`, engages
`verify_mode="sketch_distance"`, and that `bench_capture().to_dict()["metrics"]` carries
`scored_pair_count`. Findings feed the runner + gate.

**Files:**
- Test: `scripts/bench_corpus_dedup/test_spike_tier.py` (temporary — deleted at end of Task 0.2)

- [ ] **Step 1: Write the spike test**

```python
# scripts/bench_corpus_dedup/test_spike_tier.py
"""Throwaway spike: confirm the #1083 tier surfaces the gate's inputs."""
import os
os.environ.setdefault("GOLDENMATCH_NATIVE", "0")
os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
import polars as pl


def test_throughput_tier_surfaces_scored_pairs():
    from goldenmatch.core.bench import bench_capture
    try:
        from goldenmatch import dedupe_df
    except ImportError:
        from goldenmatch._api import dedupe_df

    # 6 docs, 3 near-dup pairs on a long text column.
    base = ["the quick brown fox jumps over the lazy dog " * 5,
            "a wholly different sentence about astronomy and stars " * 5,
            "machine learning models require large training corpora " * 5]
    rows = []
    for i, t in enumerate(base):
        rows.append({"doc_id": f"d{i}a", "text": t})
        rows.append({"doc_id": f"d{i}b", "text": t + " extra tail clause"})
    df = pl.DataFrame(rows)

    with bench_capture() as bench:
        ded = dedupe_df(df, throughput=0.95)

    metrics = bench.to_dict().get("metrics", {})
    print("METRICS KEYS:", sorted(metrics))
    print("scored_pair_count:", metrics.get("scored_pair_count"))
    print("throughput_posture:", getattr(ded, "throughput_posture", None))
    assert "scored_pair_count" in metrics, metrics
```

- [ ] **Step 2: Run the spike, capture the real key names**

Run (from repo root, main `.venv` shadow):
```bash
PYTHONPATH=packages/python/goldenmatch GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 \
  .venv/bin/python -m pytest scripts/bench_corpus_dedup/test_spike_tier.py -v -s
```
Expected: PASS, and the `-s` output prints the metrics keys + `scored_pair_count` value +
the posture object. **Record these in the Task 0.2 commit message** — later tasks reference
the exact key names. If `scored_pair_count` is absent, fall back to the spec's instrumentation
hook (env-gated counter in `run_goldenmatch.py`); note that deviation in the commit.

- [ ] **Step 3: Delete the spike, commit the finding**

```bash
git rm -f scripts/bench_corpus_dedup/test_spike_tier.py 2>/dev/null || rm -f scripts/bench_corpus_dedup/test_spike_tier.py
git commit --allow-empty -m "spike(bench): confirm #1083 tier surfaces scored_pair_count + sketch_distance (#1086)

bench_capture metrics keys: <PASTE>
throughput_posture shape: <PASTE>"
```

---

## Phase 1: Corpus adapters + vendored offline fixture

### Task 1.1: Vendor the offline public-domain corpus

A small, license-clean, network-free corpus slice = the per-PR gate fixture. Source:
Project Gutenberg public-domain texts (no copyright; attribution-free). Acquire ONCE
(author-side), commit the slice; CI never fetches it.

**Files:**
- Create: `scripts/bench_corpus_dedup/data/offline_corpus.jsonl` (committed)
- Create: `scripts/bench_corpus_dedup/data/README.md` (provenance + license note)

- [ ] **Step 1: Build the slice (one-time, author-side)**

Fetch 2–3 public-domain Gutenberg texts, split into ~2,000 paragraph-documents of
≥200 chars, write JSONL `{"doc_id": "...", "text": "..."}`. Keep it a few MB.

```bash
python - <<'PY'
import json, re, urllib.request
SOURCES = {  # public domain (Gutenberg plain-text mirrors)
    "pg1342": "https://www.gutenberg.org/files/1342/1342-0.txt",  # Pride and Prejudice
    "pg11":   "https://www.gutenberg.org/files/11/11-0.txt",       # Alice in Wonderland
    "pg84":   "https://www.gutenberg.org/files/84/84-0.txt",       # Frankenstein
}
out = open("scripts/bench_corpus_dedup/data/offline_corpus.jsonl", "w", encoding="utf-8")
n = 0
for key, url in SOURCES.items():
    txt = urllib.request.urlopen(url, timeout=60).read().decode("utf-8", "replace")
    for i, para in enumerate(re.split(r"\n\s*\n", txt)):
        para = " ".join(para.split())
        if len(para) >= 200:
            out.write(json.dumps({"doc_id": f"{key}-{i}", "text": para}) + "\n")
            n += 1
        if n >= 2000:
            break
    if n >= 2000:
        break
out.close()
print("wrote", n, "docs")
PY
```

- [ ] **Step 2: Write provenance README**

`data/README.md`: list the Gutenberg IDs/titles, state "public domain (Project Gutenberg),
no license restriction", and that the slice is the deterministic offline gate fixture.

- [ ] **Step 3: Commit**

```bash
git add scripts/bench_corpus_dedup/data/offline_corpus.jsonl scripts/bench_corpus_dedup/data/README.md
git commit -m "chore(bench): vendor public-domain offline corpus slice for the gate (#1086)"
```

### Task 1.2: `corpora.py` — offline adapter (TDD)

**Files:**
- Create: `scripts/bench_corpus_dedup/corpora.py`
- Test: `scripts/bench_corpus_dedup/test_corpora.py`

- [ ] **Step 1: Write the failing test**

```python
# scripts/bench_corpus_dedup/test_corpora.py
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpora


def test_offline_is_deterministic_and_bounded():
    a = list(corpora.load_corpus("offline", n_docs=50, seed=0))
    b = list(corpora.load_corpus("offline", n_docs=50, seed=0))
    assert a == b                      # deterministic
    assert len(a) == 50                # honors n_docs
    assert all(isinstance(d, str) and isinstance(t, str) and t for d, t in a)
    ids = [d for d, _ in a]
    assert len(set(ids)) == 50         # unique doc ids


def test_offline_seed_changes_selection():
    a = list(corpora.load_corpus("offline", n_docs=50, seed=0))
    b = list(corpora.load_corpus("offline", n_docs=50, seed=1))
    assert a != b                      # different seed -> different (shuffled) slice
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv/bin/python -m pytest scripts/bench_corpus_dedup/test_corpora.py -v
```
Expected: FAIL (`ModuleNotFoundError: corpora` / `AttributeError: load_corpus`).

- [ ] **Step 3: Implement `corpora.py` offline adapter**

```python
# scripts/bench_corpus_dedup/corpora.py
"""Pluggable corpus adapters: load_corpus(name, n_docs, seed) -> (doc_id, text) stream.

Adapters:
  offline   - vendored data/offline_corpus.jsonl (network-free; the per-PR gate fixture)
  fineweb   - HF HuggingFaceFW/fineweb (ODC-By), streaming   [headline default]
  c4        - HF allenai/c4 'en', streaming
  wikipedia - HF wikimedia/wikipedia, streaming
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Iterator

HERE = Path(__file__).resolve().parent
OFFLINE_PATH = HERE / "data" / "offline_corpus.jsonl"


def _offline(n_docs: int, seed: int) -> Iterator[tuple[str, str]]:
    docs = [json.loads(l) for l in OFFLINE_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    rng = random.Random(seed)
    rng.shuffle(docs)
    for d in docs[:n_docs]:
        yield str(d["doc_id"]), str(d["text"])


def load_corpus(name: str, n_docs: int, seed: int = 0) -> Iterator[tuple[str, str]]:
    if name == "offline":
        yield from _offline(n_docs, seed)
    elif name in ("fineweb", "c4", "wikipedia"):
        yield from _hf(name, n_docs, seed)
    else:
        raise ValueError(f"unknown corpus {name!r}")
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
.venv/bin/python -m pytest scripts/bench_corpus_dedup/test_corpora.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/bench_corpus_dedup/corpora.py scripts/bench_corpus_dedup/test_corpora.py
git commit -m "feat(bench): offline corpus adapter, deterministic (#1086)"
```

### Task 1.3: `corpora.py` — HF streaming adapters (TDD, skip-if-offline)

**Files:**
- Modify: `scripts/bench_corpus_dedup/corpora.py` (add `_hf`)
- Test: `scripts/bench_corpus_dedup/test_corpora.py` (add network-guarded test)

- [ ] **Step 1: Add the failing test**

```python
import importlib, pytest

_HAS_DATASETS = importlib.util.find_spec("datasets") is not None


@pytest.mark.skipif(not _HAS_DATASETS, reason="datasets not installed (CI headline lane only)")
def test_fineweb_streams_when_available():
    try:
        docs = list(corpora.load_corpus("fineweb", n_docs=5, seed=0))
    except Exception as e:
        pytest.skip(f"network/HF unavailable: {e}")
    assert len(docs) == 5
    assert all(t for _, t in docs)
```

- [ ] **Step 2: Run it (expect SKIP locally)**

```bash
.venv/bin/python -m pytest scripts/bench_corpus_dedup/test_corpora.py::test_fineweb_streams_when_available -v
```
Expected: SKIP (datasets not installed locally) — that's the pass condition here.

- [ ] **Step 3: Implement `_hf`**

```python
# add to corpora.py
_HF_SPECS = {
    "fineweb":   ("HuggingFaceFW/fineweb", "sample-10BT", "train", "text"),
    "c4":        ("allenai/c4", "en", "train", "text"),
    "wikipedia": ("wikimedia/wikipedia", "20231101.en", "train", "text"),
}


def _hf(name: str, n_docs: int, seed: int) -> Iterator[tuple[str, str]]:
    from datasets import load_dataset

    repo, config, split, field = _HF_SPECS[name]
    ds = load_dataset(repo, config, split=split, streaming=True)
    ds = ds.shuffle(seed=seed, buffer_size=10_000)
    for i, row in enumerate(ds):
        if i >= n_docs:
            break
        text = (row.get(field) or "").strip()
        if text:
            yield f"{name}-{i}", text
```

- [ ] **Step 4: Run the offline tests still pass; HF test still skips**

```bash
.venv/bin/python -m pytest scripts/bench_corpus_dedup/test_corpora.py -v
```
Expected: offline tests PASS, fineweb test SKIP.

- [ ] **Step 5: Commit**

```bash
git add scripts/bench_corpus_dedup/corpora.py scripts/bench_corpus_dedup/test_corpora.py
git commit -m "feat(bench): HF streaming corpus adapters (fineweb/c4/wikipedia) (#1086)"
```

---

## Phase 2: Ground-truth near-dup injector

### Task 2.1: `inject_dups.py` (TDD)

Deterministically layer near-dups onto a base corpus and emit corpus + truth parquets.
Every output doc gets a `cluster_id`: an injected dup shares its source doc's cluster;
every base doc that is NOT duplicated is its own singleton cluster. This is the ground
truth the evaluator scores recall against.

**Files:**
- Create: `scripts/bench_corpus_dedup/inject_dups.py`
- Test: `scripts/bench_corpus_dedup/test_inject_dups.py`

- [ ] **Step 1: Write the failing test**

```python
# scripts/bench_corpus_dedup/test_inject_dups.py
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import inject_dups
import polars as pl


def _base(n=100):
    return [(f"b{i}", f"document number {i} " + "filler words here " * 20) for i in range(n)]


def test_determinism(tmp_path):
    out1, t1 = inject_dups.build(_base(), seed=0, frac=0.3, out_dir=tmp_path / "a")
    out2, t2 = inject_dups.build(_base(), seed=0, frac=0.3, out_dir=tmp_path / "b")
    assert pl.read_parquet(out1).equals(pl.read_parquet(out2))
    assert pl.read_parquet(t1).equals(pl.read_parquet(t2))


def test_truth_columns_and_dup_membership(tmp_path):
    out, truth = inject_dups.build(_base(100), seed=0, frac=0.3, out_dir=tmp_path)
    corpus = pl.read_parquet(out)
    tr = pl.read_parquet(truth)
    assert set(corpus.columns) == {"doc_id", "text"}
    assert set(tr.columns) == {"record_id", "cluster_id"}
    # every corpus doc has exactly one truth row
    assert tr.height == corpus.height
    assert set(tr["record_id"]) == set(corpus["doc_id"])
    # at least one non-singleton cluster exists (dups were injected)
    sizes = tr.group_by("cluster_id").len()
    assert sizes["len"].max() >= 2
    # every injected dup id maps to its source's cluster
    dup_rows = corpus.filter(pl.col("doc_id").str.contains("~dup"))
    assert dup_rows.height > 0


def test_modes_present(tmp_path):
    out, _ = inject_dups.build(_base(200), seed=1, frac=0.5, out_dir=tmp_path)
    ids = pl.read_parquet(out)["doc_id"].to_list()
    # dup ids encode their mode: ...~dup-exact / ~dup-partial / ~dup-paraphrase
    assert any("exact" in i for i in ids)
    assert any("partial" in i for i in ids)
    assert any("paraphrase" in i for i in ids)
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv/bin/python -m pytest scripts/bench_corpus_dedup/test_inject_dups.py -v
```
Expected: FAIL (`AttributeError: build`).

- [ ] **Step 3: Implement `inject_dups.py`**

Key contract: `build(base_docs, *, seed, frac, out_dir, mode_weights=(0.34,0.33,0.33), strength=0.15) -> (corpus_path, truth_path)`.
- Shuffle a copy of base docs with `random.Random(seed)`; pick `round(frac*len)` source docs.
- For each source, choose a mode (exact / partial / paraphrase) by `mode_weights`, produce a
  derived text, append a new doc `f"{src_id}~dup-{mode}-{k}"` with the SAME `cluster_id` as src.
- Base docs each start as their own cluster (`cluster_id = doc_id`); dups inherit the src cluster.
- `exact`: copy text. `partial`: drop/keep a random contiguous run of tokens (deterministic via the
  seeded rng), plus an inserted clause. `paraphrase`: case flips + whitespace + a few token swaps
  bounded by `strength` so it stays a near-dup, not a different doc.
- Write `corpus.parquet {doc_id, text}` and `truth.parquet {record_id=doc_id, cluster_id}` (zstd).
- A `main()` CLI: `--corpus NAME --n-docs N --seed S --frac F --out-dir DIR` that pulls base docs
  via `corpora.load_corpus` then calls `build`.

- [ ] **Step 4: Run the test to verify it passes**

```bash
.venv/bin/python -m pytest scripts/bench_corpus_dedup/test_inject_dups.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/bench_corpus_dedup/inject_dups.py scripts/bench_corpus_dedup/test_inject_dups.py
git commit -m "feat(bench): seeded ground-truth near-dup injector (#1086)"
```

---

## Phase 3: Engine runners

### Task 3.1: `run_goldenmatch.py` — throughput-tier datapoint (TDD)

Mirrors `bench_er_headtohead/run_goldenmatch.py`: env set before import, `bench_capture()`,
atomic JSON, `_peak_rss_mb()`, `--pred-out` parquet `{record_id, pred_cluster_id}`. **Differs**:
calls `dedupe_df(df, throughput=recall_target)` and **asserts the tier engaged** — refuses to
report a number if `verify_mode != "sketch_distance"` or blocking strategy not in `{lsh, simhash}`.
Reports `docs_per_sec`, `mb_per_sec`, `candidate_pairs` (= `scored_pair_count`), derived
`reduction_ratio`, `throughput_posture`.

**Files:**
- Create: `scripts/bench_corpus_dedup/run_goldenmatch.py`
- Test: `scripts/bench_corpus_dedup/test_runners.py`

- [ ] **Step 1: Write the failing test** (drives the result schema + tier assertion)

```python
# scripts/bench_corpus_dedup/test_runners.py
import json, subprocess, sys
from pathlib import Path
import polars as pl

HERE = Path(__file__).resolve().parent


def _make_corpus(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("inj", HERE / "inject_dups.py")
    inj = importlib.util.module_from_spec(spec); sys.path.insert(0, str(HERE)); spec.loader.exec_module(inj)
    base = [(f"b{i}", f"unique-ish doc {i} " + "shared filler clause " * 30) for i in range(60)]
    return inj.build(base, seed=0, frac=0.4, out_dir=tmp_path)


def test_goldenmatch_runner_engages_tier(tmp_path):
    corpus, _truth = _make_corpus(tmp_path)
    out = tmp_path / "gm.json"; pred = tmp_path / "gm.pred.parquet"
    rc = subprocess.run(
        [sys.executable, str(HERE / "run_goldenmatch.py"),
         "--input", str(corpus), "--out", str(out), "--pred-out", str(pred),
         "--recall-target", "0.95"],
        env={"PYTHONPATH": "packages/python/goldenmatch", "GOLDENMATCH_NATIVE": "0",
             "POLARS_SKIP_CPU_CHECK": "1", "PATH": __import__("os").environ["PATH"]},
    ).returncode
    assert rc == 0
    r = json.loads(out.read_text())
    assert r["status"] == "ok"
    assert r["verify_mode"] == "sketch_distance"        # tier engaged
    assert r["blocking_strategy"] in ("lsh", "simhash")
    assert r["candidate_pairs"] is not None
    assert r["docs_per_sec"] and r["mb_per_sec"]
    assert 0.0 <= r["reduction_ratio"] <= 1.0
    p = pl.read_parquet(pred)
    assert set(p.columns) == {"record_id", "pred_cluster_id"}
    # ids must JOIN to the corpus doc_ids — guards the __row_id__->doc_id remap.
    # (A raw-integer pred would make this intersection empty and recall silently 0.)
    corpus_ids = set(pl.read_parquet(corpus)["doc_id"].to_list())
    assert set(p["record_id"].to_list()) <= corpus_ids and len(p) > 0
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv/bin/python -m pytest scripts/bench_corpus_dedup/test_runners.py::test_goldenmatch_runner_engages_tier -v
```
Expected: FAIL (runner missing).

- [ ] **Step 3: Implement `run_goldenmatch.py`**

Structure (adapt `bench_er_headtohead/run_goldenmatch.py`):
- Set `GOLDENMATCH_*` env BEFORE importing goldenmatch.
- Read parquet `{doc_id, text}` -> Polars df.
- `with bench_capture() as bench: ded = dedupe_df(df, throughput=args.recall_target)`.
- **Tier assertion + cost metrics come from `ded.throughput_posture` (a dict), NOT `bench_capture`.**
  Task 0.2 spike proved `bench_capture().scored_pair_count` is `0` on the throughput path (it counts
  fuzzy/FS-scored pairs; the tier verifies via sketch_distance, bypassing that scorer). The posture
  dict carries: `candidate_pairs`, `verified_pairs`, `reduction_ratio`, `expected_recall`, `bands`,
  `rows_per_band`, `metric`, `similarity_threshold`, `notes`.
  - Tier-engaged check: `post = ded.throughput_posture; if not post or "candidate_pairs" not in post:
    raise RuntimeError("throughput tier did not engage")`. (Also read the blocking strategy from
    `ded.config` for the `blocking_strategy` field; `verify_mode` likewise lives on the resolved
    config — record whatever the config exposes, but the posture presence is the load-bearing gate.)
  - `candidate_pairs = post["candidate_pairs"]`; `reduction_ratio = post["reduction_ratio"]`
    (use the posture's value directly — do NOT re-derive from `scored_pair_count`).
- `n = df.height`.
- `bytes_in = int(df["text"].str.len_bytes().sum())`; `docs_per_sec = n / dedupe_wall`;
  `mb_per_sec = bytes_in/1e6 / dedupe_wall`.
- Write `--pred-out` parquet `{record_id, pred_cluster_id}` (`record_id := doc_id` string).
  **CRITICAL — `ded.clusters` members are internal positional `__row_id__` integers (0..N-1),
  NOT the `doc_id` strings.** Remap exactly like the reference autoconfig branch
  (`bench_er_headtohead/run_goldenmatch.py:219-240`): `doc_ids = df["doc_id"].to_list()`, then for
  each member `m` write `record_id = doc_ids[m]` (a string). Writing the raw integer members makes
  the evaluator's `p.record_id = t.record_id` join (string-vs-int) match **zero rows** → silent
  recall=0 / empty accuracy, and the perf gate's `measured_recall` would read 0.0. This is the one
  place a silent wrong number can slip in — get the remap right.
- Atomic JSON result with keys: `engine="goldenmatch"`, `status`, `n_docs`, `bytes_in`,
  `dedupe_wall_seconds`, `docs_per_sec`, `mb_per_sec`, `candidate_pairs`, `reduction_ratio`,
  `verify_mode`, `blocking_strategy`, `clusters`, `throughput_posture`, `peak_rss_mb`.
- `--recall-target` (default 0.95). `MemoryError -> status="OOM"`. `finally:` atomic write + print.

- [ ] **Step 4: Run the test to verify it passes**

```bash
.venv/bin/python -m pytest scripts/bench_corpus_dedup/test_runners.py::test_goldenmatch_runner_engages_tier -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/bench_corpus_dedup/run_goldenmatch.py scripts/bench_corpus_dedup/test_runners.py
git commit -m "feat(bench): goldenmatch throughput-tier runner, fails loud if tier not engaged (#1086)"
```

### Task 3.2: `run_datatrove.py` — datatrove MinHash datapoint (TDD, skip-if-not-installed)

**Files:**
- Create: `scripts/bench_corpus_dedup/run_datatrove.py`
- Test: `scripts/bench_corpus_dedup/test_runners.py` (add datatrove test)

- [ ] **Step 1: Add the failing (skip-guarded) test**

```python
import importlib, pytest
_HAS_DATATROVE = importlib.util.find_spec("datatrove") is not None

@pytest.mark.skipif(not _HAS_DATATROVE, reason="datatrove not installed (headline lane installs it)")
def test_datatrove_runner_schema(tmp_path):
    corpus, _ = _make_corpus(tmp_path)
    out = tmp_path / "dt.json"; pred = tmp_path / "dt.pred.parquet"
    rc = subprocess.run(
        [sys.executable, str(HERE / "run_datatrove.py"),
         "--input", str(corpus), "--out", str(out), "--pred-out", str(pred)],
    ).returncode
    assert rc == 0
    r = json.loads(out.read_text())
    assert r["engine"] == "datatrove"
    assert r["status"] in ("ok", "OOM", "error")
    if r["status"] == "ok":
        assert r["docs_per_sec"] and r["mb_per_sec"] and r["candidate_pairs"] is not None
        assert set(pl.read_parquet(pred).columns) == {"record_id", "pred_cluster_id"}
```

- [ ] **Step 2: Run it (expect SKIP locally)**

```bash
.venv/bin/python -m pytest scripts/bench_corpus_dedup/test_runners.py::test_datatrove_runner_schema -v
```
Expected: SKIP (datatrove not installed locally).

- [ ] **Step 3: Implement `run_datatrove.py`**

- datatrove's MinHash dedup is a multi-stage pipeline (`MinhashDedupSignature` ->
  `MinhashDedupBuckets` -> `MinhashDedupCluster` -> `MinhashDedupFilter`) over `LocalPipelineExecutor`
  with a `Document` reader. Implement a thin adapter: read `{doc_id, text}` parquet into datatrove
  `Document(text=..., id=doc_id)`, run the signature->buckets->cluster stages in a temp dir, parse the
  emitted clusters into `{record_id, pred_cluster_id}` (docs not in any near-dup cluster are singletons).
- Same speed/memory schema as the goldenmatch runner: `n_docs`, `bytes_in`, `dedupe_wall_seconds`,
  `docs_per_sec`, `mb_per_sec`, `candidate_pairs` (datatrove's bucket-matched pairs, if exposed; else
  null with a note), `clusters`, `peak_rss_mb`, `status`. Atomic write; `MemoryError -> OOM`.
- Match the MinHash config to a comparable similarity threshold so the head-to-head is fair, and
  **record the concrete mapping in code comments + the README** so the "fair comparison" claim is
  auditable, not asserted: datatrove `MinhashConfig(num_buckets=B, hashes_per_bucket=R, n_grams=G)`
  vs the tier's effective Jaccard `similarity_threshold` (the LSH S-curve 50%-point of `(B, R)`
  should sit at roughly the tier's threshold). Pick `(B, R, G)` so both target ~0.8 Jaccard near-dup
  (datatrove's own `minhash` example defaults are the starting reference) and write the chosen
  numbers + the resulting S-curve midpoint into `run_datatrove.py` and `README.md`.

- [ ] **Step 4: Confirm SKIP still holds locally; schema is exercised in CI**

```bash
.venv/bin/python -m pytest scripts/bench_corpus_dedup/test_runners.py -v
```
Expected: goldenmatch test PASS, datatrove test SKIP.

- [ ] **Step 5: Commit**

```bash
git add scripts/bench_corpus_dedup/run_datatrove.py scripts/bench_corpus_dedup/test_runners.py
git commit -m "feat(bench): datatrove MinHash dedup runner (CI-installed competitor) (#1086)"
```

---

## Phase 4: Orchestrator (reuses the existing evaluator)

### Task 4.1: `orchestrate.py` (TDD smoke + summary)

**Files:**
- Create: `scripts/bench_corpus_dedup/orchestrate.py`
- Test: `scripts/bench_corpus_dedup/test_smoke.py`

- [ ] **Step 1: Write the failing end-to-end smoke test**

```python
# scripts/bench_corpus_dedup/test_smoke.py
import json, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent


def test_offline_smoke_goldenmatch_only(tmp_path):
    rc = subprocess.run(
        [sys.executable, str(HERE / "orchestrate.py"),
         "--corpus", "offline", "--scales", "200", "--engines", "goldenmatch",
         "--seed", "0", "--workdir", str(tmp_path)],
        env={"PYTHONPATH": "packages/python/goldenmatch", "GOLDENMATCH_NATIVE": "0",
             "POLARS_SKIP_CPU_CHECK": "1", "PATH": __import__("os").environ["PATH"]},
    ).returncode
    assert rc == 0
    results = json.loads((tmp_path / "bench_results.json").read_text())
    assert results and results[0]["engine"] == "goldenmatch"
    assert results[0]["status"] == "ok"
    assert "accuracy" in results[0] and results[0]["accuracy"]["pairwise"]["recall"] >= 0.0
    summary = (tmp_path / "summary.md").read_text()
    assert "docs/sec" in summary and "MB/sec" in summary
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv/bin/python -m pytest scripts/bench_corpus_dedup/test_smoke.py -v
```
Expected: FAIL (orchestrate missing).

- [ ] **Step 3: Implement `orchestrate.py`** (adapt `bench_er_headtohead/orchestrate.py`)

- For each `(corpus, scale)`: build the fixture once via a subprocess to `inject_dups.py`
  (`--corpus --n-docs --seed --frac --out-dir`) producing `corpus.parquet` + `truth.parquet`.
- For each engine: `run_engine` subprocess to `run_{engine}.py` with `--input corpus.parquet
  --out RESULT.json --pred-out PRED.parquet` (+ `--recall-target` for goldenmatch). Reuse the
  `_run` / `_load_or_synthesize` OOM-tolerant helpers verbatim.
- **Reuse the existing evaluator**: call `scripts/bench_er_headtohead/evaluate.py` by path with
  `--pred PRED.parquet --truth truth.parquet --out METRICS.json`; attach as `result["accuracy"]`.
  (record_id := doc_id — identical contract, no new evaluator code.)
- Flush `bench_results.json` after every datapoint.
- `render_markdown`: headline table **`| corpus | docs | engine | status | dedupe wall (s) | peak RSS (MB) | docs/sec | MB/sec | candidate pairs | pairwise R | pairwise F1 |`**, plus a
  "vs datatrove" delta table (GM docs/sec ÷ datatrove docs/sec at matched recall), plus a
  NeMo-Curator **cited reference row** (constant: published GPU docs/sec + its corpus/hardware,
  labelled "GPU, not run here"). Append to `$GITHUB_STEP_SUMMARY`.
- CLI: `--corpus` (one of the 4), `--scales` (ints), `--engines`, `--seed`, `--frac`,
  `--recall-target`, `--workdir`.

- [ ] **Step 4: Run the smoke test to verify it passes**

```bash
.venv/bin/python -m pytest scripts/bench_corpus_dedup/test_smoke.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/bench_corpus_dedup/orchestrate.py scripts/bench_corpus_dedup/test_smoke.py
git commit -m "feat(bench): corpus-dedup orchestrator (reuses head-to-head evaluator) (#1086)"
```

---

## Phase 5: Deterministic per-PR perf gate

### Task 5.1: `throughput_perf_gate.py` (TDD)

**Files:**
- Create: `scripts/bench_corpus_dedup/throughput_perf_gate.py`
- Test: `scripts/bench_corpus_dedup/test_perf_gate.py`

- [ ] **Step 1: Write the failing test** (tolerance logic is the unit under test, isolated from the run)

```python
# scripts/bench_corpus_dedup/test_perf_gate.py
from pathlib import Path
import json, sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import throughput_perf_gate as gate


BASE = {"candidate_pairs": 1000, "reduction_ratio": 0.95, "measured_recall": 0.97}


def test_pass_at_baseline():
    ok, fails = gate.compare(BASE, dict(BASE))
    assert ok and not fails


def test_fail_when_pairs_blow_up():
    cur = dict(BASE, candidate_pairs=1200)   # +20% > +15% tol
    ok, fails = gate.compare(BASE, cur)
    assert not ok and any("candidate_pairs" in f for f in fails)


def test_pass_within_pairs_tolerance():
    cur = dict(BASE, candidate_pairs=1140)   # +14% < +15%
    ok, fails = gate.compare(BASE, cur)
    assert ok


def test_fail_when_recall_drops():
    cur = dict(BASE, measured_recall=0.955)  # < 0.97 - 0.01
    ok, fails = gate.compare(BASE, cur)
    assert not ok and any("recall" in f for f in fails)


def test_fail_when_reduction_drops():
    cur = dict(BASE, reduction_ratio=0.93)   # < 0.95 - 0.01
    ok, fails = gate.compare(BASE, cur)
    assert not ok and any("reduction_ratio" in f for f in fails)


def test_update_baseline_roundtrip(tmp_path):
    p = tmp_path / "baseline.json"
    gate.write_baseline(p, BASE)
    assert json.loads(p.read_text())["candidate_pairs"] == 1000
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv/bin/python -m pytest scripts/bench_corpus_dedup/test_perf_gate.py -v
```
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `throughput_perf_gate.py`**

```python
# scripts/bench_corpus_dedup/throughput_perf_gate.py
"""Deterministic per-PR throughput-tier cost gate (#1086).

Runs the tier on the OFFLINE corpus at a fixed size+seed, extracts machine-independent
cost metrics, and compares vs a committed baseline. No wall-clock in the verdict.

Usage:
  python throughput_perf_gate.py --check            # compare vs perf_gate_baseline.json -> exit 0/1
  python throughput_perf_gate.py --update-baseline  # regenerate the baseline (intentional change)
"""
from __future__ import annotations

import argparse, json, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASELINE = HERE / "perf_gate_baseline.json"

PAIRS_TOL = 0.15   # candidate_pairs may grow at most +15%
EPS = 0.01         # recall / reduction_ratio floors

# Fixed gate workload — change these only with --update-baseline.
GATE_N_DOCS = 1500
GATE_SEED = 0
GATE_FRAC = 0.4
GATE_RECALL_TARGET = 0.95


def measure(workdir: Path) -> dict:
    """Build the fixed offline fixture, run the tier, return the cost metrics."""
    workdir.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(HERE / "inject_dups.py"),
                    "--corpus", "offline", "--n-docs", str(GATE_N_DOCS),
                    "--seed", str(GATE_SEED), "--frac", str(GATE_FRAC),
                    "--out-dir", str(workdir)], check=True)
    out = workdir / "gate.json"; pred = workdir / "gate.pred.parquet"
    subprocess.run([sys.executable, str(HERE / "run_goldenmatch.py"),
                    "--input", str(workdir / "corpus.parquet"), "--out", str(out),
                    "--pred-out", str(pred), "--recall-target", str(GATE_RECALL_TARGET)],
                   check=True)
    r = json.loads(out.read_text())
    # recall on the injected truth via the shared evaluator
    metrics_out = workdir / "gate.metrics.json"
    subprocess.run([sys.executable,
                    str(HERE.parent / "bench_er_headtohead" / "evaluate.py"),
                    "--pred", str(pred), "--truth", str(workdir / "truth.parquet"),
                    "--out", str(metrics_out)], check=True)
    recall = json.loads(metrics_out.read_text())["pairwise"]["recall"]
    return {"candidate_pairs": r["candidate_pairs"],
            "reduction_ratio": round(r["reduction_ratio"], 4),
            "measured_recall": round(recall, 4)}


def compare(baseline: dict, current: dict) -> tuple[bool, list[str]]:
    fails = []
    if current["candidate_pairs"] > baseline["candidate_pairs"] * (1 + PAIRS_TOL):
        fails.append(f"candidate_pairs {current['candidate_pairs']} > "
                     f"{baseline['candidate_pairs']}*(1+{PAIRS_TOL})")
    if current["measured_recall"] < baseline["measured_recall"] - EPS:
        fails.append(f"measured_recall {current['measured_recall']} < "
                     f"{baseline['measured_recall']}-{EPS}")
    if current["reduction_ratio"] < baseline["reduction_ratio"] - EPS:
        fails.append(f"reduction_ratio {current['reduction_ratio']} < "
                     f"{baseline['reduction_ratio']}-{EPS}")
    return (not fails), fails


def write_baseline(path: Path, metrics: dict) -> None:
    path.write_text(json.dumps(metrics, indent=2) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--workdir", type=Path, default=HERE / ".gate_tmp")
    args = ap.parse_args()
    current = measure(args.workdir)
    print("[gate] measured:", json.dumps(current))
    if args.update_baseline:
        write_baseline(BASELINE, current)
        print(f"[gate] baseline updated -> {BASELINE}")
        return
    baseline = json.loads(BASELINE.read_text())
    ok, fails = compare(baseline, current)
    if ok:
        print("[gate] PASS")
    else:
        print("[gate] FAIL\n  - " + "\n  - ".join(fails))
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the unit tests to verify they pass**

```bash
.venv/bin/python -m pytest scripts/bench_corpus_dedup/test_perf_gate.py -v
```
Expected: PASS (compare/write_baseline are pure; `measure` is not exercised by these units).

- [ ] **Step 5: Commit**

```bash
git add scripts/bench_corpus_dedup/throughput_perf_gate.py scripts/bench_corpus_dedup/test_perf_gate.py
git commit -m "feat(bench): deterministic throughput perf gate (cost vs committed baseline) (#1086)"
```

### Task 5.2: Generate + commit the baseline

**Files:**
- Create: `scripts/bench_corpus_dedup/perf_gate_baseline.json`

- [ ] **Step 1: Generate the baseline from the real tier**

```bash
PYTHONPATH=packages/python/goldenmatch GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 \
  .venv/bin/python scripts/bench_corpus_dedup/throughput_perf_gate.py --update-baseline
```
Expected: writes `perf_gate_baseline.json` with `candidate_pairs`, `reduction_ratio`,
`measured_recall`. Sanity-check the values are plausible (recall ≥ ~0.9, reduction_ratio ≥ ~0.9).

- [ ] **Step 2: Verify the gate passes against its own fresh baseline**

```bash
PYTHONPATH=packages/python/goldenmatch GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 \
  .venv/bin/python scripts/bench_corpus_dedup/throughput_perf_gate.py --check
echo "exit: $?"
```
Expected: `[gate] PASS`, exit 0.

- [ ] **Step 3: Commit**

```bash
git add scripts/bench_corpus_dedup/perf_gate_baseline.json
git commit -m "chore(bench): commit throughput perf-gate baseline (#1086)"
```

---

## Phase 6: CI wiring

### Task 6.1: Dispatch headline workflow

**Files:**
- Create: `.github/workflows/bench-corpus-dedup.yml`

- [ ] **Step 1: Write the workflow** (model on `bench-er-headtohead.yml`)

- `name: bench-corpus-dedup`, `on: workflow_dispatch` with inputs: `corpus`
  (default `fineweb`), `scales` (default e.g. `"100000 1000000"`), `engines`
  (default `"goldenmatch datatrove"`), `recall_target` (default `0.95`),
  `runner` (default `large-new-64GB`).
- Steps: checkout; setup-uv; `uv sync --all-packages`; `uv run python scripts/build_native.py`
  (so the tier runs at its best); `uv pip install datatrove` (if engines contains datatrove);
  `uv pip install duckdb datasets` (eval + corpus streaming);
  `uv run python scripts/bench_corpus_dedup/orchestrate.py --corpus ${{ inputs.corpus }}
  --scales ${{ inputs.scales }} --engines ${{ inputs.engines }}
  --recall-target ${{ inputs.recall_target }} --workdir .bench_corpus`;
  upload `.bench_corpus/{summary.md,bench_results.json}` as an artifact **named exactly
  `corpus-dedup-results`** (90d) — Task 7.2's `gh run download -n corpus-dedup-results` depends on
  this name.
- Pin all action SHAs to match the repo convention (copy the exact pinned versions from
  `bench-er-headtohead.yml`).

- [ ] **Step 2: Lint the YAML**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/bench-corpus-dedup.yml')); print('yaml ok')"
```
Expected: `yaml ok`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/bench-corpus-dedup.yml
git commit -m "ci(bench): dispatch headline corpus-dedup bench (vs datatrove, 64GB) (#1086)"
```

### Task 6.2: Per-PR gate job in `ci.yml`

**Files:**
- Modify: `.github/workflows/ci.yml` (add a `throughput` filter area + a `throughput-gate` job)

- [ ] **Step 1: Read the current filter + job conventions**

```bash
grep -nE "changes:|paths-filter|outputs:|if: needs.changes" .github/workflows/ci.yml | head -40
```
Identify the `changes` job's filter block and the `if: needs.changes.outputs.<area> == 'true'`
gate pattern (per CLAUDE.md "CI path filters").

- [ ] **Step 2: Add a `throughput` filter area**

In the `changes` job's `dorny/paths-filter` `filters:` map, add:
```yaml
throughput:
  - 'packages/python/goldenmatch/goldenmatch/core/throughput_verify.py'
  - 'packages/python/goldenmatch/goldenmatch/core/autoconfig_planner.py'
  - 'packages/python/goldenmatch/goldenmatch/config/schemas.py'
  - 'scripts/bench_corpus_dedup/**'
  - '.github/workflows/ci.yml'
```
Expose it as a job output alongside the others. **Verify each tier path matches a real file on the
post-rebase tree** (`ls` them after the #1129 rebase) — a filter entry that never matches a real
file silently means the gate never fires on tier changes, defeating its purpose. The first three
land with #1129; confirm the exact `core/throughput_verify.py` / `core/autoconfig_planner.py` /
`config/schemas.py` names (the spec's grep confirmed `autoconfig_planner.py:125` and
`throughput_verify.py`, but re-check at implementation time).

- [ ] **Step 3: Add the `throughput-gate` job**

```yaml
throughput-gate:
  needs: changes
  if: needs.changes.outputs.throughput == 'true'
  runs-on: ubuntu-latest
  timeout-minutes: 20
  steps:
    - uses: actions/checkout@<pinned-sha>
    - uses: actions/setup-python@<pinned-sha>
      with: { python-version: "3.12" }
    - run: pip install uv
    - run: uv sync --all-packages
    - run: uv pip install -e packages/python/goldenmatch
    - run: uv pip install duckdb
    - name: Run perf gate + unit tests
      run: |
        set -euo pipefail
        uv run python -m pytest scripts/bench_corpus_dedup/ -q
        GOLDENMATCH_NATIVE=auto POLARS_SKIP_CPU_CHECK=1 \
          uv run python scripts/bench_corpus_dedup/throughput_perf_gate.py --check
```
(Use the exact pinned action SHAs already in `ci.yml`. The gate runs pure-Python — no native
build needed — because the cost metrics are algorithmic, not wall-clock.)

- [ ] **Step 4: Lint the YAML**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')"
```
Expected: `yaml ok`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: per-PR throughput perf gate (deterministic cost vs baseline) (#1086)"
```

---

## Phase 7: README, headline run, docs rollout

### Task 7.1: Harness README

**Files:**
- Create: `scripts/bench_corpus_dedup/README.md`

- [ ] **Step 1: Write it** (model on `bench_er_headtohead/README.md`): what it measures
  (docs/sec + MB/sec vs datatrove at measured recall, NeMo cited), the corpus adapters, how the
  injected ground truth makes recall measurable, how subprocess isolation survives OOM, how the
  deterministic gate works (and `--update-baseline` when a change is intentional), how to run
  (dispatch for the headline; `pytest` + `--check` locally), and the known limitations
  (single fixture family; datatrove config is reasonable-not-optimal; NeMo cited not run).

- [ ] **Step 2: Commit**

```bash
git add scripts/bench_corpus_dedup/README.md
git commit -m "docs(bench): README for corpus-dedup throughput harness (#1086)"
```

### Task 7.2: Run the headline bench, capture the number

- [ ] **Step 1: Verify #1129 is merged to main; rebase the branch onto main**

```bash
gh pr view 1129 --json state -q .state    # must be MERGED
git fetch origin && git rebase origin/main
```
If not merged, STOP and wait — the headline must run against the tier as it lands on main.

- [ ] **Step 2: Push the branch and dispatch the headline bench**

```bash
git push -u origin feat/1086-throughput-bench
gh workflow run bench-corpus-dedup.yml --ref feat/1086-throughput-bench \
  -f corpus=fineweb -f scales="100000 1000000" -f engines="goldenmatch datatrove"
```

- [ ] **Step 2b: Poll, then download the artifact**

```bash
# after the run completes
gh run download <run-id> -n corpus-dedup-results -D .bench_corpus_headline
cat .bench_corpus_headline/summary.md
```
Record the headline: GoldenMatch docs/sec + MB/sec, datatrove docs/sec + MB/sec, the ratio,
and the pairwise recall each achieved.

### Task 7.3: Docs rollout sweep

- [ ] **Step 1: Invoke the rollout-docs-sweep skill**

Sweep the headline number into: README throughput table, context-network ADR + nav + log,
docs-site throughput/tuning page, CHANGELOG. Per `feedback_rollout_docs_sweep` +
`reference_tuning_opt_ins_doc` (canonical runtime-config doc = `docs-site/goldenmatch/tuning.mdx`).

- [ ] **Step 2: Commit the docs**

```bash
git add -A
git commit -m "docs: publish throughput headline (docs/sec vs datatrove) + gate (#1086)"
```

### Task 7.4: Open the PR (only after #1129 is on main)

- [ ] **Step 1: Final local check**

```bash
PYTHONPATH=packages/python/goldenmatch GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 \
  .venv/bin/python -m pytest scripts/bench_corpus_dedup/ -q
```
Expected: all PASS (datatrove + HF tests SKIP locally).

- [ ] **Step 2: Push + open PR, arm auto-merge**

```bash
git push -u origin feat/1086-throughput-bench
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create --fill \
  --title "feat(bench): throughput benchmark + CI perf gate (#1086)" \
  --body "Closes #1086. ..."
gh pr merge --auto --squash
```
Per `feedback_dont_poll_ci_arm_automerge`: arm auto-merge and stop — don't sit in a CI poll loop.

---

## Notes for the executor

- **DRY win:** the accuracy evaluator is reused from `bench_er_headtohead/evaluate.py` by path —
  do NOT write a second evaluator.
- **Cost metric is already surfaced:** Task 0.2 confirms `bench_capture()` exposes
  `scored_pair_count`; `reduction_ratio` is derived (`1 - pairs/C(N,2)`). The spec's
  instrumentation-hook fallback is only needed if Task 0.2 finds the counter absent.
- **Loud-fail discipline** (from `bench_er_headtohead`): the goldenmatch runner must refuse to
  report a number if the throughput tier didn't actually engage.
- **Env hygiene** (`reference_polars_wmi_hang_windows`, `feedback_avoid_full_suite_oom`): always
  `POLARS_SKIP_CPU_CHECK=1` locally; never run the full pytest suite locally — run only the
  `scripts/bench_corpus_dedup/` files; CI is the full-suite gate.
- **Auth** (`feedback_github_auth_switch`): goldenmatch uses the `benzsevern` gh account; stay on it.
```
