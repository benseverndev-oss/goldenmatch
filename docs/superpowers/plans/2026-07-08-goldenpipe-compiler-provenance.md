# GoldenPipe Compiler SP2 — Field-Level Provenance — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the SP1 `CompiledPipeline` into a field-level lineage report (per column: Check ops → ordered Flow transforms → matching role), via a pure `provenance` kernel function + a Match-capture enrichment that makes blocking-key/scorer roles real. Non-perf, net-new, additive.

**Architecture:** A pure `provenance(CompiledPipeline) → {fields, unmapped}` in the `goldenpipe-core` kernel (Rust + Python mirror + golden vectors), same pattern as `lower`. The host enriches `capture.py`'s Match branch (normalize the real `GoldenMatchConfig`'s nested `blocking`/`matchkeys` into flat `{keys, scorer}` so `Partition.keys`/`PairScore.scorer` name real columns) and adds a `field_lineage` wrapper + `format_lineage`.

**Tech Stack:** Python (goldenpipe compiler + goldenmatch config), Rust (serde_json), the existing goldenpipe-core golden-vector infra.

---

## Box / environment constraints

- **Python is box-runnable** (real red→green). Rust is CI-only (write-against-spec + rustfmt + grep/eye).
- Python env (`;` PYTHONPATH; **includes goldenmatch** for the config shapes):
  ```bash
  INTERP="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
  export PYTHONPATH="packages/python/goldenpipe;packages/python/goldencheck;packages/python/infermap;packages/python/goldencheck-types;packages/python/goldenflow;packages/python/goldenmatch"
  export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENMATCH_NATIVE=0 GOLDENMATCH_AUTOCONFIG_MEMORY=0
  ```
  `cd "D:/show_case/gg-local-llm"` each Bash call. `ruff check` touched Python.
- **Depends on SP1** (`goldenpipe/compiler/{ir,capture,compiled_runner}.py` + `goldenpipe-core/src/ir.rs`). SP1 is PR #1592 (in the merge queue). Task 6 (ship) rebases onto merged `main`.
- Spec: `docs/superpowers/specs/2026-07-08-goldenpipe-compiler-provenance-design.md`. @superpowers:test-driven-development per Python task.

## Canonical shapes (verified against `goldenmatch/config/schemas.py`)

`GoldenMatchConfig.model_dump()` nests (NO top-level `keys`/`scorer`):
- `blocking.keys: [ {fields: [str], ...} ]`, `blocking.passes: [ {fields:[str]} ] | None`, `blocking.sub_block_keys: [ {fields:[str]} ] | None` — the **blocking column names** (union all three; `multi_pass` puts recall columns in `passes`).
- `matchkeys: [ {fields: [ {field: str|None, columns: [str]|None, ...} ], ...} ]` — the **scorer column names** (`field`, plus `columns` for `record_embedding`).

