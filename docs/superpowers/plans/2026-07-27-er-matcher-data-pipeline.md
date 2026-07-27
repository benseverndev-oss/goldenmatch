# ER-Matcher Multi-Source Data Pipeline Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a license-clean, multi-source, provenance-tracked data pipeline that produces the ER-matcher training/eval corpus (person + product + citation), emitting the trainer's existing JSONL row contract so the trainer and Modal run need no change.

**Architecture:** A small pluggable `PairSource` registry. Bundled license-clean loaders (FEBRL synthetic-person, Leipzig CC-BY product/citation/music) plus a Rich synthetic generator feed a `build_corpus` driver that blends per `sources.yaml`, writes `data/er_matcher/{train,val,test}.jsonl` + a provenance/license `manifest.json`. Real-PII/no-license sources (Magellan, NCVR) are fetch-at-build, eval-only, never committed. Everything is pure/deterministic and CPU/box-safe — no GPU, no network in the unit tests.

**Tech Stack:** Python 3.11 stdlib (csv, json, hashlib, urllib, random), PyYAML (already a dep), pytest. Follows the existing `scripts/er_matcher/` pure-helper + `test_*.py` conventions.

**Spec:** `docs/superpowers/specs/2026-07-27-er-matcher-multi-source-data-pipeline-design.md`

---

## File Structure

**Phase 1a — benchmark ingestion (PR 1):**

| Path | Responsibility |
|---|---|
| `scripts/er_matcher/sources/__init__.py` | Re-export `Row`, `PairSource`, `register`/`get_source`/`iter_sources` |
| `scripts/er_matcher/sources/base.py` | `Row` TypedDict, `PairSource` Protocol, the loader registry |
| `scripts/er_matcher/sources/splits.py` | Deterministic entity-level split (generalized `_split_of`, str-keyed) |
| `scripts/er_matcher/sources/negatives.py` | Deterministic blocker + hard/easy negative synthesis |
| `scripts/er_matcher/sources/leipzig.py` | Leipzig CC-BY loader (two tables + perfect mapping → labeled pairs) |
| `scripts/er_matcher/sources/febrl.py` | FEBRL synthetic-person loader (ships match status) |
| `scripts/er_matcher/sources/magellan.py` | Fetch-at-build DeepMatcher-CSV loader, **eval-only** |
| `scripts/er_matcher/sources_config.py` | Load + validate `sources.yaml` |
| `scripts/er_matcher/sources.yaml` | Per-source: mechanism, license, attribution, domain, blend weight |
| `scripts/er_matcher/build_corpus.py` | Driver: run bundled sources → blend → write JSONL + `manifest.json` |
| `scripts/er_matcher/tests/fixtures/leipzig_mini/` | Tiny 2-table + mapping fixture |
| `scripts/er_matcher/tests/fixtures/febrl_mini/` | Tiny FEBRL fixture |
| `scripts/er_matcher/tests/fixtures/magellan_mini/` | Tiny DeepMatcher train/valid/test fixture |
| `scripts/er_matcher/test_sources_base.py` | Registry + Row contract tests |
| `scripts/er_matcher/test_splits.py` | Split determinism / no-leak / str+int parity |
| `scripts/er_matcher/test_negatives.py` | Blocker + negative-synthesis tests |
| `scripts/er_matcher/test_leipzig.py` | Leipzig loader tests (mini fixture) |
| `scripts/er_matcher/test_febrl.py` | FEBRL loader tests (mini fixture) |
| `scripts/er_matcher/test_magellan.py` | Magellan CSV-parse tests (fixture; network behind marker) |
| `scripts/er_matcher/test_build_corpus.py` | End-to-end blend + manifest tests |
| `scripts/er_matcher/gen_pairs.py` | **Modify:** import `_split_of` from `sources/splits.py` (DRY) |

**Phase 1b — Rich synthetic generator (PR 2):**

| Path | Responsibility |
|---|---|
| `scripts/er_matcher/synthetic/__init__.py` | Package init |
| `scripts/er_matcher/synthetic/vocab.py` | Census-scale name/place tables + Zipf sampler |
| `scripts/er_matcher/synthetic/data/*.txt` | Vendored census name/place frequency lists (public-domain source) |
| `scripts/er_matcher/synthetic/schemas.py` | Per-domain schemas: people/healthcare/business + crm_contact + organization |
| `scripts/er_matcher/synthetic/corruption.py` | Parameterized error channels, calibrated to FEBRL `dsgen` |
| `scripts/er_matcher/synthetic/generate.py` | Rich generator emitting `Row`s via the `PairSource` interface |
| `scripts/er_matcher/synthetic/test_*.py` | Vocab / schema / corruption / generate tests + dsgen-calibration histogram test |
| `scripts/er_matcher/gen_pairs.py` | **Modify:** delegate to `synthetic.generate` (keep CLI + determinism contract) |
| `scripts/er_matcher/sources.yaml` | **Modify:** register `synthetic` source |

