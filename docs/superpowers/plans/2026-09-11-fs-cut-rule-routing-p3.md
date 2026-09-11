# FS Link-Cut Routing, Phase P3: Held-Out Corpus and Per-Rule Matrix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze a design and a held-out corpus. For every dataset in them, measure F1, precision and recall once per link-cut rule, on one shared EM model, together with the cut diagnostics a router will read. Produce that matrix in CI as a mergeable scorecard. This phase measures only; it writes no routing rule.

**Architecture:**
- **Held-out datasets.** New loaders in `scripts/bench_er_headtohead/datasets.py`, pyarrow only:
  - Leipzig Abt-Buy;
  - three DeepMatcher/Magellan benchmarks: Walmart-Amazon, iTunes-Amazon, Fodors-Zagats;
  - FEBRL `dataset1` and `dataset2`;
  - eight seeded synthetic variants. These use a new `corruption` knob on `generate_fixture.generate`.
- **Frozen corpus.** `scripts/autoconfig_quality/corpus.py` pins the design and held-out name lists.
- **Per-rule sweep.** `scripts/autoconfig_quality/rule_sweep.py` auto-configures each dataset's probabilistic config once. It then runs `dedupe_df` once per arm:
  - `default`, which trains and saves the EM model through `MatchkeyConfig.model_path`;
  - `default_loaded`, which loads that model and is the baseline;
  - one arm per `link_cut_rule`, each loading the same model.

  It records per-arm metrics and the `fs_link_thresholds` report. A pinned arm that an earlier precedence step overrode fails the run.
- **CI.** A new workflow `fs-cut-rule-matrix.yml` fans the corpus out one job per dataset and merges the per-dataset scorecards into one artifact plus a markdown summary.

**Tech Stack:** Python 3.12, pyarrow, polars (the existing harness frames), numpy, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-11-fs-cut-rule-routing-design.md` (branch `docs/fs-cut-rule-routing-spec`): "Corpus and harness", "Gate", "Rollout" P3. It builds on P1–P2, draft PR #2942.

## Global Constraints

**Scope and corpus rules (from the spec)**
- **Measurement only.** Do not add `choose_cut_rule`, `GOLDENMATCH_FS_CUT_ROUTER` or any table row; those are P4.
- **Frozen held-out set.** It is frozen before any row is written. Changing it after rows exist means re-running the full gate. `corpus.py` is pinned verbatim by a test.
- **Gate tolerance.** A routed rule must never score below the default's F1 by more than `0.01` on any dataset in either set. The sweep reports `below_default` with that tolerance and does not gate on it.
- **Where data runs.** Large datasets run in CI or on the homelab, never on a laptop. Local runs use tiny fixtures and the `person` anchor only.
- **Deviation from the spec's wording, deliberate.** The spec asks for the per-rule results as "a new scorecard block beside `f1` and `f1_probabilistic`". Here they go in their own scorecard file and CI artifact instead, in the same format as the harness scorecard.
  - The reason: the committed baseline `scripts/autoconfig_quality/baselines/scorecard.json` is diffed by the required 20-minute `quality_gate` job, and 11 full pipeline runs per dataset do not fit in it.
  - The committed baseline and its gate stay untouched.

**Data and licences**
- **Leipzig (Abt-Buy):** CC-BY, attribution required ("Database Group Leipzig; Köpcke, Thor & Rahm, VLDB 2010"). Fetched at run time.
- **Magellan/DeepMatcher (Walmart-Amazon, iTunes-Amazon, Fodors-Zagats):** cite-only. Fetch at run time, never commit, evaluation only. Attribution: "Konda et al. 2016; Mudgal et al. 2018".
- **FEBRL `dataset1` / `dataset2`:** ANU open-source licence, MPL-1.1 derived and redistributable. Fetched at run time from the recordlinkage repository. Attribution: "Christen 2008".
- **No committed data.** Everything is fetched under `packages/python/goldenmatch/tests/benchmarks/datasets/`, which is gitignored.
- **`ncvr_real` is local-only PII.** It is in the design set, but CI never provisions it.

**Code rules**
- **No pandas.** New and changed loaders read CSVs with the existing `_read_csv_lossy` / `_with_source_ids` pyarrow helpers, which arrive with the #2940 cherry-pick in Task 1.
- **Default generator output is byte-identical.** `generate_fixture.generate` with the default `corruption=1.0` must write exactly what it writes today. A digest test captured from the unmodified code pins this.
- **Commit text.** No AI or Claude attribution lines in commits or PR bodies. No CI-skip directive text anywhere in a commit message.
- **Ruff.**
  - Run `ruff check` on every changed Python file.
  - A PostToolUse ruff hook strips imports that look unused between separate edits to the same `.py` file. When one change adds an import and its use, make both in ONE edit, or write the whole file.
- **Workflow hygiene.**
  - Pin actions by SHA, reusing the repo's pins:
    - `actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10`
    - `astral-sh/setup-uv@caf0cab7a618c569241d31dcd442f54681755d39`
    - `actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405`
    - `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a`
    - `actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093`
  - Never interpolate `${{ }}` inside a `run:` script; pass values through `env:`. zizmor rejects template injection.
  - No duplicate YAML keys; `scripts/test_workflow_yaml.py` rejects them.
  - The workflow sets `ARROW_DEFAULT_MEMORY_POOL: system` and `_RJEM_MALLOC_CONF: "dirty_decay_ms:1000,muzzy_decay_ms:0"` at `env:` level.

**Environment**
- **Worktree.** `D:/Temp/gm-matrix`, branch `feat/fs-cut-rule-matrix`. It is created in Task 1 from `feat/fs-cut-rule-router` (PR #2942 head `065d67c8c`) plus a cherry-pick of #2940 (`5a2daa381`).
- **Test command.** Run from `D:/Temp/gm-matrix` in Git Bash. The repo root must be the working directory so `scripts.` imports resolve. Re-define the variables in every Bash call:
  ```bash
  PY=D:/show_case/goldenmatch/.venv/Scripts/python.exe
  PP=$(ls -d packages/python/*/ | sed 's#/$##' | sed "s#^#D:/Temp/gm-matrix/#" | paste -sd';')
  PYTHONPATH="D:/Temp/gm-matrix;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider <test files>
  ```
- **Stray outputs.** Some pre-existing goldenmatch tests write timestamped `*_clusters.csv` / `*_lineage.json` into the working directory. Delete any that appear in the worktree root; they are gitignored.
- **GitHub auth.** `export GH_TOKEN=$(gh auth token -u benzsevern)`. Never run `gh auth switch`.

---

### Task 1: Worktree and a `corruption` knob on the synthetic generator

**Files:**
- Modify: `scripts/bench_er_headtohead/generate_fixture.py` (`generate` signature and body, `main`)
- Create: `scripts/bench_er_headtohead/test_generate_fixture_corruption.py`

**Interfaces:**
- **Produces:**
  - `generate(rows, dupe_rate, out, truth, seed, batch, shape="person", corruption=1.0) -> dict`. `corruption` multiplies every duplicate-noise probability and is capped at 1.0 per probability; `0.0` means duplicates carry no name/title noise and no nulls.
  - A CLI flag `--corruption FLOAT`, default `1.0`.

- [ ] **Step 1: Create the worktree and cherry-pick #2940**

```bash
cd D:/Temp/gm-route
export GH_TOKEN=$(gh auth token -u benzsevern)
git fetch -q origin main
git worktree add -b feat/fs-cut-rule-matrix D:/Temp/gm-matrix feat/fs-cut-rule-router
cd D:/Temp/gm-matrix
git cherry-pick 5a2daa381
git log --oneline -3
```

Expected: the cherry-pick applies cleanly (verified with `git merge-tree` while planning), and HEAD is `refactor(bench): read the Leipzig CSVs with pyarrow instead of pandas (#2940)` on top of `065d67c8c`.

- [ ] **Step 2: Write the test file with the digest helper**

Create `scripts/bench_er_headtohead/test_generate_fixture_corruption.py`:

```python
"""generate_fixture's `corruption` knob: default output unchanged, noise scales with it."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent

#: sha256 of generate(rows=600, dupe_rate=0.3, seed=42) per shape, captured from the
#: generator BEFORE the corruption knob existed (see `--print-digests` below).
EXPECTED_DIGESTS: dict[str, str] = {
    "person": "PASTE_FROM_STEP_3",
    "biblio": "PASTE_FROM_STEP_3",
    "product": "PASTE_FROM_STEP_3",
}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def fixture_digest(gen, shape: str, tmp: Path, **kwargs) -> str:
    tmp.mkdir(parents=True, exist_ok=True)
    out, truth = tmp / f"{shape}.parquet", tmp / f"{shape}.truth.parquet"
    gen.generate(
        rows=600, dupe_rate=0.3, out=out, truth=truth, seed=42, batch=1_000_000,
        shape=shape, **kwargs,
    )
    h = hashlib.sha256()
    for path in (out, truth):
        h.update(json.dumps(pq.read_table(path).to_pydict(), sort_keys=True, default=str).encode())
    return h.hexdigest()


def _duplicate_name_noise(gen, tmp: Path, corruption: float) -> float:
    """Share of duplicate person rows whose (first_name, surname) differs from their
    cluster's canonical row (the first row written for that cluster)."""
    tmp.mkdir(parents=True, exist_ok=True)
    out, truth = tmp / "p.parquet", tmp / "p.truth.parquet"
    gen.generate(
        rows=4000, dupe_rate=0.4, out=out, truth=truth, seed=7, batch=1_000_000,
        shape="person", corruption=corruption,
    )
    rec = pq.read_table(out).to_pydict()
    clusters = pq.read_table(truth).column("cluster_id").to_pylist()
    canonical: dict[int, tuple[str, str]] = {}
    dups = differ = 0
    for cid, first, sur in zip(clusters, rec["first_name"], rec["surname"]):
        if cid not in canonical:
            canonical[cid] = (first, sur)
            continue
        dups += 1
        differ += (first, sur) != canonical[cid]
    assert dups > 0
    return differ / dups


def test_default_corruption_is_byte_identical_to_the_pre_knob_generator(tmp_path):
    gen = _load("generate_fixture")
    for shape, expected in EXPECTED_DIGESTS.items():
        assert fixture_digest(gen, shape, tmp_path / shape) == expected
        assert fixture_digest(gen, shape, tmp_path / f"{shape}-explicit", corruption=1.0) == expected


def test_corruption_scales_duplicate_name_noise(tmp_path):
    """KNOWN-POSITIVE: zero corruption leaves duplicate names untouched; more corruption
    changes more of them."""
    gen = _load("generate_fixture")
    none = _duplicate_name_noise(gen, tmp_path / "none", 0.0)
    low = _duplicate_name_noise(gen, tmp_path / "low", 0.5)
    high = _duplicate_name_noise(gen, tmp_path / "high", 2.0)
    assert none == 0.0
    assert 0.0 < low < high


def test_negative_corruption_is_rejected(tmp_path):
    import pytest

    gen = _load("generate_fixture")
    with pytest.raises(ValueError, match="corruption"):
        gen.generate(
            rows=10, dupe_rate=0.2, out=tmp_path / "o.parquet", truth=tmp_path / "t.parquet",
            seed=1, batch=1_000_000, corruption=-0.1,
        )


def test_cli_accepts_corruption(tmp_path, monkeypatch):
    gen = _load("generate_fixture")
    out, truth = tmp_path / "o.parquet", tmp_path / "t.parquet"
    monkeypatch.setattr(
        sys, "argv",
        ["generate_fixture.py", "--rows", "50", "--out", str(out),
         "--ground-truth", str(truth), "--corruption", "0"],
    )
    gen.main()
    assert pq.read_table(out).num_rows == 50


if __name__ == "__main__" and "--print-digests" in sys.argv:
    import tempfile

    _gen = _load("generate_fixture")
    with tempfile.TemporaryDirectory() as _td:
        for _shape in ("person", "biblio", "product"):
            print(f'    "{_shape}": "{fixture_digest(_gen, _shape, Path(_td) / _shape)}",')
```

- [ ] **Step 3: Capture the digests from the UNMODIFIED generator and paste them in**

```bash
cd D:/Temp/gm-matrix
PY=D:/show_case/goldenmatch/.venv/Scripts/python.exe
PP=$(ls -d packages/python/*/ | sed 's#/$##' | sed "s#^#D:/Temp/gm-matrix/#" | paste -sd';')
PYTHONPATH="D:/Temp/gm-matrix;$PP" PYTHONIOENCODING=utf-8 $PY scripts/bench_er_headtohead/test_generate_fixture_corruption.py --print-digests
```

Replace the three `"PASTE_FROM_STEP_3"` values in `EXPECTED_DIGESTS` with the printed hex digests. Do this before touching `generate_fixture.py`: the digests are the proof that the default output does not move.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `PYTHONPATH="D:/Temp/gm-matrix;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/bench_er_headtohead/test_generate_fixture_corruption.py`

