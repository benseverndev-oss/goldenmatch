#!/usr/bin/env python3
"""Score the codebase against the governing architecture frame.

Frame: context-network/architecture/one-product-two-engines.md (decision 0047) --
"one product, two engines, many surfaces." Five decision tests (tenets):

  T1  One authoritative semantic owner per capability (no second source of truth).
  T2  Conformance defines correctness (every divergence has a level + a test).
  T3  Kernelize on measurement (deferrals classified; no silent regress to fallback).
  T4  Compute vs control stay architecturally distinct (explicit, versioned seam).
  T5  Arrow at bulk boundaries, not the universal calling convention.

This tool has two halves:

  LIVE HARVEST (deterministic, no toolchain -- box-safe static parse):
    * per-package surface-gap counts from parity/*.yaml (python_only / ts_only) --
      capabilities stranded on one surface;
    * known-divergent kernels forced to fallback (_FALLBACK_ONLY in each
      per-package _native_loader.py);
    * scorer/blocking coverage: kernel-backed vs deferred vs UNCOVERED.

  CURATED INVENTORY (parity/thesis_conformance.yaml):
    * the ranked, evidence-linked weakness list mapped to the five tenets, with
      severity and `declared` (false = a real divergence with no conformance level
      or contract -- the dangerous class).

Modes:
  (default)   Render the scorecard: live harvest + curated inventory by tenet/severity.
  --check     Exit non-zero on a DRIFT invariant (advisory unless --strict):
                - a scorer is UNCOVERED (not kernel-backed, not deferred);
                - a package grew a NEW _FALLBACK_ONLY kernel with no inventory entry;
                - a curated weakness marked declared-divergent that names a
                  parity_test file that does not exist on disk.
  --strict    Make --check gating (exit 1 on any drift). Default is advisory (exit 0).
  --json      Emit the scorecard as JSON instead of text.

Deliberately NOT wired into ci-required: this is a visibility/roadmap instrument,
like the advisory SQL surface. Wire it (add --strict to a lane) only when the team
decides thesis-drift should block a PR.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("PyYAML required (same dep as check_api_parity.py)", file=sys.stderr)
    sys.exit(2)

REPO = Path(__file__).resolve().parent.parent
PARITY = REPO / "parity"
INVENTORY = PARITY / "thesis_conformance.yaml"
PY_PKGS = REPO / "packages" / "python"

SURFACES = ("mcp_tools", "cli_commands", "a2a_skills", "scorers",
            "transforms", "blocking_strategies", "scorer_kernels")
SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
# _FALLBACK_ONLY kernels known + explained in the curated inventory / CLAUDE.md.
# A NEW one appearing here that is not in this set (and not covered by an inventory
# entry) is flagged by --check as an undocumented silent divergence.
KNOWN_FALLBACK_ONLY = {"sail_scoring", "phone_validate"}


def _load_yaml(path: Path):
    return yaml.safe_load(path.read_text()) if path.exists() else None


def harvest_surface_gaps() -> dict:
    """python_only / ts_only counts per package per surface (the surface-gap map)."""
    out = {}
    for f in sorted(PARITY.glob("*.yaml")):
        if f.name == INVENTORY.name:
            continue
        d = _load_yaml(f) or {}
        pkg = d.get("package", f.stem)
        gaps = {}
        for s in SURFACES:
            block = d.get(s)
            if not isinstance(block, dict):
                continue
            po = block.get("python_only") or []
            to = block.get("ts_only") or []
            if po or to:
                gaps[s] = {"python_only": list(po), "ts_only": list(to)}
        if gaps:
            out[pkg] = gaps
    return out


def harvest_scorer_coverage() -> dict:
    """goldenmatch scorers: total vs kernel-backed vs deferred vs UNCOVERED."""
    d = _load_yaml(PARITY / "goldenmatch.yaml") or {}
    scorers = d.get("scorers", {}) or {}
    all_scorers = set(scorers.get("shared", []) or []) \
        | set(scorers.get("python_only", []) or []) \
        | set(scorers.get("ts_only", []) or [])
    kernels = d.get("scorer_kernels", {}) or {}
    backed = set(kernels.get("shared", []) or []) \
        | set(kernels.get("python_only", []) or []) \
        | set(kernels.get("ts_only", []) or [])
    deferred = set((d.get("scorer_kernels_deferred") or {}).keys())
    uncovered = sorted(all_scorers - backed - deferred)
    return {
        "total": len(all_scorers),
        "kernel_backed": len(all_scorers & backed),
        "deferred": {k: v for k, v in (d.get("scorer_kernels_deferred") or {}).items()},
        "uncovered": uncovered,  # MUST be empty (coverage floor)
    }


def _eval_collection(node: ast.AST):
    """literal_eval a set/list literal, or a set()/frozenset({...}) call wrapping one.

    ast (not regex) so comments mentioning _FALLBACK_ONLY can't produce false hits.
    """
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("frozenset", "set"):
        if not node.args:
            return []
        node = node.args[0]
    try:
        return sorted(ast.literal_eval(node))
    except Exception:
        return []


def harvest_fallback_only() -> dict:
    """Known-divergent kernels forced to pure-language fallback, per package.

    _FALLBACK_ONLY marks kernels whose native symbol exists but diverges from the
    reference (f32-vs-f64, wrong-spec), so `auto` deliberately runs the fallback.
    """
    out = {}
    loaders = sorted(PY_PKGS.glob("*/*/core/_native_loader.py")) \
        + sorted(PY_PKGS.glob("*/*/_native_loader.py"))
    for loader in loaders:
        pkg = loader.parts[len(PY_PKGS.parts)]
        try:
            tree = ast.parse(loader.read_text())
        except SyntaxError:
            continue
        for stmt in tree.body:
            targets = []
            if isinstance(stmt, ast.Assign):
                targets = stmt.targets
            elif isinstance(stmt, ast.AnnAssign) and stmt.target is not None:
                targets = [stmt.target]
            if any(isinstance(t, ast.Name) and t.id == "_FALLBACK_ONLY" for t in targets):
                names = _eval_collection(stmt.value)
                if names:
                    out[pkg] = names
                break
    return out


def load_inventory() -> dict:
    inv = _load_yaml(INVENTORY)
    if not inv:
        print(f"missing curated inventory: {INVENTORY}", file=sys.stderr)
        sys.exit(2)
    return inv


def build_scorecard() -> dict:
    inv = load_inventory()
    all_w = inv.get("weaknesses", [])

    def _key(w):
        return (SEV_ORDER.get(w.get("severity"), 9), w.get("id", ""))

    # `status: resolved` archives an entry OFF the live risk board (conformance v2,
    # 0047 amendment #4): the record stays, but only OPEN entries are the risk
    # surface the gate checks + the scorecard ranks.
    weaknesses = sorted((w for w in all_w if w.get("status") != "resolved"), key=_key)
    resolved = sorted((w for w in all_w if w.get("status") == "resolved"), key=_key)
    return {
        "tenets": inv.get("tenets", {}),
        "weaknesses": weaknesses,
        "resolved": resolved,
        "live": {
            "surface_gaps": harvest_surface_gaps(),
            "scorer_coverage": harvest_scorer_coverage(),
            "fallback_only_kernels": harvest_fallback_only(),
        },
    }


def run_check(card: dict, strict: bool) -> int:
    """Drift invariants. Advisory (exit 0) unless --strict."""
    problems = []
    cov = card["live"]["scorer_coverage"]
    if cov["uncovered"]:
        problems.append(f"UNCOVERED scorers (not kernel-backed, not deferred): {cov['uncovered']}")

    seen_fallback = set()
    for names in card["live"]["fallback_only_kernels"].values():
        seen_fallback.update(names)
    novel = seen_fallback - KNOWN_FALLBACK_ONLY
    inv_ids = " ".join(w.get("id", "") + " " + w.get("title", "") for w in card["weaknesses"])
    for k in sorted(novel):
        if k not in inv_ids:
            problems.append(
                f"NEW _FALLBACK_ONLY kernel '{k}' with no inventory entry -- an "
                f"undocumented silent divergence; add it to KNOWN_FALLBACK_ONLY + "
                f"parity/thesis_conformance.yaml with a reason.")

    for w in card["weaknesses"]:
        pt = w.get("parity_test", "__unset__")
        if pt not in ("__unset__", None) and not (REPO / pt).exists():
            problems.append(f"weakness '{w['id']}' names parity_test '{pt}' that does not exist")

    # Conformance v2 (0047 amendment) -- T1 default-routing: a shared owner that the
    # DEFAULT caller path does NOT route to is a LATENT second source (opt-in while a
    # second impl is the default). `default_routed: true` = owner is the default;
    # `opt-in` = deliberate fallback-default (edge-bundle discipline); `false` = flag.
    for w in card["weaknesses"]:
        if w.get("default_routed") is False:
            problems.append(
                f"LATENT second source: '{w['id']}' has a shared owner but the default "
                f"path does not route to it (default_routed: false) -- conformance v2 T1")

    if problems:
        print("THESIS-CONFORMANCE DRIFT:")
        for p in problems:
            print(f"  - {p}")
        return 1 if strict else 0
    print("thesis-conformance drift check: OK")
    return 0


def render(card: dict) -> None:
    tenets = card["tenets"]
    ws = card["weaknesses"]
    counts = {}
    for w in ws:
        counts[w["severity"]] = counts.get(w["severity"], 0) + 1
    undeclared = [w for w in ws if not w.get("declared", True)]

    print("=" * 78)
    print("THESIS CONFORMANCE SCORECARD -- one product, two engines, many surfaces")
    print("  frame: context-network/architecture/one-product-two-engines.md (0047)")
    print("=" * 78)
    print()
    print("Open weaknesses by severity: " + (", ".join(
        f"{n} {s}" for s, n in sorted(counts.items(), key=lambda kv: SEV_ORDER[kv[0]])) or "none"))
    print(f"Resolved (archived off the live board): {len(card.get('resolved', []))}")
    print(f"Undeclared/under-declared (divergence with no conformance level): {len(undeclared)}"
          + (" -> " + ", ".join(w["id"] for w in undeclared) if undeclared else ""))
    print()

    by_tenet = {t: [] for t in tenets}
    for w in ws:
        by_tenet.setdefault(w["tenet"], []).append(w)
    for t, desc in tenets.items():
        items = by_tenet.get(t, [])
        print(f"[{t}] {desc}")
        if not items:
            print("     (no recorded weaknesses)")
        for w in items:
            flag = "" if w.get("declared", True) else "  !!UNDECLARED"
            print(f"     - ({w['severity']}) {w['title']}{flag}")
        print()

    routed = [w for w in ws if "default_routed" in w]
    revalidate = [w for w in ws if w.get("un_defer")]
    if routed or revalidate:
        print("-" * 78)
        print("CONFORMANCE v2 (0047 amendment: behavioral + default-routing + re-validation)")
        print("-" * 78)
        if routed:
            print("Default-routing (T1: does the DEFAULT caller path route to the owner?)")
            for w in routed:
                dr = w["default_routed"]
                tag = {True: "DEFAULT", "opt-in": "opt-in (classified fallback is default)"}.get(
                    dr, str(dr))
                mark = "  !!LATENT-2ND-SOURCE" if dr is False else ""
                print(f"    [{tag}] {w['id']}{mark}")
        if revalidate:
            print("Re-validate (deferral premises -- re-check the un-defer trigger):")
            for w in revalidate:
                first = " ".join(str(w["un_defer"]).split())
                print(f"    - {w['id']}: {first[:110]}{'...' if len(first) > 110 else ''}")
        print()

    print("-" * 78)
    print("LIVE HARVEST")
    print("-" * 78)
    cov = card["live"]["scorer_coverage"]
    status = "OK" if not cov["uncovered"] else f"FAIL uncovered={cov['uncovered']}"
    print(f"goldenmatch scorers: {cov['total']} total, {cov['kernel_backed']} kernel-backed, "
          f"{len(cov['deferred'])} deferred  [coverage floor: {status}]")
    fo = card["live"]["fallback_only_kernels"]
    if fo:
        print("known-divergent kernels forced to fallback (_FALLBACK_ONLY):")
        for pkg, names in fo.items():
            print(f"    {pkg}: {', '.join(names)}")
    print("surface gaps (capabilities stranded on one runtime):")
    for pkg, gaps in card["live"]["surface_gaps"].items():
        parts = []
        for s, g in gaps.items():
            parts.append(f"{s}(+{len(g['python_only'])}py/+{len(g['ts_only'])}ts)")
        print(f"    {pkg}: {', '.join(parts)}")
    print()
    print("Full detail + evidence: parity/thesis_conformance.yaml")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="run drift invariants")
    ap.add_argument("--strict", action="store_true", help="make --check gating (exit 1 on drift)")
    ap.add_argument("--json", action="store_true", help="emit scorecard as JSON")
    args = ap.parse_args()

    card = build_scorecard()
    if args.json:
        print(json.dumps(card, indent=2, default=list))
        return 0
    if args.check:
        return run_check(card, strict=args.strict)
    render(card)
    return 0


if __name__ == "__main__":
    sys.exit(main())
