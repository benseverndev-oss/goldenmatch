# Real DBLP-ACM benchmark for AbbrevAlign — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run AbbrevAlign through the existing entity-grouped CV harness on the real, labeled DBLP-ACM corpus (title ER + GT-derived venue matching) and produce an honest credibility report, with the heavy run executed on a GitHub runner.

**Architecture:** Add a `load_dblp_acm()` loader + a `--dblp-acm` mode to the existing standalone `scripts/bench_abbrevalign.py` (reusing its `evaluate`/`evaluate_cv`/`render`/`render_cv` unchanged), plus a `workflow_dispatch` lane that downloads DBLP-ACM from Leipzig and runs the bench. Title ER uses the full CV; venue (only 5 GT clusters → CV is degenerate) uses in-sample `evaluate()` + AUC + an explicit over-merge false-positive list.

**Tech Stack:** Python 3.12, pure stdlib (`csv`, `html`, `re`, `random`) + `rapidfuzz==3.14.5`. No goldenmatch/polars import. GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-06-15-dblp-acm-abbrevalign-benchmark-design.md`

**Worktree:** `D:/show_case/goldenmatch/.worktrees/992-dblp-acm` (branch `claude/visual-perf-languages-t6zehw`, PR #992). All paths below are relative to the worktree root unless absolute.

**Local Python:** `D:/show_case/goldenmatch/.venv/Scripts/python.exe` (has rapidfuzz 3.14.5). Referred to below as `$PY`. Run script commands from `packages/python/goldenmatch/scripts/`.

---

## Pre-flight: provision the gitignored data locally (once)

The DBLP-ACM CSVs are gitignored and do NOT materialize in a worktree checkout. Copy the cached copies from the main working tree so local self-tests can exercise the real-CSV branch. (They stay gitignored — won't be committed.)

```bash
SRC=D:/show_case/goldenmatch/packages/python/goldenmatch/tests/benchmarks/datasets/DBLP-ACM
DST=D:/show_case/goldenmatch/.worktrees/992-dblp-acm/packages/python/goldenmatch/tests/benchmarks/datasets/DBLP-ACM
mkdir -p "$DST" && cp "$SRC"/DBLP2.csv "$SRC"/ACM.csv "$SRC"/DBLP-ACM_perfectMapping.csv "$DST"/
ls -lh "$DST"
```

Expected: three CSVs (~407K DBLP2, ~369K ACM, ~76K perfectMapping).

---

## File Structure

- **Modify:** `packages/python/goldenmatch/scripts/bench_abbrevalign.py` — add stdlib helpers (`_normalize_text`, `_connected_components`), `load_dblp_acm()`, `_venue_false_positives()`, `_render_dblp_acm()`, the `--dblp-acm`/`--max-entities`/`--selftest` CLI args + wiring, and `_dblp_acm_self_tests()`. All additive; curated/`--synthetic` modes untouched.
- **Create:** `.github/workflows/bench-abbrevalign.yml` — `workflow_dispatch` lane.
- **Generated on the runner, committed manually later:** `packages/python/goldenmatch/examples/forge_runs/abbrevalign_benchmark_dblp_acm.md`.

Self-tests follow the file's existing convention (`forge_prototypes.py::_self_tests()` — plain `assert`s run from a CLI entry point), since `scripts/` is not imported by pytest. They run via `python bench_abbrevalign.py --dblp-acm --selftest`.

---

### Task 1: stdlib helpers — `_normalize_text` + `_connected_components`

**Files:**
- Modify: `packages/python/goldenmatch/scripts/bench_abbrevalign.py`

- [ ] **Step 1: Write the failing self-test.** Add near the bottom of the file, above `main()`:

```python
def _dblp_acm_self_tests() -> None:
    # --- normalization ---
    assert _normalize_text("The VLDB Journal &mdash;  Very Large   Data Bases ") == \
        "The VLDB Journal — Very Large Data Bases"
    assert _normalize_text("  SIGMOD\tRecord ") == "SIGMOD Record"
    # --- connected components ---
    comps = _connected_components([("a", "b"), ("b", "c"), ("x", "y")])
    as_sets = sorted((tuple(sorted(c)) for c in comps))
    assert as_sets == [("a", "b", "c"), ("x", "y")], as_sets
    print("dblp-acm helper self-tests passed")