Expected:
- FAIL: `test_default_corruption_is_byte_identical...`, `test_corruption_scales...` and `test_negative_corruption...` fail with `TypeError: generate() got an unexpected keyword argument 'corruption'`. In the byte-identical test, the default-call assertion passes before the explicit `corruption=1.0` call raises.
- FAIL: `test_cli_accepts_corruption` fails with an argparse `unrecognized arguments: --corruption` SystemExit.

- [ ] **Step 5: Add the knob**

In `scripts/bench_er_headtohead/generate_fixture.py`:

1. Change the `generate` signature and add the guard and helper at the top of its body, directly after the `if shape not in (...)` check:

```python
def generate(
    rows: int,
    dupe_rate: float,
    out: Path,
    truth: Path,
    seed: int,
    batch: int,
    shape: str = "person",
    corruption: float = 1.0,
) -> dict:
    if shape not in ("person", "biblio", "product"):
        raise ValueError(f"unknown shape: {shape!r}")
    if corruption < 0:
        raise ValueError(f"corruption must be >= 0, got {corruption!r}")

    def noise(base: float) -> float:
        # Scale one duplicate-noise probability. corruption=1.0 leaves every
        # threshold, the RNG draw sequence, and so every written byte unchanged.
        return min(1.0, base * corruption)
```

2. Replace these literal probabilities inside `generate` with `noise(...)` of the same literal:

| Shape | Replace | With |
|---|---|---|
| person | `is_dup & (r < 0.40)` (first name) | `is_dup & (r < noise(0.40))` |
| person | `is_dup & (r < 0.50)` (surname) | `is_dup & (r < noise(0.50))` |
| person | `is_dup & (rng.random(total) < 0.20)` (city null) | `is_dup & (rng.random(total) < noise(0.20))` |
| person | `is_dup & (rng.random(total) < 0.05)` (dob null, then postcode null; two sites) | `is_dup & (rng.random(total) < noise(0.05))` |
| biblio | `is_dup & (r < 0.30)` (title word) | `is_dup & (r < noise(0.30))` |
| biblio | `is_dup & (rng.random(total) < 0.50)` (author reorder) | `is_dup & (rng.random(total) < noise(0.50))` |
| biblio | `is_dup & (rng.random(total) < 0.05)` (year null) | `is_dup & (rng.random(total) < noise(0.05))` |
| product | `is_dup & (r < 0.30)` (title word) | `is_dup & (r < noise(0.30))` |
| product | `is_dup & (rng.random(total) < 0.40)` (price perturbation) | `is_dup & (rng.random(total) < noise(0.40))` |
| product | `is_dup & (rng.random(total) < 0.05)` (price null) | `is_dup & (rng.random(total) < noise(0.05))` |

Leave the cluster-size probabilities (`p_dup`, `size_probs`) and every `rng` call itself untouched.

3. In `main()`, add the flag after `--shape` and pass it through:

```python
    ap.add_argument(
        "--corruption",
        type=float,
        default=1.0,
        help="multiplier on every duplicate-noise probability (typos, reorders, nulls); "
        "1.0 is the historical fixture, 0 gives noise-free duplicates",
    )
```

```python
    meta = generate(
        args.rows, args.dupe_rate, args.out, args.ground_truth, args.seed, args.batch, args.shape,
        corruption=args.corruption,
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `PYTHONPATH="D:/Temp/gm-matrix;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/bench_er_headtohead/test_generate_fixture_corruption.py scripts/bench_er_headtohead/test_scale_envelope.py`

Expected: PASS. A digest mismatch means a threshold changed value or an RNG call moved; fix the edit, never the digest.

- [ ] **Step 7: Lint and commit**

```bash
$PY -m ruff check scripts/bench_er_headtohead/generate_fixture.py scripts/bench_er_headtohead/test_generate_fixture_corruption.py
git add scripts/bench_er_headtohead/generate_fixture.py scripts/bench_er_headtohead/test_generate_fixture_corruption.py
git commit -m "feat(bench): corruption knob on the synthetic fixture generator (default output unchanged)"
```

---

### Task 2: Held-out dataset loaders

**Files:**
- Modify: `scripts/bench_er_headtohead/datasets.py`
  - extend `_synthetic` with keyword-only defaults;
  - add the loaders directly before `_LOADERS`;
  - register them in `_LOADERS`.
- Create: `scripts/bench_er_headtohead/test_heldout_loaders.py`

**Interfaces:**
- **Consumes:**
  - `generate(..., corruption=...)` (Task 1);
  - the existing `_read_csv_lossy`, `_with_source_ids`, `_two_source_leipzig`, `_cluster_ids_from_pairs`, `_rename_columns`, `_synthetic_rows`, `DatasetUnavailable` and `DATASETS_DIR`.
- **Produces:**
  - `_synthetic(shape, rows, *, dupe_rate=0.20, seed=42, corruption=1.0)`;
  - `_abt_buy()`;
  - `_magellan(subdir)`, with `_walmart_amazon()`, `_itunes_amazon()` and `_fodors_zagats()`;
  - `_febrl_raw(name)`, with `_febrl1()` and `_febrl2()`;
  - `_HELDOUT_SYNTH_SEED = 1009`;
  - `_SYNTH_HELDOUT: dict[str, tuple[str, float, float]]`;
  - `_synthetic_heldout(shape, corruption, dupe_rate)`;
  - `_LOADERS` keys `abt_buy`, `walmart_amazon`, `itunes_amazon`, `fodors_zagats`, `febrl1`, `febrl2`, and the eight `synth_{person,biblio}_c{05,20}_d{10,40}` names.
- **Data paths:** each loader reads under `DATASETS_DIR`:
  - `Abt-Buy/{Abt.csv,Buy.csv,abt_buy_perfectMapping.csv}`;
  - `Magellan/<Name>/{tableA,tableB,train,valid,test}.csv`;
  - `FEBRL/{dataset1,dataset2}.csv`.

- [ ] **Step 1: Write the failing tests**

Create `scripts/bench_er_headtohead/test_heldout_loaders.py`:

