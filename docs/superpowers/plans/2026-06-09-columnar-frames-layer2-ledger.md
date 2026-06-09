# Columnar/Frames Layer 2 — Decision-Debt Ledger (Phase 0)

**Date:** 2026-06-09
**Worktree:** `D:/show_case/goldenmatch/.worktrees/layer2-sp1`
**Branch:** `feat/layer2-remove-sp1-columnar-build`
**Base HEAD at ledger time:** `fd0a2911ad5dcc7cf181a68bf3d5e92ae53a1dfd` (main)

Phase 0 is READ-ONLY. This ledger is the manifest the later deletion phase consumes.
Every claim below was verified by reading source in this worktree (not executed).

## Scope recap (the three opt-in gates)

| Gate env var | Path | Verdict |
| --- | --- | --- |
| `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` | SP1 internal columnar cluster build (`_build_clusters_via_frames`) | **DELETE** (strictly dominated: 0.77x@1M, ~2x RSS) |
| `GOLDENMATCH_CLUSTER_FRAMES_OUT` | SP-A/B/C frames-out cutover (`build_cluster_frames` / `cluster_frames_to_dict`) | **KEEP** |
| `GOLDENMATCH_COLUMNAR_PIPELINE` | Phase A columnar *scorer* (`score_blocks_columnar` -> `build_clusters_columnar`) | **KEEP** (separate axis, not clustering) |

The authoritative sweep regex was:
`GOLDENMATCH_COLUMNAR_CLUSTER_BUILD|GOLDENMATCH_COLUMNAR_PIPELINE|GOLDENMATCH_CLUSTER_FRAMES_OUT|_columnar_cluster_build_enabled|_build_clusters_via_frames|_cluster_frames_out_enabled|_use_columnar|build_clusters_columnar`
over `packages/ docs/ README.md .github/`.

`docs/superpowers/specs|plans/*` references are design history → **out of scope** (never in the delete manifest). They are listed in the History appendix only.

---

## Gate 1 — `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` (SP1) — DELETE

All paths are relative to the worktree root unless noted. Line numbers are at base HEAD `fd0a291`.

