# ER-Matcher Honest Yardstick Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the ER-matcher's training signal and metric honest — entity-level splits, FS-mined hard negatives, and FS-score-driven soft confidence targets — then confirm with a retrain measured against the SP3 zero-shot held-out suite.

**Architecture:** A new pure, box-testable module `fs_enrich.py` runs one post-blend pass over the blended corpus: it attaches an FS-score-driven soft confidence to every pair and mines near-threshold gold non-matches as extra hard negatives, using an *injected* `scorer(a,b)->float`. `build_corpus.py` builds that scorer from goldenmatch's own FS pipeline (`auto_configure_df` -> `score_pair`/`build_blocks`). Entity-level splits (connected components over gold match edges) replace record-level splits in `splits.py`/`leipzig.py`. `train.py` reads per-row confidence instead of the fixed 0.9/0.1. A final Modal run rebuilds, retrains on the frozen SP2 config, and re-measures.

**Tech Stack:** Python 3.11 stdlib for the pure logic; goldenmatch's FS scorer/blocker for the real enrichment; torch/transformers on the Modal GPU path; pytest.

**Spec:** `docs/superpowers/specs/2026-07-28-er-matcher-honest-yardstick-design.md`

---

## File Structure

| Path | Change | Responsibility |
|---|---|---|
| `scripts/er_matcher/sources/splits.py` | **Modify** | Add `entity_keys_from_edges` (connected-components entity keying); keep `split_of` |
| `scripts/er_matcher/sources/leipzig.py` | **Modify** | Build gold edges, key records to entities, split on the entity key, generate negatives per-split, expose `record_pools()` |
| `scripts/er_matcher/sources/base.py` | **Modify** | Add `record_pools()` to the source protocol/ABC |
| `scripts/er_matcher/sources/febrl.py` | **Modify** | Expose `record_pools()` (group by `_entity_of`'s split); split logic unchanged |
| `scripts/er_matcher/fs_enrich.py` | **Create** | Pure: `soft_confidence`, `select_hard_negatives`, `enrich` orchestration, cache key |
| `scripts/er_matcher/build_corpus.py` | **Modify** | Build the real goldenmatch FS scorer/blocker closure; call `fs_enrich.enrich` |
| `scripts/er_matcher/train.py` | **Modify** | Read per-row `confidence`; drop the fixed 0.9/0.1 default path |
| `scripts/er_matcher/modal_train.py` | **Modify** | Nothing structural; the frozen-config retrain uses existing `train_full` |
| `scripts/er_matcher/test_splits.py` | **Modify** | Entity-keying + anti-leakage invariant tests |
| `scripts/er_matcher/test_fs_enrich.py` | **Create** | Soft-target + mining + orchestration tests (injected fake scorer) |
| `scripts/er_matcher/test_train_helpers.py` | **Modify** | Per-row confidence read test |

**Boundary:** Tasks 1-5 are pure and box-tested (no GPU, no goldenmatch import — a fake scorer is injected). Task 6 (goldenmatch wiring) is verified by a real corpus build. Task 7 (retrain + measure) is verified by the real Modal run, same boundary as SP2/SP3.

**Leakage-free ordering (applies across Tasks 1 + 6):** split records into train/val/test FIRST (by entity), THEN generate/mine negatives WITHIN each split's record pool only. This guarantees both endpoints of every negative live in the same split, so no entity crosses the split boundary through a pair.

---

## Task 1: Entity-level splits (pure, TDD)

**Files:**
- Modify: `scripts/er_matcher/sources/splits.py`
- Modify: `scripts/er_matcher/sources/leipzig.py`
- Test: `scripts/er_matcher/test_splits.py`

- [ ] **Step 1: Read the current split logic.** Read `sources/splits.py` (the `split_of` hash) and `sources/leipzig.py:30-100` (the record-level `split_of(eid_a)` call the design flags as leaky). Confirm the gold match mapping available in `leipzig.py` (the perfect-match pairs) so you know the edge source.

- [ ] **Step 2: Write the failing test** for connected-components entity keying + the anti-leakage invariant. Match the existing `test_splits.py` import style (`from sources.splits import ...`) — do NOT add a second `sources`-on-path import that double-loads the module. NOTE the real `split_of` signature is `split_of(eid, *, seed, val_frac, test_frac, holdout_domain=None, domain=None)` — `seed`/`val_frac`/`test_frac` are REQUIRED keyword-only args; pass them.

```python
from sources.splits import entity_keys_from_edges, split_of

def test_connected_components_merges_transitively():
    # A1-B1 and B1-A2 gold edges => {A1,B1,A2} are ONE entity
    keys = entity_keys_from_edges(
        ["A1", "A2", "B1", "B2"],
        [("A1", "B1"), ("B1", "A2")],
    )
    assert keys["A1"] == keys["B1"] == keys["A2"]
    assert keys["B2"] != keys["A1"]          # unconnected -> own entity
    # deterministic canonical key (min member), order-independent
    assert keys["A1"] == "A1"

def test_singletons_get_own_key():
    keys = entity_keys_from_edges(["X", "Y"], [])
    assert keys["X"] == "X" and keys["Y"] == "Y" and keys["X"] != keys["Y"]

def test_entity_split_has_no_leakage():
    # every record of an entity must land in exactly one split
    ids = [f"A{i}" for i in range(50)] + [f"B{i}" for i in range(50)]
    edges = [(f"A{i}", f"B{i}") for i in range(50)]        # 50 two-record entities
    keys = entity_keys_from_edges(ids, edges)
    sp = {rid: split_of(keys[rid], seed=1, val_frac=0.15, test_frac=0.15) for rid in ids}
    for i in range(50):                                    # both records share a split
        assert sp[f"A{i}"] == sp[f"B{i}"]
```

- [ ] **Step 3: Run the test to verify it fails.** Run: `uv run python -m pytest scripts/er_matcher/test_splits.py -k "connected or singletons or leakage" -v`. Expected: FAIL (`entity_keys_from_edges` undefined).

- [ ] **Step 4: Implement `entity_keys_from_edges` in `sources/splits.py`** (union-find, deterministic canonical key = min member):

```python
from collections.abc import Iterable

def entity_keys_from_edges(
    record_ids: Iterable[str], edges: Iterable[tuple[str, str]]
) -> dict[str, str]:
    """Map each record id to a stable entity key via connected components over the
    gold match edges. Records with no edges are their own singleton entity. The key
    is the min member id, so it is deterministic regardless of union order."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path halving
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for rid in record_ids:
        find(rid)
    for a, b in edges:
        union(a, b)

    groups: dict[str, list[str]] = {}
    for rid in list(parent):
        groups.setdefault(find(rid), []).append(rid)
    key_of: dict[str, str] = {}
    for members in groups.values():
        canonical = min(members)
        for m in members:
            key_of[m] = canonical
    return key_of
```

- [ ] **Step 5: Run the test to verify it passes.** Run the same command. Expected: PASS.

- [ ] **Step 6: Wire entity splits into `leipzig.py`.** Replace the record-level `split_of(eid_a, ...)` with: build gold edges from `_read_gold_pairs` (the perfect-match mapping), compute `key_of = entity_keys_from_edges(all_record_ids, gold_edges)`, and assign each record's split via `split_of(key_of[record_id], seed=..., val_frac=..., test_frac=...)` (reuse the seed/fracs the loader already passes). Move negative generation to happen AFTER records are split, over each split's record pool only (per the leakage-free ordering). Keep the FEBRL loader's split logic unchanged (it already splits at entity level via `_entity_of`).

- [ ] **Step 6b: Expose per-split record pools (needed by Task 6 mining).** `PairSource.splits()` yields labeled *pairs* only; hard-negative mining needs the raw per-split *record pool* (including never-paired records). Add a method to the source protocol — `record_pools(self) -> dict[str, list[dict]]` returning `{split: [record dicts]}`, where each record is assigned to the split of its entity (the same `split_of(key_of[record_id], ...)` used in Step 6, so pools are leakage-consistent with the pairs). Implement it in `sources/base.py` (protocol/ABC), `sources/leipzig.py` (group `tableA`+`tableB` records by their entity's split), and `sources/febrl.py` (group records by `_entity_of`'s split). Sources with no meaningful record pool may return `{}`.

- [ ] **Step 6c: Test `record_pools` leakage-consistency.** Add a test asserting: (a) every record id in `record_pools()` appears in exactly one split, and (b) for a known gold-linked record pair, both records land in the same split as their pairs do. Run: `uv run python -m pytest scripts/er_matcher/test_leipzig.py scripts/er_matcher/test_febrl.py -k record_pool -v`. Expected: PASS.

- [ ] **Step 7: Run the full sources test suite.** Run: `uv run python -m pytest scripts/er_matcher/test_splits.py scripts/er_matcher/test_leipzig.py scripts/er_matcher/test_febrl.py scripts/er_matcher/test_sources_base.py -q`. Expected: PASS (fix any leipzig test that assumed record-level splits — update the assertion to the entity-level behavior, do not weaken the invariant).

- [ ] **Step 8: Commit.** `git add scripts/er_matcher/sources/ scripts/er_matcher/test_splits.py scripts/er_matcher/test_leipzig.py scripts/er_matcher/test_febrl.py && git commit -m "feat(er-matcher): entity-level splits + per-split record pools (no leakage)"`

---

## Task 2: FS score -> soft confidence (pure, TDD)

**Files:**
- Create: `scripts/er_matcher/fs_enrich.py`
- Test: `scripts/er_matcher/test_fs_enrich.py`

- [ ] **Step 1: Write the failing test.** Header: `import os, sys; sys.path.insert(0, os.path.dirname(__file__))`.

```python
from fs_enrich import soft_confidence

def test_soft_confidence_monotonic_and_clamped():
    # match: higher FS score -> higher confidence, never > 0.97, floor 0.55 near/below tau
    assert soft_confidence(1.0, True, tau=0.5) == 0.97
    assert soft_confidence(0.5, True, tau=0.5) == 0.55          # at threshold -> uncertain
    assert soft_confidence(0.1, True, tau=0.5) == 0.55          # matcher missed it -> floor, not 0
    mid = soft_confidence(0.75, True, tau=0.5)
    assert 0.55 < mid < 0.97
    # non-match: lower FS score -> lower confidence, never < 0.03, ceiling 0.45 near/above tau
    assert soft_confidence(0.0, False, tau=0.5) == 0.03
    assert soft_confidence(0.5, False, tau=0.5) == 0.45         # at threshold -> uncertain
    assert soft_confidence(0.9, False, tau=0.5) == 0.45         # matcher wrongly high -> ceiling
    nm = soft_confidence(0.25, False, tau=0.5)
    assert 0.03 < nm < 0.45

def test_soft_confidence_never_hits_0_or_1():
    for s in (0.0, 0.5, 1.0):
        for m in (True, False):
            c = soft_confidence(s, m, tau=0.5)
            assert 0.0 < c < 1.0
```

- [ ] **Step 2: Run to verify it fails.** Run: `uv run python -m pytest scripts/er_matcher/test_fs_enrich.py -k soft_confidence -v`. Expected: FAIL (module/function missing).

- [ ] **Step 3: Implement `soft_confidence` in `fs_enrich.py`.**

```python
"""Post-blend FS enrichment: FS-score-driven soft confidence targets + hard-negative
mining. Pure and box-testable; the FS matcher is injected as scorer(a,b)->float."""


def soft_confidence(
    score: float,
    is_match: bool,
    *,
    tau: float = 0.5,
    hi: float = 0.97,
    lo: float = 0.03,
    mid_hi: float = 0.55,
    mid_lo: float = 0.45,
) -> float:
    """Map an FS match-probability to a P(match) training target, compressed toward
    0.5 near the decision threshold tau. Gold label picks the direction; the score's
    distance from tau picks how extreme. Never 0 or 1."""
    s = min(max(score, 0.0), 1.0)
    if is_match:
        frac = max((s - tau) / (1.0 - tau), 0.0) if tau < 1.0 else 0.0
        return mid_hi + (hi - mid_hi) * min(frac, 1.0)
    frac = min(s / tau, 1.0) if tau > 0.0 else 0.0
    return lo + (mid_lo - lo) * frac
```

- [ ] **Step 4: Run to verify it passes.** Same command. Expected: PASS.

- [ ] **Step 5: Commit.** `git add scripts/er_matcher/fs_enrich.py scripts/er_matcher/test_fs_enrich.py && git commit -m "feat(er-matcher): FS-score-driven soft confidence target"`

---

## Task 3: Hard-negative selection (pure, TDD)

**Files:**
- Modify: `scripts/er_matcher/fs_enrich.py`
- Test: `scripts/er_matcher/test_fs_enrich.py`

- [ ] **Step 1: Write the failing test.**

```python
from fs_enrich import select_hard_negatives

def _cand(a, b, score, gold):  # helper: a scored candidate with its gold label
    return {"a_id": a, "b_id": b, "score": score, "gold_match": gold}

def test_select_keeps_near_threshold_gold_nonmatches_only():
    cands = [
        _cand("a", "b", 0.52, False),   # near tau, gold NON-match -> KEEP (hard neg)
        _cand("c", "d", 0.99, True),    # gold MATCH -> reject (not a negative)
        _cand("e", "f", 0.05, False),   # far below band -> reject (easy)
        _cand("g", "h", 0.48, False),   # near tau, gold NON-match -> KEEP
    ]
    out = select_hard_negatives(cands, tau=0.5, delta=0.1, cap=10)
    kept = {(c["a_id"], c["b_id"]) for c in out}
    assert kept == {("a", "b"), ("g", "h")}

def test_select_caps_and_is_deterministic():
    cands = [_cand(f"a{i}", f"b{i}", 0.5, False) for i in range(20)]
    out = select_hard_negatives(cands, tau=0.5, delta=0.1, cap=5)
    assert len(out) == 5
    assert out == select_hard_negatives(cands, tau=0.5, delta=0.1, cap=5)  # deterministic
```

- [ ] **Step 2: Run to verify it fails.** Run: `uv run python -m pytest scripts/er_matcher/test_fs_enrich.py -k select -v`. Expected: FAIL.

- [ ] **Step 3: Implement `select_hard_negatives`.**

```python
def select_hard_negatives(
    scored_candidates: list[dict],
    *,
    tau: float = 0.5,
    delta: float = 0.1,
    cap: int,
) -> list[dict]:
    """Keep candidates whose FS score is within [tau-delta, tau+delta] AND whose gold
    label is non-match. Sort by closeness to tau (hardest first) for a deterministic
    cap. Gold label is the truth; FS only selects difficulty."""
    band = [
        c for c in scored_candidates
        if not c["gold_match"] and abs(c["score"] - tau) <= delta
    ]
    band.sort(key=lambda c: (abs(c["score"] - tau), c["a_id"], c["b_id"]))
    return band[:cap]
```

- [ ] **Step 4: Run to verify it passes.** Same command. Expected: PASS.

- [ ] **Step 5: Commit.** `git add -A scripts/er_matcher/fs_enrich.py scripts/er_matcher/test_fs_enrich.py && git commit -m "feat(er-matcher): near-threshold hard-negative selection"`

---

## Task 4: `enrich` orchestration + cache key (pure, TDD, injected scorer)

**Files:**
- Modify: `scripts/er_matcher/fs_enrich.py`
- Test: `scripts/er_matcher/test_fs_enrich.py`

- [ ] **Step 1: Write the failing test** (fake scorer + fake candidate generator, no goldenmatch).

```python
from fs_enrich import enrich, cache_key

def test_enrich_attaches_soft_conf_and_appends_mined_negs():
    pairs = [
        {"a": {"id": "a"}, "b": {"id": "b"}, "label": "match", "eid_a": "a", "eid_b": "b"},
        {"a": {"id": "c"}, "b": {"id": "d"}, "label": "no_match", "eid_a": "c", "eid_b": "d"},
    ]
    # fake FS scorer: match->0.9, the mined candidate scores 0.5 (near tau)
    def scorer(x, y):
        return {("a", "b"): 0.9, ("c", "d"): 0.1, ("e", "f"): 0.5}[(x["id"], y["id"])]
    # fake candidate gen yields one near-threshold gold non-match
    def candidates(records):
        return [{"a": {"id": "e"}, "b": {"id": "f"}, "gold_match": False,
                 "eid_a": "e", "eid_b": "f"}]
    out = enrich(pairs, records=[{"id": "e"}, {"id": "f"}], scorer=scorer,
                 candidates_fn=candidates, tau=0.5, delta=0.1, mine_cap=10)
    # every original pair now carries a per-row confidence in (0,1)
    assert all(0.0 < p["confidence"] < 1.0 for p in out)
    # the mined near-threshold non-match was appended as a hard negative
    mined = [p for p in out if (p["eid_a"], p["eid_b"]) == ("e", "f")]
    assert len(mined) == 1 and mined[0]["label"] == "no_match"

def test_cache_key_stable_and_sensitive():
    a = cache_key(corpus_hash="h1", scorer_cfg="c1", tau=0.5, delta=0.1)
    assert a == cache_key(corpus_hash="h1", scorer_cfg="c1", tau=0.5, delta=0.1)
    assert a != cache_key(corpus_hash="h2", scorer_cfg="c1", tau=0.5, delta=0.1)
```

- [ ] **Step 2: Run to verify it fails.** Run: `uv run python -m pytest scripts/er_matcher/test_fs_enrich.py -k "enrich or cache_key" -v`. Expected: FAIL.

- [ ] **Step 3: Implement `enrich` + `cache_key`.**

```python
import hashlib


def cache_key(*, corpus_hash: str, scorer_cfg: str, tau: float, delta: float) -> str:
    raw = f"{corpus_hash}|{scorer_cfg}|{tau:.4f}|{delta:.4f}"
    return hashlib.sha256(raw.encode()).hexdigest()


def enrich(
    pairs: list[dict],
    *,
    records: list[dict],
    scorer,
    candidates_fn,
    tau: float = 0.5,
    delta: float = 0.1,
    mine_cap: int,
) -> list[dict]:
    """Attach FS-score-driven soft confidence to every pair, and append near-threshold
    gold non-matches mined from candidates_fn(records). Pure given injected scorer +
    candidates_fn. Records must already be within a single split (leakage-free ordering)."""
    enriched = []
    for p in pairs:
        s = scorer(p["a"], p["b"])
        conf = soft_confidence(s, p["label"] == "match", tau=tau)
        enriched.append({**p, "confidence": round(conf, 4), "fs_score": round(s, 4)})

    scored_cands = [
        {**c, "score": scorer(c["a"], c["b"])} for c in candidates_fn(records)
    ]
    for c in select_hard_negatives(scored_cands, tau=tau, delta=delta, cap=mine_cap):
        conf = soft_confidence(c["score"], False, tau=tau)
        enriched.append({
            "a": c["a"], "b": c["b"], "label": "no_match",
            "eid_a": c["eid_a"], "eid_b": c["eid_b"],
            "confidence": round(conf, 4), "fs_score": round(c["score"], 4),
            "negative_kind": "fs_mined",
        })
    return enriched
```

- [ ] **Step 4: Run to verify it passes.** Same command. Expected: PASS. Then run the whole module: `uv run python -m pytest scripts/er_matcher/test_fs_enrich.py -q`.

- [ ] **Step 5: Commit.** `git add -A scripts/er_matcher/fs_enrich.py scripts/er_matcher/test_fs_enrich.py && git commit -m "feat(er-matcher): fs_enrich orchestration + cache key"`

---

## Task 5: `train.py` reads per-row confidence (pure, TDD)

**Files:**
- Modify: `scripts/er_matcher/train.py`
- Test: `scripts/er_matcher/test_train_helpers.py`

- [ ] **Step 1: Read** `train.py` — the real helper is `example_to_messages(row: dict, cfg: TrainConfig) -> list[dict[str, str]]`, returning a 3-message chat list (`system`/`user`/`assistant`) where the **assistant** message's `content` holds the `render_target(...)` JSON. Confirm `DEFAULT_MATCH_CONF` (0.9) / `DEFAULT_NOMATCH_CONF` (0.1) and how `example_to_messages` currently chooses the confidence. Read `test_train_helpers.py`'s existing import header + how it builds a `TrainConfig` and a minimal row, and mirror that.

- [ ] **Step 2: Write the failing test** asserting the per-row confidence is used when present, falling back to the constant only when absent. Extract the assistant message content and assert on the compact JSON (`render_target` uses `separators=(",", ":")`, so it's `"confidence":0.62` with no space).

```python
def _assistant(msgs):
    return next(m["content"] for m in msgs if m["role"] == "assistant")

def test_row_confidence_overrides_constant():
    cfg = _make_cfg()                       # same TrainConfig the other tests build
    row = {"a": _rec(), "b": _rec(), "label": "match", "confidence": 0.62}
    msgs = example_to_messages(row, cfg)
    assert '"confidence":0.62' in _assistant(msgs)

def test_missing_confidence_falls_back_to_constant():
    cfg = _make_cfg()
    row = {"a": _rec(), "b": _rec(), "label": "match"}    # no confidence
    msgs = example_to_messages(row, cfg)
    assert '"confidence":0.9' in _assistant(msgs)         # DEFAULT_MATCH_CONF
```
(`_make_cfg`/`_rec` = the minimal `TrainConfig` + record helpers already used in `test_train_helpers.py`; reuse them, don't invent new shapes.)

- [ ] **Step 3: Run to verify it fails.** Run: `uv run python -m pytest scripts/er_matcher/test_train_helpers.py -k confidence -v`. Expected: FAIL (the current code ignores `row["confidence"]`).

- [ ] **Step 4: Implement.** In `example_to_messages`, use `row.get("confidence", DEFAULT_MATCH_CONF if match else DEFAULT_NOMATCH_CONF)` as the confidence passed to `render_target`. Keep the constants as the fallback only.

- [ ] **Step 5: Run to verify it passes**, then the train-helper suite: `uv run python -m pytest scripts/er_matcher/test_train_helpers.py -q`. Expected: PASS.

- [ ] **Step 6: Commit.** `git add scripts/er_matcher/train.py scripts/er_matcher/test_train_helpers.py && git commit -m "feat(er-matcher): train reads per-row confidence, constant is fallback"`

---

## Task 6: Wire the real goldenmatch FS scorer into `build_corpus` (integration)

**Files:**
- Modify: `scripts/er_matcher/build_corpus.py`

This task uses the heavy goldenmatch package, so it is verified by a real corpus build, not a box test. Keep the goldenmatch imports INSIDE the scorer-builder function (not at module top level) so the box suite stays clean.

- [ ] **Step 1: Discovery — choose the scorer entry point (must return a P(match) in [0,1]).** The soft-confidence and band-selection math (Tasks 2-3) assume `score in [0,1]` centered on `tau`. `auto_configure_df` + `score_pair` (`scorer.py:536`) gives the *weighted heuristic* aggregator, whose output is NOT guaranteed to be a `[0,1]` probability — so **prefer the genuine FS posterior path**: `auto_configure_probabilistic_df` (`autoconfig.py:5220`) to fit an `EMResult`, then `score_pair_probabilistic` (`probabilistic.py:3925`), which returns the Fellegi-Sunter posterior P(match) in `[0,1]`. This also matches the design's "dogfood goldenmatch's own FS pipeline" framing. Discovery to record: (a) the exact args `auto_configure_probabilistic_df` needs and what it returns (config + `EMResult`?), (b) the exact signature of `score_pair_probabilistic` (does it take the `EMResult` + fields, or a bound scorer?), (c) how to get the runtime fields it wants from the returned config (grep `grep -rn "MatchkeyField(" packages/python/goldenmatch/goldenmatch/core/` and check `probabilistic.py`/`pipeline.py` for a compile/build-fields helper), (d) the FS decision threshold `tau` (from the config/EMResult; default 0.5 if absent). **Fallback:** if EM degenerates on a split's pool (see `feedback_fs_measure_degenerate_em_harness` — EM can degenerate on small/tiny data), fall back to the weighted `score_pair` normalized into `[0,1]` (e.g. min-max over the split's scored pairs) and log that the split used the fallback.

- [ ] **Step 2: Write `_make_fs_scorer(records, domain)`** in `build_corpus.py` using the Step 1 posterior path. Heavy goldenmatch imports stay INSIDE the function (box-safety):

```python
def _make_fs_scorer(records: list[dict], domain: str):
    """Build (scorer, candidates_fn, tau, scorer_cfg_str) from goldenmatch's FS
    posterior for a single split's record pool. scorer(a,b) MUST return P(match) in
    [0,1]. Heavy imports local."""
    import pyarrow as pa
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    from goldenmatch.core.blocker import build_blocks
    # + score_pair_probabilistic and the fields/EMResult per Step 1 discovery

    table = pa.Table.from_pylist(records)
    cfg, em = _fit_fs_posterior(table)         # auto_configure_probabilistic_df (Step 1)
    fields = _fields_from_config(cfg)          # runtime MatchkeyFields (Step 1)
    tau = _threshold_from_config(cfg, em)      # FS threshold; 0.5 if absent

    def scorer(a: dict, b: dict) -> float:
        p = _score_posterior(a, b, fields, em)  # score_pair_probabilistic (Step 1)
        return min(max(float(p), 0.0), 1.0)     # guaranteed [0,1]

    def candidates_fn(recs: list[dict]) -> list[dict]:
        blocks = build_blocks(pa.Table.from_pylist(recs), cfg.blocking)
        return _pairs_from_blocks(blocks, recs)  # within-block pairs; gold_match from labels

    scorer_cfg = cfg.model_dump_json()          # stable string for the cache key
    return scorer, candidates_fn, tau, scorer_cfg
```
(Implement `_fit_fs_posterior`, `_fields_from_config`, `_threshold_from_config`, `_score_posterior`, `_pairs_from_blocks` per Step 1 + the `BlockResult` shape at `blocker.py:266`. `_pairs_from_blocks` sets `gold_match` from the source's gold mapping — a mined pair is a non-match iff the two records are not gold-linked.)

- [ ] **Step 2b: Verify the score is in [0,1] AND separates** (spec risk: garbage-in). After building the scorer for one split, assert every score is within `[0,1]`, then score ~200 known-match and ~200 known-non-match pairs and print `mean(score|match)` vs `mean(score|non-match)`. If scores fall outside `[0,1]`, or the means don't separate, STOP and surface — the config/EM fit is wrong and soft targets built on it would be garbage (use the Step 1 fallback or fix the config before proceeding).

- [ ] **Step 3: Call `fs_enrich.enrich` per split** in `build_corpus.py`, threading the cache. Get the per-split record pool from the source's new `record_pools()` (Task 1 Step 6b): `pools = source.record_pools()`. For each split, build the scorer over that split's pool and enrich that split's pairs:

```python
pools = source.record_pools()
for split, split_pairs in blended_by_split.items():
    recs = pools.get(split, [])
    scorer, candidates_fn, tau, scorer_cfg = _make_fs_scorer(recs, source.domain)
    enriched = enrich(split_pairs, records=recs, scorer=scorer,
                      candidates_fn=candidates_fn, tau=tau, delta=DELTA, mine_cap=MINE_CAP)
```
Cache the enriched output under `fs_enrich.cache_key(corpus_hash=..., scorer_cfg=scorer_cfg, tau=tau, delta=DELTA)` so rebuilds skip the FS pass. Write the enriched pairs (now carrying `confidence`) to the corpus JSONL. `DELTA`/`MINE_CAP` are module constants (start `DELTA=0.1`, `MINE_CAP` ~= the count of existing synthetic hard negatives per split so mined negatives augment rather than dominate).

- [ ] **Step 4: Guard `eval_only` sources.** Confirm the enrichment loop only runs over `bundle`/`generate` sources; `magellan`/`ncvr` stay untouched (they never enter training). Re-run `test_build_corpus.py` to confirm the eval_only contribution invariant still holds.

- [ ] **Step 5: Real corpus build (small).** Run the corpus build over a small slice (e.g. FEBRL dataset1 + one Leipzig benchmark). Confirm: it completes, the JSONL rows carry a `confidence` field with a spread of values (not all 0.9/0.1), the separation check in Step 2b passed, and mined `fs_mined` negatives appear. Capture the counts.

- [ ] **Step 6: Commit.** `git add scripts/er_matcher/build_corpus.py && git commit -m "feat(er-matcher): wire goldenmatch FS scorer into corpus enrichment"`

---

## Task 7: Retrain + re-measure (execution — real Modal GPU, NOT box-tested)

> Execute step (real Modal GPU + on-disk `benzsevern` token). NOT CI. Same boundary as SP3 T6. Set `PYTHONUTF8=1 PYTHONIOENCODING=utf-8` for the local Modal CLI on Windows.

- [ ] **Step 1: Build the full enriched corpus** (FEBRL + 4 Leipzig, entity-split, soft targets, mined negatives). Confirm the leakage invariant (no entity id in two splits) and the FS-score separation check on the logs.

- [ ] **Step 2: Retrain on the FROZEN SP2 config.** Qwen2.5-3B-Instruct, bf16 LoRA, 2 epochs, all hyperparams identical to SP2 — the ONLY changes are the data (entity splits + mined negatives) and the per-row soft confidence targets. Run via the existing `modal_train.py::full` / `train_full` entrypoint against the new corpus.

- [ ] **Step 3: Measure.** Run in-distribution eval (`modal_train.py::evaluate`) AND the SP3 held-out zero-shot suite (`modal_train.py::zeroshot --dataset walmart_amazon`, `--dataset beer`). Pull the scorecards.

- [ ] **Step 4: Check the success criteria** (from the spec):
  - In-distribution F1 should DROP from 0.983 (honest metric).
  - SP3 zero-shot F1 (WA 0.640 / Beer 0.897 baseline) should HOLD or IMPROVE.
  - Raw ECE should improve sharply vs SP2's 0.46 (soft targets), ideally near the post-hoc calibrated level without temp scaling.
  - Confirm the mined-negative and leakage watch-items on the logs before trusting the numbers.

- [ ] **Step 5: Report + open the PR.** Summarize before/after (in-distribution F1, zero-shot F1 vs SP3 baseline, raw + calibrated ECE) and open the SP3.5 PR (Tasks 1-6 code). The PR push is outward-facing — confirm with the user first, then arm merge-on-green.

---

## Testing & conventions
- Box-safe: Tasks 1-5 fully unit-tested, no GPU/goldenmatch import. Task 6 verified by the real corpus build; Task 7 by the real Modal run.
- `sys.path.insert(0, os.path.dirname(__file__))` test header; add `sources/` to the path for split tests.
- `ruff check --fix` on touched files; NEVER `ruff format`. Commit per task.
- Leakage-free ordering (split records first, then generate/mine negatives within each split) is load-bearing — do not reorder.

## Out of scope
- 7B base model / hyperparameter sweep (epochs, lr, LoRA rank).
- Rich Synthetic Generator (Phase 1b data diversity) — the next step.
- New sources (MusicBrainz-20K, NCVR real-person loader).
