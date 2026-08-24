"""Generate (or check) docs-site/thesis-weaknesses.mdx — the repo-thesis weakness
board, rendered straight from the thesis-conformance scorecard.

The sibling suite-matrix page renders cross-language *surface* parity; this one
renders the codebase's standing against the governing architecture frame ("one
product, two engines, many surfaces" — context-network/architecture/
one-product-two-engines.md, decision 0047). It reuses the SAME scorecard the CI gate
builds (scripts/check_thesis_conformance.py::build_scorecard) — the curated weakness
inventory (parity/thesis_conformance.yaml) plus the live static harvest (surface-gap
counts, scorer-kernel coverage, `_FALLBACK_ONLY` kernels) — so the page cannot drift
from the gate. Mirrors gen_suite_matrix.py: the whole file is generated between
markers and drift-gated in CI.

  python scripts/gen_thesis_weaknesses.py --write    # regenerate the page
  python scripts/gen_thesis_weaknesses.py --check     # CI drift gate
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_thesis_conformance as t  # single source: the SAME scorecard the gate builds

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs-site" / "thesis-weaknesses.mdx"

MARKER_START = (
    "{/* thesis-weaknesses:generated:start -- DO NOT EDIT. "
    "Regenerate: python scripts/gen_thesis_weaknesses.py --write */}"
)
MARKER_END = "{/* thesis-weaknesses:generated:end */}"

_SEV_BADGE = {"critical": "🔴 critical", "high": "🟠 high", "medium": "🟡 medium", "low": "🟢 low"}


def _cell(text: object) -> str:
    """Collapse whitespace + escape pipes so a value is safe in one markdown cell."""
    s = re.sub(r"\s+", " ", str(text)).strip()
    return s.replace("|", "\\|")


def _sev_badge(sev: str) -> str:
    return _SEV_BADGE.get(sev, sev)


def _declared(w: dict) -> str:
    # declared:false is the DANGEROUS class — a real divergence with no conformance
    # level / contract covering it. Make it loud.
    return "yes" if w.get("declared", True) else "⚠ **NO**"


def _routed(w: dict) -> str:
    r = w.get("default_routed", "__unset__")
    if r is True:
        return "owner is default"
    if r == "opt-in":
        return "opt-in (fallback default)"
    if r is False:
        return "⚠ latent 2nd source"
    return "—"  # not a routing-relevant weakness


def render_block() -> str:
    card = t.build_scorecard()
    tenets: dict = card["tenets"]
    weaknesses: list = card["weaknesses"]  # already sorted (severity, id) by build_scorecard
    resolved: list = card.get("resolved", [])  # archived off the live risk board
    live: dict = card["live"]

    sev_counts: dict[str, int] = {}
    for w in weaknesses:
        sev_counts[w["severity"]] = sev_counts.get(w["severity"], 0) + 1
    per_tenet: dict[str, int] = {}
    for w in weaknesses:
        per_tenet[w["tenet"]] = per_tenet.get(w["tenet"], 0) + 1
    undeclared = [w for w in weaknesses if not w.get("declared", True)]

    L: list[str] = [MARKER_START, ""]

    # -- Summary --------------------------------------------------------------
    sev_line = ", ".join(
        f"{_sev_badge(s)} {sev_counts[s]}"
        for s in ("critical", "high", "medium", "low")
        if s in sev_counts
    ) or "none"
    L += [
        "## Standing",
        "",
        (
            f"**{len(weaknesses)}** open weakness(es) — {sev_line}. "
            f"**{len(resolved)}** resolved (archived below). "
            f"Undeclared (a real divergence with no conformance level / contract): "
            f"**{len(undeclared)}**."
        ),
        "",
        (
            "A weakness is a place the codebase does not yet fully meet the frame; each maps "
            "to one of the five decision tests (tenets). *Declared* means the divergence has a "
            "conformance level and a test — an accepted, contained cost — not an accident. The "
            "`declared: false` class is the dangerous one and is flagged below. Resolved items "
            "are archived off this live board (conformance v2 — the live list is the real risk "
            "surface, not a museum of closed wins) but kept for the record."
        ),
        "",
    ]

    # -- Tenets ---------------------------------------------------------------
    L += [
        "## The five tenets",
        "",
        "| Tenet | Decision test | Open |",
        "|---|---|---|",
    ]
    for tid in sorted(tenets):
        L.append(f"| **{tid}** | {_cell(tenets[tid])} | {per_tenet.get(tid, 0)} |")
    L.append("")

    # -- Weakness board -------------------------------------------------------
    L += [
        "## Weakness board",
        "",
        (
            "Ranked by severity. *Routing* applies the conformance-v2 default-routing test "
            "(0047 amendment): a shared owner the default caller path does not use is a latent "
            "second source of truth, even when a fixture proves parity."
        ),
        "",
        "| Severity | Tenet | Declared | Routing | Weakness |",
        "|---|---|---|---|---|",
    ]
    for w in weaknesses:
        L.append(
            f"| {_sev_badge(w['severity'])} | {w['tenet']} | {_declared(w)} "
            f"| {_routed(w)} | {_cell(w.get('title', w.get('id', '')))} |"
        )
    L.append("")

    # -- Re-validation triggers ----------------------------------------------
    revalidate = [w for w in weaknesses if w.get("un_defer")]
    if revalidate:
        L += [
            "## Re-validate (deferral triggers)",
            "",
            (
                "Each deferral names the explicit condition that would *un-defer* it (conformance-v2 "
                "test T3). A deferral whose premise has already lifted is an OPEN divergence, not a "
                "settled low — these are re-checked each audit."
            ),
            "",
        ]
        for w in revalidate:
            L.append(f"- **{_cell(w.get('id'))}** — {_cell(w['un_defer'])}")
        L.append("")

    # -- Live harvest ---------------------------------------------------------
    cov = live["scorer_coverage"]
    fallback = live["fallback_only_kernels"]
    gaps = live["surface_gaps"]

    L += [
        "## Live harvest (auto-detected)",
        "",
        (
            "Static signals harvested directly from `parity/*.yaml` + each package's "
            "`_native_loader.py` (no toolchain) — so drift shows up here without editing the "
            "curated board above."
        ),
        "",
        (
            "**Scorer-kernel coverage (goldenmatch).** "
            f"{cov['kernel_backed']} of {cov['total']} scorers are kernel-backed; "
            f"{len(cov['deferred'])} declared-deferred; "
            "uncovered (must be empty — coverage floor): "
            f"{('none' if not cov['uncovered'] else '`' + '`, `'.join(cov['uncovered']) + '`')}."
        ),
        "",
    ]
    if cov["deferred"]:
        L += ["| Deferred scorer | Reason |", "|---|---|"]
        for name in sorted(cov["deferred"]):
            L.append(f"| `{_cell(name)}` | {_cell(cov['deferred'][name])} |")
        L.append("")

    L += [
        (
            "**Fallback-only kernels** (a `-core` symbol the host references but the default "
            "does not run — `_FALLBACK_ONLY`):"
        ),
        "",
    ]
    any_fb = False
    for pkg in sorted(fallback):
        names = sorted(fallback[pkg])
        if names:
            any_fb = True
            L.append(f"- `{_cell(pkg)}`: " + ", ".join(f"`{_cell(n)}`" for n in names))
    if not any_fb:
        L.append("- none")
    L.append("")

    L += [
        (
            "**Cross-language surface gaps** (declared Python-only / TS-only per surface — the "
            "same partition the [suite matrix](/suite-matrix) renders in full):"
        ),
        "",
        "| Package | Surface | Python-only | TS-only |",
        "|---|---|---|---|",
    ]
    for pkg in sorted(gaps):
        for surface in sorted(gaps[pkg]):
            block = gaps[pkg][surface]
            po = len(block.get("python_only") or [])
            to = len(block.get("ts_only") or [])
            if po or to:
                L.append(f"| `{pkg}` | {surface} | {po} | {to} |")
    L.append("")

    # -- Resolved (archived) --------------------------------------------------
    if resolved:
        L += [
            "## Resolved (archived)",
            "",
            (
                "Weaknesses verified resolved-and-stable and moved off the live risk board "
                "(conformance v2, 0047 amendment #4). Kept for the record — the evidence + "
                "reasoning live in `parity/thesis_conformance.yaml`; a rotted premise un-archives."
            ),
            "",
            "| Tenet | Weakness |",
            "|---|---|",
        ]
        for w in resolved:
            L.append(f"| {w['tenet']} | {_cell(w.get('title', w.get('id', '')))} |")
        L.append("")

    L += [MARKER_END]
    return "\n".join(L)


def _compose(block: str) -> str:
    intro = (
        "---\n"
        'title: "Repo thesis weaknesses"\n'
        'description: "Where the codebase does not yet meet the governing architecture frame '
        "(one product, two engines, many surfaces) — the thesis-conformance weakness board and "
        'live-harvested drift signals, generated and gated in CI."\n'
        'keywords: ["thesis", "conformance", "architecture", "weaknesses", "one product two engines", "governance", "reference"]\n'
        "---\n\n"
        "The [suite matrix](/suite-matrix) renders cross-language *surface* parity. This page "
        "renders the codebase's standing against the **governing architecture frame** — *one "
        "product, two engines, many surfaces* "
        "([frame](https://github.com/benseverndev-oss/goldenmatch/blob/main/context-network/architecture/one-product-two-engines.md), "
        "decision 0047) — scored by its five decision tests (tenets). Everything below the line "
        "is generated from the thesis-conformance scorecard "
        "(`parity/thesis_conformance.yaml` + the live static harvest in "
        "`scripts/check_thesis_conformance.py`, via `scripts/gen_thesis_weaknesses.py`) and "
        "verified in CI, so it can't drift from the gate.\n\n"
    )
    return intro + block + "\n"


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--check"
    block = render_block()
    if mode == "--write":
        PAGE.write_text(_compose(block), encoding="utf-8", newline="\n")
        print(f"wrote {PAGE.relative_to(ROOT)}")
        return 0
    if mode == "--check":
        current = PAGE.read_text(encoding="utf-8") if PAGE.exists() else ""
        fresh = _compose(block)
        if current != fresh:
            import difflib

            diff = difflib.unified_diff(
                current.splitlines(), fresh.splitlines(),
                fromfile="committed", tofile="live", lineterm="",
            )
            print("thesis-weaknesses.mdx is STALE vs the thesis-conformance scorecard. "
                  "Regenerate: python scripts/gen_thesis_weaknesses.py --write")
            print("\n".join(list(diff)[:40]))
            return 1
        print("thesis-weaknesses.mdx OK: matches the thesis-conformance scorecard.")
        return 0
    print(f"unknown mode {mode!r} (use --write / --check)")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