```python
"""Held-out loaders for FS link-cut routing P3 (pyarrow only, fetched data)."""
from __future__ import annotations

import inspect

import pytest

from scripts.bench_er_headtohead import datasets as D

HELDOUT_NAMES = (
    "abt_buy", "walmart_amazon", "itunes_amazon", "fodors_zagats", "febrl1", "febrl2",
    "synth_person_c05_d10", "synth_person_c05_d40", "synth_person_c20_d10", "synth_person_c20_d40",
    "synth_biblio_c05_d10", "synth_biblio_c05_d40", "synth_biblio_c20_d10", "synth_biblio_c20_d40",
)


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _cluster_of(truth) -> dict:
    return dict(zip(truth.column("record_id").to_pylist(), truth.column("cluster_id").to_pylist()))


def test_every_heldout_name_is_registered():
    missing = [n for n in HELDOUT_NAMES if n not in D._LOADERS]
    assert not missing, missing


def test_abt_buy_unions_both_sources_and_links_the_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    _write(tmp_path / "Abt-Buy" / "Abt.csv", "id,name,description,price\n1,sony tv,big tv,$10\n2,lg radio,small,$5\n")
    _write(tmp_path / "Abt-Buy" / "Buy.csv",
           "id,name,description,manufacturer,price\n7,Sony TV 40,tv,Sony,10\n8,Bose,spk,Bose,99\n")
    _write(tmp_path / "Abt-Buy" / "abt_buy_perfectMapping.csv", "idAbt,idBuy\n1,7\n")
    records, truth = D.load_dataset("abt_buy")
    assert records.column("record_id").to_pylist() == ["abt:1", "abt:2", "buy:7", "buy:8"]
    cid = _cluster_of(truth)
    assert cid["abt:1"] == cid["buy:7"]
    assert len({cid["abt:1"], cid["abt:2"], cid["buy:8"]}) == 3


def test_magellan_links_only_label_one_pairs_across_all_splits(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    base = tmp_path / "Magellan" / "Walmart-Amazon"
    _write(base / "tableA.csv", "id,title\n0,apple ipod\n1,bose speaker\n")
    _write(base / "tableB.csv", "id,title\n0,ipod apple 8gb\n1,sony tv\n")
    _write(base / "train.csv", "ltable_id,rtable_id,label\n1,1,0\n")
    _write(base / "valid.csv", "ltable_id,rtable_id,label\n")
    _write(base / "test.csv", "ltable_id,rtable_id,label\n0,0,1\n")
    records, truth = D.load_dataset("walmart_amazon")
    assert records.column("record_id").to_pylist() == ["a:0", "a:1", "b:0", "b:1"]
    cid = _cluster_of(truth)
    assert cid["a:0"] == cid["b:0"]
    assert cid["a:1"] != cid["b:1"]


def test_febrl_raw_trims_the_space_after_commas_and_groups_by_entity(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    _write(tmp_path / "FEBRL" / "dataset1.csv",
           "rec_id, given_name, surname\nrec-1-org, ann, lee\nrec-1-dup-0, anne, lee\nrec-2-org, bob, ray\n")
    records, truth = D.load_dataset("febrl1")
    assert records.column_names == ["record_id", "given_name", "surname"]
    assert records.column("given_name").to_pylist() == ["ann", "anne", "bob"]
    cid = _cluster_of(truth)
    assert cid["rec-1-org"] == cid["rec-1-dup-0"] != cid["rec-2-org"]


def test_febrl_raw_rejects_an_unexpected_record_id(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    _write(tmp_path / "FEBRL" / "dataset2.csv", "rec_id, surname\nperson-9, lee\n")
    with pytest.raises(ValueError, match="rec_id"):
        D.load_dataset("febrl2")


@pytest.mark.parametrize("name", ["abt_buy", "itunes_amazon", "fodors_zagats", "febrl1"])
def test_absent_data_is_unavailable_not_a_crash(tmp_path, monkeypatch, name):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    with pytest.raises(D.DatasetUnavailable):
        D.load_dataset(name)


def test_synthetic_heldout_variant_uses_its_own_seed_and_knobs(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_BENCH_SYNTHETIC_ROWS", "300")
    assert D._SYNTH_HELDOUT["synth_biblio_c20_d40"] == ("biblio", 2.0, 0.40)
    records, truth = D.load_dataset("synth_person_c20_d40")
    assert records.num_rows == 300 and truth.num_rows == 300
    assert len(set(truth.column("cluster_id").to_pylist())) < 300


def test_design_synthetic_defaults_are_unchanged():
    params = inspect.signature(D._synthetic).parameters
    assert params["dupe_rate"].default == 0.20
    assert params["seed"].default == 42
    assert params["corruption"].default == 1.0
    assert D._HELDOUT_SYNTH_SEED != params["seed"].default
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH="D:/Temp/gm-matrix;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/bench_er_headtohead/test_heldout_loaders.py`

Expected: FAIL. `test_every_heldout_name_is_registered` lists all 14 names. The load tests raise `KeyError: unknown dataset 'abt_buy'` (and similar). The signature test raises `KeyError: 'dupe_rate'`.

- [ ] **Step 3: Extend `_synthetic`**

Replace the `def _synthetic(shape: str, rows: int) -> tuple[pa.Table, pa.Table]:` line with:

```python
def _synthetic(
    shape: str,
    rows: int,
    *,
    dupe_rate: float = 0.20,
    seed: int = 42,
    corruption: float = 1.0,
) -> tuple[pa.Table, pa.Table]:
```

In its `generate(...)` call, replace `dupe_rate=0.20,` with `dupe_rate=dupe_rate,` and `seed=42,` with `seed=seed,`, and add `corruption=corruption,` after `shape=shape,`. The existing `_synthetic_person` / `_synthetic_biblio` calls keep their current output.

- [ ] **Step 4: Add the loaders**

Directly before `_LOADERS = {`, add:

```python
# ── Held-out corpus for FS link-cut routing (spec 2026-09-11, P3) ─────────────
# Never used to write or tune a routing row. Data is fetched at run time (see
# .github/workflows/fs-cut-rule-matrix.yml) and never committed.


def _abt_buy() -> tuple[pa.Table, pa.Table]:
    """Leipzig Abt-Buy product ER (CC-BY: credit the Database Group Leipzig and
    Köpcke, Thor & Rahm, VLDB 2010). Buy's extra ``manufacturer`` column is kept,
    null on Abt rows."""
    return _two_source_leipzig(
        "Abt-Buy", "Abt.csv", "Buy.csv", "abt_buy_perfectMapping.csv",
        ("idAbt", "idBuy"), "abt", "buy",
    )


_MAGELLAN_SPLITS = ("train.csv", "valid.csv", "test.csv")


def _magellan(subdir: str) -> tuple[pa.Table, pa.Table]:
    """A DeepMatcher/Magellan structured benchmark (cite-only: fetched at run time,
    never committed; Konda et al. 2016, Mudgal et al. 2018).

    Records are tableA + tableB with ``a:<id>`` / ``b:<id>`` ids. Truth links the
    ``label == 1`` pairs of the train/valid/test candidate splits, so a true match
    DeepMatcher's own blocking never proposed counts as a non-match -- identically
    for every arm a sweep compares."""
    base = DATASETS_DIR / "Magellan" / subdir
    paths = [base / "tableA.csv", base / "tableB.csv", *(base / s for s in _MAGELLAN_SPLITS)]
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise DatasetUnavailable(
            f"{subdir} Magellan CSVs not found under {base}; missing: {[p.name for p in missing]}"
        )
    records = pa.concat_tables(
        [
            _with_source_ids(_read_csv_lossy(paths[0]), "a"),
            _with_source_ids(_read_csv_lossy(paths[1]), "b"),
        ],
        promote_options="default",
    )
    pairs: list[tuple[Hashable, Hashable]] = []
    for split in paths[2:]:
        table = _read_csv_lossy(split)
        for left, right, label in zip(
            table.column("ltable_id").to_pylist(),
            table.column("rtable_id").to_pylist(),
            table.column("label").to_pylist(),
        ):
            if label.strip() == "1":
                pairs.append((f"a:{left}", f"b:{right}"))
    all_ids = records.column("record_id").to_pylist()
    cmap = _cluster_ids_from_pairs(all_ids, pairs)
    truth = pa.table({"record_id": all_ids, "cluster_id": [cmap[r] for r in all_ids]})
    return records, truth


def _walmart_amazon() -> tuple[pa.Table, pa.Table]:
    """Magellan Walmart-Amazon: electronics products, ~24.6k records."""
    return _magellan("Walmart-Amazon")


def _itunes_amazon() -> tuple[pa.Table, pa.Table]:
    """Magellan iTunes-Amazon: music tracks, ~62.8k records, not saturated."""
    return _magellan("iTunes-Amazon")


def _fodors_zagats() -> tuple[pa.Table, pa.Table]:
    """Magellan Fodors-Zagats: restaurants, 946 records, near-saturated (F1 ~1.0)."""
    return _magellan("Fodors-Zagats")


def _febrl_raw(name: str) -> tuple[pa.Table, pa.Table]:
    """FEBRL ``dataset1`` / ``dataset2`` from the raw CSVs recordlinkage ships (ANU
    open-source licence, MPL-1.1 derived; Christen 2008), read without pandas.

    Headers and values carry a space after each comma, so both are trimmed.
    ``rec-<N>-org`` and ``rec-<N>-dup-<k>`` are the same entity ``N``."""
    path = DATASETS_DIR / "FEBRL" / f"{name}.csv"
    if not path.exists():
        raise DatasetUnavailable(f"FEBRL {name} not found at {path}")
    raw = _read_csv_lossy(path)
    records = _rename_columns(
        pa.table({col.strip(): pc.utf8_trim_whitespace(raw.column(col)) for col in raw.column_names}),
        {"rec_id": "record_id"},
    )
    ids = records.column("record_id").to_pylist()
    entity: list[int] = []
    for rid in ids:
        parts = rid.split("-")
        if len(parts) < 3 or parts[0] != "rec" or not parts[1].isdigit():
            raise ValueError(f"FEBRL {name}: unexpected rec_id {rid!r}")
        entity.append(int(parts[1]))
    truth = pa.table({"record_id": ids, "cluster_id": entity})
    return records, truth


def _febrl1() -> tuple[pa.Table, pa.Table]:
    """FEBRL dataset1: 1,000 records, 500 originals with one duplicate each."""
    return _febrl_raw("dataset1")


def _febrl2() -> tuple[pa.Table, pa.Table]:
    """FEBRL dataset2: 5,000 records, 4,000 originals and 1,000 duplicates."""
    return _febrl_raw("dataset2")


#: Seed for the held-out synthetic variants; the design panel's synthetic sets use 42.
_HELDOUT_SYNTH_SEED = 1009

#: name -> (shape, corruption, dupe_rate). c05 = corruption 0.5, c20 = 2.0;
#: d10 = dupe_rate 0.10, d40 = 0.40.
_SYNTH_HELDOUT: dict[str, tuple[str, float, float]] = {
    f"synth_{shape}_c{int(c * 10):02d}_d{int(round(d * 100)):02d}": (shape, c, d)
    for shape in ("person", "biblio")
    for c in (0.5, 2.0)
    for d in (0.10, 0.40)
}


def _synthetic_heldout(shape: str, corruption: float, dupe_rate: float) -> tuple[pa.Table, pa.Table]:
    """A held-out synthetic variant: a seed the design panel never used, duplicate
    noise scaled by ``corruption``, and ``dupe_rate`` duplicates."""
    return _synthetic(
        shape, _synthetic_rows(), dupe_rate=dupe_rate, seed=_HELDOUT_SYNTH_SEED, corruption=corruption
    )
```

