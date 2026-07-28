# ER-Matcher Phase 1b (Lean) Synthetic Generator - Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a lean synthetic ER data source (CRM-contact / organization / business, census-name vocab, fixed corruption channels) that flows through the SP3.5 FS enrichment, plus a domain-matched held-out benchmark (Fodors-Zagats), to test whether data diversity improves zero-shot generalization.

**Architecture:** A new pure, box-testable `synthetic/` package (vocab -> schemas -> corruption -> `SyntheticSource`) reusing the existing `sources.negatives` / `sources.splits` primitives and exposing `record_pools()` so synthetic pairs get FS soft-targets + hard-negative mining. Wired via `sources.yaml` + a `_BUILDERS` factory (the `generate` mechanism already folds it into `build_corpus`). Fodors-Zagats added to the eval path like WA/Beer. Then a real Modal retrain + measure.

**Tech Stack:** Python 3.11 stdlib (random, csv) for the pure generator; the existing goldenmatch FS pipeline for enrichment (unchanged); torch/transformers on Modal for the retrain. pytest.

**Spec:** `docs/superpowers/specs/2026-07-28-er-matcher-p1b-synthetic-lean-design.md`

---

## File Structure

| Path | Change | Responsibility |
|---|---|---|
| `scripts/er_matcher/synthetic/__init__.py` | **Create** | package marker |
| `scripts/er_matcher/synthetic/data/census_surnames.csv` | **Create** | vendored copy of goldenmatch refdata surnames (`name,rank,count`) + PROVENANCE |
| `scripts/er_matcher/synthetic/data/first_names.txt` | **Create** | bundled public-domain given names (one per line) |
| `scripts/er_matcher/synthetic/data/cities.csv` | **Create** | bundled US `city,state,zip_prefix` |
| `scripts/er_matcher/synthetic/vocab.py` | **Create** | frequency-weighted name/address sampler (reads bundled data; NO goldenmatch import) |
| `scripts/er_matcher/synthetic/schemas.py` | **Create** | 3 domain schemas: fields + strong-id key + a per-domain record builder |
| `scripts/er_matcher/synthetic/corruption.py` | **Create** | fixed corruption channels + `light`/`heavy` profile |
| `scripts/er_matcher/synthetic/generate.py` | **Create** | `SyntheticSource(PairSource)` + `record_pools()` |
| `scripts/er_matcher/synthetic/test_vocab.py` | **Create** | vocab determinism / weighting / size |
| `scripts/er_matcher/synthetic/test_schemas.py` | **Create** | schema fields + strong-id |
| `scripts/er_matcher/synthetic/test_corruption.py` | **Create** | each channel + profile |
| `scripts/er_matcher/synthetic/test_generate.py` | **Create** | source determinism / balance / domains / record_pools / hard-neg conflict |
| `scripts/er_matcher/sources.yaml` | **Modify** | add `synthetic` entry (`loader: synthetic, mechanism: generate`) |
| `scripts/er_matcher/sources_config.py` | **Modify** | add `"synthetic"` factory to `_BUILDERS` |
| `scripts/er_matcher/sources/magellan.py` | **Modify** | add `fodors_zagats` to `_URL_NAMES` |
| `scripts/er_matcher/sota_baselines.py` | **Modify** | add `fodors_zagats` SOTA row |

**Reuse (do NOT reimplement):** `sources.negatives.synth_negatives(entities, *, block_keys, hard_frac, seed, n)`, `sources.splits.split_of(eid, *, seed, val_frac, test_frac)` and `entity_keys_from_edges`, the `Row` TypedDict (`a,b,label,domain,source,dataset,eid_a,eid_b`). **Mirror** `sources/febrl.py`'s `splits()` / `record_pools()` / `_entities_and_splits()` shape.

**Test env note:** the pure `synthetic/` tests import no goldenmatch -> run with `uv run python -m pytest scripts/er_matcher/synthetic/ -q`. Anything importing `sources_config`/`build_corpus` (Task 5) needs the worktree goldenmatch shadow: `cd scripts/er_matcher && PYTHONPATH=D:/ER/gm-p1b/packages/python/goldenmatch /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest ...`. Convention: `ruff check --fix` touched files, NEVER `ruff format`; commit per task (no push) with trailer:
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
`Claude-Session: https://claude.ai/code/session_01JXyU2FtzzQ68AYMaNKTX81`