**Design boundaries:** loaders never import torch/transformers (CPU/box-safe). `base.py` has zero source-specific logic. `negatives.py` and `splits.py` are pure and shared by every positives-only loader. Fetch loaders isolate all network in one function guarded by an env flag so unit tests hit fixtures only.

---

## Phase 1a — Benchmark ingestion

### Task 1: Row contract, PairSource protocol, registry

**Files:**
- Create: `scripts/er_matcher/sources/base.py`
- Create: `scripts/er_matcher/sources/__init__.py`
- Test: `scripts/er_matcher/test_sources_base.py`

- [ ] **Step 1: Write the failing test**

```python
# test_sources_base.py
from sources.base import Row, PairSource, register, get_source, iter_sources

def test_row_has_dataset_provenance_field():
    row: Row = {"a": {"x": "1"}, "b": {"x": "2"}, "label": "no_match",
                "domain": "people", "source": "synthetic", "dataset": "febrl",
                "eid_a": "1", "eid_b": "2"}
    assert row["dataset"] == "febrl"          # the field this pipeline ADDS
    assert set(row) >= {"a", "b", "label", "domain", "source", "dataset", "eid_a", "eid_b"}

def test_registry_roundtrip_and_isolation():
    class Dummy:
        name = "dummy_ds"
        def splits(self): return {"train": [], "val": [], "test": []}
    register(Dummy())
    assert get_source("dummy_ds").name == "dummy_ds"
    assert "dummy_ds" in {s.name for s in iter_sources()}

def test_get_unknown_source_raises():
    import pytest
    with pytest.raises(KeyError):
        get_source("does_not_exist")
```

- [ ] **Step 2: Run to verify it fails** — `pytest scripts/er_matcher/test_sources_base.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement `base.py`**

```python
# sources/base.py
"""Loader contract + registry for the multi-source ER data pipeline.
CPU/box-safe: stdlib only, never imports torch/transformers."""
from __future__ import annotations
from typing import Any, Iterable, Protocol, TypedDict, runtime_checkable

class Row(TypedDict):
    a: dict[str, Any]
    b: dict[str, Any]
    label: str           # "match" | "no_match"
    domain: str
    source: str          # e.g. "leipzig", "febrl", "synthetic"
    dataset: str         # PairSource.name; == registry key == sources.yaml key
    eid_a: str
    eid_b: str

@runtime_checkable
class PairSource(Protocol):
    name: str
    def splits(self) -> dict[str, Iterable[Row]]: ...

_REGISTRY: dict[str, PairSource] = {}

def register(source: PairSource) -> None:
    _REGISTRY[source.name] = source

def get_source(name: str) -> PairSource:
    if name not in _REGISTRY:
        raise KeyError(f"no registered source {name!r}; have {sorted(_REGISTRY)}")
    return _REGISTRY[name]

def iter_sources() -> Iterable[PairSource]:
    return list(_REGISTRY.values())
```

`sources/__init__.py` re-exports the five names. Tests register inside the test (xdist worker isolation — see the repo CLAUDE.md note).

- [ ] **Step 4: Run to verify pass** — `pytest scripts/er_matcher/test_sources_base.py -v` → PASS.
- [ ] **Step 5: Commit** — `git add scripts/er_matcher/sources/ scripts/er_matcher/test_sources_base.py && git commit -m "feat(er-matcher): PairSource contract + registry"`

---

### Task 2: Deterministic entity-level splits (generalized `_split_of`)

**Files:**
- Create: `scripts/er_matcher/sources/splits.py`
- Modify: `scripts/er_matcher/gen_pairs.py` (import `_split_of` from splits; delete local copy)
- Test: `scripts/er_matcher/test_splits.py`

- [ ] **Step 1: Write the failing test**

```python
# test_splits.py
from sources.splits import split_of

def test_str_and_int_eid_parity():
    # generalization requirement from spec: benchmark IDs are strings
    assert split_of(7, seed=1, val_frac=.15, test_frac=.15, holdout_domain=None) == \
           split_of("7", seed=1, val_frac=.15, test_frac=.15, holdout_domain=None)

