# Layer 2 Phase 0 + Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce the Layer 2 decision-debt ledger (Phase 0) and surgically delete the strictly-dominated `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` / SP1 path (Phase 1) without disturbing the kept `GOLDENMATCH_CLUSTER_FRAMES_OUT` frames-out path or the shared columnar helpers.

**Architecture:** Phase 0 is a read-only investigation that emits a file:line-cited ledger doc — the guardrail/input every later phase consumes. Phase 1 removes the SP1 gate (`_columnar_cluster_build_enabled`), its `build_clusters` branch, `_build_clusters_via_frames`, the `pipeline.py` identity-view branch that depends on the gate, and the SP1-only tests/benches — while KEEPING the shared `_columnar_presplit`/`_finalize_clusters` helpers, `build_cluster_frames`, `cluster_frames_to_dict`, and the entire frames-out path. The canonical cascading-split adversarial fixture is preserved on the frames-out parity test BEFORE the SP1 tests are deleted.

**Tech Stack:** Python 3.11-3.13, polars, ruff, `py_compile`, pyright, pytest (CI-only here — see Environment), `gh` CLI, git.

---

## Environment constraints (HARD — override the skill's default "run pytest locally")

Per this repo's CLAUDE.md + project memory:
- **Do NOT run `pytest`, `import goldenmatch`, or `uv` locally.** Importing polars/goldenmatch hangs the box and spawns zombie python that starves it. Local validation is **`ruff` + `python -m py_compile` ONLY**; the orchestrator may run **pyright once, time-bounded**.
- **The native cluster kernel path is CI-only validatable** (local `_native.pyd` lacks `build_clusters_arrow`/`mst_split_components` and can't be rebuilt). Byte-identical parity under `GOLDENMATCH_NATIVE=1` is verified in CI's fresh-native `python (goldenmatch)` lane.
- **Subagent rule:** subagents run `ruff` + `py_compile` only — never import/pytest/pyright/uv. The orchestrator runs pyright (bounded) and pushes to CI.
- Branch off `main` (not the current release branch) per the branch/merge SOP; squash-merge via PR. A worktree off `main` is recommended for execution.

So the standard TDD "run the failing test locally" loop is replaced by: **ruff → py_compile → grep for dangling references → pyright (orchestrator) → push → CI full suite green.** This is the established posture for cluster/native parity work in this codebase.

---

## File / touch map

**Phase 0 (create):**
- `docs/superpowers/plans/2026-06-09-columnar-frames-layer2-ledger.md` — the decision-debt ledger.

**Phase 1 (modify):**
- `packages/python/goldenmatch/goldenmatch/core/cluster.py` — remove SP1 gate fn, the `build_clusters` branch, and `_build_clusters_via_frames`; scrub stale docstring refs to `_build_clusters_via_frames` (≈ :850 in `_build_clusters_dict_path`, ≈ :1151 in `_columnar_presplit`).
- `packages/python/goldenmatch/goldenmatch/core/pipeline.py` — collapse the SP1-dependent `elif` in the identity pair-score-view block (≈ lines 2078-2087).
- `packages/python/goldenmatch/tests/test_identity_from_frames_parity.py` (≈ :84) — drop the now-dead `monkeypatch.setenv("GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", "1")` (the test builds via `build_cluster_frames` + `CLUSTER_FRAMES_OUT`, so it still passes).
- `packages/python/goldenmatch/tests/test_cluster_pairscore_view_parity.py` (≈ :23-25) — scrub docstring refs to the deleted gate/function.
- `packages/python/goldenmatch/scripts/bench_cluster_frames_out.py` (≈ :109,:146) — the baseline sets `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD=1` (the "score-free dict"); after deletion that env var is dead, so repoint the baseline at plain `build_clusters` and reword the framing.
- `.github/workflows/bench-cluster-frames-out.yml` (≈ :6) — same baseline fix at the workflow layer.

**Note on cascading-split coverage (verified, GOOD NEWS):** `tests/test_cluster_frames_out_parity.py::_adversarial_pairs` (≈ :46-84) ALREADY carries the canonical cascading fixture (Group A ids 40-48, Group B ids 50-57) wired through `build_cluster_frames` → `cluster_frames_to_dict` vs the dict path. So deleting the SP1 tests loses NO adversarial coverage of the shared `_columnar_presplit`/`_finalize_clusters`. Task 1.1 is therefore a **verification-only no-op** (confirm, don't port).

**Phase 1 (delete — per the Phase 0 manifest):**
- `packages/python/goldenmatch/tests/test_columnar_cluster_build_parity.py` (SP1 parity gate)
- `packages/python/goldenmatch/tests/test_columnar_drop_pairscores_parity.py` (SP4 / SP1-gated)
- `packages/python/goldenmatch/scripts/bench_columnar_cluster_build.py`
- `packages/python/goldenmatch/scripts/bench_columnar_drop_pairscores.py`
- `.github/workflows/bench-columnar-cluster-build.yml` AND `.github/workflows/bench-columnar-drop-pairscores.yml` — these are STANDALONE `workflow_dispatch` files whose only job runs the deleted bench scripts; `git rm` them wholesale (confirm names in the Phase 0 manifest).
- doc references to the gate (CLAUDE.md / README / CHANGELOG) per manifest

**Phase 1 (KEEP — shared with the frames-out path; logic untouched, light reference scrub only where noted in the modify list):**
- `cluster.py::_columnar_presplit`, `cluster.py::_finalize_clusters`, `cluster.py::build_cluster_frames`, `cluster.py::cluster_frames_to_dict`, `cluster.py::build_clusters_arrow_native`, `cluster.py::_cluster_frames_out_enabled` (logic unchanged; only the two stale docstrings in the modify list are scrubbed)
- `tests/test_cluster_frames_out_parity.py` (the kept adversarial gate — read-only verify in Task 1.1), `tests/test_identity_from_frames_parity.py` + `tests/test_cluster_pairscore_view_parity.py` (KEEP the tests; only scrub the dead gate reference per the modify list)
- `scripts/bench_pipeline_complete_path.py` (Phase 3 verdict harness — untouched), `scripts/bench_cluster_frames_out.py` (KEEP; baseline reference fixed per the modify list)

---

## Phase 0 — Decision-debt ledger

> Read-only. No code changes. Output is one doc that the rest of the roadmap consumes.

### Task 0.1: Build the gate reference manifest

**Files:** Create `docs/superpowers/plans/2026-06-09-columnar-frames-layer2-ledger.md`

- [ ] **Step 1: Enumerate every reference to all three Layer 2 gates, repo-wide.**

Run (repo root):
```
rg -n "GOLDENMATCH_COLUMNAR_CLUSTER_BUILD|GOLDENMATCH_COLUMNAR_PIPELINE|GOLDENMATCH_CLUSTER_FRAMES_OUT|_columnar_cluster_build_enabled|_build_clusters_via_frames|_cluster_frames_out_enabled|_use_columnar|build_clusters_columnar" packages/ docs/ README.md .github/
```
Expected: hits in `core/cluster.py`, `core/pipeline.py`, the 5 test files, 3 bench scripts, possibly CLAUDE.md/README/CHANGELOG/workflows.

- [ ] **Step 2: Record each hit in the ledger** under a per-gate table: `file:line | role (definition / branch / consumer / test / bench / doc) | keep-or-delete`. Note: references under `docs/superpowers/specs|plans/*` (the SP1 design/plan history) are **gitignored local-only and out of scope** — log them as "history, do not touch," don't add them to the delete manifest.

### Task 0.2: Map the SP1 deletion boundary + residual `_clusters_dict()` sites

- [ ] **Step 1: Confirm the SP1-exclusive surface vs shared helpers.**

Verify in `core/cluster.py` that:
- `_build_clusters_via_frames` (def ≈ :1044) calls `_columnar_presplit` and `_finalize_clusters`.
- `build_cluster_frames` (def ≈ :559) ALSO uses `_columnar_presplit` / mirrors `_finalize_clusters`.

Conclusion to record: `_columnar_presplit` + `_finalize_clusters` are **shared → KEEP**; only `_build_clusters_via_frames` + `_columnar_cluster_build_enabled` + the `build_clusters` branch are SP1-exclusive → delete.

- [ ] **Step 2: Confirm the cascading-split adversarial fixture location.**

Check whether `tests/test_cluster_frames_out_parity.py` exercises a cascading (multi-level) split fixture (the Group A ids 40-48 + Group B ids 50-57 shape from `test_columnar_drop_pairscores_parity.py::_adversarial_pairs`). Record YES/NO — this decides whether Phase 1 Task 1.1 must port the fixture. **Plan-review already confirmed YES (present at ≈ :46-84); record YES unless the code has changed.** This makes Task 1.1 verification-only.

- [ ] **Step 3: Classify the residual `_clusters_dict()` rebuild sites** (`pipeline.py` `_clusters_dict` ≈ :1667). For each caller (adaptive refiner, `output_clusters` rows, lineage, golden_provenance, `results["clusters"]`), mark **hot-path** (would defeat the Phase 3 RSS measurement) vs **output-only / by-design**. Record the bench-config implication (which output flags must be off for a clean RSS read).

### Task 0.3: Finalize and review the ledger

- [ ] **Step 1: Write the ledger** with three sections (one per gate), the deletion manifest for Phase 1, the cascading-fixture YES/NO finding, and the residual-`_clusters_dict()` classification table.
- [ ] **Step 2: ruff + py_compile sanity** (doc only — no code; skip if no code touched).
- [ ] **Step 3: Commit.**
```
git add docs/superpowers/plans/2026-06-09-columnar-frames-layer2-ledger.md
git commit -m "docs(layer2): decision-debt ledger (Phase 0) for columnar/frames gates"
```

**Definition of done:** the ledger unambiguously answers — (a) the exact SP1 deletion manifest, (b) whether the cascading adversarial fixture is preserved on the frames-out path, (c) which `_clusters_dict()` sites are hot-path. Phase 1 consumes this.

---

## Phase 1 — Delete the dominated gate (`GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` / SP1)

> Surgical deletion. The DEFAULT dict path and the frames-out path must stay byte-identical. Validate via ruff + py_compile + grep + CI (no local pytest).

### Task 1.1: Verify cascading-split adversarial coverage on the frames-out path (GUARDRAIL — do this FIRST)

**Files:** Read-only `packages/python/goldenmatch/tests/test_cluster_frames_out_parity.py`

Rationale: the SP1 tests being deleted carry the canonical cascading-split fixture that adversarially covers the SHARED `_columnar_presplit`/`_finalize_clusters` (cascading split bugs survived all static review historically; CI's adversarial fixture is the real verifier). That coverage must already live on the kept frames-out parity test before the SP1 tests go. **Plan-review confirmed it does** (≈ :46-84, Group A ids 40-48 + Group B ids 50-57, wired through `build_cluster_frames` → `cluster_frames_to_dict`), so this task is verification-only.

- [ ] **Step 1: Confirm the cascading fixture is present.**
```
rg -n "40, 41|50, 51|Group A|Group B|build_cluster_frames" packages/python/goldenmatch/tests/test_cluster_frames_out_parity.py
```
Expected: the Group A/B ids and a `build_cluster_frames(...)` → `cluster_frames_to_dict(...)` vs dict-path comparison are present. If — and only if — Phase 0 Task 0.2 Step 2 unexpectedly recorded ABSENT, port `_adversarial_pairs` from `test_columnar_drop_pairscores_parity.py` into this file (members-as-set, strict-except-pair_scores) and commit before proceeding. Otherwise this is a no-op (no edit, no commit).

### Task 1.2: Remove the SP1 gate, branch, and `_build_clusters_via_frames` from cluster.py

**Files:** Modify `packages/python/goldenmatch/goldenmatch/core/cluster.py`

- [ ] **Step 1: Delete the SP1 branch in `build_clusters`** (≈ :513-524). Remove the `# SP1 (columnar cluster-build core)...` comment block and:
```python
    if _columnar_cluster_build_enabled():
        return _build_clusters_via_frames(
            pairs, all_ids, max_cluster_size, weak_cluster_threshold, auto_split,
            split_edge_budget,
        )
```
Leave the subsequent `return _build_clusters_dict_path(...)` as the sole path.

- [ ] **Step 2: Delete `_columnar_cluster_build_enabled`** (≈ :532-543) entirely.

- [ ] **Step 3: Delete `_build_clusters_via_frames`** (≈ :1044-1126) entirely. Do NOT touch `_columnar_presplit` (≈ :1129) or `_finalize_clusters` — they are shared with `build_cluster_frames` (`_columnar_presplit` is called by `build_cluster_frames` ≈ :633; `_finalize_clusters` by the default `_build_clusters_dict_path` ≈ :916).

- [ ] **Step 3b: Scrub stale docstring references to the deleted function** so the Task 1.4 grep comes back clean and the prose isn't misleading:
  - `_build_clusters_dict_path` docstring (≈ :850): remove the "The columnar path (`_build_clusters_via_frames`) shares the tail..." sentence.
  - `_columnar_presplit` docstring (≈ :1151): reword "membership must EXACTLY match the current `_build_clusters_via_frames`..." to reference `build_cluster_frames` / the frames-out parity gate instead.

- [ ] **Step 4: ruff + py_compile.**
```
ruff check packages/python/goldenmatch/goldenmatch/core/cluster.py
python -m py_compile packages/python/goldenmatch/goldenmatch/core/cluster.py
```
Expected: clean (no unused-import / undefined-name).

- [ ] **Step 5: Commit.**
```
git add packages/python/goldenmatch/goldenmatch/core/cluster.py
git commit -m "refactor(cluster): remove dominated GOLDENMATCH_COLUMNAR_CLUSTER_BUILD (SP1) path"
```

### Task 1.3: Collapse the SP1-dependent branch in pipeline.py

**Files:** Modify `packages/python/goldenmatch/goldenmatch/core/pipeline.py`

The identity pair-score-view block (≈ :2072-2087) has three branches: `cluster_frames is not None` (frames-out → `from_frames`, KEEP), `elif isinstance(clusters, dict): if _columnar_cluster_build_enabled(): ... from_pairs(...)` (SP1 → DELETE), and the implicit gate-OFF `else` (view stays None, KEEP).

- [ ] **Step 1: Remove the SP1 `elif` branch** (≈ :2078-2087):
```python
    elif isinstance(clusters, dict):
        from goldenmatch.core.cluster import _columnar_cluster_build_enabled
        if _columnar_cluster_build_enabled():
            from goldenmatch.core.cluster_pairscores import ClusterPairScores
            # SP4: ... build the view from the RAW input pairs ...
            pair_score_view = ClusterPairScores.from_pairs(all_pairs, clusters)
```
After removal, only the `if cluster_frames is not None:` branch sets `pair_score_view`; the gate-OFF dict path leaves it `None` (real pair_scores reach identity), which is byte-identical to today's default. Update the block's leading comment to drop the `_columnar_cluster_build_enabled` bullet (≈ :2055-2056) so the doc matches.

- [ ] **Step 2: ruff + py_compile.**
```
ruff check packages/python/goldenmatch/goldenmatch/core/pipeline.py
python -m py_compile packages/python/goldenmatch/goldenmatch/core/pipeline.py
```
Expected: clean.

- [ ] **Step 3: Commit.**
```
git add packages/python/goldenmatch/goldenmatch/core/pipeline.py
git commit -m "refactor(pipeline): drop SP1 identity pair-score-view branch (gate removed)"
```

### Task 1.4: Scrub orphan gate references in kept artifacts (so the Task 1.5 grep is clean)

These four KEPT files reference the gate but keep their logic; without this task the Task 1.5 dangling-reference grep cannot pass.

**Files:** Modify `tests/test_identity_from_frames_parity.py`, `tests/test_cluster_pairscore_view_parity.py`, `scripts/bench_cluster_frames_out.py`, `.github/workflows/bench-cluster-frames-out.yml`

- [ ] **Step 1: `tests/test_identity_from_frames_parity.py` (≈ :84)** — delete the now-dead `monkeypatch.setenv("GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", "1")` line. The test builds via `build_cluster_frames` + `GOLDENMATCH_CLUSTER_FRAMES_OUT`, so removing the dead setenv changes nothing functionally.
- [ ] **Step 2: `tests/test_cluster_pairscore_view_parity.py` (≈ :23-25)** — scrub the docstring references to the deleted gate/function (prose only).
- [ ] **Step 3: `scripts/bench_cluster_frames_out.py` (≈ :109,:146)** — the baseline currently does `os.environ["GOLDENMATCH_COLUMNAR_CLUSTER_BUILD"] = "1"` to get the "score-free dict." That env var is now dead. Repoint the baseline at plain `build_clusters` (the only dict path) and reword the comment/framing so the bench still measures "frames-out delta vs the dict path."
- [ ] **Step 4: `.github/workflows/bench-cluster-frames-out.yml` (≈ :6)** — apply the same baseline fix at the workflow layer (drop/replace the `COLUMNAR_CLUSTER_BUILD=1` baseline env).
- [ ] **Step 5: ruff + py_compile the two touched Python files.**
```
ruff check packages/python/goldenmatch/tests/test_identity_from_frames_parity.py packages/python/goldenmatch/tests/test_cluster_pairscore_view_parity.py packages/python/goldenmatch/scripts/bench_cluster_frames_out.py
python -m py_compile packages/python/goldenmatch/tests/test_identity_from_frames_parity.py packages/python/goldenmatch/tests/test_cluster_pairscore_view_parity.py packages/python/goldenmatch/scripts/bench_cluster_frames_out.py
```
Expected: clean.
- [ ] **Step 6: Commit.**
```
git add -A
git commit -m "chore(layer2): scrub dead COLUMNAR_CLUSTER_BUILD refs from kept tests/benches"
```

### Task 1.5: Delete SP1-only tests and benches (per the Phase 0 manifest)

**Files:** Delete the SP1-exclusive tests + benches + standalone workflow files confirmed by the ledger.

- [ ] **Step 1: Delete the SP1 parity tests** (their gate-on path no longer exists; cascading coverage now lives on the frames-out parity test via Task 1.1):
```
git rm packages/python/goldenmatch/tests/test_columnar_cluster_build_parity.py
git rm packages/python/goldenmatch/tests/test_columnar_drop_pairscores_parity.py
```

- [ ] **Step 2: Delete the SP1 bench scripts:**
```
git rm packages/python/goldenmatch/scripts/bench_columnar_cluster_build.py
git rm packages/python/goldenmatch/scripts/bench_columnar_drop_pairscores.py
```

- [ ] **Step 3: Delete the two standalone SP1 bench workflow files** (each is a dedicated `workflow_dispatch` file whose only job runs a deleted bench script — `git rm` wholesale, do not edit):
```
git rm .github/workflows/bench-columnar-cluster-build.yml
git rm .github/workflows/bench-columnar-drop-pairscores.yml
```
(Confirm the exact filenames against the Phase 0 manifest; if a bench was instead an embedded job in a shared workflow, edit that file to drop the job.)

- [ ] **Step 4: Grep for dangling references** — must be ZERO in `packages/` and `.github/` now that Task 1.4 scrubbed the kept artifacts and Task 1.2 scrubbed the docstrings:
```
rg -n "COLUMNAR_CLUSTER_BUILD|_build_clusters_via_frames|_columnar_cluster_build_enabled|bench_columnar_cluster_build|bench_columnar_drop_pairscores|test_columnar_cluster_build_parity|test_columnar_drop_pairscores_parity" packages/ .github/
```
Expected: no hits. (Hits under `docs/` — the roadmap/ledger describing the removal, and the gitignored `docs/superpowers/specs|plans/*` SP1 design history — are out of scope and fine; do NOT scrub gitignored local-only history.)

- [ ] **Step 5: Commit.**
```
git add -A
git commit -m "chore(layer2): delete SP1-only parity tests + bench scripts + bench workflows"
```

### Task 1.6: Clean documentation references to the gate

**Files:** Modify the docs the Phase 0 manifest names (likely `packages/python/goldenmatch/CLAUDE.md`, root `CLAUDE.md`, `README.md`, `CHANGELOG.md`).

- [ ] **Step 1:** Remove or update prose that documents `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` as a live opt-in. Add a one-line CHANGELOG entry: "Removed the dominated `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` opt-in (SP1); superseded by `GOLDENMATCH_CLUSTER_FRAMES_OUT`." Keep ASCII (no em-dashes) per the repo's release-notes rule.
- [ ] **Step 2: Commit.**
```
git add -A
git commit -m "docs(layer2): drop GOLDENMATCH_COLUMNAR_CLUSTER_BUILD references"
```

### Task 1.7: Verify and open the PR

- [ ] **Step 1: Orchestrator-only — run pyright once, time-bounded**, on the strict-include modules:
```
pyright packages/python/goldenmatch/goldenmatch/core/cluster.py packages/python/goldenmatch/goldenmatch/core/pipeline.py
```
Expected: no NEW errors vs baseline (`cluster.py`/`pipeline.py` are in the pyright CI include).

- [ ] **Step 2: Push the branch + open the PR** (auth dance per repo SOP — push as the personal account, switch back after).
```
git push -u origin <branch>
gh pr create --title "Layer 2 Phase 1: remove dominated COLUMNAR_CLUSTER_BUILD (SP1)" --body "<summary + link to roadmap + ledger>"
```

- [ ] **Step 3: Watch CI** — the full goldenmatch suite (incl. the fresh-native lane that validates the frames-out path's byte-identical parity with `GOLDENMATCH_NATIVE=1`) must be green. Poll, don't trust per-step JSON conclusions for `continue-on-error` steps — grep the raw pytest summary.
```
while gh pr checks <N> | grep -qE "pending|in_progress"; do sleep 30; done
gh pr checks <N>
```
Expected: required checks pass; the kept `test_cluster_frames_out_parity.py` (now with cascading coverage) is green native AND off-native.

- [ ] **Step 4: Merge** (squash, delete branch) once green and approved.

**Definition of done:** `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` and `_build_clusters_via_frames` no longer exist anywhere in `packages/`; the default dict path and the frames-out path are byte-identical to pre-change (CI fresh-native lane green); the shared `_columnar_presplit`/`_finalize_clusters` retain adversarial cascading-split coverage via the frames-out parity test.

---

## Risks

- **Deleting a shared helper by mistake** — the single biggest risk. `_columnar_presplit` and `_finalize_clusters` are used by BOTH `_build_clusters_via_frames` (delete) and `build_cluster_frames` (keep). Task 1.2 Step 3 explicitly scopes the deletion; the Task 1.4 grep + CI frames-out parity catch an over-delete.
- **Losing cascading-split coverage** — the kept `test_cluster_frames_out_parity.py` already carries the cascading fixture (plan-review confirmed, ≈ :46-84), so deleting the SP1 tests loses nothing; Task 1.1 verifies this FIRST as a guardrail.
- **`continue-on-error` CI masking a real failure** — grep the raw pytest summary line, don't trust step `conclusion`.
- **Native-only divergence** — the SP1 path had native vs off-native branches; the frames-out path it shares helpers with is CI-only validatable. Trust the fresh-native lane, not local runs.

## Out of scope (later phases of the roadmap)
- Phase 2 (verify SP-C residual), Phase 3 (25M complete-path verdict bench via `scripts/bench_pipeline_complete_path.py`), Phase 4 (flip/delete `CLUSTER_FRAMES_OUT`), Phase 5 (the `COLUMNAR_PIPELINE` scorer gate). See `docs/superpowers/plans/2026-06-09-columnar-frames-layer2-verdict-roadmap.md`.