---

## Task 1: vocab (bundled data + frequency-weighted sampler)

**Files:** Create `synthetic/__init__.py`, `synthetic/data/{census_surnames.csv,first_names.txt,cities.csv}`, `synthetic/vocab.py`, `synthetic/test_vocab.py`

- [ ] **Step 1: Vendor + bundle the data.**
  - Copy the census surnames: `cp packages/python/goldenmatch/goldenmatch/refdata/data/census_surnames_2010_top10k.csv scripts/er_matcher/synthetic/data/census_surnames.csv`. Add a one-line provenance comment file or header note (US Census 2010 top-10k surnames, public domain; copied from goldenmatch refdata).
  - Create `first_names.txt`: a public-domain given-name list, one per line (start with a concrete ~150-name list of common US given names spanning genders/eras; extend if trivial). Public domain (SSA given names are US-gov public domain).
  - Create `cities.csv` (`city,state,zip_prefix`): ~100 US city/state rows with a plausible 3-digit zip prefix. Public-domain facts.
  - (These files are the box-safe substitute for a goldenmatch import; vocab reads them directly.)

- [ ] **Step 2: Write the failing test** (`synthetic/test_vocab.py`, header `import os, sys; sys.path.insert(0, os.path.dirname(__file__))`):

```python
import random
from vocab import Vocab

def test_vocab_deterministic_per_seed():
    v = Vocab()
    a = [v.sample_surname(random.Random(1)) for _ in range(20)]
    b = [v.sample_surname(random.Random(1)) for _ in range(20)]
    assert a == b                       # same seed -> identical draw

def test_surname_draw_is_frequency_weighted():
    # SMITH (rank 1, count ~2.4M) must dominate a rare top-10k surname over many draws
    v = Vocab()
    rng = random.Random(0)
    draws = [v.sample_surname(rng) for _ in range(5000)]
    assert draws.count("Smith") > 0     # frequent name appears
    # the single most common draw should be among the highest-frequency surnames
    top = max(set(draws), key=draws.count)
    assert v.surname_rank(top.upper()) <= 50

def test_vocab_size_past_ceiling():
    v = Vocab()
    assert v.n_surnames > 1000 and v.n_first_names > 50   # >> old 900-combo ceiling

def test_sample_full_name_and_address_shape():
    v = Vocab()
    rng = random.Random(3)
    first, last = v.sample_person_name(rng)
    assert first and last
    addr = v.sample_address(rng)
    assert set(addr) >= {"street", "city", "state", "zip"}
```

- [ ] **Step 3: Run -> FAIL.** `uv run python -m pytest scripts/er_matcher/synthetic/test_vocab.py -q`. Expected: FAIL (no `vocab`).

- [ ] **Step 4: Implement `vocab.py`.** Load `data/census_surnames.csv` (name,rank,count) once; build a cumulative-weight list for O(log n) frequency-weighted draw (`bisect` over cumulative counts). Load `first_names.txt` (uniform draw is fine for lean) and `cities.csv`. Title-case names on output. Expose `sample_surname(rng)`, `sample_first_name(rng)`, `sample_person_name(rng) -> (first,last)`, `sample_address(rng) -> {street,city,state,zip}`, `surname_rank(NAME_UPPER)`, `n_surnames`, `n_first_names`. Data-dir resolved via `os.path.join(os.path.dirname(__file__), "data")` (works regardless of CWD). Pure stdlib (`csv`, `bisect`, `random` passed in).

- [ ] **Step 5: Run -> PASS.**

- [ ] **Step 6: Commit** `feat(er-matcher): synthetic vocab (census-weighted name/address sampler)`.

---

## Task 2: schemas (3 domains, fields + strong-id)

**Files:** Create `synthetic/schemas.py`, `synthetic/test_schemas.py`

- [ ] **Step 1: Failing test:**