def test_holdout_domain_forced_to_test():
    assert split_of("abc", seed=1, val_frac=.15, test_frac=.15,
                    holdout_domain="business", domain="business") == "test"

def test_deterministic_and_partitions():
    got = {split_of(str(i), seed=9, val_frac=.15, test_frac=.15, holdout_domain=None)
           for i in range(200)}
    assert got == {"train", "val", "test"}
    # same seed -> identical
    assert split_of("k", seed=9, val_frac=.15, test_frac=.15, holdout_domain=None) == \
           split_of("k", seed=9, val_frac=.15, test_frac=.15, holdout_domain=None)
```

- [ ] **Step 2: Run to verify it fails** — FAIL (module missing).
- [ ] **Step 3: Implement `splits.py`** (lift `gen_pairs._split_of`, key on `str(eid)`):

```python
# sources/splits.py
from __future__ import annotations
import hashlib

def split_of(eid, *, seed: int, val_frac: float, test_frac: float,
             holdout_domain: str | None = None, domain: str | None = None) -> str:
    """Deterministic entity-level split. Holdout domain -> 'test'; else hash
    (seed, str(eid)) into train/val/test. str(eid) so benchmark string IDs work."""
    if holdout_domain and domain == holdout_domain:
        return "test"
    h = hashlib.sha256(f"{seed}:{eid}".encode()).hexdigest()
    frac = int(h[:8], 16) / 0xFFFFFFFF
    if frac < test_frac:
        return "test"
    if frac < test_frac + val_frac:
        return "val"
    return "train"
```

- [ ] **Step 4: Update `gen_pairs.py`** — replace the local `_split_of` body with `from sources.splits import split_of` and delegate (keep the old signature as a thin wrapper so `test_gen_pairs.py` stays green). Run `pytest scripts/er_matcher/test_gen_pairs.py -v` → still PASS (regression guard).
- [ ] **Step 5: Run new tests** — `pytest scripts/er_matcher/test_splits.py -v` → PASS.
- [ ] **Step 6: Commit** — `feat(er-matcher): shared str-keyed entity split; gen_pairs reuses it`

---

### Task 3: Deterministic blocker + negative synthesis

**Files:**
- Create: `scripts/er_matcher/sources/negatives.py`
- Test: `scripts/er_matcher/test_negatives.py`

- [ ] **Step 1: Write the failing test**

```python
# test_negatives.py
from sources.negatives import blocking_key, synth_negatives

def test_blocking_key_is_deterministic_prefix_token():
    e = {"name": "Robert Smith", "city": "Newark"}
    assert blocking_key(e, ["name"]) == blocking_key(dict(e), ["name"])
    assert blocking_key(e, ["name"]) != ""

def test_synth_negatives_balance_and_determinism():
    ents = {f"e{i}": {"name": f"Person {i%5}", "phone": str(i)} for i in range(20)}
    negs1 = synth_negatives(ents, block_keys=["name"], hard_frac=0.5, seed=3, n=10)
    negs2 = synth_negatives(ents, block_keys=["name"], hard_frac=0.5, seed=3, n=10)
    assert negs1 == negs2                                  # deterministic
    assert all(a != b for a, b, _ in negs1)               # no self-pairs
    assert {tag for _, _, tag in negs1} <= {"hard", "easy"}
    hard = [n for n in negs1 if n[2] == "hard"]
    assert all(blocking_key(ents[a], ["name"]) == blocking_key(ents[b], ["name"])
               for a, b, _ in hard)                        # hard = same block
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement `negatives.py`** — `blocking_key` (lowercased first token of the joined block fields); `synth_negatives` builds a block index, samples hard negatives from within-block distinct-entity pairs and easy negatives cross-block, seeded `random.Random(seed)`, returns `list[(eid_a, eid_b, "hard"|"easy")]`. Pure stdlib.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `feat(er-matcher): deterministic blocker + hard/easy negative synthesis`

---

### Task 4: Leipzig CC-BY loader

**Files:**
- Create: `scripts/er_matcher/sources/leipzig.py`
- Create: `scripts/er_matcher/tests/fixtures/leipzig_mini/{tableA.csv,tableB.csv,mapping.csv}`
- Test: `scripts/er_matcher/test_leipzig.py`

- [ ] **Step 1: Build the mini fixture** — 4 rows in tableA, 4 in tableB, 2 perfect-mapping matches. Fields e.g. `id,title,manufacturer,price`.
- [ ] **Step 2: Write the failing test**

