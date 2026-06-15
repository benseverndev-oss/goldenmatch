# Arrow 55 -> 59 pyarrow-Crate Upgrade Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking. NOTE: this is a dependency upgrade — exact code breakages are unknown until the first build, so several steps are "build, read the compiler errors, fix" rather than pre-written code. That is intentional and correct for this kind of work.

**Goal:** Move the five pyarrow-FFI Rust crates from arrow 55 to arrow 59, in two phases, leaving the insulated `datafusion-udf` on arrow 58.

**Architecture:** Phase 1 bumps the one coupled pair (`graph-core` + `native`) in a single lockstep PR; Phase 2 bumps the three independent crates (`analysis-native`, `goldencheck-native`, `native-flow`) in separate PRs. Each crate is a standalone cargo workspace, so locks/wheels are per-crate. Validation is CI-centric — the merge queue's full-matrix `merge_group` run builds every wheel (exactly what caught #999).

**Tech Stack:** Rust / cargo, arrow-rs (the `pyarrow` + `ffi` features), maturin (PyO3 wheels), `gh` CLI + the native merge queue.

**Spec:** `docs/superpowers/specs/2026-06-15-arrow-59-pyarrow-upgrade-design.md`

---

## Pre-flight: environment + workflow constraints (read first)

- **Do NOT do heavy local Rust builds on this box.** Memory `feedback_ort_onnxruntime_no_local_link`: ort/onnxruntime-linked crates don't link locally on Windows, and `cargo test`/`cargo build` are OOM-risk. `cargo check` on the pyo3-free `graph-core` is fine locally and is the cheap way to enumerate arrow breakages. For wheel crates (`native` etc.), do NOT rely on a local wheel build — let CI build it. The authoritative validation is the **merge queue full-matrix run**.
- **Subagents:** keep local commands light (`cargo check` on graph-core, `ruff`/`py_compile` for any touched Python). Do NOT run `uv sync`, `pytest`, or full wheel builds locally (zombie-process / OOM starvation, per `project_663_arrow_kernels` env note). CI does the heavy lifting.
- **Merge queue:** enqueue with `gh pr merge <N> --auto --squash` (NO `--delete-branch` — rejected under a queue). The queue runs the full matrix on `merge_group` and merges FIFO. Don't poll CI (`feedback_dont_poll_ci_arm_automerge`).
- **Auth:** push/PR to `benseverndev-oss` needs `gh auth switch --user benzsevern`; switch back to `benzsevern-mjh` after (`feedback_github_auth_switch`). `gh pr create` may need `GH_TOKEN=$(gh auth token --user benzsevern)`.
- **Branch off fresh `origin/main`** for each phase (`feedback_branch_off_fresh_origin_main`). This worktree (`feat/arrow-59-pyarrow`, off `origin/main`) is fine for Phase 1; cut Phase 2 branches off fresh `origin/main` at the time.
- **Cargo.lock:** #999 (the graph-core arrow bump) changed only `Cargo.toml`, no lockfile — so these crates' `Cargo.lock` are not tracked (libraries). Verify per crate with `git ls-files <crate>/Cargo.lock`; only regenerate + commit a lock if it is tracked.

## File Structure

| File | Phase | Responsibility |
|------|-------|----------------|
| `packages/rust/extensions/graph-core/Cargo.toml` | 1 | arrow `55` -> `59` |
| `packages/rust/extensions/graph-core/src/*.rs` | 1 | fix arrow 55->59 API breaks (if any) |
| `packages/rust/extensions/native/Cargo.toml` | 1 | arrow `55` -> `59` (lockstep with graph-core) |
| `packages/rust/extensions/native/src/*.rs` | 1 | fix arrow 55->59 API breaks (if any) |
| `packages/rust/extensions/analysis-native/{Cargo.toml,src/*.rs}` | 2 | arrow `55` -> `59` + fixes |
| `packages/rust/extensions/goldencheck-native/{Cargo.toml,src/*.rs}` | 2 | arrow `55` -> `59` + fixes |
| `packages/rust/extensions/native-flow/{Cargo.toml,src/*.rs}` | 2 | arrow `55` -> `59` + fixes |
| `~/.claude/.../memory/project_arrow_59_workspace_upgrade.md` | post-1 | mark #999 superseded/closed |

---

## Task 1 (Phase 1): graph-core + native -> arrow 59 (single lockstep PR)

**Files:** `graph-core/Cargo.toml`, `native/Cargo.toml`, plus whatever `.rs` files the compiler flags.