```python
import random
from schemas import DOMAINS, build_record

def test_domains_declared():
    assert set(DOMAINS) == {"crm_contact", "organization", "business"}
    for d in DOMAINS.values():
        assert d.strong_id in d.fields          # strong id is one of the fields
        assert d.name_field in d.fields         # name field (for negative blocking) is a field
        assert d.name_field != d.strong_id      # name shared by hard negs, strong id differs

def test_build_record_yields_all_fields():
    from vocab import Vocab
    v = Vocab()
    for name, dom in DOMAINS.items():
        rec = build_record(name, v, random.Random(7))
        assert set(rec) == set(dom.fields)
        assert all(rec[f] for f in dom.fields)   # non-empty

def test_build_record_deterministic():
    from vocab import Vocab
    v = Vocab()
    r1 = build_record("crm_contact", v, random.Random(9))
    r2 = build_record("crm_contact", v, random.Random(9))
    assert r1 == r2
```

- [ ] **Step 2: Run -> FAIL.**

- [ ] **Step 3: Implement `schemas.py`.** A small `Domain` dataclass `(fields: list[str], strong_id: str, name_field: str)` (`name_field` = the name-bearing field hard negatives collide on; `strong_id` = the field that stays distinct, so a same-name pair with a different strong id is a true non-match). `DOMAINS = {...}`:
  - `crm_contact`: fields `["first","last","email","phone","company","title","street","city","state","zip"]`, strong_id `"email"`, name_field `"last"`.
  - `organization`: fields `["legal_name","dba","website_domain","ein","address"]`, strong_id `"ein"`, name_field `"legal_name"`.
  - `business`: fields `["name","email","phone","city","state","website"]`, strong_id `"website"`, name_field `"name"`.
  `build_record(domain, vocab, rng) -> dict` composes a plausible record from `vocab` (e.g. email = `f"{first}.{last}@{company_slug}.com"`; phone = a seeded 10-digit; ein = seeded 9-digit; website/website_domain from company slug). Deterministic given rng. Keep helpers tiny and pure.

- [ ] **Step 4: Run -> PASS.**  **Step 5: Commit** `feat(er-matcher): synthetic domain schemas (crm/org/business + strong-id)`.

---

## Task 3: corruption channels (+ profile)

**Files:** Create `synthetic/corruption.py`, `synthetic/test_corruption.py`

- [ ] **Step 1: Failing test:**

```python
import random
from corruption import char_typo, nickname, token_drop, format_variant, corrupt_record, PROFILES

def test_char_typo_mutates_about_one_char():
    out = char_typo("williams", random.Random(1), rate=1.0)
    assert out != "williams" and abs(len(out) - len("williams")) <= 1

def test_char_typo_rate_zero_is_identity():
    assert char_typo("smith", random.Random(1), rate=0.0) == "smith"

def test_nickname_maps_known_name():
    # a known formal->nickname mapping fires at rate 1.0
    assert nickname("Robert", random.Random(1), rate=1.0) in {"Rob", "Bob", "Bobby"}

def test_token_drop_removes_a_token():
    out = token_drop("123 Main Street", random.Random(1), rate=1.0)
    assert len(out.split()) < 3 and out  # dropped one, still non-empty

def test_corrupt_record_deterministic_and_changes_something():
    rec = {"first": "Robert", "last": "Williams", "email": "r.w@acme.com", "phone": "5551234567"}
    strong_id = "email"
    c1 = corrupt_record(rec, strong_id=strong_id, rng=random.Random(2), profile="heavy")
    c2 = corrupt_record(rec, strong_id=strong_id, rng=random.Random(2), profile="heavy")
    assert c1 == c2                       # deterministic
    assert c1 != rec                      # something corrupted
    assert c1[strong_id] == rec[strong_id]  # strong id preserved (it identifies the entity)

def test_profiles_exist():
    assert set(PROFILES) == {"light", "heavy"} and PROFILES["heavy"] > PROFILES["light"]
```

- [ ] **Step 2: Run -> FAIL.**

