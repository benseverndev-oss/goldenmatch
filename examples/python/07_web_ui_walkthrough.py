"""07_web_ui_walkthrough.py -- exercise every workbench endpoint from Python.

The web UI ships as the optional ``goldenmatch[web]`` extra. This script does
NOT replace the browser experience -- it walks through the same API surface
the React frontend talks to, so you can:

  - script bulk operations (e.g. compare 50 historical runs, sweep
    sensitivity nightly, learn from accumulated corrections weekly);
  - integrate the workbench into your own tooling (Slack bot, Airflow DAG,
    notebook); or
  - smoke-test a deployment.

Setup (terminal 1):

    pip install 'goldenmatch[web]'
    goldenmatch serve-ui packages/python/goldenmatch/web/demo --no-open --port 5050

Then run this script in terminal 2:

    python examples/python/07_web_ui_walkthrough.py

It uses ``requests`` (stdlib ``urllib`` would also work). Every endpoint is
typed and validated server-side with Pydantic -- 422 errors carry the exact
field path so you can localize problems quickly.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    sys.stderr.write("This example requires `requests`. Install with: pip install requests\n")
    sys.exit(1)


BASE = "http://127.0.0.1:5050"


def _h(title: str) -> None:
    """Print a section header so the walkthrough is easy to follow."""
    print(f"\n{'=' * 72}\n  {title}\n{'=' * 72}")


def _show(obj: object, max_lines: int = 30) -> None:
    """Pretty-print a response, truncated to keep output readable."""
    text = json.dumps(obj, indent=2, default=str)
    lines = text.splitlines()
    if len(lines) > max_lines:
        text = "\n".join(lines[:max_lines]) + f"\n  ...({len(lines) - max_lines} more lines)"
    print(text)


def _check_server() -> None:
    try:
        resp = requests.get(f"{BASE}/api/v1/healthz", timeout=2)
        resp.raise_for_status()
    except Exception as exc:
        sys.stderr.write(
            f"Couldn't reach {BASE}: {exc}\n"
            "Start the workbench in another terminal:\n"
            "  goldenmatch serve-ui <project_dir> --no-open --port 5050\n",
        )
        sys.exit(1)


def project_overview() -> dict:
    """GET /project -- runs, current rules, project root."""
    _h("Project -- runs and current rules")
    resp = requests.get(f"{BASE}/api/v1/project").json()
    runs = resp["runs"]
    rules = resp["rules"]
    print(f"  project root  : {resp['project_root']}")
    print(f"  current rules : {len(rules['matchkeys'])} matchkey(s), threshold {rules['threshold']}")
    print(f"  saved runs    : {len(runs)}")
    for r in runs[:5]:
        print(f"    * {r['run_name']}  rows={r['row_count']}  clusters={r['cluster_count']}  pairs={r['total_pairs']}")
    return resp


def quality_findings() -> None:
    """GET /quality -- GoldenCheck scan-only output."""
    _h("Quality findings (GoldenCheck) -- scan-only, no fixes applied")
    resp = requests.get(f"{BASE}/api/v1/quality").json()
    if not resp["available"]:
        print("  GoldenCheck not installed -- install with `pip install goldencheck`.")
        return
    if "error" in resp:
        print(f"  Scan reported a soft failure: {resp['error']}")
        return
    print(f"  total findings: {resp['summary']['total']} ({resp['summary']['errors']} errors, {resp['summary']['warnings']} warnings)")
    for finding in resp["issues"][:5]:
        print(f"    * [{finding.get('severity', '?')}] {finding.get('column', '?')}: {finding.get('message', '')}")


def domains() -> None:
    """GET /domains -- built-in domain rulebooks."""
    _h("Domain packs -- pre-built rulebooks the workbench can pin")
    resp = requests.get(f"{BASE}/api/v1/domains").json()
    for pack in resp:
        print(f"  * {pack['name']:14s}  signals={pack['signal_count']:2d}  brands={pack['brand_count']:3d}  ids={pack['identifier_count']}")


def autoconfig_with_domain(domain: str = "people") -> dict:
    """POST /autoconfig?domain= -- let the engine profile data and propose rules."""
    _h(f"Auto-configure with domain={domain!r}")
    resp = requests.post(f"{BASE}/api/v1/autoconfig", params={"domain": domain}).json()
    print(f"  threshold: {resp['threshold']}")
    print(f"  matchkeys: {len(resp['matchkeys'])}")
    for mk in resp["matchkeys"]:
        print(f"    * {mk['column']:12s}  scorer={mk['scorer']}  weight={mk['weight']}  transforms={mk['transforms']}")
    return resp


def edit_rules(rules: dict) -> dict:
    """PUT /rules -- update the in-memory rules with standardization + blocking."""
    _h("Edit rules -- add standardization + multi_pass blocking")
    rules = {
        **rules,
        "standardization": {"name": ["name_proper", "strip"]},
        "blocking": {
            "strategy": "multi_pass",
            "keys": [{"fields": ["email"], "transforms": ["lowercase", "strip"]}],
            "passes": [
                {"fields": ["email"], "transforms": ["lowercase", "strip"]},
                {"fields": ["name"], "transforms": ["soundex"]},
            ],
        },
    }
    resp = requests.put(f"{BASE}/api/v1/rules", json=rules).json()
    print("  Saved rules now include:")
    print(f"    * standardization: {resp.get('standardization')}")
    print(f"    * blocking strategy: {resp.get('blocking', {}).get('strategy')}")
    return resp


def run_preview(rules: dict) -> str | None:
    """POST /preview -- sampled in-memory run, registered under a synthetic name."""
    _h("Preview -- sampled in-memory run")
    resp = requests.post(
        f"{BASE}/api/v1/preview",
        json={"rules": rules, "sample": {"n": 200, "seed": 42}},
    )
    if resp.status_code == 200:
        run_name = resp.json()["run_name"]
        print(f"  preview run name: {run_name}")
        return run_name
    print(f"  preview rejected: {resp.status_code} {resp.text}")
    return None


def inspect_cluster(run_name: str, cluster_id: int = 1) -> None:
    """GET /runs/{name}/clusters/{id} -- pair drilldown with NL prose."""
    _h(f"Cluster {cluster_id} drilldown -- pairs carry one-line prose")
    resp = requests.get(f"{BASE}/api/v1/runs/{run_name}/clusters/{cluster_id}")
    if resp.status_code != 200:
        print(f"  cluster {cluster_id} not in {run_name}")
        return
    body = resp.json()
    print(f"  cluster {cluster_id}: {len(body['row_ids'])} members, {len(body['pairs'])} pairs")
    for pair in body["pairs"][:3]:
        print(f"    * #{pair['row_id_a']} -> #{pair['row_id_b']}  score={pair['score']:.3f}")
        if pair.get("prose"):
            print(f"      {pair['prose']}")


def label_a_pair(pair_a: int = 0, pair_b: int = 1) -> None:
    """POST /labels -- write a steward decision; mirror to MemoryStore."""
    _h(f"Label pair ({pair_a}, {pair_b}) as match -- auto-mirrors to Learning Memory")
    resp = requests.post(
        f"{BASE}/api/v1/labels",
        json={"row_id_a": pair_a, "row_id_b": pair_b, "label": "match", "note": "walkthrough"},
    ).json()
    print(f"  saved: pair=({resp['row_id_a']}, {resp['row_id_b']}) label={resp['label']}")
    print(f"  mirrored to MemoryStore: {resp.get('mirrored', '?')}")
    if resp.get("mirror_error"):
        print(f"  -> mirror error: {resp['mirror_error']}")


def evaluation(run_name: str) -> None:
    """GET /runs/{name}/evaluation -- F1/precision/recall vs labels."""
    _h(f"Evaluation vs steward labels -- run {run_name}")
    resp = requests.get(f"{BASE}/api/v1/runs/{run_name}/evaluation").json()
    s = resp["summary"]
    print(f"  precision={s['precision']:.3f}  recall={s['recall']:.3f}  f1={s['f1']:.3f}")
    print(f"  tp={s['tp']}  confirmed_fp={s['confirmed_fp']}  unlabeled_fp={s['unlabeled_fp']}  fn={s['fn']}")
    if s["label_counts"]["total"] == 0:
        print("  (no labels yet -- F1 will be more useful once you've labeled some pairs)")


def compare_two_runs(runs: list[dict]) -> None:
    """POST /compare -- CCMS classification of run B vs run A."""
    if len(runs) < 2:
        print("\n[skip] compare needs >=2 runs in the project")
        return
    _h(f"Compare {runs[0]['run_name']} (A) vs {runs[1]['run_name']} (B) -- CCMS")
    resp = requests.post(
        f"{BASE}/api/v1/compare",
        json={"run_a": runs[0]["run_name"], "run_b": runs[1]["run_name"]},
    ).json()
    s = resp["summary"]
    print(f"  TWI: {s['twi']:.4f}   (1.0 = identical)")
    print(f"  unchanged={s['unchanged']}  merged={s['merged']}  partitioned={s['partitioned']}  overlapping={s['overlapping']}")
    print(f"  cc1={s['cc1']} -> cc2={s['cc2']}  singletons={s['sc1']} -> {s['sc2']}")


def sensitivity_sweep() -> None:
    """POST /sensitivity -- sweep one parameter, CCMS-compare each point."""
    _h("Sensitivity -- sweep `threshold` from 0.7 to 0.95 step 0.05 on n=300")
    resp = requests.post(
        f"{BASE}/api/v1/sensitivity",
        json={
            "field": "threshold",
            "start": 0.7,
            "stop": 0.95,
            "step": 0.05,
            "sample_n": 300,
        },
    )
    if resp.status_code != 200:
        print(f"  sweep rejected: {resp.status_code} {resp.text}")
        return
    body = resp.json()
    stab = body["stability"]
    print(f"  baseline value      : {body['baseline_value']}")
    print(f"  most-stable value   : {stab['best_value']} ({stab['best_unchanged_pct'] * 100:.1f}% unchanged)")
    print(f"  per-point shape (clusters_b * TWI * unchanged):")
    for p in body["points"]:
        print(f"    {p['value']:.3f}  ->  cc={p['cluster_count_b']:3d}  twi={p['twi']:.3f}  unchanged={p['unchanged']}")


def match_run() -> None:
    """POST /match -- target x reference one-to-many.

    Skipped if there's no reference.csv next to data.csv. The bundled demo
    project ships one -- drop your own next to data.csv to use this against
    your project.
    """
    _h("Match -- target x reference one-to-many")
    resp = requests.post(
        f"{BASE}/api/v1/match",
        json={"reference_path": "reference.csv", "target_path": "data.csv"},
    )
    if resp.status_code != 200:
        print(f"  match skipped: {resp.status_code} {resp.json().get('detail', resp.text)}")
        print("  Drop a reference.csv into your project root to enable this endpoint.")
        return
    body = resp.json()
    s = body["stats"]
    print(f"  targets matched : {s['matched_targets']} / {s['target_total']} ({s['match_rate'] * 100:.1f}%)")
    print(f"  reference total : {s['reference_total']}")
    print(f"  matched pairs   : {s['matched_pairs']}")
    for row in body["matched"][:3]:
        print(
            f"    * #{row['__target_row_id__']} -> #{row['__ref_row_id__']}  "
            f"score={row['__match_score__']:.3f}",
        )


def memory_stats() -> None:
    """GET /memory/stats + /memory/corrections -- Learning Memory state."""
    _h("Learning Memory -- corrections + stats")
    stats = requests.get(f"{BASE}/api/v1/memory/stats").json()
    print(f"  corrections   : {stats['count']}")
    print(f"  last learn    : {stats['last_learn_time'] or 'never'}")
    print(f"  adjustments   : {len(stats['adjustments'])}")

    corrections = requests.get(f"{BASE}/api/v1/memory/corrections", params={"limit": 5}).json()
    if corrections["items"]:
        print("  most-recent corrections:")
        for c in corrections["items"]:
            print(
                f"    * ({c['id_a']}, {c['id_b']})  decision={c['decision']:6s}  "
                f"source={c['source']:8s}  trust={c['trust']:.2f}",
            )


def trigger_learn() -> None:
    """POST /memory/learn -- run a learning pass over accumulated corrections."""
    _h("Trigger learn pass")
    resp = requests.post(f"{BASE}/api/v1/memory/learn").json()
    if not resp["adjustments"]:
        print("  no adjustments produced (need >=10 corrections for threshold tuning, >=50 for weights)")
        return
    print(f"  produced {len(resp['adjustments'])} adjustment(s):")
    for adj in resp["adjustments"]:
        print(f"    * matchkey={adj.get('matchkey_name')}  threshold={adj.get('threshold')}  evidence={adj.get('evidence_count')}")


def main() -> None:
    _check_server()

    project = project_overview()
    quality_findings()
    domains()

    rules = autoconfig_with_domain("people")
    edited = edit_rules(rules)

    preview_run = run_preview(edited)
    inspect_cluster(preview_run or project["runs"][0]["run_name"])

    if project["runs"]:
        label_a_pair()
        evaluation(project["runs"][0]["run_name"])
        compare_two_runs(project["runs"])

    sensitivity_sweep()
    match_run()
    memory_stats()
    trigger_learn()

    print("\nDone. The same shapes power the React frontend -- open http://127.0.0.1:5050 to see them rendered.")


if __name__ == "__main__":
    main()