```python
# test_leipzig.py — fixtures anchored to __file__ (CWD differs local vs CI)
from pathlib import Path
from sources.leipzig import LeipzigSource
FIX = Path(__file__).parent / "tests" / "fixtures" / "leipzig_mini"

def test_leipzig_emits_positives_and_negatives():
    src = LeipzigSource(name="abt_buy", root=FIX, domain="product",
                        block_fields=["title"], seed=1)
    rows = [r for split in src.splits().values() for r in split]
    labels = {r["label"] for r in rows}
    assert labels == {"match", "no_match"}
    assert all(r["dataset"] == "abt_buy" and r["source"] == "leipzig" for r in rows)
    # positives come from the perfect mapping (2 matches in the fixture)
    assert sum(r["label"] == "match" for r in rows) == 2

def test_leipzig_deterministic():
    mk = lambda: [r for s in LeipzigSource("abt_buy", FIX, "product", ["title"], 1)
                  .splits().values() for r in s]
    assert mk() == mk()
```

- [ ] **Step 3: Run to verify it fails.**
- [ ] **Step 4: Implement `LeipzigSource`** — read tableA/tableB (csv.DictReader), read mapping → positive `(idA,idB)` pairs → `Row(label="match")`; call `synth_negatives` over the union of records for negatives; assign split via `split_of(idA, ...)`; tag `source="leipzig"`, `dataset=name`, `domain=domain`. Attribution string carried as a class attr for the manifest.
- [ ] **Step 5: Run to verify pass.**
- [ ] **Step 6: Commit** — `feat(er-matcher): Leipzig CC-BY loader (positives + synthesized negatives)`

---

### Task 5: FEBRL synthetic-person loader

**Files:**
- Create: `scripts/er_matcher/sources/febrl.py`
- Create: `scripts/er_matcher/tests/fixtures/febrl_mini/febrl_sample.csv`
- Test: `scripts/er_matcher/test_febrl.py`

- [ ] **Step 1: Build the mini fixture** — FEBRL-style rows with `rec_id` encoding original vs duplicate + the entity id (FEBRL `rec-<n>-org` / `rec-<n>-dup-<k>`), fields `given_name,surname,street,suburb,postcode,phone,soc_sec_id`.
- [ ] **Step 2: Write the failing test** — assert person matches pair org↔dup of the same entity (label match), negatives synthesized cross-entity, `domain="person"`, `dataset="febrl"`, determinism, and split-no-leak (all rows of one entity in one split).
- [ ] **Step 3: Run to verify it fails.**
- [ ] **Step 4: Implement `FebrlSource`** — parse `rec_id` → entity id; group org+dup as positives; `synth_negatives` for negatives; `split_of(entity_id, ...)` (entity-level, no leak). License = MPL-1.1 attr.
- [ ] **Step 5: Run to verify pass.**
- [ ] **Step 6: Commit** — `feat(er-matcher): FEBRL synthetic-person loader`

---

### Task 6: Magellan fetch-at-build loader (eval-only)

**Files:**
- Create: `scripts/er_matcher/sources/magellan.py`
- Create: `scripts/er_matcher/tests/fixtures/magellan_mini/{train,valid,test}.csv` + `tableA.csv,tableB.csv`
- Test: `scripts/er_matcher/test_magellan.py`

- [ ] **Step 1: Build the mini fixture** — DeepMatcher format: `tableA`/`tableB` + `train/valid/test.csv` with `ltable_id,rtable_id,label` rows.
- [ ] **Step 2: Write the failing test** — `MagellanSource.load_from_dir(FIX)` yields labeled pairs joined against the two tables; canonical train/valid/test preserved; `eval_only is True`; asserts **no network** is touched when reading a local dir. A separate test asserts `fetch()` raises unless `GOLDENMATCH_ALLOW_FETCH=1` (guard so CI never downloads).
- [ ] **Step 3: Run to verify it fails.**
- [ ] **Step 4: Implement `MagellanSource`** — `load_from_dir(dir)` (pure parse, tested); `fetch(cache_dir)` (urllib download + sha256 verify, guarded by `GOLDENMATCH_ALLOW_FETCH`) → `load_from_dir`. Mark `eval_only=True`; `source="magellan"`, `dataset=<name>`. Document cite-only license in the class.
- [ ] **Step 5: Run to verify pass.**
- [ ] **Step 6: Commit** — `feat(er-matcher): Magellan fetch-at-build eval loader (network-guarded)`