| file:line | role | action |
| --- | --- | --- |
| `packages/python/goldenmatch/goldenmatch/core/cluster.py:513-519` | comment block describing the SP1 dispatch ("SP1 (columnar cluster-build core)...") | DELETE |
| `packages/python/goldenmatch/goldenmatch/core/cluster.py:520-524` | branch `if _columnar_cluster_build_enabled(): return _build_clusters_via_frames(...)` inside `build_clusters` | DELETE |
| `packages/python/goldenmatch/goldenmatch/core/cluster.py:532-543` | definition `_columnar_cluster_build_enabled()` (reads the env var, default `"0"`) | DELETE |
| `packages/python/goldenmatch/goldenmatch/core/cluster.py:1044-1126` | definition `_build_clusters_via_frames(...)` (def line 1044 through its `return _finalize_clusters(...)` ending 1126) | DELETE |
| `packages/python/goldenmatch/goldenmatch/core/cluster.py:850` | docstring of `_build_clusters_dict_path`: sentence naming `_build_clusters_via_frames` ("The columnar path (`_build_clusters_via_frames`) shares the tail via `_finalize_clusters`.") | SCRUB |
| `packages/python/goldenmatch/goldenmatch/core/cluster.py:535` | docstring inside `_columnar_cluster_build_enabled` naming `_build_clusters_via_frames` | DELETE (removed with the function) |
| `packages/python/goldenmatch/goldenmatch/core/cluster.py:1151` | docstring of `_columnar_presplit` (shared, KEPT) referencing `_build_clusters_via_frames` for the parity invariant | SCRUB (reword to `build_cluster_frames` / frames-out parity gate) |
| `packages/python/goldenmatch/goldenmatch/core/pipeline.py:2055-2056` | comment bullet "`_columnar_cluster_build_enabled (GOLDENMATCH_COLUMNAR_CLUSTER_BUILD): the columnar build returns pair_scores={}`" in the identity pair-score-view block | SCRUB (drop the bullet) |
| `packages/python/goldenmatch/goldenmatch/core/pipeline.py:2078-2087` | `elif isinstance(clusters, dict): from ... import _columnar_cluster_build_enabled; if _columnar_cluster_build_enabled(): ... pair_score_view = ClusterPairScores.from_pairs(all_pairs, clusters)` (the SP1 identity-view branch) | DELETE |
| `packages/python/goldenmatch/tests/test_columnar_cluster_build_parity.py` (whole file, 1-end) | SP1 byte-identical parity test (lines 1-2 name the gate + `_build_clusters_via_frames`; setenv at 77/85; monkeypatch spy at 82-83/88) | DELETE FILE |
| `packages/python/goldenmatch/tests/test_columnar_drop_pairscores_parity.py` (whole file, 1-end) | SP4 drop-pairscores parity test (setenv at 87/90) | DELETE FILE |
| `packages/python/goldenmatch/scripts/bench_columnar_cluster_build.py` (whole file) | SP2 measure-first bench (docstring line 4; setenv 94/129/132) | DELETE FILE |
| `packages/python/goldenmatch/scripts/bench_columnar_drop_pairscores.py` (whole file) | SP4 measure-first bench (docstring line 8; setenv 81/113/115) | DELETE FILE |
| `.github/workflows/bench-columnar-cluster-build.yml` (whole file) | standalone SP2 bench workflow (comment line 7) | DELETE FILE |
| `.github/workflows/bench-columnar-drop-pairscores.yml` (whole file) | standalone SP4 bench workflow (comment line 6) | DELETE FILE |
| `packages/python/goldenmatch/tests/test_identity_from_frames_parity.py:84` | dead `monkeypatch.setenv("GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", "1")` (test builds via `build_cluster_frames` + `CLUSTER_FRAMES_OUT`) | SCRUB (delete the line) |
| `packages/python/goldenmatch/tests/test_cluster_pairscore_view_parity.py:23,25` | docstring naming `_columnar_cluster_build_enabled()` (23) + `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD=1` (25) | SCRUB (reword docstring) |
| `packages/python/goldenmatch/tests/test_cluster_frames_out_parity.py:96,118,138,157` | reference baseline sets `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD=1` ("score-free dict") for the KEPT frames-out parity tests | SCRUB (see Cascading-fixture finding + Unanticipated #1) |
| `packages/python/goldenmatch/scripts/bench_cluster_frames_out.py:7,109,146` | KEPT frames-out bench uses the SP1 gate-ON `build_clusters` as its baseline (docstring 6-11; subprocess baseline 109; parity baseline 146) | SCRUB (repoint baseline at plain `build_clusters`, reword framing) |
| `packages/python/goldenmatch/scripts/bench_pipeline_complete_path.py:177,315` | KEPT complete-path bench `os.environ.pop("GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", None)` defensive cleanup | SCRUB (optional/cosmetic — see Unanticipated #2) |
| `packages/python/goldenmatch/CHANGELOG.md` (new entry) | add: "Removed the dominated `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` opt-in (SP1); superseded by `GOLDENMATCH_CLUSTER_FRAMES_OUT`." | ADD (ASCII, no em-dash) |

---

## Gate 2 — `GOLDENMATCH_CLUSTER_FRAMES_OUT` (frames-out) — KEEP

Everything below stays. Listed so the deletion phase knows what NOT to touch.

| file:line | role | action |
| --- | --- | --- |
| `packages/python/goldenmatch/goldenmatch/core/cluster.py:546-556` | definition `_cluster_frames_out_enabled()` | KEEP |
| `packages/python/goldenmatch/goldenmatch/core/cluster.py:559-...` | definition `build_cluster_frames(...)` (frames-out entry point) | KEEP |
| `packages/python/goldenmatch/goldenmatch/core/cluster.py:633` | `build_cluster_frames` calls `_columnar_presplit(...)` — **proves `_columnar_presplit` has a live non-SP1 caller** | KEEP |
| `packages/python/goldenmatch/goldenmatch/core/pipeline.py:122` | import `_cluster_frames_out_enabled` | KEEP |
| `packages/python/goldenmatch/goldenmatch/core/pipeline.py:1600-1650` | SP-B cluster-stage `elif _cluster_frames_out_enabled(): cluster_frames = build_cluster_frames(...)` branch | KEEP |
| `packages/python/goldenmatch/goldenmatch/core/pipeline.py:1635` | `elif _cluster_frames_out_enabled():` dispatch | KEEP |
| `packages/python/goldenmatch/goldenmatch/core/pipeline.py:2057-2077` | identity-view comment + the `if cluster_frames is not None: pair_score_view = ClusterPairScores.from_frames(...)` branch | KEEP |
| `packages/python/goldenmatch/scripts/bench_pipeline_frames_out.py:7,87` | frames-out pipeline bench | KEEP |
| `packages/python/goldenmatch/scripts/bench_pipeline_complete_path.py:10,174,176,314,324,333` | complete-path bench frames-out variant | KEEP |
| `packages/python/goldenmatch/tests/test_pipeline_frames_out_parity.py` (all `CLUSTER_FRAMES_OUT` refs) | frames-out pipeline parity | KEEP |
| `packages/python/goldenmatch/tests/test_identity_from_frames_parity.py:83` | `setenv CLUSTER_FRAMES_OUT=1` (the load-bearing gate for this test) | KEEP |
| `packages/python/goldenmatch/tests/test_datafusion_spine_parity.py:288,305,315` | spine parity uses frames-out | KEEP |
| `.github/workflows/bench-pipeline-frames-out.yml:6` | frames-out pipeline bench workflow | KEEP |
| `.github/workflows/bench-cluster-frames-out.yml` (whole file) | frames-out build-stage bench workflow — KEPT, but see SCRUB of its `COLUMNAR_CLUSTER_BUILD` baseline (line 6 comment) | KEEP (with scrub of the baseline mention) |
| `packages/python/goldenmatch/tests/test_cluster_frames_out_parity.py` (CLUSTER_FRAMES_OUT refs 97,100,119,121,139,141,159) + cascading fixture | frames-out roundtrip parity incl. cascading-split adversarial fixture | KEEP (scrub only the `COLUMNAR_CLUSTER_BUILD` baseline lines) |

---

## Gate 3 — `GOLDENMATCH_COLUMNAR_PIPELINE` (Phase A scorer) — KEEP (unrelated axis)

This is the columnar *scorer* path, NOT clustering. The sweep regex catches it via
`build_clusters_columnar` + `_use_columnar`. None of it is SP1.

| file:line | role | action |
| --- | --- | --- |
| `packages/python/goldenmatch/goldenmatch/core/cluster.py:1647-...` | definition `build_clusters_columnar(...)` (thin wrapper over `build_clusters` on a pairs frame) | KEEP |
| `packages/python/goldenmatch/goldenmatch/core/cluster.py:1689,1719,1940,1952` | internal refs to `build_clusters_columnar` (profiling note, fallback, DataFusion legacy) | KEEP |
| `packages/python/goldenmatch/goldenmatch/core/pipeline.py:125` | import `build_clusters_columnar` | KEEP |
| `packages/python/goldenmatch/goldenmatch/core/pipeline.py:131,148-149` | `_columnar_pipeline_enabled()` (reads `GOLDENMATCH_COLUMNAR_PIPELINE`) + comment | KEEP |
| `packages/python/goldenmatch/goldenmatch/core/pipeline.py:1124,1129,1363,1365,1383,1597,1625,1628,2102,2104` | `_use_columnar` flag + columnar pair-stream cluster branch (`build_clusters_columnar(...)`) | KEEP |
| `packages/python/goldenmatch/scripts/profile_hotspots.py:28,61,119` | hotspot profiler uses `build_clusters_columnar` | KEEP |
| `packages/python/goldenmatch/scripts/bench_pair_stream_columnar.py:64,178` | pair-stream bench | KEEP |
| `packages/python/goldenmatch/tests/test_columnar_pipeline_parity.py` (all, incl. `COLUMNAR_PIPELINE` at 206) | Phase A scorer parity | KEEP |
| `packages/python/goldenmatch/tests/test_pair_stream_columnar_parity.py:6,26,457,474,479` | pair-stream parity | KEEP |
| `packages/python/goldenmatch/tests/test_cluster_columnar_parity.py:8` | columnar == build_clusters parity | KEEP |
| `packages/python/goldenmatch/CHANGELOG.md:375,377` | historical CHANGELOG entry naming `build_clusters_columnar` (#594/#647/#648) | KEEP (history) |
| `docs/columnar-pipeline-wiring.md:14,24,28,41,70,81,85` | tracked doc — describes `build_clusters_columnar` + `GOLDENMATCH_COLUMNAR_PIPELINE` ONLY (no SP1 ref) | KEEP |

---

## SP1 DELETION MANIFEST (consolidated)

### DELETE (code spans + whole files)

Source code:
- `core/cluster.py:513-524` — SP1 comment block + dispatch branch in `build_clusters`.
- `core/cluster.py:532-543` — `_columnar_cluster_build_enabled()` definition.
- `core/cluster.py:1044-1126` — `_build_clusters_via_frames(...)` definition.
- `core/pipeline.py:2078-2087` — SP1 identity pair-score-view branch (`elif isinstance(clusters, dict): if _columnar_cluster_build_enabled(): ... from_pairs(...)`).

Whole files:
- `tests/test_columnar_cluster_build_parity.py`
- `tests/test_columnar_drop_pairscores_parity.py`
- `scripts/bench_columnar_cluster_build.py`
- `scripts/bench_columnar_drop_pairscores.py`
- `.github/workflows/bench-columnar-cluster-build.yml`
- `.github/workflows/bench-columnar-drop-pairscores.yml`

### SCRUB (orphan refs in KEPT files — remove/reword the dead `COLUMNAR_CLUSTER_BUILD` / `_build_clusters_via_frames` mention, file stays)

- `core/cluster.py:850` — `_build_clusters_dict_path` docstring sentence naming `_build_clusters_via_frames`.
- `core/cluster.py:1151` — `_columnar_presplit` docstring naming `_build_clusters_via_frames` (reword to `build_cluster_frames` / frames-out parity).
- `core/pipeline.py:2055-2056` — comment bullet for `_columnar_cluster_build_enabled`.
- `tests/test_identity_from_frames_parity.py:84` — delete dead `monkeypatch.setenv("GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", "1")`.
- `tests/test_cluster_pairscore_view_parity.py:23,25` — reword docstring (gate + fn name).
- `tests/test_cluster_frames_out_parity.py:96,118,138,157` — repoint the reference baseline off the deleted gate (see Cascading-fixture finding + Unanticipated #1).
- `scripts/bench_cluster_frames_out.py:7,109,146` — repoint baseline at plain `build_clusters`, reword docstring framing.
- `.github/workflows/bench-cluster-frames-out.yml:6` — comment names `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD=1` baseline.
- `scripts/bench_pipeline_complete_path.py:177,315` — optional/cosmetic: dead `os.environ.pop(...)` (see Unanticipated #2).
- `CHANGELOG.md` — ADD removal entry.

### KEEP (shared helpers + proof of a live non-SP1 caller)

| symbol | file:line (def) | proven live non-SP1 caller (file:line) |
| --- | --- | --- |
| `_columnar_presplit` | `core/cluster.py:1129` | `build_cluster_frames` calls it at `core/cluster.py:633` (frames-out path, KEPT) |
| `_finalize_clusters` | `core/cluster.py:922` | `_build_clusters_dict_path` calls it at `core/cluster.py:916` (the DEFAULT gate-OFF dict path, always live) |
| `build_cluster_frames` | `core/cluster.py:559` | `core/pipeline.py:1636` (SP-B `_cluster_frames_out_enabled()` branch) |
| `cluster_frames_to_dict` | `core/cluster.py` (frames-out adapter) | `core/pipeline.py:1672` (lazy dict rebuild `_clusters_dict()`) |
| `_cluster_frames_out_enabled` | `core/cluster.py:546` | `core/pipeline.py:1635` |
| `build_clusters_columnar` | `core/cluster.py:1647` | `core/pipeline.py:1628` (`GOLDENMATCH_COLUMNAR_PIPELINE` scorer path) |
| `_build_clusters_dict_path` | `core/cluster.py:840` | `build_clusters` default return at `core/cluster.py:526` |

Note: `_build_clusters_via_frames` is the ONLY caller of `_columnar_presplit`+`_finalize_clusters` that disappears. Both helpers retain the live callers above → deleting SP1 must NOT touch them.

---

## Cascading-fixture finding

**YES.** `packages/python/goldenmatch/tests/test_cluster_frames_out_parity.py` already contains
the cascading-split adversarial fixture inside `_adversarial_pairs()`:
- Definition: lines **46-84**.
- Group A (ids 40-48, three triangles + two weak bridges → splits into 3 components at `max_cluster_size=5`): lines **68-74**.
- Group B (ids 50-57, two 4-cliques + weak bridge → a second splittable oversized cluster): lines **75-81**.
- Wired through `build_cluster_frames` → `cluster_frames_to_dict`: lines **101-102, 122, 142, 160** (tests `test_frames_out_roundtrips_to_dict_full`, `test_budget_break_frames_out_matches_dict`, `test_step3_quality_matches_dict_loop`).

This is the canonical cascading-split adversarial coverage that the shared
`_columnar_presplit`/`_finalize_clusters` retain AFTER SP1 deletion (the frames-out
path exercises the same shared helpers). So the SP1-only
`test_columnar_drop_pairscores_parity.py` / `test_columnar_cluster_build_parity.py`
can be deleted wholesale without losing cascading-split coverage.

**Caveat for the deletion phase:** this KEPT test currently uses the SP1 gate
(`GOLDENMATCH_COLUMNAR_CLUSTER_BUILD=1`) to build its REFERENCE `build_clusters` dict
(lines 96, 118, 138, 157). The `_norm()` helper (line 21) strips `pair_scores`, so the
parity assertions hold even after `build_clusters` reverts to populating real
`pair_scores`. The reference baseline must be repointed to plain `build_clusters` during
the scrub; behavior is unchanged because `pair_scores` is normalized away.

---

## Residual `_clusters_dict()` rebuild-site classification (pipeline.py)

`_clusters_dict()` is the SP-B/SP-C lazy dict rebuild closure (def `core/pipeline.py:1667`).
On the frames-out branch `clusters` stays `{}` until the first call builds + caches the dict
from `cluster_frames` (`cluster_frames_to_dict`, line 1672). On gate-OFF/columnar it returns the
already-bound real dict cheaply. None of these are SP1; the table classifies each caller for the
later phase's RSS reasoning (no action required by SP1 deletion).

| call site (file:line) | consumer | classification |
| --- | --- | --- |
| `core/pipeline.py:1667` | definition of the closure | n/a (definition) |
| `core/pipeline.py:1724` | adaptive golden-rules refiner (`refine_golden_rules(clusters=...)`), only when `golden_rules.adaptive=True` | output-only / by-design (opt-in feature; not the default hot path) |
| `core/pipeline.py:2013` | `output_clusters` rows builder (`for cid, cinfo in _clusters_dict().items()`) | output-only / by-design (guarded by `output_clusters` flag) |
| `core/pipeline.py:2036` | `build_lineage(... _clusters_dict())` | output-only / by-design (guarded by `output_golden or output_clusters or output_dupes`) |
| `core/pipeline.py:2042` | `golden_records_to_provenance(..., _clusters_dict(), ...)` | output-only / by-design (guarded by `lineage_provenance` + `golden_records`) |
| `core/pipeline.py:2109` | `results["clusters"] = _clusters_dict()` | hot-path TERMINAL by-design — on the frames-out identity-ON/output-OFF path this is the FIRST and ONLY rebuild, deliberately placed AFTER `stage("identity_resolve")` so cluster→golden→identity runs dict-free (the SP-C RSS win). Required: callers expect `results["clusters"]`. |

Hot-path note: stats/dupes/golden NEVER call `_clusters_dict()` — they read the metadata/assignments
frames directly (`core/pipeline.py:1675-1696`). Identity reads `cluster_frames` (not the dict) on the
frames-out path (`core/pipeline.py:2088-2094`). So the only unavoidable rebuild on the frames-out hot
path is the `results["clusters"]` terminal at 2109, which is by-design and output-shaped.

---

## Unanticipated references (NOT in the Phase-0 known-facts list)

These are the highest-value findings — a missed reference becomes a CI failure later.

1. **`tests/test_cluster_frames_out_parity.py:96,118,138,157` set `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD=1` as the KEPT frames-out parity tests' reference baseline.** The known-facts list said this file "ALREADY contains a cascading-split fixture" (true) but did NOT flag that the file *also* uses the deleted gate to produce its reference dict. After deletion the env var is dead; the reference must be repointed to plain `build_clusters`. The `_norm()` strip of `pair_scores` (line 21) makes this behavior-neutral, but the lines MUST be scrubbed or the test documents/relies on a dead gate. SCRUB.

2. **`scripts/bench_pipeline_complete_path.py:177,315` reference `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` via `os.environ.pop(..., None)`.** This KEPT complete-path bench was not in the known-facts list. Both are defensive cleanup pops on the dict/baseline path. They are HARMLESS after deletion (popping an unset var is a no-op) — NOT a CI risk — but they reference a dead var name. Classification: optional/cosmetic SCRUB.

3. **`.github/workflows/bench-cluster-frames-out.yml:6` (comment only) names `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD=1` as the bench baseline.** KEPT workflow; comment is stale after deletion. SCRUB (cosmetic). (The actual env var is set by `bench_cluster_frames_out.py:109/146`, already on the SCRUB list.)

4. **`docs/columnar-pipeline-wiring.md` is git-TRACKED (not gitignored) and references ONLY `build_clusters_columnar` + `GOLDENMATCH_COLUMNAR_PIPELINE`.** It is about the KEPT Gate 3 scorer, NOT SP1 — so no SP1 scrub is needed there. Flagged to pre-empt a false-positive "doc mentions columnar → must edit" during deletion. KEEP.

5. **`docs/superpowers/` is only PARTIALLY gitignored in this worktree.** `git check-ignore` returns "not ignored" for the Layer2 plan path and 80 files under `docs/superpowers/` are tracked; the newly-authored Layer2 plans show as untracked (`??`) in `git status`. Per Phase-0 instructions, ALL `docs/superpowers/specs|plans/*` references remain history / out of scope regardless of tracked state — they are NOT in the delete manifest.

---

## History appendix — `docs/superpowers/specs|plans/*` (out of scope)

Logged for completeness; do NOT add to the delete manifest. These are design history.

- `docs/superpowers/specs/2026-06-02-columnar-drop-pairscores-design.md` (SP4 design)
- `docs/superpowers/specs/2026-06-02-columnar-cluster-build-core-design.md` (SP1 design; cites `_build_clusters_via_frames` :39/:41, gate :120, scope :192)
- `docs/superpowers/specs/2026-06-02-cluster-pairscore-view-design.md` (cites `_columnar_cluster_build_enabled()` :436, `_build_clusters_via_frames` :621, `_finalize_clusters` :530 — pre-current line numbers)
- `docs/superpowers/specs/2026-06-01-arrow-phase1-cutover-design.md`, `2026-06-01-arrow-native-finish-line-design.md`
- `docs/superpowers/plans/2026-06-02-*` (scored-pairs-decouple, columnar-drop-pairscores, columnar-cluster-build-core, cluster-pairscore-view, arrow-phase1-cutover)
- `docs/superpowers/plans/2026-06-09-columnar-frames-layer2-phase0-phase1.md`, `2026-06-09-columnar-frames-layer2-verdict-roadmap.md` (the plans driving THIS work)