- [ ] **Step 3: Implement `corruption.py`.** Each channel is `fn(value: str, rng, *, rate) -> str`, pure/seeded, identity at rate 0:
  - `char_typo` (insert/delete/swap/substitute one char with prob `rate`),
  - `case_ws` (random case flip / stray whitespace),
  - `nickname` (a small formal->nickname dict, e.g. Robert->{Rob,Bob,Bobby}, William->{Will,Bill,Billy}, ...; falls back to identity if unknown),
  - `token_drop` (drop a random token from a multi-token string),
  - `format_variant` (reformat by hint: phone `5551234567` <-> `(555) 123-4567`; email lowercase/dot-variants; address abbreviations St<->Street).
  `PROFILES = {"light": 0.15, "heavy": 0.4}` (base rate multiplier). `corrupt_record(rec, *, strong_id, rng, profile) -> dict`: apply an appropriate channel subset per field at `PROFILES[profile]`, but **never mutate `strong_id`** (that's what makes a corrupted self still the same entity, and makes a hard NEGATIVE that shares a name but differs on strong_id a true non-match). Deterministic given rng.

- [ ] **Step 4: Run -> PASS.** **Step 5: Commit** `feat(er-matcher): synthetic corruption channels + profile`.

---

## Task 4: SyntheticSource (generate.py)

**Files:** Create `synthetic/generate.py`, `synthetic/test_generate.py`

- [ ] **Step 1: Failing test** (mirror `test_febrl.py`'s shape):

```python
from generate import SyntheticSource

def _src(**kw):
    # n_entities kept generous so per-domain surname collisions (needed by the
    # hard-negative test) reliably occur under Zipf-weighted census draws.
    return SyntheticSource(name="synthetic", seed=20260728, n_entities=180,
                           profile="light", **kw)

def test_deterministic_byte_identical():
    import json
    a = {s: [json.dumps(r, sort_keys=True) for r in rows] for s, rows in _src().splits().items()}
    b = {s: [json.dumps(r, sort_keys=True) for r in rows] for s, rows in _src().splits().items()}
    assert a == b

def test_all_three_domains_present():
    rows = [r for rows in _src().splits().values() for r in rows]
    assert {r["domain"] for r in rows} == {"crm_contact", "organization", "business"}

def test_balance_and_labels():
    rows = [r for rows in _src().splits().values() for r in rows]
    labels = [r["label"] for r in rows]
    frac = labels.count("match") / len(labels)
    assert 0.4 < frac < 0.6                       # ~50/50
    assert set(labels) <= {"match", "no_match"}

def test_record_pools_leakage_consistent():
    src = _src()
    splits, pools = src.splits(), src.record_pools()
    # every pooled record appears in exactly one split
    seen = {}
    for split, recs in pools.items():
        for rec in recs:
            k = tuple(sorted(rec.items()))
            assert k not in seen, "record in two splits"
            seen[k] = split
    assert all(pools[s] for s in ("train", "val", "test"))

def test_hard_negative_shares_name_but_conflicts_on_strong_id():
    # at least one no_match pair shares a surname yet differs on the domain strong id
    from schemas import DOMAINS
    rows = [r for rows in _src().splits().values() for r in rows]
    negs = [r for r in rows if r["label"] == "no_match"]
    def shares_name_conflicts_id(r):
        sid = DOMAINS[r["domain"]].strong_id
        a, b = r["a"], r["b"]
        name_a = a.get("last") or a.get("name") or a.get("legal_name") or ""
        name_b = b.get("last") or b.get("name") or b.get("legal_name") or ""
        return name_a and name_a == name_b and a.get(sid) != b.get(sid)
    assert any(shares_name_conflicts_id(r) for r in negs)
```

- [ ] **Step 2: Run -> FAIL.**

- [ ] **Step 3: Implement `SyntheticSource`.** Constructor `(name, seed, n_entities=..., profile="light", domain_weights=None, val_frac=0.15, test_frac=0.15)`. Build entities across the 3 domains (round-robin or weighted), each with a stable `eid` like `f"{domain}:{i}"`. For each entity: a base record (`build_record`) and >=1 corrupted duplicate (`corrupt_record`) -> positives = base vs dup (mirror febrl's star topology; positives are same-entity). Assign each entity to a split via `split_of(eid, seed=seed, val_frac=..., test_frac=...)`. **Negatives: call `synth_negatives` once PER DOMAIN per split**, with that domain's `DOMAINS[domain].name_field` as `block_keys` (so hard negatives genuinely collide on the name-bearing field for ALL three domains, not just one), `hard_frac=0.5`, `seed=seed`, `n=<that domain's positive count in the split>`; drop same-entity sampled pairs (mirror febrl). A single cross-domain `synth_negatives` call is WRONG here: the three domains use different name fields (`last`/`legal_name`/`name`), so one fixed `block_keys` would give empty keys (random, not hard negatives) for two of them. Emit `Row`s with `source="synthetic"`, `dataset=name`, `domain=<entity domain>`, `eid_a/eid_b`. Implement `record_pools()` mirroring febrl (every record grouped by its entity's split; raw field dicts). Follow febrl's `_entities_and_splits` factoring to keep `splits()` and `record_pools()` leakage-consistent.

- [ ] **Step 4: Run -> PASS**, then the whole package: `uv run python -m pytest scripts/er_matcher/synthetic/ -q`.

- [ ] **Step 5: Commit** `feat(er-matcher): SyntheticSource (3-domain generator + record_pools)`.

---

## Task 5: wire the synthetic source into the pipeline

**Files:** Modify `scripts/er_matcher/sources.yaml`, `scripts/er_matcher/sources_config.py`

- [ ] **Step 1: Read** `sources_config.py:137-142` (`_BUILDERS`) and an existing `sources.yaml` entry (e.g. `febrl`) to mirror the shape + how `seed`/config are threaded to the factory.

- [ ] **Step 2: Add the `synthetic` entry to `sources.yaml`.** Loader-specific args MUST nest under `kwargs:` (top-level unknown keys make `load_sources` raise via `_KNOWN_ENTRY_KEYS`). Mirror an existing entry's exact shape:
```yaml
synthetic:
  loader: synthetic
  mechanism: generate
  domain: multi          # SyntheticSource sets a per-ROW domain internally; this is just the entry's required field
  weight: 1.0            # illustrative (blend ratios deferred)
  kwargs:
    n_entities: 3000
    profile: light
```

- [ ] **Step 3: Add the factory** to `_BUILDERS`, matching the existing builders' `(e, seed) + **e.kwargs` style (the real `SourceEntry` exposes `.name` / `.domain` / `.kwargs` - there is NO `.params`): `"synthetic": lambda e, seed: SyntheticSource(name=e.name, seed=seed, **e.kwargs)`. `SyntheticSource.__init__`'s own defaults cover `n_entities`/`profile` when omitted. Do NOT forward `e.domain` (the source is multi-domain; domain is per-row). Top import of `SyntheticSource` is fine (it's pure).

- [ ] **Step 4: Test wiring** (extend `test_sources_config.py` or add a small test): building the `synthetic` entry returns a `SyntheticSource` whose `.splits()` yields rows; and `isinstance(SyntheticSource(...), PairSource)` holds (it exposes `name` + `splits`). Run with the goldenmatch-shadow env (Task-header note) since `sources_config` imports the loaders.

- [ ] **Step 5: Real integration check (small).** Run `build_corpus --fs-enrich` over a config that includes ONLY the synthetic source (or add `--only`/a temp sources file) at small `n_entities`. Confirm: it completes, synthetic rows carry an FS-driven `confidence` spread (not constant), some `fs_mined` negatives appear, `record_pools()` fed the mining. Command (goldenmatch-shadow env): `cd scripts/er_matcher && PYTHONPATH=... python -m build_corpus --sources <tmp-synthetic-only.yaml> --out-dir <tmp> --seed 20260728 --fs-enrich`. Capture counts.

- [ ] **Step 6: Commit** `feat(er-matcher): register synthetic source (sources.yaml + _BUILDERS)`.

---

## Task 6: Fodors-Zagats held-out benchmark

**Files:** Modify `scripts/er_matcher/sources/magellan.py`, `scripts/er_matcher/sota_baselines.py`

- [ ] **Step 1: Confirm the fetch URL.** Probe `https://pages.cs.wisc.edu/~anhai/data1/deepmatcher_data/Structured/Fodors-Zagats/exp_data/{tableA,tableB,train,valid,test}.csv` (HEAD/GET, like the SP3 WA/Beer URL verification). Record the exact `UrlName` casing (likely `Fodors-Zagats`).

- [ ] **Step 2: Add `fodors_zagats` to `magellan.py::_URL_NAMES`** = the confirmed UrlName. No other magellan change (the fetch shape is identical to WA/Beer).

- [ ] **Step 3: Add the SOTA row to `sota_baselines.py`** with published DeepMatcher/Ditto F1 for Fodors-Zagats (both papers report it; DeepMatcher ~1.0, Ditto ~1.0 - confirm exact figures from the papers, display-only, cite in a comment). Extend `test_sota_baselines.py` to assert `sota_for("fodors_zagats")` is present.

- [ ] **Step 4:** Run the sota-baselines test (`uv run python -m pytest scripts/er_matcher/test_sota_baselines.py -q`). PASS.

- [ ] **Step 5: Commit** `feat(er-matcher): Fodors-Zagats held-out benchmark (URL + SOTA)`.

---

## Task 7: Execute - build, retrain, measure (real Modal GPU + fetch; NOT CI)

> Execute step. On-disk `benzsevern` token. Set `PYTHONUTF8=1 PYTHONIOENCODING=utf-8` for the local Modal CLI on Windows. Modal long jobs: use `modal run --detach ...::evaluate_detached` (spawn) for anything long; foreground for short zeroshots. Corpus build needs the worktree-goldenmatch shadow env.

- [ ] **Step 1: Baseline the new benchmark FIRST.** Fetch Fodors-Zagats and run `zeroshot_eval --dataset fodors_zagats` against the CURRENT (SP3.5) merged model on the volume to get the **no-synthetic baseline** F1. (`GOLDENMATCH_ALLOW_FETCH=1` is set internally.) Record it.

- [ ] **Step 2: Build the enriched corpus WITH synthetic** (FEBRL + Leipzig + synthetic, `--fs-enrich`, seed 20260727). Confirm synthetic rows present with FS-driven confidence + leakage-clean splits + the block-shape log.

- [ ] **Step 3: Retrain** on the frozen SP2 config (`modal run scripts/er_matcher/modal_train.py::main`). Confirm the corpus loaded (pair count grew by the synthetic addition) and the merge completed.

- [ ] **Step 4: Measure** (fast logit eval + zeroshots): `evaluate` (in-distribution), `zeroshot --dataset fodors_zagats`, `--dataset walmart_amazon`, `--dataset beer`. Pull all scorecards.

- [ ] **Step 5: Report the decision.** Compare Fodors-Zagats F1 (with-synthetic) vs its Step-1 baseline, and WA/Beer vs SP3.5 (0.645 / 0.897). **Greenlight full Phase 1b if Fodors-Zagats improved; else recommend the model-scale pivot.** Open the Phase-1b-lean PR (Tasks 1-6 code) with the before/after; confirm with the user before the outward-facing push, then arm merge-on-green.

---

## Testing & conventions
- Box-safe: Tasks 1-4 (the `synthetic/` package) fully unit-tested, no GPU/network/goldenmatch import (they read bundled data files). Task 5 wiring verified by a real corpus build; Task 6 by the sota test + the real fetch in Task 7; Task 7 by the real Modal run.
- `ruff check --fix` touched files; NEVER `ruff format`. Commit per task; PR after Tasks 1-6.
- Reuse `synth_negatives` / `split_of` / the febrl `record_pools` pattern - do not reimplement negative sampling or splitting.

## Out of scope (deferred to full Phase 1b if the test passes)
- dsgen calibration histogram; healthcare + person synthetic domains; richer `--profile` levels; blend-ratio/person-upweight tuning; additional benchmarks; consolidating legacy `gen_pairs.py`.
