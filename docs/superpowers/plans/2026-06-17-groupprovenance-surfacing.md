# GroupProvenance Surfacing Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the lock-step `GroupProvenance` that `resolve_cluster` already computes across every Python consumer (the golden-record provenance objects, lineage JSON, CLI explain, the MCP `lineage` tool, and a review-queue golden-composition view), with byte-identical output when no survivorship levers are used.

**Architecture:** `resolve_cluster` is the single source of truth. Its rich `ClusterProvenance` (groups + conditional/validated field provenance), today discarded as `_prov` in `build_golden_records_batch`, is stamped with the real `cluster_id` and carried on the enriched survivorship rows under an internal `__survivorship_prov__` key; `golden_records_to_provenance` consumes it instead of its lossy row-reconstruction. The structured `groups` then ride the existing `save_lineage` `"golden_records"` JSON section (via `asdict`); the dead `render_cluster_provenance_nl` NL renderer is wired into the CLI cluster-explain path and a per-cluster `"audit"` JSON string; `cli/lineage` and the MCP `_tool_lineage` thread `golden_provenance` in. `build_lineage` (per-pair) is untouched.

**Tech Stack:** Python 3.11+, Polars, Pydantic v2, frozen dataclasses, pytest. Spec: `docs/superpowers/specs/2026-06-17-groupprovenance-surfacing-design.md`.