- [ ] **Step 1: Confirm clean base**

This worktree is already `feat/arrow-59-pyarrow` off `origin/main`. Confirm:
Run: `git -C /d/show_case/gm-arrow59 status --porcelain` (expect: only the committed spec, clean tree otherwise) and `git -C /d/show_case/gm-arrow59 log --oneline -1` (the spec commit).

- [ ] **Step 2: Bump both Cargo.tomls to arrow 59**

In `packages/rust/extensions/graph-core/Cargo.toml`: `arrow = { version = "55", default-features = false }` -> `version = "59"`.
In `packages/rust/extensions/native/Cargo.toml`: `arrow = { version = "55", default-features = false, features = ["pyarrow"] }` -> `version = "59"`.
Preserve every other field (features, default-features) exactly.

- [ ] **Step 3: Enumerate breakages on graph-core (cheap, local)**

Run: `cd /d/show_case/gm-arrow59/packages/rust/extensions/graph-core && cargo check`
Expected: either clean, or a list of arrow-59 compile errors. graph-core is pyo3-free so this is safe locally. Record every error.

- [ ] **Step 4: Fix graph-core arrow-59 breaks**

Fix each error the compiler reports. The arrow surface here is `ArrayData`, `Int64Array`/`Float64Array`/`StringArray`, `ListArray`, the builders, `DataType`. Common arrow-rs major-bump breaks: builder method signature tweaks, `ArrayData` construction, deprecated-then-removed helpers. Follow the compiler; consult the arrow-rs CHANGELOG for 56/57/58/59 only if an error is non-obvious. Re-run `cargo check` until clean. **Do NOT change the arrow-free slice-kernel signatures** (`dedup_pairs_max_score`, `connected_components`) — datafusion-udf and postgres depend on them across the arrow boundary (spec: "datafusion-udf boundary").

- [ ] **Step 5: native arrow-59 breaks**

`native` links PyO3 (and possibly ort via embeddings) so a local build may fail to link on this box — that's expected, not an arrow problem. Try `cd ../native && PYO3_PYTHON=<venv python> cargo check` to surface arrow errors; if it won't link locally, skip and rely on CI (Step 8) to surface them. Fix any arrow-59 errors in `native/src/*.rs` the same way (follow the compiler / CI log).

- [ ] **Step 6: Regenerate locks only if tracked**

Run: `git -C /d/show_case/gm-arrow59 ls-files packages/rust/extensions/graph-core/Cargo.lock packages/rust/extensions/native/Cargo.lock`
If a path prints (tracked), regenerate it (`cargo generate-lockfile` in that crate) and stage it. If nothing prints, skip — CI resolves fresh.

- [ ] **Step 7: Commit**

```bash
cd /d/show_case/gm-arrow59
git add packages/rust/extensions/graph-core packages/rust/extensions/native
git commit -m "chore(deps): arrow 55->59 for graph-core + native (Phase 1, supersedes #999)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 8: Push, PR, and let the queue validate (the real gate)**

```bash
gh auth switch --user benzsevern
git -C /d/show_case/gm-arrow59 push -u origin feat/arrow-59-pyarrow
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create --repo benseverndev-oss/goldenmatch --base main \
  --head feat/arrow-59-pyarrow --title "chore(deps): arrow 55->59 for graph-core + native (Phase 1)" \
  --body-file <(printf '%s\n' "Phase 1 of the arrow-59 pyarrow upgrade (spec under docs/superpowers/specs/). Bumps the coupled graph-core+native pair together; supersedes #999. datafusion-udf stays on arrow 58 (insulated).")