Enrichment output (what `lower`'s match branch reads): `{"keys": [str], "scorer": {"columns": [str]}}`.

## File structure

- Modify `packages/python/goldenpipe/goldenpipe/compiler/capture.py` — add `_normalize_match_config` + apply it in the Match branch.
- Create `packages/python/goldenpipe/goldenpipe/compiler/provenance.py` — pure `provenance` (Python kernel mirror).
- Create `packages/python/goldenpipe/goldenpipe/compiler/lineage.py` — host `field_lineage` + `format_lineage`.
- Modify `packages/python/goldenpipe/goldenpipe/core/_planner_json.py` — `provenance_json`.
- Modify `packages/python/goldenpipe/tests/core/test_planner_parity.py` — register `provenance`.
- Create `packages/rust/extensions/goldenpipe-core/tests/vectors/provenance.json`.
- Rust: `goldenpipe-core/src/provenance.rs`, `lib.rs`, `json.rs`, `tests/golden_vectors.rs`; shims `goldenpipe-wasm/src/lib.rs`, `goldenpipe-native/src/lib.rs`; `_native_loader.py`.
- Tests: `tests/compiler/test_match_enrich.py`, `test_provenance.py`, `test_lineage.py`.

---

## Task 1: Match-capture enrichment (box TDD)

**Files:** Modify `goldenpipe/compiler/capture.py`; Test `tests/compiler/test_match_enrich.py`.

Normalize a `GoldenMatchConfig`-shaped dict (from either the explicit stage config or `_build_config_from_contexts(...).model_dump()`) into flat `{keys, scorer}` so the IR's `Partition`/`PairScore` name real columns.

- [ ] **Step 1: Write failing test** `tests/compiler/test_match_enrich.py`:

```python
from goldenpipe.compiler.capture import _normalize_match_config


def test_blocking_keys_union_keys_and_passes():
    cfg = {
        "blocking": {
            "keys": [{"fields": ["last_name"]}],
            "passes": [{"fields": ["last_name"]}, {"fields": ["email"]}],
            "sub_block_keys": None,
        },
        "matchkeys": [{"fields": [{"field": "email"}, {"field": "first_name"}]}],
    }
    out = _normalize_match_config(cfg)
    assert out["keys"] == ["email", "last_name"]  # union, sorted, deduped
    assert out["scorer"] == {"columns": ["email", "first_name"]}  # matchkey field refs, first-seen order


def test_embedding_columns_and_missing_fields_graceful():
    cfg = {"blocking": {"keys": [], "passes": None}, "matchkeys": [{"fields": [{"columns": ["name_a", "name_b"]}]}]}
    out = _normalize_match_config(cfg)
    assert out["keys"] == []
    assert out["scorer"] == {"columns": ["name_a", "name_b"]}


def test_record_embedding_sentinel_is_skipped():
    # record_embedding sets field="__record__" (not a real column) + real columns
    cfg = {"matchkeys": [{"fields": [{"field": "__record__", "columns": ["full_name"]}]}]}
    out = _normalize_match_config(cfg)
    assert out["scorer"] == {"columns": ["full_name"]}  # __record__ filtered out


def test_empty_config_is_empty():
    assert _normalize_match_config({}) == {"keys": [], "scorer": {"columns": []}}
```

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3: Implement.** Add to `capture.py`:

```python
def _normalize_match_config(cfg: dict) -> dict:
    """Flatten a GoldenMatchConfig-shaped dict into {keys, scorer:{columns}} — the
    column names the IR's Partition/PairScore need. Blocking column names come from
    blocking.keys/passes/sub_block_keys[].fields (union); scorer column names from
    matchkeys[].fields[].field (+ .columns for record_embedding)."""
    blocking = cfg.get("blocking") or {}
    key_cols: set[str] = set()
    for group in ("keys", "passes", "sub_block_keys"):
        for bk in (blocking.get(group) or []):
            for f in (bk.get("fields") or []):
                if isinstance(f, str):
                    key_cols.add(f)
    scorer_cols: list[str] = []
    seen: set[str] = set()
    for mk in (cfg.get("matchkeys") or []):
        for f in (mk.get("fields") or []):
            refs = []
            if f.get("field"):
                refs.append(f["field"])
            refs.extend(f.get("columns") or [])
            for c in refs:
                # skip the record_embedding sentinel (MatchkeyField sets field="__record__"
                # for a whole-record scorer) — it is not a real column.
                if c == "__record__" or c in seen:
                    continue
                seen.add(c)
                scorer_cols.append(c)
    return {"keys": sorted(key_cols), "scorer": {"columns": scorer_cols}}
```

Then in the Match branch, apply it to BOTH paths (explicit + contexts):
```python
    if name == "goldenmatch.dedupe":
        raw = dict(cfg) if cfg else _match_config_from_ctx(ctx)
        return "match", _normalize_match_config(raw), resolved
```
(Keep `_match_config_from_ctx` returning the `.model_dump()` dict; the normalize step now runs on its output. The explicit `cfg` path is also a GoldenMatchConfig-shaped dict, normalized identically.)

- [ ] **Step 4: Run to verify PASS (3 tests).**

- [ ] **Step 5: Confirm no SP1 regression + ruff + commit.**
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/compiler/ -q   # SP1 capture/equivalence still green
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/compiler/capture.py packages/python/goldenpipe/tests/compiler/test_match_enrich.py
git add packages/python/goldenpipe/goldenpipe/compiler/capture.py packages/python/goldenpipe/tests/compiler/test_match_enrich.py && git commit -m "feat(goldenpipe): enrich Match capture with real blocking/scorer columns (SP2)"
```
NOTE: the SP1 equivalence gate asserts only Match node KINDS present (not `Partition.keys` contents) — so populated keys won't break it. If it does, STOP and report (unexpected).

---

## Task 2: Pure `provenance` (box TDD)

**Files:** Create `goldenpipe/compiler/provenance.py`; Test `tests/compiler/test_provenance.py`.

- [ ] **Step 1: Write failing tests** `tests/compiler/test_provenance.py`:

```python
from goldenpipe.compiler.provenance import provenance


def _cp(nodes):
    return {"nodes": nodes, "edges": []}


def _n(kind, nid, stage="s", resolved=False, **rest):
    return {"kind": kind, "id": nid, "origin_stage": stage, "resolved": resolved, **rest}


def test_column_gets_checks_and_ordered_transforms():
    cp = _cp([
        _n("Scan", 0, column="email", ops=["pattern_consistency"]),
        _n("Map", 1, column="email", op="email_normalize"),
        _n("Map", 2, column="email", op="email_canonical"),
    ])
    out = provenance(cp)
    f = next(x for x in out["fields"] if x["column"] == "email")
    assert f["checks"] == ["pattern_consistency"]
    assert f["transforms"] == ["email_normalize", "email_canonical"]  # node-id order
    assert f["node_ids"] == [0, 1, 2]


def test_blocking_and_scorer_roles():
    cp = _cp([
        _n("Map", 0, column="last_name", op="name_proper"),
        _n("Partition", 1, keys=["last_name"]),
        _n("PairScore", 2, scorer={"columns": ["email", "last_name"]}),
    ])
    out = provenance(cp)
    ln = {x["column"]: x for x in out["fields"]}
    assert ln["last_name"]["blocking_key"] is True
    assert ln["last_name"]["scorer_input"] is True
    assert ln["email"]["scorer_input"] is True
    assert ln["email"]["blocking_key"] is False


def test_source_connected_barrier_are_unmapped_notes():
    cp = _cp([_n("Source", 0, produces=["df"]), _n("Connected", 1, method={"name": "cc"}), _n("Barrier", 2, raw_config={})])
    out = provenance(cp)
    assert out["fields"] == []
    kinds = [u["kind"] for u in out["unmapped"]]
    assert kinds == ["Source", "Connected", "Barrier"]


def test_empty_pipeline():
    assert provenance({"nodes": [], "edges": []}) == {"fields": [], "unmapped": []}
```

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3: Implement `provenance.py`:**

```python
"""Pure field-level provenance over the SP1 IR — the mirror of
goldenpipe-core/src/provenance.rs. provenance(CompiledPipeline) -> {fields, unmapped}."""
from __future__ import annotations

_COLUMN_KINDS = {"Scan", "Map"}


def provenance(compiled: dict) -> dict:
    nodes = compiled.get("nodes", [])
    fields: dict[str, dict] = {}
    order: list[str] = []
    unmapped: list[dict] = []
    blocking: set[str] = set()
    scorer: set[str] = set()

    def field(col: str) -> dict:
        if col not in fields:
            fields[col] = {
                "column": col, "origin": "source", "checks": [], "transforms": [],
                "blocking_key": False, "scorer_input": False, "node_ids": [],
            }
            order.append(col)
        return fields[col]

    for n in nodes:  # nodes already in id order from lower()
        kind = n.get("kind")
        if kind == "Scan":
            f = field(n["column"]); f["checks"].extend(n.get("ops", [])); f["node_ids"].append(n["id"])
        elif kind == "Map":
            f = field(n["column"]); f["transforms"].append(n["op"]); f["node_ids"].append(n["id"])
        elif kind == "Partition":
            for k in (n.get("keys") or []):
                blocking.add(k)
        elif kind == "PairScore":
            for c in ((n.get("scorer") or {}).get("columns") or []):
                scorer.add(c)
        else:  # Source / Connected / Barrier
            unmapped.append({"node_id": n["id"], "kind": kind, "note": _note(kind)})

    # apply blocking/scorer roles. A key/scorer-only column (no Scan/Map) gets a field
    # entry; append those in SORTED order — Python set iteration is nondeterministic and
    # would flake the golden-vector / Rust-parity comparison. (Do NOT use list(blocking)
    # + list(scorer) here.)
    for col in sorted(blocking | scorer):
        field(col)
    for col, f in fields.items():
        f["blocking_key"] = col in blocking
        f["scorer_input"] = col in scorer

    return {"fields": [fields[c] for c in order], "unmapped": unmapped}


def _note(kind: str) -> str:
    return {"Source": "data loaded", "Connected": "clustering", "Barrier": "opaque stage"}.get(kind, kind)
```

`node_ids` deliberately lists only the **column-bearing** Scan/Map nodes (blocking/scorer
participation is the `blocking_key`/`scorer_input` boolean flags, not node ids) — keep it
that way so the Python and Rust mirrors stay simple and identical.

- [ ] **Step 4: Run to verify PASS (4 tests).** Confirm the field order is deterministic (first-seen for Scan/Map columns, then sorted for role-only columns).

- [ ] **Step 5: ruff + commit** (`feat(goldenpipe): pure field-level provenance over the IR (SP2)`).

---

## Task 3: Golden vectors + Python parity leg (box)

**Files:** Create `goldenpipe-core/tests/vectors/provenance.json`; Modify `core/_planner_json.py`, `tests/core/test_planner_parity.py`.

- [ ] **Step 1: Author `provenance.json`** — `{input: CompiledPipeline, expected: {fields, unmapped}}` cases mirroring the unit tests (Scan+Map column; blocking+scorer roles; Source/Connected/Barrier unmapped; empty). Compute `expected` by hand from provenance.py.

- [ ] **Step 2: Add `provenance_json`** to `_planner_json.py`:
```python
def provenance_json(s: str) -> str:
    from goldenpipe.compiler.provenance import provenance
    return json.dumps(provenance(json.loads(s)))
```

- [ ] **Step 3: Register** `("provenance", PJ.provenance_json)` in `_CASES` (Leg A) and `("provenance", "provenance_json")` in the Leg B list of `test_planner_parity.py`.

- [ ] **Step 4: Run Leg A** — `"$INTERP" -m pytest packages/python/goldenpipe/tests/core/test_planner_parity.py -k provenance -v` → Leg A PASS; Leg B fails on the stale wheel (expected — CI handles it; report which).

- [ ] **Step 5: ruff + commit** (`test(goldenpipe): provenance golden vectors + python parity leg (SP2)`).

---

## Task 4: Host `field_lineage` + `format_lineage` + real-pipeline test (box)

**Files:** Create `goldenpipe/compiler/lineage.py`; Test `tests/compiler/test_lineage.py`.

- [ ] **Step 1: Write failing tests** `tests/compiler/test_lineage.py`:
- A `format_lineage` unit test (structured lineage → the expected human string).
- A **real-pipeline** test: reuse the equivalence-gate fixture helpers (`_tiny_people_df`, `_write_csv`, `_read_source`, `_registry`, `_plan` from `tests/compiler/test_equivalence.py` — import them), `compile_and_run` the full `load→check→flow→match` (contexts path), call `field_lineage(compiled)`, and assert:
  - `email`'s `transforms` match the `manifest.records` transforms for `email` (from `compiled_ctx.artifacts["manifest"]`) — a real fidelity check.
  - **`blocking_key` columns == the compiled `Partition` node's `keys`** (read the `Partition` node out of `compiled["nodes"]` and compare the set of `blocking_key=True` columns to `set(partition["keys"])`). This asserts lineage matches the recorded plan WITHOUT assuming blocking is non-empty — if the fixture's classification yields `auto_suggest`/empty keys (`adapters/match.py`), both sides are empty and the assertion still holds honestly. Inspect the actual `column_contexts`/`Partition` node rather than assuming a particular column blocks.

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3: Implement `lineage.py`:**
```python
"""Host lineage: compute field-level provenance from a compiled pipeline (via the
pure kernel) and render it human-readable."""
from __future__ import annotations

from goldenpipe.compiler.provenance import provenance


def field_lineage(compiled: dict) -> dict:
    return provenance(compiled or {"nodes": [], "edges": []})


def format_lineage(lineage: dict) -> str:
    lines = []
    for f in lineage.get("fields", []):
        parts = []
        if f["checks"]:
            parts.append(f"checks[{','.join(f['checks'])}]")
        if f["transforms"]:
            parts.append(f"transforms[{','.join(f['transforms'])}]")
        roles = [r for r, on in (("blocking-key", f["blocking_key"]), ("scorer-input", f["scorer_input"])) if on]
        if roles:
            parts.append(",".join(roles))
        lines.append(f"{f['column']}: " + " -> ".join(parts) if parts else f"{f['column']}: (no ops)")
    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify PASS.** Report what the real-pipeline lineage showed for `email` (checks/transforms/roles) — a sanity artifact.

- [ ] **Step 5: ruff + commit** (`feat(goldenpipe): field_lineage host wrapper + format_lineage (SP2)`).

---

## Task 5: Rust kernel mirror + shims (write-against-spec, CI-verified)

**Files:** Create `goldenpipe-core/src/provenance.rs`; Modify `src/lib.rs`, `src/json.rs`, `tests/golden_vectors.rs`; shims `goldenpipe-wasm/src/lib.rs`, `goldenpipe-native/src/lib.rs`; `_native_loader.py`.

Mirror `provenance.py` exactly, building output as `serde_json::Value` objects by hand for byte-parity (key order `column, origin, checks, transforms, blocking_key, scorer_input, node_ids`). Follow the SP1 `ir.rs`/`lower_json` pattern precisely (the recently-shipped reference).

- [ ] **Step 1: Read** `src/ir.rs` (the by-hand `serde_json::Map` build + the no-temporary `.into_iter().flatten()` iterator form), `src/json.rs` (`lower_json` wrapper + `parse_err`), `tests/golden_vectors.rs` (`run`/`vec_*`), and grep `lower_json` in the two shim `lib.rs` + `_native_loader.py`.

- [ ] **Step 2: Write `src/provenance.rs`** — `pub fn provenance(compiled: &serde_json::Value) -> serde_json::Value` reproducing the Python: iterate `nodes` (already id-ordered), accumulate per-column `checks`/`transforms`/`node_ids` (first-seen column order), collect `blocking`/`scorer` sets, emit `unmapped` for Source/Connected/Barrier, apply roles, and — CRITICAL for parity — append role-only columns in **sorted** order (matching Python's `sorted(blocking | scorer)`). Field object key order exactly `column, origin, checks, transforms, blocking_key, scorer_input, node_ids`. Add `#[cfg(test)] mod tests` with ~2 cases. rustfmt.

- [ ] **Step 3: `lib.rs` `pub mod provenance;`; `json.rs` `provenance_json`** (parse a `CompiledPipeline` JSON → call `provenance` → to_string), mirroring `lower_json`.

- [ ] **Step 4: `tests/golden_vectors.rs` `#[test] fn vec_provenance() { run("provenance", provenance_json); }`.**

- [ ] **Step 5: Shims** — `provenance_json` in `goldenpipe-wasm/src/lib.rs` (`#[wasm_bindgen]`) and `goldenpipe-native/src/lib.rs` (`#[pyfunction]` + `wrap_pyfunction!(provenance_json, m)` registration — verify the registration line), + `_native_loader.py` passthrough. rustfmt.

- [ ] **Step 6: rustfmt + grep-verify** (`pub mod provenance`, `provenance_json` reachable, native registration present, key order matches Python). No cargo. Commit (`feat(goldenpipe-core): provenance kernel + json + shims (SP2)`).

---

## Task 6: Ship

- [ ] **Step 1: Gate on SP1 merged.** `gh pr view 1592 --json state -q .state`. If not MERGED, WAIT (SP2 stacks on SP1's IR + capture). Once merged:
- [ ] **Step 2: Rebase onto merged main** — `git fetch origin && git rebase --onto origin/main <last-SP1-commit>` (drop SP1 commits now in main; keep the SP2 doc + code commits). Resolve conflicts in `capture.py` (SP2 edits the Match branch SP1 created), `json.rs`/`_planner_json.py`/`test_planner_parity.py`/`golden_vectors.rs`/`_native_loader.py` (keep both `lower*`+`provenance*` entries). Re-run the box suite:
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/compiler/ packages/python/goldenpipe/tests/core/test_planner_parity.py -k "provenance or lineage or match_enrich or compiler" -q
```
- [ ] **Step 3: Push + PR + arm auto-merge, STOP.**
```bash
unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)
git push -u origin feat/goldenpipe-compiler-provenance
gh pr create --base main --title "feat(goldenpipe): compiler SP2 — field-level provenance" --body "<summary: provenance(IR)->field lineage kernel + Match-capture enrichment (real blocking/scorer cols); non-perf net-new; additive. Links spec+plan. Note: SP2 of the compiler program; pivoted from fusion after measurement showed perf levers already covered.>"
gh pr merge <N> --auto --squash   # merge-queue: NO --delete-branch
```
Watch CI: Rust `vec_provenance`, Python `test_planner_parity` Leg A+B, the SP2 compiler tests.

---

## Verification summary
- Box-green: `test_match_enrich`, `test_provenance`, `test_lineage` (incl. real-pipeline), `test_planner_parity -k provenance` Leg A, SP1 `tests/compiler/` unchanged (equivalence gate still green — enrichment only touches recorded IR configs).
- CI-green: Rust `vec_provenance`, Python Leg B, + all above.
- Additive: `provenance`/`field_lineage` are read-only; the only existing-behavior edit is `capture.py`'s Match branch (recorded IR configs), which the equivalence gate (kind-only Match assertion) tolerates.