**Dependency:** Stacks on correlated-survivorship v1 (PR #1047). Branch this off `origin/main` only AFTER #1047 merges (the `goldenmatch/core/survivorship/` package, `GroupProvenance`, and the renderers must be present).

---

## Conventions for every task

- **Run tests** (per project CLAUDE.md — targeted local runs only, never the full xdist suite locally):
  `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 <repo>/.venv/Scripts/python.exe -m pytest <path> -v`
  (set `GOLDENMATCH_NATIVE=0` if a stale native wheel interferes).
- **Commit** after each green step. Squash-merge via PR at the end (branch/merge SOP).
- **No em dashes / ASCII only** in committed strings.
- New tests live in `packages/python/goldenmatch/tests/survivorship/` (mirrors v1).

---

## File Structure

**Modified files**
- `packages/python/goldenmatch/goldenmatch/core/survivorship/resolve.py` — `resolve_cluster` gains a `cluster_id` param so the returned `ClusterProvenance` is correctly stamped (no `-1`).
- `packages/python/goldenmatch/goldenmatch/core/golden.py` — `build_golden_records_batch` survivorship branch stamps + carries the rich prov on rows; `golden_records_to_provenance` consumes it; `build_golden_record_with_provenance` thin survivorship delegation; confirm `__survivorship_prov__` is stripped from golden output.
- `packages/python/goldenmatch/goldenmatch/core/lineage.py` — `save_lineage` / `save_lineage_streaming` emit a per-cluster `"audit"` NL string in the `golden_records` section via a fail-open `render_cluster_provenance_nl` wrapper.
- `packages/python/goldenmatch/goldenmatch/core/explain.py` — `explain_cluster_nl` gains an optional `cluster_provenance` param and appends a `Survivorship:` block.
- `packages/python/goldenmatch/goldenmatch/cli/explain.py` — `--cluster` path computes the cluster's `ClusterProvenance` and passes it to `explain_cluster_nl`.
- `packages/python/goldenmatch/goldenmatch/cli/lineage.py` + `goldenmatch/mcp/server.py` (`_tool_lineage`) — thread `golden_provenance` into `save_lineage` via a shared helper.
- `packages/python/goldenmatch/goldenmatch/core/review_queue.py` — `ReviewItem.golden_composition` field + populate at enqueue from the cluster's `ClusterProvenance`.
- Docs: `docs-site/` golden-record + lineage pages, `docs-site/goldenmatch/tuning.mdx`.

**New test files**
- `tests/survivorship/test_provenance_surfacing.py` (Phase A)
- `tests/survivorship/test_lineage_golden_records.py` (Phase B)
- `tests/survivorship/test_explain_cluster_survivorship.py` (Phase C)
- `tests/survivorship/test_lineage_tool_provenance.py` (Phase D)
- `tests/survivorship/test_review_golden_composition.py` (Phase E)
- `tests/survivorship/test_surfacing_parity.py` (parity gate, Phase A + final)

---

## Task 0: Branch setup

**Files:** none (git only)

- [ ] **Step 1:** Confirm #1047 is merged to `main`, then branch off fresh `origin/main` (this repo's main moves fast — branch off freshly-fetched origin/main).

```bash
git fetch origin
git switch -c feat/groupprovenance-surfacing origin/main
# sanity: the survivorship package + renderers must exist
ls packages/python/goldenmatch/goldenmatch/core/survivorship/
grep -n "def render_cluster_provenance_nl" packages/python/goldenmatch/goldenmatch/core/lineage.py
```

- [ ] **Step 2:** Smoke-run an existing golden test to confirm the env is green.

Run: `POLARS_SKIP_CPU_CHECK=1 .venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_golden.py -q`
Expected: PASS.

---

## Phase A — Core wiring (groups reach GoldenRecordResult.provenance)

### Task A1: `resolve_cluster` stamps the real `cluster_id`

**Files:**
- Modify: `core/survivorship/resolve.py`
- Test: `tests/survivorship/test_provenance_surfacing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/survivorship/test_provenance_surfacing.py
import polars as pl
from goldenmatch.config.schemas import GoldenRulesConfig, GoldenGroupRule
from goldenmatch.core.survivorship.conditions import build_resolution_order
from goldenmatch.core.survivorship.resolve import resolve_cluster


def _addr_rules():
    return GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", columns=["street", "city", "zip"])],
    )


def _cluster_df():
    return pl.DataFrame({
        "__cluster_id__": [5, 5],
        "__row_id__": [10, 11],
        "street": ["1 Main St", "1 Main"],
        "city": ["LA", "LA"],
        "zip": [None, "90001"],
    })


def test_resolve_cluster_stamps_cluster_id():
    rules = _addr_rules()
    order = build_resolution_order(rules.field_rules, rules.field_groups, ["street", "city", "zip"])
    _, prov = resolve_cluster(_cluster_df(), rules, order, provenance=True, cluster_id=5)
    assert prov is not None
    assert prov.cluster_id == 5          # not the -1 placeholder
    assert len(prov.groups) == 1
    assert prov.groups[0].name == "addr"
```

- [ ] **Step 2: Run to verify it fails** (TypeError: unexpected keyword 'cluster_id').

- [ ] **Step 3: Implement.** In `resolve_cluster`, add the param and use it when stamping:

```python
def resolve_cluster(cluster_df, rules, resolution_order, *,
                    quality_scores=None, pair_scores=None, provenance=False,
                    cluster_id=None):
    ...
    prov = None
    if provenance:
        prov = ClusterProvenance(
            cluster_id=cluster_id if cluster_id is not None else -1,
            cluster_quality="strong", cluster_confidence=0.0,
            fields=field_provs, groups=group_provs,
        )
    return result, prov
```

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(survivorship): resolve_cluster stamps real cluster_id on provenance`.

### Task A2: batch survivorship branch carries the rich prov on rows

**Files:**
- Modify: `core/golden.py` (`build_golden_records_batch` survivorship branch, ~L884-897)
- Test: `tests/survivorship/test_provenance_surfacing.py` (append)

- [ ] **Step 1: Write the failing test** — assert the enriched rows carry the prov for downstream consumption.

```python
from goldenmatch.core.golden import build_golden_records_batch


def test_batch_survivorship_rows_carry_provenance():
    df = _cluster_df()
    # NOTE: build_golden_records_batch is (multi_df, rules, quality_scores=None,
    # provenance=False, cluster_pair_scores=None) -- there is NO user_cols arg;
    # it derives user columns internally.
    rows = build_golden_records_batch(df, _addr_rules(), provenance=True)
    assert len(rows) == 1
    prov = rows[0]["__survivorship_prov__"]
    assert prov.cluster_id == 5
    assert prov.groups[0].columns == ["street", "city", "zip"]
    # row 11 is most-complete (3/3) -> wins; lock-step pins its row id + zip
    assert prov.groups[0].winner_row_id == 11
    assert prov.groups[0].values["zip"] == "90001"
```

- [ ] **Step 2: Run to verify it fails** (KeyError `__survivorship_prov__`).

- [ ] **Step 3: Implement.** In the survivorship branch, pass `cluster_id` and embed the prov:

```python
for cdf in s_sorted.partition_by("__cluster_id__", maintain_order=True):
    cid = cdf["__cluster_id__"][0]
    per_scores = (cluster_pair_scores or {}).get(int(cid))
    rec, prov = resolve_cluster(
        cdf, rules, order, quality_scores=quality_scores,
        pair_scores=per_scores, provenance=provenance, cluster_id=int(cid),
    )
    rec["__cluster_id__"] = cid
    if provenance and prov is not None:
        rec["__survivorship_prov__"] = prov
    s_results.append(rec)
```

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(golden): carry survivorship ClusterProvenance on enriched rows`.

### Task A3: `golden_records_to_provenance` consumes the carried prov

**Files:**
- Modify: `core/golden.py` (`golden_records_to_provenance`, ~L1001)
- Test: `tests/survivorship/test_provenance_surfacing.py` (append)

- [ ] **Step 1: Write the failing test** — the adapter must return groups + the conditional strategy, not the lossy reconstruction.

```python
from goldenmatch.core.golden import golden_records_to_provenance


def test_adapter_uses_carried_prov_with_groups():
    df = _cluster_df()
    rows = build_golden_records_batch(df, _addr_rules(), provenance=True)
    clusters = {5: {"cluster_quality": "strong", "confidence": 0.9}}
    provs = golden_records_to_provenance(rows, clusters, _addr_rules())
    assert len(provs) == 1
    cp = provs[0]
    assert cp.cluster_id == 5
    assert cp.cluster_confidence == 0.9          # re-stamped from clusters
    assert len(cp.groups) == 1                     # groups carried through (not lost)
    assert cp.groups[0].name == "addr"
```

- [ ] **Step 2: Run to verify it fails** (groups empty — reconstruction path).

- [ ] **Step 3: Implement.** At the top of the `for rec in golden_records:` loop, short-circuit on the carried prov:

```python
for rec in golden_records:
    cid = rec["__cluster_id__"]
    cinfo = clusters.get(cid, {})
    carried = rec.get("__survivorship_prov__")
    if carried is not None:
        # Re-stamp cluster-level fields from `clusters`; keep the resolver's
        # rich fields + groups verbatim (single source of truth).
        carried.cluster_id = cid
        carried.cluster_quality = cinfo.get("cluster_quality", "strong")
        carried.cluster_confidence = cinfo.get("confidence", 0.0)
        out.append(carried)
        continue
    # ... existing reconstruction path unchanged (non-survivorship) ...
```

(`ClusterProvenance` is a non-frozen `@dataclass`, so attribute re-stamping is allowed — confirm it is not `frozen=True`; it is not, per `core/golden.py`.)

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(golden): adapter surfaces carried survivorship groups + fields`.

### Task A4: `build_golden_record_with_provenance` survivorship delegation

**Files:**
- Modify: `core/golden.py` (`build_golden_record_with_provenance`, ~L1115)
- Test: `tests/survivorship/test_provenance_surfacing.py` (append)

This entry point has **zero production callers** (only `tests/test_golden.py`) and
is survivorship-blind today. Give it a thin branch that delegates to the batch
path + adapter, with a materializer that uses the **same enriched-cell guard**
the pipeline uses (`isinstance(val_info, dict) and "value" in val_info`) so the
`__survivorship_prov__` carrier (a `ClusterProvenance` object, not a `{value}`
dict) is skipped. Do NOT rely on `_is_internal` for that -- it only matches
`("__row_id__", "__source__", "__block_key__", "__mk_")` and will NOT catch the
carrier or `__cluster_id__`/`__golden_confidence__`.

- [ ] **Step 1: Write the failing test** — surfaces groups.

```python
def test_build_golden_record_with_provenance_surfaces_groups():
    df = _cluster_df()
    result = build_golden_record_with_provenance(df, _addr_rules(), {5: {"confidence": 0.9}})
    cp = result.provenance[0]
    assert cp.cluster_id == 5
    assert len(cp.groups) == 1 and cp.groups[0].name == "addr"
```

- [ ] **Step 2: Run to verify it fails** (survivorship-blind: groups empty).

- [ ] **Step 3: Implement.** Add a thin branch at the top of the function body
  (after `cluster_ids`/`cluster_dfs` are computed):

```python
    if _survivorship_active(rules):
        rows = build_golden_records_batch(df, rules, provenance=True)   # NO user_cols arg
        provenance_list = golden_records_to_provenance(rows, clusters, rules)
        golden_rows = []
        for rec in rows:
            row = {"__cluster_id__": rec["__cluster_id__"]}
            for col, val_info in rec.items():
                if col in ("__cluster_id__", "__golden_confidence__", "__survivorship_prov__"):
                    continue
                if isinstance(val_info, dict) and "value" in val_info:   # same guard as pipeline.py:2013
                    row[col] = val_info["value"]
            golden_rows.append(row)
        golden_df = pl.DataFrame(golden_rows) if golden_rows else pl.DataFrame()
        return GoldenRecordResult(df=golden_df, provenance=provenance_list)
    # ... existing per-field loop unchanged (non-survivorship path, byte-identical) ...
```

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(golden): build_golden_record_with_provenance delegates survivorship to resolver`.

### Task A5: carrier stays out of golden columns (regression guard)

**Files:**
- Test: `tests/survivorship/test_provenance_surfacing.py` (append)

The guarded materializer in A4 already excludes `__survivorship_prov__`; this is
a pure regression test (no production change).

- [ ] **Step 1: Write the test**

```python
def test_survivorship_prov_not_in_golden_columns():
    df = _cluster_df()
    result = build_golden_record_with_provenance(df, _addr_rules(), {5: {}})
    assert "__survivorship_prov__" not in result.df.columns
    assert set(result.df.columns) >= {"street", "city", "zip"}
```

- [ ] **Step 2: Run to verify it passes** (A4 already excludes the carrier).
- [ ] **Step 3: Commit** `test(golden): carrier key absent from golden columns`.

### Task A6: Phase-A parity gate

**Files:**
- Test: `tests/survivorship/test_surfacing_parity.py`

- [ ] **Step 1: Write the test** — a config with NO survivorship levers produces identical provenance vs a baseline (no new keys, no groups, identical field strategies).

```python
import polars as pl
from goldenmatch.config.schemas import GoldenRulesConfig
from goldenmatch.core.golden import build_golden_records_batch, golden_records_to_provenance


def test_no_levers_byte_identical_provenance():
    df = pl.DataFrame({
        "__cluster_id__": [1, 1], "__row_id__": [1, 2],
        "name": ["Acme", "Acme Inc"], "city": ["LA", "LA"],
    })
    rules = GoldenRulesConfig(default_strategy="most_complete")  # no field_groups, no when:/validate
    rows = build_golden_records_batch(df, rules, provenance=True)   # NO user_cols arg
    assert all("__survivorship_prov__" not in r for r in rows)   # carrier absent
    provs = golden_records_to_provenance(rows, {1: {}}, rules)
    assert provs[0].groups == []                                 # no groups
```

- [ ] **Step 2: Run to verify pass.** (Pure assertion of the parity invariant; no production change.)
- [ ] **Step 3: Commit** `test(survivorship): Phase-A no-levers provenance parity gate`.

---

## Phase B — `golden_records` JSON `audit` NL

### Task B1: per-cluster `audit` string in `save_lineage`

**Files:**
- Modify: `core/lineage.py` (`save_lineage` + `save_lineage_streaming`)
- Test: `tests/survivorship/test_lineage_golden_records.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/survivorship/test_lineage_golden_records.py
import json
from goldenmatch.core.golden import ClusterProvenance, GroupProvenance
from goldenmatch.core.lineage import save_lineage


def _prov():
    g = GroupProvenance(name="addr", columns=["street", "city"], strategy="most_complete",
                        winner_row_id=7, winner_source=None, values={"street": "1 Main", "city": "LA"},
                        tie=False, confidence=1.0)
    return [ClusterProvenance(cluster_id=5, cluster_quality="strong", cluster_confidence=0.9, fields={}, groups=[g])]


def test_golden_records_section_has_groups_and_audit(tmp_path):
    path = save_lineage([], tmp_path, "run", golden_provenance=_prov())
    data = json.loads(path.read_text(encoding="utf-8"))
    rec = data["golden_records"][0]
    assert rec["groups"][0]["name"] == "addr"                       # structured (asdict, free)
    assert "promoted together from record 7" in rec["audit"]        # NL line


def test_plain_provenance_has_no_audit(tmp_path):
    plain = [ClusterProvenance(cluster_id=1, cluster_quality="strong", cluster_confidence=0.0, fields={}, groups=[])]
    path = save_lineage([], tmp_path, "run", golden_provenance=plain)
    rec = json.loads(path.read_text(encoding="utf-8"))["golden_records"][0]
    assert rec.get("audit", "") == ""                               # nothing survivorship-specific
```

- [ ] **Step 2: Run to verify it fails** (no `audit` key).

- [ ] **Step 3: Implement.** Add a fail-open NL wrapper and inject `audit` into each serialized cluster record (both savers):

```python
def _safe_cluster_audit(cp) -> str:
    """Fail-open render of a cluster's survivorship audit trail (group +
    condition + validation lines). '' when nothing survivorship-specific."""
    try:
        return render_cluster_provenance_nl(cp)
    except Exception:
        logger.warning("lineage: survivorship audit render failed; omitting", exc_info=False)
        return ""

def _serialize_golden_records(provenance: list) -> list[dict]:
    records = _serialize_provenance(provenance)
    for cp, rec in zip(provenance, records):
        rec["audit"] = _safe_cluster_audit(cp)
    return records
```

Use `_serialize_golden_records(golden_provenance)` where `save_lineage` / `save_lineage_streaming` currently call `_serialize_provenance(golden_provenance)` for the `"golden_records"` key.

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(lineage): per-cluster survivorship audit NL in golden_records`.

---

## Phase C — CLI cluster-explain `Survivorship:` block

### Task C1: `explain_cluster_nl` optional provenance block

**Files:**
- Modify: `core/explain.py` (`explain_cluster_nl`)
- Test: `tests/survivorship/test_explain_cluster_survivorship.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/survivorship/test_explain_cluster_survivorship.py
from goldenmatch.core.explain import explain_cluster_nl
from goldenmatch.core.golden import ClusterProvenance, GroupProvenance
import polars as pl


def _cp():
    g = GroupProvenance(name="addr", columns=["street", "city"], strategy="most_complete",
                        winner_row_id=7, winner_source=None, values={}, tie=False, confidence=1.0)
    return ClusterProvenance(cluster_id=5, cluster_quality="strong", cluster_confidence=0.9, fields={}, groups=[g])


def test_cluster_explain_appends_survivorship_block():
    cinfo = {"id": 5, "members": [10, 11], "size": 2}
    df = pl.DataFrame({"__row_id__": [10, 11], "street": ["a", "b"], "city": ["LA", "LA"]})
    out = explain_cluster_nl(cinfo, df, [], cluster_provenance=_cp())
    assert "Survivorship:" in out
    assert "promoted together from record 7" in out


def test_cluster_explain_no_provenance_unchanged():
    cinfo = {"id": 5, "members": [10], "size": 1}
    df = pl.DataFrame({"__row_id__": [10], "street": ["a"]})
    out = explain_cluster_nl(cinfo, df, [])      # no cluster_provenance -> no block
    assert "Survivorship:" not in out
```

- [ ] **Step 2: Run to verify it fails** (unexpected kwarg / no block).

- [ ] **Step 3: Implement.** Add the optional param and append the block (fail-open, parity when None/empty):

```python
def explain_cluster_nl(cluster, df, matchkeys, *, cluster_provenance=None) -> str:
    summary = _existing_cluster_summary(cluster, df, matchkeys)   # unchanged body
    if cluster_provenance is not None:
        from goldenmatch.core.lineage import render_cluster_provenance_nl
        try:
            trail = render_cluster_provenance_nl(cluster_provenance)
        except Exception:
            trail = ""
        if trail:
            summary = f"{summary}\n\nSurvivorship:\n{trail}"
    return summary
```

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(explain): cluster summary appends survivorship audit block`.

### Task C2: `cli/explain --cluster` threads the cluster's provenance

**Files:**
- Modify: `cli/explain.py` (`--cluster` path)
- Test: `tests/survivorship/test_explain_cluster_survivorship.py` (append — invoke the CLI via `typer.testing.CliRunner` against a tiny survivorship config)

- [ ] **Step 1: Write the failing test** (CliRunner on a 2-row address-group dataset + config; assert the `--cluster N` output contains "Survivorship:" and "promoted together").
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement.** In the `--cluster` branch, after obtaining `result` and the cluster, compute the cluster's `ClusterProvenance` (build the golden provenance for the run via the batch path + `golden_records_to_provenance`, look up the requested `cluster_id`) and pass it as `cluster_provenance=` to `explain_cluster_nl`. Reuse the shared helper from Task D1.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(cli): explain --cluster shows survivorship audit`.

---

## Phase D — thread `golden_provenance` into `cli/lineage` + MCP `_tool_lineage`

### Task D1: shared `golden_provenance` helper

**Files:**
- Modify: `core/lineage.py` (add `golden_provenance_for_run`)
- Test: `tests/survivorship/test_lineage_tool_provenance.py`

**Key constraint (from plan review):** the standalone surfaces (`cli/lineage`,
`_tool_lineage`) do NOT have a multi-member `__cluster_id__`-tagged frame -- they
only have the source frame (`engine.data`, carrying `__row_id__`) and the
`clusters` dict (`{cid: {"members": [row_ids], "size": n, ...}}`). They run only
`build_lineage`, never the golden stage. So the helper must BUILD the multi-member
frame from those two inputs.

- [ ] **Step 1: Write the failing test** — given a source frame (with `__row_id__`) + a `clusters` dict (one size>1 grouped cluster) + a field-group config, the helper returns a `ClusterProvenance` list with groups; with a plain config it returns a list with no groups; on a malformed input it returns `None` (fail-open, so callers stay byte-identical).
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** the shared helper (used by `cli/lineage`, `_tool_lineage`, and `cli/explain`):

```python
def golden_provenance_for_run(data_df, clusters, rules) -> list | None:
    """Build golden ClusterProvenance for a finished run from the source frame +
    clusters dict (the inputs the standalone lineage surfaces have). Fail-open."""
    try:
        import polars as pl
        from goldenmatch.core.golden import build_golden_records_batch, golden_records_to_provenance
        # Tag each member of a multi-member cluster with its __cluster_id__.
        member_rows = [
            {"__row_id__": rid, "__cluster_id__": cid}
            for cid, cinfo in clusters.items()
            if cinfo.get("size", len(cinfo.get("members", []))) > 1
            for rid in cinfo.get("members", [])
        ]
        if not member_rows:
            return None
        multi_df = pl.DataFrame(member_rows).join(data_df, on="__row_id__", how="inner")
        rows = build_golden_records_batch(multi_df, rules, provenance=True)   # NO user_cols arg
        return golden_records_to_provenance(rows, clusters, rules)
    except Exception:
        logger.warning("lineage: golden provenance unavailable; skipping", exc_info=False)
        return None
```

(Requires `data_df` to carry `__row_id__`; the engine assigns it. If a surface's
frame lacks it, the `join` raises and the fail-open returns `None`.)

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(lineage): shared golden_provenance_for_run helper`.

### Task D2: `cli/lineage` + `_tool_lineage` pass `golden_provenance`

**Files:**
- Modify: `cli/lineage.py`, `mcp/server.py` (`_tool_lineage`)
- Test: `tests/survivorship/test_lineage_tool_provenance.py` (append)

- [ ] **Step 1: Write the failing test** — running `cli/lineage` (CliRunner) and `_tool_lineage` against a survivorship dataset writes a `golden_records` section with `groups`; a plain dataset writes none.
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement.** In both call sites, derive the golden rules and call the helper, then pass the result to `save_lineage`:

```python
from goldenmatch.config.schemas import GoldenRulesConfig
from goldenmatch.core.lineage import golden_provenance_for_run
golden_rules = getattr(cfg, "golden_rules", None) or GoldenRulesConfig(default_strategy="most_complete")
gp = golden_provenance_for_run(engine.data, result.clusters, golden_rules)   # source frame + clusters dict
save_lineage(lineage, output_dir, run_name=run_name, golden_provenance=gp)
```

`cli/lineage` uses `engine.data` / `result.clusters` / `cfg`; `_tool_lineage`
uses `_engine.data` / `_result.clusters` / `_config`. Confirm `result.clusters`
has the `{cid: {"members": [...], "size": n}}` shape (the pipeline's
`_clusters_dict()` shape) before relying on it; the helper is fail-open if not.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(lineage,mcp): thread golden_provenance into standalone lineage surfaces`.

---

## Phase E — review_queue golden-composition view

### Task E1: `ReviewItem.golden_composition`

**Files:**
- Modify: `core/review_queue.py` (`ReviewItem`)
- Test: `tests/survivorship/test_review_golden_composition.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/survivorship/test_review_golden_composition.py
from goldenmatch.core.review_queue import ReviewItem


def test_review_item_golden_composition_defaults_none():
    it = ReviewItem(job_name="j", id_a=1, id_b=2, score=0.8, explanation="x")
    assert it.golden_composition is None
```

- [ ] **Step 2: Run to verify it fails** (unexpected kwarg / attr missing).
- [ ] **Step 3: Implement.** Add `golden_composition: str | None = None` to the `ReviewItem` dataclass (defaulted -> existing construction unaffected; backends that persist/rehydrate gain the column where applicable, else it stays None on rehydration).
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(review): ReviewItem.golden_composition field`.

### Task E2: populate `golden_composition` at enqueue

**Files:**
- Modify: `core/review_queue.py` (the enqueue path, ~L402, + the gating call site that owns clusters)
- Test: `tests/survivorship/test_review_golden_composition.py` (append)

- [ ] **Step 1: Write the failing test** — enqueue with a `cluster_provenance_by_id` map + a `cluster_of` (record -> cluster) map; the item for a record in a grouped cluster gets `golden_composition` containing "promoted together"; without the maps it stays None.
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement.** Thread optional `cluster_provenance_by_id: dict[int, ClusterProvenance] | None` + `cluster_of: dict[int, int] | None` into the enqueue method; when both present, render `golden_composition` for `cluster_of[id_a]`'s `ClusterProvenance` via `render_cluster_provenance_nl` (fail-open). Default both None -> byte-identical to today. (Spec open question: confirm the gating call site that owns both the pairs and the cluster `ClusterProvenance`; pass the maps from there.)
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(review): populate golden_composition from cluster survivorship trail`.

---

## Phase F — docs + final parity

### Task F1: docs sweep

**Files:** `docs-site/` golden-record + lineage pages, `docs-site/goldenmatch/tuning.mdx`

- [ ] **Step 1:** Document the `golden_records` lineage section (structured `groups` + `audit` NL) with the NL example; note the CLI `explain --cluster` survivorship block and the MCP `lineage` tool `golden_records` output; cross-reference from the `field_groups` / `GOLDENMATCH_FIELD_GROUP_SURVIVORSHIP` tuning entry. Use the rollout-docs-sweep skill.
- [ ] **Step 2: Commit** `docs: GroupProvenance surfacing (lineage golden_records + explain + MCP)`.

### Task F2: end-to-end + final parity gate

**Files:** `tests/survivorship/test_surfacing_parity.py` (extend)

- [ ] **Step 1: Write the test** — one survivorship config flows through the pipeline lineage path and a plain config produces byte-identical lineage JSON `pairs` (assert `build_lineage` per-pair output unchanged) and no `golden_records.audit`. Add a fail-open test: monkeypatch `render_cluster_provenance_nl` to raise; assert `save_lineage` + `explain_cluster_nl` still produce output (line omitted).
- [ ] **Step 2: Run to verify pass.**
- [ ] **Step 3: Commit** `test(survivorship): end-to-end surfacing + fail-open + parity`.

---

## Open items carried from the spec (resolve during execution)

- **review_queue bridge (E2):** the exact gating call site that owns both the scored pairs and the per-cluster `ClusterProvenance`. If no single site owns both, populate `golden_composition` only on the path that does and leave it None elsewhere (documented, parity-safe).
- **`__survivorship_prov__` strip (A4/A5):** RESOLVED in-plan. `_is_internal` does NOT catch it (it only matches `__row_id__`/`__source__`/`__block_key__`/`__mk_`). The carrier stays out of golden columns via the enriched-cell guard `isinstance(val_info, dict) and "value" in val_info` (pipeline.py ~L2013; adapter golden.py ~L1039; A4's new materializer). No `_is_internal` change.
- **`build_golden_record_with_provenance` (A4):** zero production callers; the delegation is thin and materializes inline with the same guard (no shared `_materialize_golden_df` extraction needed).