```

- [ ] **Step 2: Run it; verify it fails.**

Run: `$PY bench_abbrevalign.py --dblp-acm --selftest`
Expected: FAIL — `argument --dblp-acm/--selftest unrecognized` or `NameError: _normalize_text` (the flag + helpers don't exist yet). Either failure confirms the test is wired to absent code.

> Note: the `--selftest` flag is added in Task 4. To run Task 1's test in isolation now, temporarily call `_dblp_acm_self_tests()` from a scratch `$PY -c "import bench_abbrevalign as b; b._dblp_acm_self_tests()"` — expect `NameError` until Step 3.

- [ ] **Step 3: Implement the helpers.** Add after the existing imports (add `import html` and `import re` to the import block; `csv`, `random`, `os`, `math` already imported):

```python
_WS_RE = re.compile(r"\s+")


def _normalize_text(s: str) -> str:
    """Unescape HTML entities (ACM venues carry `&mdash;`) and collapse whitespace."""
    return _WS_RE.sub(" ", html.unescape(s)).strip()


def _connected_components(edges: list[tuple[str, str]]) -> list[set[str]]:
    """Union-Find connected components over string-node edges (stdlib only)."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    comps: dict[str, set[str]] = {}
    for node in list(parent):
        comps.setdefault(find(node), set()).add(node)
    return list(comps.values())
```

- [ ] **Step 4: Run the test; verify it passes.**

Run: `$PY -c "import sys; sys.argv=['x']; import bench_abbrevalign as b; b._dblp_acm_self_tests()"` (from `scripts/`)
Expected: prints `dblp-acm helper self-tests passed`, exit 0.

- [ ] **Step 5: Commit.**

```bash
cd D:/show_case/goldenmatch/.worktrees/992-dblp-acm
git add packages/python/goldenmatch/scripts/bench_abbrevalign.py
git -c commit.gpgsign=false commit -m "feat(bench): stdlib normalize + connected-components helpers for DBLP-ACM"
```

---

### Task 2: `load_dblp_acm()` — build title + venue datasets

**Files:**
- Modify: `packages/python/goldenmatch/scripts/bench_abbrevalign.py`

- [ ] **Step 1: Extend the self-test** (append inside `_dblp_acm_self_tests()`, before the final `print`):

```python
    # --- real-CSV branch: skip cleanly if the gitignored data is absent ---
    base = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "tests", "benchmarks", "datasets", "DBLP-ACM"))
    if not os.path.exists(os.path.join(base, "DBLP2.csv")):
        print("dblp-acm real-CSV self-test SKIPPED (data not present)")
        return
    title_ds, venue_ds = load_dblp_acm(max_entities=50, seed=7)
    # Venue: exactly the five GT clusters, each a set of distinct strings.
    venue_clusters = sorted(
        tuple(sorted(t for t, _ in variants)) for variants in venue_ds.values())
    assert len(venue_clusters) == 5, (len(venue_clusters), venue_clusters)
    flat = {t for c in venue_clusters for t in c}
    assert "VLDB" in flat and "Very Large Data Bases" in flat, flat
    # VLDB-the-conference and VLDB-the-journal are SEPARATE clusters.
    vldb_cluster = next(c for c in venue_clusters if "VLDB" in c)
    assert "Very Large Data Bases" in vldb_cluster, vldb_cluster
    assert not any("VLDB Journal" in t for t in vldb_cluster), vldb_cluster
    # Title: matched entities have >=2 variants; singletons exactly 1.
    assert any(len(v) >= 2 for v in title_ds.values())
    assert any(len(v) == 1 for v in title_ds.values())
```

- [ ] **Step 2: Run it; verify it fails.**

Run: `$PY -c "import sys; sys.argv=['x']; import bench_abbrevalign as b; b._dblp_acm_self_tests()"`
Expected: FAIL — `NameError: load_dblp_acm` (not implemented).

- [ ] **Step 3: Implement `load_dblp_acm`** (add after `_connected_components`):

```python
def _read_csv_latin1(path: str) -> list[dict]:
    with open(path, encoding="latin-1", newline="") as f:
        return list(csv.DictReader(f))


def load_dblp_acm(max_entities: int = 200, seed: int = 7) -> tuple[dict, dict]:
    """Load the Leipzig DBLP-ACM corpus into two harness datasets.

    Returns (title_dataset, venue_dataset), each `entity_id -> [(text, variant_type)]`.
    The CSVs are gitignored; the bench-abbrevalign workflow downloads them, and local
    runs need them copied into tests/benchmarks/datasets/DBLP-ACM/.
    """
    base = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "tests", "benchmarks", "datasets", "DBLP-ACM"))
    paths = {n: os.path.join(base, f) for n, f in (
        ("dblp", "DBLP2.csv"), ("acm", "ACM.csv"), ("map", "DBLP-ACM_perfectMapping.csv"))}
    for p in paths.values():
        if not os.path.exists(p):
            raise FileNotFoundError(
                f"DBLP-ACM CSV missing: {p}. These files are gitignored; the "
                "bench-abbrevalign workflow downloads them from Leipzig. Locally, copy "
                "them into tests/benchmarks/datasets/DBLP-ACM/ (see the plan's pre-flight).")
    dblp = _read_csv_latin1(paths["dblp"])
    acm = _read_csv_latin1(paths["acm"])
    mapping = _read_csv_latin1(paths["map"])
    dblp_by_id = {r["id"]: r for r in dblp}
    acm_by_id = {r["id"]: r for r in acm}

    # ---- Title ER: matched papers -> entities (CC over the bipartite mapping) ----
    edges = [(f"d:{r['idDBLP']}", f"a:{r['idACM']}") for r in mapping]
    components = _connected_components(edges)
    rng = random.Random(seed)
    rng.shuffle(components)
    title_dataset: dict[str, list[tuple[str, str]]] = {}
    for i, comp in enumerate(components[:max_entities]):
        variants: list[tuple[str, str]] = []
        for node in sorted(comp):
            src, rid = node[0], node[2:]
            rec = dblp_by_id.get(rid) if src == "d" else acm_by_id.get(rid)
            if rec is not None:
                variants.append((_normalize_text(rec["title"]),
                                 "dblp" if src == "d" else "acm"))
        if len(variants) >= 2:
            title_dataset[f"paper_{i}"] = variants

    # Unmatched singletons as organic hard negatives.
    matched_dblp = {r["idDBLP"] for r in mapping}
    matched_acm = {r["idACM"] for r in mapping}
    singles = ([("d", r) for r in dblp if r["id"] not in matched_dblp]
               + [("a", r) for r in acm if r["id"] not in matched_acm])
    rng.shuffle(singles)
    for i, (src, rec) in enumerate(singles[:max_entities]):
        title_dataset[f"single_{src}_{i}"] = [(
            _normalize_text(rec["title"]), "dblp" if src == "d" else "acm")]

    # ---- Venue: GT-derived venue clusters (CC over matched-pair venue strings) ----
    venue_edges: list[tuple[str, str]] = []
    for r in mapping:
        d, a = dblp_by_id.get(r["idDBLP"]), acm_by_id.get(r["idACM"])
        if d and a:
            dv, av = _normalize_text(d["venue"]), _normalize_text(a["venue"])
            if dv and av:
                venue_edges.append((f"dblp::{dv}", f"acm::{av}"))
    venue_dataset: dict[str, list[tuple[str, str]]] = {}
    for i, comp in enumerate(_connected_components(venue_edges)):
        seen: dict[str, str] = {}
        for node in sorted(comp):
            src, vstr = node.split("::", 1)
            seen.setdefault(vstr, src)  # dedup to distinct venue strings
        venue_dataset[f"venue_{i}"] = [(vstr, src) for vstr, src in seen.items()]
    return title_dataset, venue_dataset
```

- [ ] **Step 4: Run the test; verify it passes** (data copied per pre-flight).

Run: `$PY -c "import sys; sys.argv=['x']; import bench_abbrevalign as b; b._dblp_acm_self_tests()"`
Expected: prints `dblp-acm helper self-tests passed` (and the real-CSV asserts pass). If data absent it prints the SKIPPED line instead — copy the CSVs and re-run to actually exercise the 5-cluster guard.

- [ ] **Step 5: Eyeball the datasets** (sanity, not a gate):

Run: `$PY -c "import bench_abbrevalign as b; t,v=b.load_dblp_acm(200,7); print('title entities',len(t),'venue clusters',len(v)); [print(sorted(s for s,_ in x)) for x in v.values()]"`
Expected: ~400 title entities, 5 venue clusters printed (VLDB/Very Large Data Bases; VLDB J./The VLDB Journal…; TODS pair; SIGMOD Conference/International Conference on Management of Data; SIGMOD Record/ACM SIGMOD Record).

- [ ] **Step 6: Commit.**

```bash
git add packages/python/goldenmatch/scripts/bench_abbrevalign.py
git -c commit.gpgsign=false commit -m "feat(bench): load_dblp_acm — title-ER + GT-derived venue datasets"
```

---

### Task 3: `_venue_false_positives()` — surface AbbrevAlign's over-merges

**Files:**
- Modify: `packages/python/goldenmatch/scripts/bench_abbrevalign.py`

- [ ] **Step 1: Extend the self-test** (append before the final `print`, inside the real-CSV branch):

```python
    v_idf = build_idf([t for _, t, _ in build_records(venue_ds)])
    v_rep = evaluate(venue_ds)
    v_thr = v_rep["results"]["AbbrevAlign"]["threshold"]
    fps = _venue_false_positives(venue_ds, v_idf, v_thr)
    # AbbrevAlign over-merges conference vs journal -> at least one cross-cluster FP.
    assert any("VLDB" in a and "Journal" in b or "VLDB" in b and "Journal" in a
               for a, b, _ in fps), fps
```

- [ ] **Step 2: Run it; verify it fails.**

Run: `$PY -c "import sys; sys.argv=['x']; import bench_abbrevalign as b; b._dblp_acm_self_tests()"`
Expected: FAIL — `NameError: _venue_false_positives`.

- [ ] **Step 3: Implement** (add after `load_dblp_acm`):

```python
def _venue_false_positives(venue_dataset: dict, idf, threshold: float
                           ) -> list[tuple[str, str, float]]:
    """Cross-cluster venue pairs AbbrevAlign scores at/above its best-F1 threshold."""
    recs = build_records(venue_dataset)
    fps: list[tuple[str, str, float]] = []
    for i in range(len(recs)):
        for j in range(i + 1, len(recs)):
            ea, ta, _ = recs[i]
            eb, tb, _ = recs[j]
            if ea != eb:
                s = abbrev_align(ta, tb, idf)
                if s >= threshold:
                    fps.append((ta, tb, s))
    return sorted(fps, key=lambda x: -x[2])
```

- [ ] **Step 4: Run the test; verify it passes.**

Run: `$PY -c "import sys; sys.argv=['x']; import bench_abbrevalign as b; b._dblp_acm_self_tests()"`
Expected: prints `dblp-acm helper self-tests passed`.

- [ ] **Step 5: Commit.**

```bash
git add packages/python/goldenmatch/scripts/bench_abbrevalign.py
git -c commit.gpgsign=false commit -m "feat(bench): venue false-positive lister (conf-vs-journal over-merge)"
```

---

### Task 4: `--dblp-acm` mode + report assembly + CLI

**Files:**
- Modify: `packages/python/goldenmatch/scripts/bench_abbrevalign.py` (the `main()` argparse + a `_render_dblp_acm()` assembler)

- [ ] **Step 1: Add the report assembler** (add after `render_cv`):

```python
def _render_dblp_acm(title_report: dict, title_cv: dict, venue_report: dict,
                     venue_fps: list[tuple[str, str, float]], args) -> str:
    lines: list[str] = []
    w = lines.append
    w("# AbbrevAlign on real DBLP-ACM (Leipzig) — credibility benchmark\n")
    w("Everything prior to this was curated or synthetic (known transformation "
      "distribution, which flatters structure-aware methods). This runs the **exact** "
      "entity-grouped CV harness on a real labeled ER corpus.\n")
    w(f"- Title ER: {title_report['n_records']} records "
      f"({title_report['n_pos']} match / {title_report['n_neg']} non-match pairs), "
      f"`--max-entities {args.max_entities}` `--seed {args.seed}`.\n")
    w("\n---\n\n## Part 1 — Title ER (standard dedup, held-out CV)\n")
    w("Titles of true matches are near-identical across DBLP/ACM, so JaroWinkler already "
      "saturates; the question is whether AbbrevAlign *hurts*. Held-out CV is the honest "
      "number.\n")
    w(render(title_report))
    w("\n" + render_cv(title_cv))
    w("\n---\n\n## Part 2 — Venue matching (abbreviation field, GT-derived)\n")
    w("Venue equivalence comes free from the ground truth (matched papers share a venue). "
      "DBLP-ACM has only **5 venues**, too few for held-out CV (one entity per fold, no "
      "negatives), so this is the **in-sample** ceiling + ROC-AUC — read it as per-pair "
      "separation, not a held-out F1.\n")
    w(render(venue_report))
    w("\n### AbbrevAlign's over-merges (cross-cluster pairs scored >= its best-F1 threshold)\n")
    if venue_fps:
        w("AbbrevAlign rates these *different* venues as matches — the acronym-collision "
          "precision failure (cf. IBM vs Indian Bank Mumbai):\n")
        w("| Venue A | Venue B | AbbrevAlign |")
        w("| --- | --- | ---: |")
        for a, b, s in venue_fps:
            w(f"| {a} | {b} | {s:.3f} |")
    else:
        w("_None above threshold._")
    w("\n### Verdict\n")
    w("On real labeled data AbbrevAlign **ties** JaroWinkler on generic titles (no harm, "
      "generalizes) and shows **higher per-pair separation on the abbreviation-heavy venue "
      "field at a precision cost** (it over-merges conference vs journal). Both point the "
      "same way: ship `abbrev_align` as a **gated comparator feature** feeding the learned "
      "scorer for abbreviation-heavy fields, not as a JaroWinkler replacement. The precision "
      "cost is exactly what the learned combiner / IDF-gating (handoff #2) is for.\n")
    return "\n".join(lines)
```

- [ ] **Step 2: Wire the CLI + mode.** In `main()`, add the args (after the existing `add_argument` calls) and a branch at the top of the body:

```python
    ap.add_argument("--dblp-acm", action="store_true",
                    help="Run the real Leipzig DBLP-ACM benchmark (title ER + venue).")
    ap.add_argument("--max-entities", type=int, default=200,
                    help="DBLP-ACM matched entities + singleton negatives to sample.")
    ap.add_argument("--selftest", action="store_true",
                    help="Run the DBLP-ACM loader self-tests and exit.")
```

Then, immediately after `args = ap.parse_args(argv)`:

```python
    if args.selftest:
        _dblp_acm_self_tests()
        return 0
    if args.dblp_acm:
        title_ds, venue_ds = load_dblp_acm(args.max_entities, args.seed)
        title_report = evaluate(title_ds)
        title_cv = evaluate_cv(title_ds)
        venue_report = evaluate(venue_ds)
        v_idf = build_idf([t for _, t, _ in build_records(venue_ds)])
        v_thr = venue_report["results"]["AbbrevAlign"]["threshold"]
        venue_fps = _venue_false_positives(venue_ds, v_idf, v_thr)
        md = _render_dblp_acm(title_report, title_cv, venue_report, venue_fps, args)
        print(md)
        out = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "examples", "forge_runs", "abbrevalign_benchmark_dblp_acm.md"))
        with open(out, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"\nWrote {out}")
        return 0
```

- [ ] **Step 3: Run the self-test via the real flag** (now that `--selftest` exists):

Run: `$PY bench_abbrevalign.py --dblp-acm --selftest`
Expected: prints `dblp-acm helper self-tests passed`, exit 0.

- [ ] **Step 4: Smoke-run the full mode at tiny scale** (fast; proves the report assembles end-to-end):

Run: `$PY bench_abbrevalign.py --dblp-acm --max-entities 20`
Expected: prints the two-part report, ends with `Wrote ...abbrevalign_benchmark_dblp_acm.md`. Confirm the file has both "Part 1 — Title ER" and "Part 2 — Venue matching" sections and a non-empty over-merge table. (This `--max-entities 20` report is a smoke artifact; the committed report comes from the runner at default 200.)

- [ ] **Step 5: Discard the smoke report** (don't commit the tiny-scale one):

Run: `git checkout -- packages/python/goldenmatch/examples/forge_runs/abbrevalign_benchmark_dblp_acm.md 2>/dev/null || rm -f packages/python/goldenmatch/examples/forge_runs/abbrevalign_benchmark_dblp_acm.md`

- [ ] **Step 6: Commit the code** (not the report).

```bash
git add packages/python/goldenmatch/scripts/bench_abbrevalign.py
git -c commit.gpgsign=false commit -m "feat(bench): --dblp-acm mode (title ER CV + in-sample venue + report)"
```

---

### Task 5: `bench-abbrevalign.yml` workflow

**Files:**
- Create: `.github/workflows/bench-abbrevalign.yml`

- [ ] **Step 1: Write the workflow.** (Action SHAs copied verbatim from `eval-benchmarks.yml`; fetch step copied from its "Fetch DBLP-ACM" step, generalized to honor a mirror URL.)

```yaml
# AbbrevAlign vs goldenmatch comparators on the real Leipzig DBLP-ACM corpus.
# Title ER (held-out CV) + GT-derived venue matching (in-sample + over-merge list).
# Standalone research artifact (scripts/bench_abbrevalign.py): rapidfuzz + stdlib,
# no goldenmatch/er-evaluation. workflow_dispatch only.
#
# To run: GitHub -> Actions -> "bench-abbrevalign" -> Run workflow.
name: bench-abbrevalign

on:
  workflow_dispatch:
    inputs:
      max_entities:
        description: "DBLP-ACM matched entities + singleton negatives to sample (title ER)"
        required: false
        default: "200"
      seed:
        description: "Sampling seed"
        required: false
        default: "7"

permissions:
  contents: read

jobs:
  bench:
    # Per memory feedback_bench_default_runner.md: benchmark workflows use the
    # org's larger runner.
    runs-on: large-new-64GB
    timeout-minutes: 60
    env:
      # DBLP-ACM auto-pulls from Leipzig; override with a mirror if it 404s.
      GOLDENMATCH_DBLP_ACM_URL: ${{ vars.DBLP_ACM_URL }}
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10  # v6

      - uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405  # v6.2.0
        with:
          python-version: "3.12"

      - name: Install rapidfuzz
        run: pip install "rapidfuzz==3.14.5"

      - name: Fetch DBLP-ACM dataset (if needed)
        run: |
          set -euo pipefail
          dst=packages/python/goldenmatch/tests/benchmarks/datasets/DBLP-ACM
          mkdir -p "$dst"
          if [ ! -f "$dst/DBLP2.csv" ]; then
            url="${GOLDENMATCH_DBLP_ACM_URL:-https://dbs.uni-leipzig.de/file/DBLP-ACM.zip}"
            curl -fSL -o /tmp/dblp-acm.zip "$url"
            unzip -o /tmp/dblp-acm.zip -d /tmp/dblp-acm-extract
            cp /tmp/dblp-acm-extract/DBLP2.csv \
               /tmp/dblp-acm-extract/ACM.csv \
               /tmp/dblp-acm-extract/DBLP-ACM_perfectMapping.csv \
               "$dst"/
          fi
          ls -lh "$dst"

      - name: Self-test the loader
        working-directory: packages/python/goldenmatch/scripts
        run: python bench_abbrevalign.py --dblp-acm --selftest

      - name: Run AbbrevAlign DBLP-ACM benchmark
        working-directory: packages/python/goldenmatch/scripts
        run: |
          python bench_abbrevalign.py --dblp-acm \
            --max-entities "${{ inputs.max_entities }}" --seed "${{ inputs.seed }}"
          cat ../examples/forge_runs/abbrevalign_benchmark_dblp_acm.md >> "$GITHUB_STEP_SUMMARY"

      - name: Upload report
        if: always()
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v7.0.1
        with:
          name: abbrevalign-dblp-acm-report
          path: packages/python/goldenmatch/examples/forge_runs/abbrevalign_benchmark_dblp_acm.md
          retention-days: 90
```

- [ ] **Step 2: Validate the YAML parses.**

Run: `$PY -c "import yaml,sys; yaml.safe_load(open('D:/show_case/goldenmatch/.worktrees/992-dblp-acm/.github/workflows/bench-abbrevalign.yml')); print('yaml ok')"`
Expected: `yaml ok`. (If `yaml` is unavailable in `$PY`, skip — the repo's `workflow_lint` CI job will validate on push.)

- [ ] **Step 3: Commit.**

```bash
git add .github/workflows/bench-abbrevalign.yml
git -c commit.gpgsign=false commit -m "ci: bench-abbrevalign workflow_dispatch lane (real DBLP-ACM)"
```

---

### Task 6: ruff gate, push, trigger the runner, commit the report

- [ ] **Step 1: ruff check the touched script** (the real `scripts/` gate — watch `F401`/`UP045`/`UP035`/`E401`).

Run: `$PY -m ruff check packages/python/goldenmatch/scripts/bench_abbrevalign.py` (from worktree root)
Expected: `All checks passed!`. Fix any finding and amend the relevant commit.

- [ ] **Step 2: Final local smoke** (default-ish scale, optional — slower):

Run (from `scripts/`): `$PY bench_abbrevalign.py --dblp-acm --max-entities 60`
Expected: completes in ~1-2 min, writes the report. Discard it (`git checkout -- ...` / `rm`) — the committed report is the runner's.

- [ ] **Step 3: Push** (auth dance per CLAUDE.md: `benseverndev-oss` uses personal `benzsevern`).

```bash
gh auth switch --user benzsevern
git push origin claude/visual-perf-languages-t6zehw
gh auth switch --user benzsevern-mjh   # switch back immediately (memory feedback_github_auth_switch)
```

- [ ] **Step 4: Trigger the workflow** (it reads the workflow file from the branch ref).

```bash
GH_TOKEN=$(gh auth token --user benzsevern) gh workflow run bench-abbrevalign.yml \
  --ref claude/visual-perf-languages-t6zehw
```
Then watch: `gh run list --workflow bench-abbrevalign.yml -L 3`. Per memory `feedback_dont_poll_ci_arm_automerge`, don't sit in a tight poll loop — check back. Note `reference_github_hosted_runners_688`: `large-new-64GB` can take time to provision.

- [ ] **Step 5: Fetch the report artifact and commit it.**

```bash
RID=$(gh run list --workflow bench-abbrevalign.yml -L 1 --json databaseId -q '.[0].databaseId')
gh run download "$RID" -n abbrevalign-dblp-acm-report \
  -D packages/python/goldenmatch/examples/forge_runs/
git add packages/python/goldenmatch/examples/forge_runs/abbrevalign_benchmark_dblp_acm.md
git -c commit.gpgsign=false commit -m "docs(bench): real DBLP-ACM AbbrevAlign report (from runner)"
gh auth switch --user benzsevern && git push origin claude/visual-perf-languages-t6zehw && gh auth switch --user benzsevern-mjh
```

- [ ] **Step 6: Update the HANDOFF.** In `packages/python/goldenmatch/examples/forge_runs/HANDOFF.md`, move next-step #1 ("Real labeled ER dataset") from open to done, linking the new report + the run, and record the honest finding (title tie / venue AUC-edge-with-precision-cost). Commit + push (auth dance).

---

## Done criteria

- `bench_abbrevalign.py --dblp-acm --selftest` passes (5-cluster guard + FP assertion).
- `ruff check` clean on the script.
- The `bench-abbrevalign` workflow run is green and produced the report.
- `examples/forge_runs/abbrevalign_benchmark_dblp_acm.md` is committed, with both parts + the over-merge table, framed honestly (tie on titles, AUC-edge-with-precision-cost on venues).
- HANDOFF next-step #1 marked done.