gh pr merge <N> --repo benseverndev-oss/goldenmatch --auto --squash   # no --delete-branch
gh auth switch --user benzsevern-mjh
```

The `merge_group` full-matrix run builds the `goldenmatch-native` wheel via `uv sync --all-packages` — the exact build that failed for #999. **This is the acceptance gate.** If it goes red, read the failing job log (`gh run view <id> --log-failed | grep -iE "error\[|error:|arrow"`), fix the surfaced arrow break, push, re-enqueue. If green, it merges FIFO.

- [ ] **Step 9: Verify the native-parity suite ran green in that matrix**

In the merged `merge_group` run, confirm the `native` lane (the goldenmatch-native parity suite) passed — proving behavior is unchanged on arrow 59, not just that it compiled. (It's part of the full matrix; check the run's `native` job conclusion.)

---

## Task 2 (Phase 1 close-out): retire #999

- [ ] **Step 1: Close #999 with a note**

After Task 1 merges:
```bash
gh auth switch --user benzsevern
gh pr comment 999 --repo benseverndev-oss/goldenmatch --body "Superseded by the Phase 1 graph-core+native arrow-59 bump (merged). Closing — a graph-core-only bump can't work; the pair had to move together."
gh pr close 999 --repo benseverndev-oss/goldenmatch
gh auth switch --user benzsevern-mjh
```

- [ ] **Step 2: Update the memory note**

Edit `C:\Users\bsevern\.claude\projects\D--show-case-goldenmatch\memory\project_arrow_59_workspace_upgrade.md`: graph-core+native now on arrow 59 (Phase 1 landed); #999 closed/superseded; Phase 2 (analysis-native/goldencheck-native/native-flow) remaining; datafusion-udf still 58 by design. Update the MEMORY.md one-liner to match.

---

## Task 3 (Phase 2a): analysis-native -> arrow 59

**Files:** `packages/rust/extensions/analysis-native/Cargo.toml` + any `.rs` the compiler flags.

- [ ] **Step 1: Fresh branch off main**

```bash
git fetch origin main
git worktree add /d/show_case/gm-arrow59-an -b chore/arrow-59-analysis-native origin/main
```

- [ ] **Step 2: Bump arrow**

`analysis-native/Cargo.toml`: `arrow = { version = "55", ... }` -> `"59"` (preserve features incl. `pyarrow`).

- [ ] **Step 3: Enumerate + fix**

analysis-native is a pyo3 wheel crate (deps `analysis-core`); if it won't `cargo check` locally (link), rely on CI. Where it does check, fix arrow-59 errors per the compiler. Otherwise push and read CI.

- [ ] **Step 4: Commit + PR + enqueue + validate**

```bash
git -C /d/show_case/gm-arrow59-an add packages/rust/extensions/analysis-native
git -C /d/show_case/gm-arrow59-an commit -m "chore(deps): arrow 55->59 for analysis-native (Phase 2)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
gh auth switch --user benzsevern
git -C /d/show_case/gm-arrow59-an push -u origin chore/arrow-59-analysis-native
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create --repo benseverndev-oss/goldenmatch --base main --head chore/arrow-59-analysis-native --title "chore(deps): arrow 55->59 for analysis-native (Phase 2)" --body "Phase 2 of the arrow-59 upgrade (spec under docs/superpowers/specs/). Independent standalone crate."
gh pr merge <N> --repo benseverndev-oss/goldenmatch --auto --squash
gh auth switch --user benzsevern-mjh
```

Acceptance: the merge_group full-matrix run builds the analysis-native wheel + its parity suite green. Then clean up the worktree.

---

## Task 4 (Phase 2b): goldencheck-native -> arrow 59

Identical recipe to Task 3, on `packages/rust/extensions/goldencheck-native` (deps `goldencheck-core`). Fresh branch `chore/arrow-59-goldencheck-native` off `origin/main`, worktree `/d/show_case/gm-arrow59-gc`. Bump arrow -> 59, fix per compiler/CI, commit, PR, enqueue, queue validates the wheel + parity.

---

## Task 5 (Phase 2c): native-flow -> arrow 59

Identical recipe on `packages/rust/extensions/native-flow` (package name `goldenflow-native`; deps `phonenumber` + arrow only, so the *smallest* arrow surface). Fresh branch `chore/arrow-59-native-flow` off `origin/main`, worktree `/d/show_case/gm-arrow59-nf`. Bump arrow -> 59, fix per compiler/CI, commit, PR, enqueue, queue validates the `goldenflow-native` wheel + parity.

Phase 2 tasks are independent — they may be done in any order or in parallel (separate worktrees, separate PRs).

---

## Done criteria

- All five crates (`graph-core`, `native`, `analysis-native`, `goldencheck-native`, `native-flow`) build on arrow 59 and pass their native-parity suites in the merge queue's full matrix.
- `datafusion-udf` untouched, still arrow 58 / datafusion 53.
- #999 closed/superseded; memory + its PR comment updated.
- No `Cargo.lock` left in a half-regenerated state (locks regenerated only where tracked).

## Rollback

Each phase is its own merged PR. To revert, `git revert` the squash-merge commit for that crate's PR through the merge queue — each crate is independent, so reverting one doesn't touch the others. (Phase 1's revert restores graph-core+native together, which is correct since they're coupled.)