- [ ] **Step 5: Register the loaders**

In `_LOADERS`, directly after the `"amazon_google": _amazon_google,` entry, add:

```python
    # Held-out corpus for FS link-cut routing (P3): never used to write a row.
    "abt_buy": _abt_buy,
    "walmart_amazon": _walmart_amazon,
    "itunes_amazon": _itunes_amazon,
    "fodors_zagats": _fodors_zagats,
    "febrl1": _febrl1,
    "febrl2": _febrl2,
    **{
        name: (lambda spec=spec: _synthetic_heldout(*spec))
        for name, spec in _SYNTH_HELDOUT.items()
    },
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `PYTHONPATH="D:/Temp/gm-matrix;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/bench_er_headtohead/test_heldout_loaders.py scripts/autoconfig_quality/tests/test_datasets.py`

Expected: PASS. `test_febrl3_loader_shape_or_skip` may skip without recordlinkage.

- [ ] **Step 7: Lint and commit**

```bash
$PY -m ruff check scripts/bench_er_headtohead/datasets.py scripts/bench_er_headtohead/test_heldout_loaders.py
git add scripts/bench_er_headtohead/datasets.py scripts/bench_er_headtohead/test_heldout_loaders.py
git commit -m "feat(bench): held-out loaders for link-cut routing (Abt-Buy, Magellan, FEBRL 1/2, synthetic variants)"
```

---

### Task 3: Frozen corpus

**Files:**
- Create: `scripts/autoconfig_quality/corpus.py`
- Create: `scripts/autoconfig_quality/tests/test_corpus.py`

**Interfaces:**
- **Produces:**
  - `DESIGN: tuple[str, ...]`, `HOLDOUT: tuple[str, ...]` and `LOCAL_ONLY: frozenset[str]`.
  - `corpus_of(name) -> str`, returning `"design"`, `"holdout"` or `"unlisted"`.
  - `select(corpus) -> tuple[str, ...]`, where `corpus` is `"design"`, `"holdout"` or `"all"`.
  - `ci_datasets(corpus) -> list[str]`.
  - A CLI, `python -m scripts.autoconfig_quality.corpus --list CORPUS [--ci]`, that prints a JSON array.

- [ ] **Step 1: Write the failing tests**

Create `scripts/autoconfig_quality/tests/test_corpus.py`:

```python
"""The FS link-cut routing corpus is frozen: rows are written from DESIGN and must
also hold on HOLDOUT, which is why HOLDOUT is pinned verbatim here."""
from __future__ import annotations

import json

import pytest

from scripts.autoconfig_quality import corpus as C


def test_design_set_is_pinned():
    assert C.DESIGN == (
        "person", "household_hardneg", "cotenant_hardneg", "febrl3", "febrl4",
        "ncvr_synthetic", "ncvr_real", "historical_50k", "dblp_acm", "dblp_scholar",
        "amazon_google",
    )


def test_holdout_set_is_frozen():
    """Changing this list after any routing row exists means re-running the full
    gate on both sets (spec: Corpus and harness)."""
    assert C.HOLDOUT == (
        "abt_buy", "walmart_amazon", "itunes_amazon", "fodors_zagats", "febrl1", "febrl2",
        "synth_person_c05_d10", "synth_person_c05_d40", "synth_person_c20_d10", "synth_person_c20_d40",
        "synth_biblio_c05_d10", "synth_biblio_c05_d40", "synth_biblio_c20_d10", "synth_biblio_c20_d40",
    )


def test_sets_are_disjoint_and_duplicate_free():
    assert not set(C.DESIGN) & set(C.HOLDOUT)
    assert len(set(C.DESIGN)) == len(C.DESIGN)
    assert len(set(C.HOLDOUT)) == len(C.HOLDOUT)


def test_corpus_of_and_select():
    assert C.corpus_of("febrl3") == "design"
    assert C.corpus_of("abt_buy") == "holdout"
    assert C.corpus_of("synthetic_person") == "unlisted"
    assert C.select("all") == C.DESIGN + C.HOLDOUT
    with pytest.raises(KeyError):
        C.select("everything")


def test_ci_never_provisions_real_voter_pii():
    assert "ncvr_real" in C.LOCAL_ONLY
    assert "ncvr_real" not in C.ci_datasets("design")
    assert C.ci_datasets("holdout") == list(C.HOLDOUT)


def test_cli_lists_ci_datasets_as_json(capsys):
    assert C.main(["--list", "design", "--ci"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed == C.ci_datasets("design")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH="D:/Temp/gm-matrix;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/autoconfig_quality/tests/test_corpus.py`

Expected: FAIL with `ImportError: cannot import name 'corpus' from 'scripts.autoconfig_quality'`.

- [ ] **Step 3: Write the module**

Create `scripts/autoconfig_quality/corpus.py`:

```python
"""Frozen corpus for FS link-cut routing (spec 2026-09-11-fs-cut-rule-routing-design, P3).

DESIGN is the labelled part of the auto-config quality registry plus the
`ab_lever` panel. The two unlabelled blocking-shape anchors (sparse_zip,
shared_email) cannot be scored and are left out. Routing rows may be written from
these datasets' results.

HOLDOUT is never used to write or tune a row. A row ships only if it is also never
below the default on every HOLDOUT dataset. Changing HOLDOUT after any row exists
means re-running the full gate, so tests/test_corpus.py pins it verbatim.
"""
from __future__ import annotations

import argparse
import json

DESIGN: tuple[str, ...] = (
    "person",
    "household_hardneg",
    "cotenant_hardneg",
    "febrl3",
    "febrl4",
    "ncvr_synthetic",
    "ncvr_real",
    "historical_50k",
    "dblp_acm",
    "dblp_scholar",
    "amazon_google",
)

HOLDOUT: tuple[str, ...] = (
    "abt_buy",
    "walmart_amazon",
    "itunes_amazon",
    "fodors_zagats",
    "febrl1",
    "febrl2",
    "synth_person_c05_d10",
    "synth_person_c05_d40",
    "synth_person_c20_d10",
    "synth_person_c20_d40",
    "synth_biblio_c05_d10",
    "synth_biblio_c05_d40",
    "synth_biblio_c20_d10",
    "synth_biblio_c20_d40",
)

#: Never provisioned in CI: real voter PII stays on the laptop.
LOCAL_ONLY: frozenset[str] = frozenset({"ncvr_real"})

_SETS = {"design": DESIGN, "holdout": HOLDOUT, "all": DESIGN + HOLDOUT}


def corpus_of(name: str) -> str:
    """``"design"``, ``"holdout"``, or ``"unlisted"`` for an ad-hoc dataset name."""
    if name in DESIGN:
        return "design"
    if name in HOLDOUT:
        return "holdout"
    return "unlisted"


def select(corpus: str) -> tuple[str, ...]:
    """The dataset names of ``corpus`` (``design``, ``holdout`` or ``all``)."""
    return _SETS[corpus]


def ci_datasets(corpus: str) -> list[str]:
    """``select(corpus)`` without the local-only datasets."""
    return [name for name in select(corpus) if name not in LOCAL_ONLY]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="List the FS link-cut routing corpus.")
    ap.add_argument("--list", required=True, choices=sorted(_SETS), help="corpus to list")
    ap.add_argument("--ci", action="store_true", help="drop local-only datasets")
    args = ap.parse_args(argv)
    names = ci_datasets(args.list) if args.ci else list(select(args.list))
    print(json.dumps(names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH="D:/Temp/gm-matrix;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/autoconfig_quality/tests/test_corpus.py`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
$PY -m ruff check scripts/autoconfig_quality/corpus.py scripts/autoconfig_quality/tests/test_corpus.py
git add scripts/autoconfig_quality/corpus.py scripts/autoconfig_quality/tests/test_corpus.py
git commit -m "feat(quality): frozen design and held-out corpus for FS link-cut routing"
```

---

### Task 4: The per-rule sweep

**Files:**
- Create: `scripts/autoconfig_quality/rule_sweep.py`
- Create: `scripts/autoconfig_quality/tests/test_rule_sweep.py`

**Interfaces:**
- **Consumes:**
  - `goldenmatch.core.fs_cut_rules.CUT_RULES`;
  - `MatchkeyConfig.link_cut_rule` and `.model_path`;
  - `DedupeResult.stats["fs_link_thresholds"][mk]`, with keys `link_threshold`, `source`, `cut_rule`, `cut_reason` and `cut_diagnostics`;
  - `scripts.autoconfig_quality.datasets` (`_<name>` loaders, `_from_headtohead`);
  - `scripts.bench_er_headtohead.datasets._LOADERS`;
  - `scripts.bench_er_headtohead.ab_lever._partition_fingerprint`;
  - `scripts.autoconfig_quality.scorecard` (`build_scorecard`, `gather_meta`, `dumps`);
  - `scripts.autoconfig_quality.corpus` (Task 3);
  - `goldenmatch.core.autoconfig.auto_configure_probabilistic_df`;
  - `goldenmatch.core.evaluate.evaluate_clusters`.
- **Produces:**
  - Constants: `BASELINE_ARM = "default_loaded"`, `ARMS: tuple[str, ...]`, `TOLERANCE = 0.01`, `CUT_ENV_VARS`.
  - `cut_env_overrides(environ=None) -> dict[str, str]`.
  - `resolve_loader(name) -> Callable[[], tuple[pl.DataFrame, set] | None]`.
  - `pin_rule(cfg, rule: str | None, model_dir: Path) -> GoldenMatchConfig`.
  - `arm_record(arm, summary, stats, partition) -> dict`.
  - `sweep_frame(df, gt, model_dir) -> dict`.
  - `sweep_dataset(name, work_dir) -> dict | None`.
  - `run(argv) -> int`.
  - `main(argv=None) -> int`.
- **Output file:** a scorecard JSON:
  - `{"meta": {..., "cut_env_overrides", "tolerance"}, "datasets": {name: record}}`;
  - `record` is `{"corpus", "rows", "gt_pairs", "arms": {arm: {"f1", "precision", "recall", "pairs", "digest", "matchkeys", "applied", "voided"}}, "cut_diagnostics", "models_saved", "model_reload_delta", "below_default"}`.

- [ ] **Step 1: Write the failing tests**

Create `scripts/autoconfig_quality/tests/test_rule_sweep.py`:

```python
"""Per-rule link-cut sweep (FS link-cut routing P3)."""
from __future__ import annotations