---

### Task 7: `sources.yaml` + config loader

**Files:**
- Create: `scripts/er_matcher/sources.yaml`
- Create: `scripts/er_matcher/sources_config.py`
- Test: `scripts/er_matcher/test_sources_config.py`

Each yaml entry must carry enough to **construct** its loader, not just describe it
(the flagged gap: build_corpus needs a factory, and the 5 Leipzig datasets are 5
entries → 5 `LeipzigSource` instances). Entry schema:

```yaml
sources:
  abt_buy:
    loader: leipzig                 # -> which PairSource class
    mechanism: bundle
    license: CC-BY
    attribution: "Leipzig DB Group; VLDB2010"
    domain: product
    weight: 1.0                      # illustrative; blend deferred
    kwargs: {root: data/er_matcher/raw/leipzig/abt_buy, block_fields: [title]}
  febrl:
    loader: febrl
    mechanism: bundle
    license: MPL-1.1
    domain: person
    weight: 2.0                      # person upweight (illustrative)
    kwargs: {root: data/er_matcher/raw/febrl}
  magellan_dblp_acm:
    loader: magellan
    mechanism: fetch
    license: cite-only
    eval_only: true
    kwargs: {name: dblp_acm}
  ncvr:                              # deferred to sub-project 3; present for model-card completeness
    loader: ncvr
    mechanism: fetch
    license: public-record
    eval_only: true
    kwargs: {}
```

- [ ] **Step 1: Write the failing test** — `load_sources(path)` returns validated entries; rejects unknown keys (mirror `train.load_config`'s strictness); every entry has `loader`, `mechanism ∈ {bundle,generate,fetch}`, `license`, and (for CC-BY) `attribution`; `eval_only` sources are flagged; `kwargs` is a dict. Add `build_source(entry)` (the factory) that maps `loader` → class and constructs it with `**kwargs` + `seed`; test it returns a registered `PairSource` whose `.name` == the yaml key.
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Author `sources.yaml`** (schema above; weights illustrative; blend deferred) and implement `sources_config.py` (PyYAML load + dataclass validate + `build_source` factory keyed on `loader`). The `ncvr` loader may be a thin fetch/`eval_only` stub (raises `NotImplementedError` until sub-project 3) so its entry validates for the model card without implementing the fetch now.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `feat(er-matcher): sources.yaml + validated loader + build_source factory`

---

### Task 8: `build_corpus` driver (blend + manifest)

**Files:**
- Create: `scripts/er_matcher/build_corpus.py`
- Test: `scripts/er_matcher/test_build_corpus.py`

- [ ] **Step 1: Write the failing test** — with the three mini fixtures registered, `build_corpus(sources_yaml, out_dir, seed)`:
  - writes `data/.../ {train,val,test}.jsonl` containing only `bundle`/`generate` sources (NOT `fetch`/`eval_only`);
  - `manifest.json` records per-source `{count, license, attribution, mechanism}` and a total;
  - respects blend weights (cap/upweight) deterministically;
  - re-run with same seed → byte-identical output (determinism test).

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement `build_corpus.py`** — for each `sources.yaml` entry, construct+register the loader via `sources_config.build_source` (Task 7 factory), then for bundled/generate sources pull `splits()`, apply per-source blend cap/weight, concat per split, write JSONL (`json.dumps(sort_keys=True)` like `gen_pairs._write`), emit `manifest.json` with provenance + licenses. `fetch`/`eval_only` sources are constructed but skipped in the training corpus (they're for the eval harness, sub-project 3) — their license/attribution still flows into the manifest for the model card.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `feat(er-matcher): build_corpus blends sources -> unified JSONL + license manifest`

---

### Task 9: Phase 1a integration + docs

- [ ] **Step 1** — Add a `README` section in `scripts/er_matcher/` documenting the pipeline, each source's license/attribution, and the fetch-only policy. Verify the CC-BY attribution + FEBRL MPL notice are present (required by license).
- [ ] **Step 2** — Run the whole box-safe suite: `pytest scripts/er_matcher/ -v` (no GPU, no network) → all PASS.
- [ ] **Step 3** — Run `build_corpus.py` locally against the real bundled FEBRL + Leipzig data (small); eyeball `manifest.json` counts + licenses.
- [ ] **Step 4: Commit + open PR 1** — `feat(er-matcher): multi-source benchmark ingestion pipeline (Phase 1a)`. PR body links the spec and lists licenses/attributions.

---

## Phase 1b — Rich synthetic generator

> Sequenced after 1a merges. Same `PairSource` interface, so it plugs into `build_corpus` by registering one more source. Full bite-sized steps are finalized at 1b kickoff; tasks + interfaces + test intent below.

### Task 10: Census-scale vocab + Zipf sampler
**Files:** `synthetic/vocab.py`, `synthetic/data/*.txt`, `synthetic/test_vocab.py`
- [ ] Vendor public-domain census first/last-name frequency lists + city/state/zip tables into `synthetic/data/` (document source + public-domain status in the manifest/README).
- [ ] `sample_name(rng)` draws by frequency (Zipf-like from the real counts); deterministic per seed.
- [ ] Tests: determinism; distribution roughly follows the frequency table; vocab size >> the old 900-combo ceiling.

### Task 11: Per-domain schemas
**Files:** `synthetic/schemas.py`, `synthetic/test_schemas.py`
- [ ] Schemas for people, healthcare, business + **crm_contact** (name, email, phone, company, title, address) + **organization** (legal name, dba, domain, ein-like id, address).
- [ ] Tests: each schema yields the declared fields; strong-id keys declared per domain (for negative blocking + hard-negative conflicts).

### Task 12: Error-channel corruption model (dsgen-calibrated)
**Files:** `synthetic/corruption.py`, `synthetic/test_corruption.py`
- [ ] Channels: typo, OCR-confusion, phonetic, transliteration, field-swap, token drop/add, formatting — each an independent, seeded, per-field-rate function.
- [ ] **Calibration test (spec requirement):** generate a corruption-type histogram over N synthetic dups and assert it matches a committed FEBRL-`dsgen` reference histogram within tolerance (per-field probabilities + error-type mix), not "looks similar."

### Task 13: Rich generator emitting `Row`s
**Files:** `synthetic/generate.py`, `synthetic/test_generate.py`
- [ ] `SyntheticSource(name="synthetic", ...)` implements `PairSource`; positives = entity vs corrupted self; negatives via `sources.negatives` (reuse — DRY); splits via `sources.splits.split_of`.
- [ ] Tests: determinism (byte-identical per seed), ~50/50 balance, all 5 domains present, hard negatives share names but conflict on strong ids.

### Task 14: Rewire `gen_pairs.py` + register source
**Files:** `gen_pairs.py` (modify), `sources.yaml` (modify), `test_gen_pairs.py` (extend)
- [ ] `gen_pairs.py` delegates to `synthetic.generate` while preserving its CLI + deterministic-output contract; keep a back-compat path so existing callers/tests don't break (regression guard: `test_gen_pairs.py` green).
- [ ] Register `synthetic` in `sources.yaml`; `build_corpus` now includes it.
- [ ] Tests: CLI parity; `build_corpus` manifest shows the synthetic source with its counts.

### Task 15: Phase 1b integration + PR 2
- [ ] Full box-safe suite green (`pytest scripts/er_matcher/ -v`).
- [ ] Run `build_corpus` with synthetic enabled; verify manifest counts + person upweight.
- [ ] Open PR 2 — `feat(er-matcher): rich synthetic generator (Phase 1b)`.

---

## Testing & conventions

- **Box-safe only:** no test imports torch/transformers or hits the network. Fetch is guarded by `GOLDENMATCH_ALLOW_FETCH`; unit tests read committed fixtures.
- **Fixture paths anchored to `__file__`** (CWD differs local vs CI — see repo CLAUDE.md).
- **Each new test file opens with** `import os, sys; sys.path.insert(0, os.path.dirname(__file__))` (matches `test_gen_pairs.py:12`) so `import sources...` resolves under a direct `python test_x.py` run and survives a future `--import-mode=importlib` switch, not just pytest's default prepend mode.
- **xdist isolation:** register sources *inside* the test that asserts them (no cross-test registry leakage).
- **Determinism:** every source + `build_corpus` has a same-seed → byte-identical test (mirrors `test_gen_pairs.py`).
- **Commits:** one per task step-group; PR per phase (1a, then 1b).

## Out of scope (later sub-projects)
- Sub-project 2 — re-architect the Modal run for the larger corpus (GPU tier, timeout, epochs, checkpoint/resume, real perf gate + learning-curve sweep).
- Sub-project 3 — cross-benchmark eval harness (this is where `fetch`/`eval_only` Magellan + optional NCVR get consumed).
- Sub-project 4 — release (quantize, publish, model card fed by `manifest.json`).