from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.fs_cut_rules import CUT_RULES

from scripts.autoconfig_quality import rule_sweep as S
from scripts.autoconfig_quality.corpus import DESIGN, HOLDOUT


def _cfg() -> GoldenMatchConfig:
    field = MatchkeyField(field="name", scorer="jaro_winkler")
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(name="fs_a", type="probabilistic", fields=[field]),
            MatchkeyConfig(name="fs_b", type="probabilistic", fields=[field]),
        ],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["name"])]),
    )


def _stats(**per_mk) -> dict:
    return {"fs_link_thresholds": per_mk}


def _entry(cut_rule=None, cut_reason=None) -> dict:
    return {"link_threshold": 0.6, "source": "evidence_rule", "cut_rule": cut_rule,
            "cut_reason": cut_reason, "cut_diagnostics": {"midpoint_bits": 2.0}}


_SUMMARY = {"f1": 0.9, "precision": 0.95, "recall": 0.85}


def test_arms_run_both_baselines_then_every_rule_once():
    assert S.ARMS == ("default", "default_loaded", *CUT_RULES)
    assert len(set(S.ARMS)) == len(S.ARMS)
    assert S.BASELINE_ARM == "default_loaded"


def test_pin_rule_pins_every_probabilistic_matchkey_without_mutating_the_input(tmp_path):
    cfg = _cfg()
    pinned = S.pin_rule(cfg, "evidence_9", tmp_path)
    got = {mk.name: (mk.link_cut_rule, mk.model_path) for mk in pinned.get_matchkeys()}
    assert got == {
        "fs_a": ("evidence_9", str(tmp_path / "fs_a.json")),
        "fs_b": ("evidence_9", str(tmp_path / "fs_b.json")),
    }
    assert all(mk.link_cut_rule is None and mk.model_path is None for mk in cfg.get_matchkeys())
    unset = S.pin_rule(cfg, None, tmp_path)
    assert all(mk.link_cut_rule is None for mk in unset.get_matchkeys())


def test_arm_record_applied_rule():
    rec = S.arm_record("evidence_9", _SUMMARY, _stats(fs=_entry("evidence_9", "pinned by link_cut_rule")), (4, "abc"))
    assert rec["applied"] is True and rec["voided"] is False
    assert (rec["f1"], rec["pairs"], rec["digest"]) == (0.9, 4, "abc")
    assert rec["matchkeys"]["fs"]["cut_rule"] == "evidence_9"


def test_arm_record_uncomputable_rule_is_not_applied_but_not_voided():
    reason = "otsu unavailable: needs a training histogram of more than 50 pairs; default rule"
    rec = S.arm_record("otsu", _SUMMARY, _stats(fs=_entry("prior_mid", reason)), (0, "x"))
    assert rec["applied"] is False and rec["voided"] is False


def test_arm_record_overridden_pin_is_voided():
    """KNOWN-POSITIVE for the pinned-arm assertion: an env var that switched on
    posterior scoring voids the arm instead of silently measuring the default."""
    reason = "link_cut_rule=evidence_9 not applied: posterior scoring"
    rec = S.arm_record("evidence_9", _SUMMARY, _stats(fs=_entry(None, reason)), (0, "x"))
    assert rec["applied"] is False and rec["voided"] is True


def test_arm_record_without_a_report_is_voided():
    assert S.arm_record("default", _SUMMARY, {}, (0, "x"))["voided"] is True
    assert S.arm_record("default", _SUMMARY, _stats(fs=_entry("prior_mid", "default rule")), (0, "x"))["voided"] is False


def test_cut_env_overrides_reports_only_non_empty_values():
    env = {"GOLDENMATCH_FS_EVIDENCE_CUT": "6", "GOLDENMATCH_FS_CALIBRATED": " ", "PATH": "x"}
    assert S.cut_env_overrides(env) == {"GOLDENMATCH_FS_EVIDENCE_CUT": "6"}


def test_every_corpus_name_resolves_to_a_loader():
    import pytest

    missing = []
    for name in DESIGN + HOLDOUT:
        try:
            assert callable(S.resolve_loader(name))
        except KeyError:
            missing.append(name)
    assert not missing, missing
    with pytest.raises(KeyError):
        S.resolve_loader("not_a_dataset")


def test_run_refuses_when_a_cut_env_var_is_set(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_EVIDENCE_CUT", "6")
    out = tmp_path / "card.json"
    assert S.run(["--datasets", "person", "--out", str(out)]) == 2
    assert not out.exists()


def test_sweep_frame_runs_every_arm_on_one_saved_model(tmp_path, monkeypatch):
    for var in S.CUT_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    from scripts.autoconfig_quality.anchors import gen_labeled

    df, gt = gen_labeled(n_entities=60, seed=7)
    out = S.sweep_frame(df, gt, tmp_path)

    assert set(out["arms"]) == set(S.ARMS)
    assert not [a for a, rec in out["arms"].items() if rec["voided"]]
    not_applied = [r for r in CUT_RULES if r != "otsu" and not out["arms"][r]["applied"]]
    assert not not_applied, not_applied
    assert out["models_saved"], "the default arm must save the EM model the other arms load"
    assert all(d and "midpoint_bits" in d for d in out["cut_diagnostics"].values())
    assert set(out["below_default"]) <= set(CUT_RULES)
    assert isinstance(out["model_reload_delta"], float)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH="D:/Temp/gm-matrix;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/autoconfig_quality/tests/test_rule_sweep.py`

Expected: FAIL with `ImportError: cannot import name 'rule_sweep' from 'scripts.autoconfig_quality'`.

- [ ] **Step 3: Write the module**

Create `scripts/autoconfig_quality/rule_sweep.py`:

```python
"""Per-rule link-cut matrix (spec 2026-09-11-fs-cut-rule-routing-design, P3).

For one dataset: auto-configure the probabilistic config once, then run the full
pipeline once per arm. Every probabilistic matchkey is pinned to one
``link_cut_rule`` and points at one saved EM model (``model_path``), so the arms
differ only in where the cut lands.

Arms, in run order:
  default         link_cut_rule unset; trains the EM model and saves it
  default_loaded  link_cut_rule unset; loads the saved model -- the baseline
                  every rule arm is compared against, on the same model
  <rule>          one arm per fs_cut_rules.CUT_RULES name, model loaded

Measurement only: nothing here chooses a rule.

Usage:
  python -m scripts.autoconfig_quality.rule_sweep --corpus all --ci --out cut-rules.json
  python -m scripts.autoconfig_quality.rule_sweep --datasets person --out person.json
"""
from __future__ import annotations

import os

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_DETERMINISTIC", "1")

import argparse  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
from collections.abc import Callable  # noqa: E402
from pathlib import Path  # noqa: E402

from goldenmatch.core.fs_cut_rules import CUT_RULES  # noqa: E402

BASELINE_ARM = "default_loaded"
ARMS: tuple[str, ...] = ("default", BASELINE_ARM, *CUT_RULES)

#: The spec's gate tolerance: a rule is below the default when F1 < default - 0.01.
TOLERANCE = 0.01

#: Env vars that move the cut or void a pin process-wide. A sweep run with any of
#: them set measures something other than the shipped default, so it refuses.
CUT_ENV_VARS = (
    "GOLDENMATCH_FS_LINEAR_CUT",
    "GOLDENMATCH_FS_CALIBRATED",
    "GOLDENMATCH_FS_EVIDENCE_CUT",
    "GOLDENMATCH_FS_CALIBRATE_THRESHOLD",
)


def cut_env_overrides(environ: dict[str, str] | None = None) -> dict[str, str]:
    """The ``CUT_ENV_VARS`` that are set to a non-blank value."""
    env = os.environ if environ is None else environ
    return {k: env[k] for k in CUT_ENV_VARS if (env.get(k) or "").strip()}


def resolve_loader(name: str) -> Callable:
    """Loader for a corpus name.

    The ``scripts.autoconfig_quality.datasets._<name>`` loader when one exists (the
    ``ab_lever`` panel convention), else the head-to-head loader via
    ``_from_headtohead``. An unknown name raises ``KeyError``, so a typo cannot
    silently shrink a run."""
    from scripts.autoconfig_quality import datasets as quality
    from scripts.bench_er_headtohead import datasets as headtohead

    fn = getattr(quality, f"_{name}", None)
    if callable(fn):
        return fn
    if name in headtohead._LOADERS:
        return lambda: quality._from_headtohead(name)
    raise KeyError(f"unknown corpus dataset {name!r}")


def pin_rule(cfg, rule: str | None, model_dir: Path):
    """Copy of ``cfg`` with every probabilistic matchkey pinned to ``rule`` (None
    leaves it unset) and pointed at one saved model per matchkey in ``model_dir``."""
    pinned = []
    for mk in cfg.get_matchkeys():
        if mk.type == "probabilistic":
            mk = mk.model_copy(
                update={"link_cut_rule": rule, "model_path": str(model_dir / f"{mk.name}.json")}
            )
        pinned.append(mk)
    return cfg.model_copy(update={"matchkeys": pinned, "match_settings": None})


def arm_record(arm: str, summary: dict, stats: dict | None, partition: tuple[int, str]) -> dict:
    """One arm's result from ``evaluate_clusters(...).summary()`` and the run's stats.

    ``applied``: every probabilistic matchkey's cut came from the pinned rule.

    ``voided``: the arm measured something other than what it claims, and the sweep
    fails. That happens when the run reported no FS cutoff at all, or when an
    earlier precedence step overrode a pin (``cut_reason`` starts ``link_cut_rule=``).

    A rule this model cannot compute is not applied but not voided: for example
    ``otsu`` on 50 or fewer training pairs, which falls back to the default rule. The
    fallback is itself the finding."""
    report = (stats or {}).get("fs_link_thresholds") or {}
    per_mk = {
        name: {key: entry.get(key) for key in ("link_threshold", "source", "cut_rule", "cut_reason")}
        for name, entry in sorted(report.items())
    }
    rule = None if arm in ("default", BASELINE_ARM) else arm
    voided = not per_mk or any(
        (entry["cut_reason"] or "").startswith("link_cut_rule=") for entry in per_mk.values()
    )
    applied = bool(per_mk) and (
        rule is None or all(entry["cut_rule"] == rule for entry in per_mk.values())
    )
    pairs, digest = partition
    return {
        "f1": summary["f1"],
        "precision": summary["precision"],
        "recall": summary["recall"],
        "pairs": pairs,
        "digest": digest,
        "matchkeys": per_mk,
        "applied": applied,
        "voided": voided,
    }


def sweep_frame(df, gt: set, model_dir: Path) -> dict:
    """Run every arm on one labelled frame. ``model_dir`` must start empty."""
    import goldenmatch
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    from goldenmatch.core.evaluate import evaluate_clusters

    from scripts.bench_er_headtohead.ab_lever import _partition_fingerprint

    base = auto_configure_probabilistic_df(df)
    arms: dict[str, dict] = {}
    diagnostics: dict[str, dict | None] = {}
    models_saved: list[str] = []
    for arm in ARMS:
        rule = None if arm in ("default", BASELINE_ARM) else arm
        result = goldenmatch.dedupe_df(df, config=pin_rule(base, rule, model_dir))
        summary = evaluate_clusters(result.clusters, gt).summary()
        arms[arm] = arm_record(arm, summary, result.stats, _partition_fingerprint(result.clusters))
        if arm == "default":
            models_saved = sorted(p.name for p in model_dir.glob("*.json"))
        if arm == BASELINE_ARM:
            report = (result.stats or {}).get("fs_link_thresholds") or {}
            diagnostics = {name: entry.get("cut_diagnostics") for name, entry in sorted(report.items())}
    baseline = arms[BASELINE_ARM]["f1"]
    return {
        "rows": df.height,
        "gt_pairs": len(gt),
        "arms": arms,
        "cut_diagnostics": diagnostics,
        "models_saved": models_saved,
        "model_reload_delta": float(baseline - arms["default"]["f1"]),
        "below_default": sorted(
            rule for rule in CUT_RULES
            if arms[rule]["applied"] and arms[rule]["f1"] < baseline - TOLERANCE
        ),
    }


def sweep_dataset(name: str, work_dir: Path) -> dict | None:
    """Sweep one corpus dataset, or None when it is unavailable or unlabelled."""
    from scripts.autoconfig_quality.corpus import corpus_of

    loaded = resolve_loader(name)()
    if loaded is None:
        return None
    df, gt = loaded
    if not gt:
        return None
    model_dir = work_dir / name
    model_dir.mkdir(parents=True, exist_ok=True)
    record = sweep_frame(df, gt, model_dir)
    record["corpus"] = corpus_of(name)
    return record


def run(argv: list[str]) -> int:
    from scripts.autoconfig_quality.corpus import ci_datasets, select
    from scripts.autoconfig_quality.scorecard import build_scorecard, dumps, gather_meta

    ap = argparse.ArgumentParser(description="Measure every FS link-cut rule per dataset.")
    ap.add_argument("--corpus", choices=["design", "holdout", "all"], default="all")
    ap.add_argument("--datasets", default="", help="comma list; overrides --corpus")
    ap.add_argument("--ci", action="store_true", help="drop local-only datasets from --corpus")
    ap.add_argument("--out", required=True, type=Path, help="scorecard JSON to write")
    ap.add_argument("--require-datasets", default="", help="comma list that MUST be measured")
    ap.add_argument(
        "--allow-cut-env", action="store_true",
        help=f"measure even with any of {', '.join(CUT_ENV_VARS)} set",
    )
    args = ap.parse_args(argv)

    overrides = cut_env_overrides()
    if overrides and not args.allow_cut_env:
        print(
            f"refusing to sweep: {sorted(overrides)} set; they move the cut or void a pin "
            "for every arm (pass --allow-cut-env to measure under them on purpose)",
            file=sys.stderr,
        )
        return 2
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"

    names = [d.strip() for d in args.datasets.split(",") if d.strip()] or (
        ci_datasets(args.corpus) if args.ci else list(select(args.corpus))
    )
    for name in names:
        resolve_loader(name)  # fail on a typo before any long run starts

    results: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="gm_cut_rules_") as td:
        for name in names:
            print(f"[cut-rules] {name}", file=sys.stderr)
            record = sweep_dataset(name, Path(td))
            if record is None:
                skipped[name] = "unavailable or unlabelled"
            else:
                results[name] = record

    native_version, git_sha = gather_meta()
    card = build_scorecard(results, native_version=native_version, git_sha=git_sha, skipped=skipped)
    card["meta"]["cut_env_overrides"] = overrides
    card["meta"]["tolerance"] = TOLERANCE
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(dumps(card) + "\n", encoding="utf-8")

    required = [d.strip() for d in args.require_datasets.split(",") if d.strip()]
    missing = [d for d in required if d not in results]
    voided = sorted(
        f"{name}:{arm}"
        for name, record in results.items()
        for arm, rec in record["arms"].items()
        if rec["voided"]
    )
    print(f"[cut-rules] measured {len(results)}, skipped {len(skipped)} -> {args.out}", file=sys.stderr)
    if not results:
        print("FAIL: 0 datasets measured; a sweep that measures nothing cannot pass", file=sys.stderr)
        return 1
    if missing:
        print(f"FAIL: required datasets not measured: {missing}", file=sys.stderr)
        return 1
    if voided:
        print(f"FAIL: voided arms (a pin was overridden or no FS cutoff reported): {voided}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH="D:/Temp/gm-matrix;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/autoconfig_quality/tests/test_rule_sweep.py`

Expected: PASS. Handle these two failures as findings, not by loosening the tests:
- **`models_saved` empty:** the probabilistic route that `dedupe_df` took did not save the model through `load_or_train_em`. Report it as BLOCKED with the route name; do not weaken the assertion.
- **A non-`otsu` rule not applied:** print that arm's `matchkeys` and report the `cut_reason`.

- [ ] **Step 5: Lint and commit**

```bash
$PY -m ruff check scripts/autoconfig_quality/rule_sweep.py scripts/autoconfig_quality/tests/test_rule_sweep.py
git add scripts/autoconfig_quality/rule_sweep.py scripts/autoconfig_quality/tests/test_rule_sweep.py
git commit -m "feat(quality): per-rule link-cut sweep on one saved EM model per dataset"
```

---

### Task 5: Merge per-dataset results and summarize

**Files:**
- Modify: `scripts/autoconfig_quality/rule_sweep.py` (add `merge_cards`, `best_rule`, `render_markdown`, `merge`; replace `main`)
- Modify: `scripts/autoconfig_quality/tests/test_rule_sweep.py` (append tests)

**Interfaces:**
- **Consumes:** the Task 4 scorecard shape, plus `build_scorecard`.
- **Produces:**
  - `merge_cards(cards: list[dict]) -> dict`
  - `best_rule(record: dict) -> tuple[str, float]`
  - `render_markdown(card: dict) -> str`
  - `merge(argv) -> int`
  - CLI: `python -m scripts.autoconfig_quality.rule_sweep merge FILE... --out merged.json --summary-md summary.md`

- [ ] **Step 1: Write the failing tests**

Append to `scripts/autoconfig_quality/tests/test_rule_sweep.py`:

```python
def _record(corpus: str, f1_by_arm: dict, applied: dict | None = None) -> dict:
    applied = applied or {}
    arms = {
        arm: {"f1": f1_by_arm.get(arm, 0.5), "precision": 0.0, "recall": 0.0, "pairs": 0,
              "digest": "d", "matchkeys": {}, "applied": applied.get(arm, True), "voided": False}
        for arm in S.ARMS
    }
    return {"corpus": corpus, "rows": 10, "gt_pairs": 3, "arms": arms, "cut_diagnostics": {},
            "models_saved": ["fs.json"], "model_reload_delta": 0.0, "below_default": ["midpoint"]}


def _card(sha: str, **datasets) -> dict:
    return {"meta": {"git_sha": sha, "native_version": "0.2.2", "datasets_run": sorted(datasets),
                     "datasets_skipped": {}, "cut_env_overrides": {}, "tolerance": 0.01},
            "datasets": datasets}


def test_best_rule_takes_the_highest_applied_rule_with_ties_in_rule_order():
    rec = _record("design", {"default_loaded": 0.8, "prior": 0.9, "evidence_9": 0.9, "otsu": 0.99},
                  applied={"otsu": False})
    assert S.best_rule(rec) == ("prior", 0.9)


def test_best_rule_falls_back_to_the_baseline_when_nothing_applied():
    rec = _record("design", {"default_loaded": 0.7}, applied={r: False for r in CUT_RULES})
    assert S.best_rule(rec) == ("default_loaded", 0.7)


def test_merge_cards_combines_datasets_from_one_commit():
    merged = S.merge_cards([
        _card("abc", febrl3=_record("design", {})),
        _card("abc", abt_buy=_record("holdout", {})),
    ])
    assert sorted(merged["datasets"]) == ["abt_buy", "febrl3"]
    assert merged["meta"]["git_sha"] == "abc"
    assert merged["meta"]["tolerance"] == 0.01


def test_merge_cards_refuses_mixed_commits_and_duplicates():
    import pytest

    with pytest.raises(ValueError, match="different commits"):
        S.merge_cards([_card("abc", a=_record("design", {})), _card("def", b=_record("design", {}))])
    with pytest.raises(ValueError, match="two sweep results"):
        S.merge_cards([_card("abc", a=_record("design", {})), _card("abc", a=_record("design", {}))])


def test_render_markdown_has_one_row_per_dataset_design_first():
    card = S.merge_cards([
        _card("abc", abt_buy=_record("holdout", {"default_loaded": 0.8, "evidence_12": 0.85})),
        _card("abc", febrl3=_record("design", {"default_loaded": 0.9, "prior_mid": 0.95})),
    ])
    lines = S.render_markdown(card).strip().splitlines()
    assert lines[0].startswith("| dataset | corpus | default F1 | best rule |")
    assert lines[2] == "| febrl3 | design | 0.9000 | prior_mid | 0.9500 | +0.0500 | midpoint |"
    assert lines[3].startswith("| abt_buy | holdout | 0.8000 | evidence_12 | 0.8500 |")


def test_merge_cli_writes_json_and_markdown(tmp_path):
    import json

    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(_card("abc", febrl3=_record("design", {}))))
    b.write_text(json.dumps(_card("abc", abt_buy=_record("holdout", {}))))
    out, md = tmp_path / "m.json", tmp_path / "m.md"
    assert S.main(["merge", str(a), str(b), "--out", str(out), "--summary-md", str(md)]) == 0
    assert sorted(json.loads(out.read_text())["datasets"]) == ["abt_buy", "febrl3"]
    assert "| febrl3 |" in md.read_text()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH="D:/Temp/gm-matrix;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/autoconfig_quality/tests/test_rule_sweep.py`

Expected: the 6 new tests FAIL with `AttributeError: module 'scripts.autoconfig_quality.rule_sweep' has no attribute 'best_rule'` (and the same for `merge_cards` and `render_markdown`). The CLI test fails because `run` rejects the `merge` argument.

- [ ] **Step 3: Add merge and summary**

In `rule_sweep.py`, directly before `def main(`, add:

```python
def merge_cards(cards: list[dict]) -> dict:
    """One scorecard from per-dataset sweep scorecards (the CI matrix writes one per job)."""
    from scripts.autoconfig_quality.scorecard import build_scorecard

    datasets: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    overrides: dict[str, str] = {}
    shas: set[str] = set()
    natives: set[str] = set()
    for card in cards:
        meta = card["meta"]
        shas.add(meta["git_sha"])
        natives.add(meta["native_version"])
        skipped.update(meta.get("datasets_skipped") or {})
        overrides.update(meta.get("cut_env_overrides") or {})
        for name, record in card["datasets"].items():
            if name in datasets:
                raise ValueError(f"dataset {name!r} appears in two sweep results")
            datasets[name] = record
    if len(shas) > 1:
        raise ValueError(f"sweep results come from different commits: {sorted(shas)}")
    merged = build_scorecard(
        datasets,
        native_version=",".join(sorted(natives)),
        git_sha=next(iter(shas), "unknown"),
        skipped={name: why for name, why in skipped.items() if name not in datasets},
    )
    merged["meta"]["cut_env_overrides"] = overrides
    merged["meta"]["tolerance"] = TOLERANCE
    return merged


def best_rule(record: dict) -> tuple[str, float]:
    """The highest-F1 applied rule arm; ties go to the earlier ``CUT_RULES`` name.
    Falls back to the baseline arm when no rule applied."""
    best: tuple[str, float] | None = None
    for rule in CUT_RULES:
        arm = record["arms"][rule]
        if arm["applied"] and (best is None or arm["f1"] > best[1]):
            best = (rule, arm["f1"])
    return best or (BASELINE_ARM, record["arms"][BASELINE_ARM]["f1"])


def render_markdown(card: dict) -> str:
    """One table row per dataset, design set first, for the CI step summary."""
    order = {"design": 0, "holdout": 1}
    lines = [
        "| dataset | corpus | default F1 | best rule | best F1 | Δ best | below default |",
        "|---|---|---|---|---|---|---|",
    ]
    names = sorted(
        card["datasets"],
        key=lambda n: (order.get(card["datasets"][n].get("corpus"), 2), n),
    )
    for name in names:
        record = card["datasets"][name]
        baseline = record["arms"][BASELINE_ARM]["f1"]
        rule, f1 = best_rule(record)
        below = ", ".join(record.get("below_default") or []) or "none"
        lines.append(
            f"| {name} | {record.get('corpus', '')} | {baseline:.4f} | {rule} | {f1:.4f} "
            f"| {f1 - baseline:+.4f} | {below} |"
        )
    return "\n".join(lines) + "\n"


def merge(argv: list[str]) -> int:
    import json

    from scripts.autoconfig_quality.scorecard import dumps

    ap = argparse.ArgumentParser(description="Merge per-dataset link-cut sweep scorecards.")
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--summary-md", type=Path, default=None)
    args = ap.parse_args(argv)
    card = merge_cards([json.loads(p.read_text(encoding="utf-8")) for p in args.files])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(dumps(card) + "\n", encoding="utf-8")
    if args.summary_md is not None:
        args.summary_md.write_text(render_markdown(card), encoding="utf-8")
    return 0
```

Replace `main` with:

```python
def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "merge":
        return merge(argv[1:])
    return run(argv)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH="D:/Temp/gm-matrix;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/autoconfig_quality/tests/test_rule_sweep.py`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
$PY -m ruff check scripts/autoconfig_quality/rule_sweep.py scripts/autoconfig_quality/tests/test_rule_sweep.py
git add scripts/autoconfig_quality/rule_sweep.py scripts/autoconfig_quality/tests/test_rule_sweep.py
git commit -m "feat(quality): merge per-dataset link-cut sweeps into one scorecard and summary"
```

---

### Task 6: CI workflow and harness docs

**Files:**
- Create: `.github/workflows/fs-cut-rule-matrix.yml`
- Modify: `scripts/autoconfig_quality/README.md` (insert a section directly before `## The gate`)

**Interfaces:**
- **Consumes:**
  - `python -m scripts.autoconfig_quality.corpus --list CORPUS --ci` (Task 3);
  - `python -m scripts.autoconfig_quality.rule_sweep ...` and its `merge` subcommand (Tasks 4–5).
- **Produces:** the artifacts `cut-rules-<dataset>` (one per job) and `cut-rule-matrix` (merged JSON plus markdown), and a step summary table.

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/fs-cut-rule-matrix.yml`:

```yaml
# FS link-cut routing P3 -- the per-rule measurement matrix
# (docs/superpowers/specs/2026-09-11-fs-cut-rule-routing-design.md).
#
# For every dataset in the frozen design + held-out corpus
# (scripts/autoconfig_quality/corpus.py), runs the full pipeline once per
# link_cut_rule on one shared EM model and records F1 / precision / recall plus
# the cut diagnostics. One job per dataset; a final job merges the per-dataset
# scorecards into the cut-rule-matrix artifact and a step-summary table.
#
# Measurement only: it chooses no rule and gates nothing. NOT in ci-required.
# Held-out data is fetched here and never committed: Leipzig Abt-Buy (CC-BY),
# Magellan/DeepMatcher (cite-only, evaluation only), FEBRL dataset1/2 (ANU
# open-source licence). ncvr_real is local-only PII and never provisioned.
name: fs-cut-rule-matrix

on:
  pull_request:
    paths:
      - "scripts/autoconfig_quality/rule_sweep.py"
      - "scripts/autoconfig_quality/corpus.py"
      - ".github/workflows/fs-cut-rule-matrix.yml"
  schedule:
    - cron: "0 9 * * 0" # weekly, Sunday 09:00 UTC
  workflow_dispatch:
    inputs:
      corpus:
        description: "Which corpus to measure"
        type: choice
        options: [all, design, holdout]
        default: all

permissions:
  contents: read

env:
  # pyarrow's bundled mimalloc SIGSEGVs on goldenmatch's pipeline worker threads.
  ARROW_DEFAULT_MEMORY_POOL: system
  # jemalloc page-decay: return freed polars pages to the OS between arms.
  _RJEM_MALLOC_CONF: "dirty_decay_ms:1000,muzzy_decay_ms:0"
  GOLDENMATCH_AUTOCONFIG_MEMORY: "0"

concurrency:
  group: fs-cut-rule-matrix-${{ github.ref }}-${{ inputs.corpus }}
  cancel-in-progress: true

jobs:
  plan:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    outputs:
      datasets: ${{ steps.list.outputs.datasets }}
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6
      - uses: astral-sh/setup-uv@caf0cab7a618c569241d31dcd442f54681755d39 # v3
      - uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6.2.0
        with:
          python-version: "3.12"
      - name: Sync workspace
        run: uv sync --all-packages
      - name: Unit tests for the corpus, loaders, generator knob and sweep
        run: >-
          uv run python -m pytest -q
          scripts/autoconfig_quality/tests/test_corpus.py
          scripts/autoconfig_quality/tests/test_rule_sweep.py
          scripts/bench_er_headtohead/test_heldout_loaders.py
          scripts/bench_er_headtohead/test_generate_fixture_corruption.py
      - name: List the corpus
        id: list
        env:
          CORPUS: ${{ inputs.corpus || 'all' }}
        run: |
          echo "datasets=$(uv run python -m scripts.autoconfig_quality.corpus --list "$CORPUS" --ci)" >> "$GITHUB_OUTPUT"

  sweep:
    needs: plan
    runs-on: ubuntu-latest
    timeout-minutes: 150
    strategy:
      fail-fast: false
      matrix:
        dataset: ${{ fromJSON(needs.plan.outputs.datasets) }}
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6
      - uses: astral-sh/setup-uv@caf0cab7a618c569241d31dcd442f54681755d39 # v3
      - uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6.2.0
        with:
          python-version: "3.12"
      - name: Sync workspace (installs goldenmatch @ HEAD)
        run: uv sync --all-packages
      - name: Build the native kernel (mirror the real default FS path)
        run: uv run python scripts/build_native.py
      - name: Install recordlinkage (febrl3 / febrl4)
        run: uv pip install recordlinkage
      - name: Fetch benchmark data
        run: |
          BASE=packages/python/goldenmatch/tests/benchmarks/datasets
          fetch_zip() {
            local name="$1" url="$2"; shift 2
            local dest="$BASE/$name"
            mkdir -p "$dest"
            if curl -fsSL --retry 3 -o "/tmp/$name.zip" "$url"; then
              mkdir -p "/tmp/$name" && unzip -o -q "/tmp/$name.zip" -d "/tmp/$name"
              for f in "$@"; do
                found=$(find "/tmp/$name" -name "$f" | head -1)
                if [ -n "$found" ]; then cp "$found" "$dest/$f"
                else echo "::warning::$name: $f not in archive"; fi
              done
            else
              echo "::warning::$name download failed; --require-datasets will fail the run"
            fi
          }
          L=https://dbs.uni-leipzig.de/file
          fetch_zip DBLP-ACM "$L/DBLP-ACM.zip" DBLP2.csv ACM.csv DBLP-ACM_perfectMapping.csv
          fetch_zip DBLP-Scholar "$L/DBLP-Scholar.zip" DBLP1.csv Scholar.csv DBLP-Scholar_perfectMapping.csv
          fetch_zip Amazon-Google "$L/Amazon-GoogleProducts.zip" Amazon.csv GoogleProducts.csv Amzon_GoogleProducts_perfectMapping.csv
          fetch_zip Abt-Buy "$L/Abt-Buy.zip" Abt.csv Buy.csv abt_buy_perfectMapping.csv
          # Magellan/DeepMatcher: cite-only -- fetched for evaluation, never committed.
          M=https://pages.cs.wisc.edu/~anhai/data1/deepmatcher_data/Structured
          for name in Walmart-Amazon iTunes-Amazon Fodors-Zagats; do
            mkdir -p "$BASE/Magellan/$name"
            for f in tableA tableB train valid test; do
              curl -fsSL --retry 3 -o "$BASE/Magellan/$name/$f.csv" "$M/$name/exp_data/$f.csv" \
                || echo "::warning::Magellan $name $f.csv download failed"
            done
          done
          # FEBRL dataset1/2 raw CSVs (ANU open-source licence) from recordlinkage.
          mkdir -p "$BASE/FEBRL"
          for f in dataset1 dataset2; do
            curl -fsSL --retry 3 -o "$BASE/FEBRL/$f.csv" \
              "https://raw.githubusercontent.com/J535D165/recordlinkage/master/recordlinkage/datasets/febrl/$f.csv" \
              || echo "::warning::FEBRL $f download failed"
          done
      - name: Sweep every link-cut rule
        env:
          DATASET: ${{ matrix.dataset }}
        run: |
          uv run python -m scripts.autoconfig_quality.rule_sweep \
            --datasets "$DATASET" --require-datasets "$DATASET" \
            --out "cut-rules/$DATASET.json"
      - name: Upload this dataset's scorecard
        if: always()
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v4
        with:
          name: cut-rules-${{ matrix.dataset }}
          path: cut-rules/
          if-no-files-found: warn

  merge:
    needs: [plan, sweep]
    if: always() && needs.plan.result == 'success'
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6
      - uses: astral-sh/setup-uv@caf0cab7a618c569241d31dcd442f54681755d39 # v3
      - uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6.2.0
        with:
          python-version: "3.12"
      - name: Sync workspace
        run: uv sync --all-packages
      - name: Download the per-dataset scorecards
        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4
        with:
          pattern: cut-rules-*
          merge-multiple: true
          path: cut-rules
      - name: Merge and summarize
        run: |
          uv run python -m scripts.autoconfig_quality.rule_sweep merge cut-rules/*.json \
            --out cut-rule-matrix.json --summary-md cut-rule-matrix.md
          {
            echo "## FS link-cut rule matrix"
            echo ""
            echo "Default = link_cut_rule unset on the loaded model; below default = F1 more than 0.01 under it."
            echo ""
            cat cut-rule-matrix.md
          } >> "$GITHUB_STEP_SUMMARY"
      - name: Upload the merged matrix
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v4
        with:
          name: cut-rule-matrix
          path: |
            cut-rule-matrix.json
            cut-rule-matrix.md
```

- [ ] **Step 2: Validate the workflow**

```bash
PYTHONPATH="D:/Temp/gm-matrix;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/test_workflow_yaml.py
zizmor --offline .github/workflows/fs-cut-rule-matrix.yml
```

Expected: the YAML test PASSES. zizmor reports no template-injection finding; the `${{ }}` uses are only in `with:`, `env:`, `if:`, `matrix`, `concurrency` and `name:`. If zizmor is unavailable offline, say so in the report instead of skipping silently.

- [ ] **Step 3: Document the matrix in the harness README**

In `scripts/autoconfig_quality/README.md`, insert this section directly before the `## The gate` heading:

````markdown
## Per-rule link-cut matrix (FS link-cut routing P3)

`rule_sweep.py` measures every Fellegi-Sunter link-cut rule on every labelled
dataset in the frozen corpus (`corpus.py`). For each dataset it auto-configures the
probabilistic config once, then runs the full pipeline once per arm on one shared
EM model (`MatchkeyConfig.model_path`):

- `default` trains and saves the model;
- `default_loaded` reloads it and is the baseline;
- each `link_cut_rule` arm reloads it too.

A pinned arm that an earlier precedence step overrode is **voided** and fails the
run, so a leaked `GOLDENMATCH_FS_EVIDENCE_CUT` cannot quietly turn nine arms into
nine copies of the default. The sweep also refuses to start while any cut-moving
env var is set.

```bash
python -m scripts.autoconfig_quality.rule_sweep --datasets person --out person.json
python -m scripts.autoconfig_quality.rule_sweep merge a.json b.json --out m.json --summary-md m.md
```

Measurement only: it writes no routing row. Large datasets run in the
`fs-cut-rule-matrix` workflow (one job per dataset), never on a laptop.

| Corpus | Datasets | Source and licence |
|---|---|---|
| design | person, household_hardneg, cotenant_hardneg, febrl3, febrl4, ncvr_synthetic, ncvr_real (local only), historical_50k, dblp_acm, dblp_scholar, amazon_google | as listed above |
| holdout | abt_buy | Leipzig, CC-BY: Database Group Leipzig; Köpcke, Thor & Rahm, VLDB 2010 |
| holdout | walmart_amazon, itunes_amazon, fodors_zagats | Magellan/DeepMatcher, cite-only, fetched for evaluation and never committed: Konda et al. 2016; Mudgal et al. 2018 |
| holdout | febrl1, febrl2 | FEBRL raw CSVs from recordlinkage, ANU open-source licence: Christen 2008 |
| holdout | synth_{person,biblio}_c{05,20}_d{10,40} | `generate_fixture.py`, seed 1009, corruption 0.5/2.0, dupe rate 0.10/0.40 |

The held-out set is frozen: a routing row ships only if it is never more than 0.01
F1 below the default on every design **and** held-out dataset. Changing the
held-out list after any row exists means re-running the full gate.
````

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/fs-cut-rule-matrix.yml scripts/autoconfig_quality/README.md
git commit -m "ci(quality): fs-cut-rule-matrix workflow and harness docs for the link-cut matrix"
```

---

### Task 7: Verify, open the stacked PR, record the first matrix

**Files:** none new in the repo. The PR body goes in the session scratchpad.

- [ ] **Step 1: Run the affected tests and ratchets**

```bash
PYTHONPATH="D:/Temp/gm-matrix;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider \
  scripts/autoconfig_quality/tests/ \
  scripts/bench_er_headtohead/test_heldout_loaders.py \
  scripts/bench_er_headtohead/test_generate_fixture_corruption.py \
  scripts/bench_er_headtohead/test_scale_envelope.py \
  scripts/test_workflow_yaml.py scripts/test_no_new_dead_code.py
```

Expected: PASS. Real-dataset loaders skip when their data is absent.

- [ ] **Step 2: One small end-to-end sweep on the person anchor**

```bash
PYTHONPATH="D:/Temp/gm-matrix;$PP" PYTHONIOENCODING=utf-8 $PY -m scripts.autoconfig_quality.rule_sweep \
  --datasets person --out D:/Temp/claude/cut-rules-person.json
```

Expected: exit 0, and `datasets.person.arms` has 11 arms, none voided. This anchor is small; do not run any other dataset locally.

- [ ] **Step 3: Docs check and cleanup**

```bash
PYTHONPATH="D:/Temp/gm-matrix;$PP" PYTHONIOENCODING=utf-8 $PY scripts/regen_docs.py --check
git status --short --ignored | rg -v '__pycache__|\.ruff_cache|\.superpowers'
```

Expected: `Docs are current`. Delete any leftover timestamped `*_clusters.csv` / `*_lineage.json` in the worktree root. The working tree is clean.

- [ ] **Step 4: Push and open a DRAFT PR stacked on #2942 (not armed)**

Write the PR body to `D:/Temp/claude/pr-fs-cut-rule-matrix-body.md`. It covers:
- what P3 adds (Tasks 1–6);
- the frozen corpus and its licences;
- the deliberate separate-scorecard deviation;
- a note that the `pull_request` trigger runs the matrix on this PR.

It carries no AI attribution.

```bash
export GH_TOKEN=$(gh auth token -u benzsevern)
SKIP_DOCS_CHECK=1 git push -u origin feat/fs-cut-rule-matrix
gh pr create --repo benseverndev-oss/goldenmatch --draft \
  --base feat/fs-cut-rule-router --head feat/fs-cut-rule-matrix \
  --title "feat(quality): FS link-cut routing P3 -- frozen held-out corpus + per-rule matrix" \
  --body-file D:/Temp/claude/pr-fs-cut-rule-matrix-body.md
```

Do NOT run `gh pr merge --auto`: the base is a feature branch, and an armed stacked PR merges into it immediately. Retarget to `main` after #2939 and #2942 merge; the #2940 cherry-pick then drops out on rebase.

- [ ] **Step 5: Record the first matrix**

The `fs-cut-rule-matrix` workflow starts from the PR's `pull_request` trigger. Wait for it without short-interval polling. When it finishes:
- download the `cut-rule-matrix` artifact;
- append its markdown table to the PR body under "## First matrix (run <id>)".

Handle a failed run as follows:
- **A dataset job failed:** read its log. A fetch 404, a column-name mismatch in a new loader, or a voided arm each go back to the owning task as a finding. Never retry blindly.
- **Otherwise:** report the table as it is. P4 writes rows from it.
