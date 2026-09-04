"""Report docstring sync claims that no test enforces. C0 is report-only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sync_claims.claims import Claim, claims, declared_symbols
from sync_claims.coverage_enforcement import coverage_enforced, function_contexts, function_spans
from sync_claims.enforcement import test_reference_sets, unenforced

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_ROOT = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch"
DEFAULT_TESTS = REPO / "packages" / "python" / "goldenmatch" / "tests"

SCOPE_NOTE = (
    "scope: claims are read from docstrings under {root} and enforcement from "
    "{tests} only -- other packages, the TypeScript port and _archive are out "
    "of reach by construction, and their silence here is not a clean bill. "
    "Low-confidence findings, module-level claims, and ambiguous-target "
    "claims are reported but NOT triaged. A module has no single "
    "symbol a test can reference. An ambiguous-target claim's resolved "
    "symbol is one of several same-named declarations and may not be the "
    "one the claim's author meant. A claim listed as UNVERIFIED is not safe "
    "-- some test references both names, which does not mean it compares "
    "them."
)


def _degrade_non_utf8_stdout() -> None:
    """Never let an unprintable claim-window character kill a report-only run.

    Some claim windows carry non-ASCII text (arrows, smart quotes, ...) and
    the platform's default stdout encoding cannot always encode it -- on
    Windows cp1252, a `UnicodeEncodeError` partway through the findings loop
    would exit non-zero after printing only the first few findings. That
    reads as a short, complete report; it is actually a truncated one, and
    `main` is contracted to exit 0 whatever it finds. Degrade the character
    instead of crashing. `reconfigure` does not exist on every stream object
    (a plain redirected file, pytest's `capsys`), so this is a guarded no-op
    wherever it is unavailable or refused.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError, OSError):
        pass


def _as_dict(claim: Claim) -> dict:
    return {
        "module": claim.module,
        "symbol": claim.symbol,
        "lineno": claim.lineno,
        "keyword": claim.keyword,
        "target": claim.target,
        "window": claim.window,
    }


def inventory(root: Path, tests_root: Path, coverage_db: Path | None = None) -> dict:
    """Bucket every claim under `root` by enforcement state.

    `coverage_db` is optional and additive. When given and readable, a
    HIGH-confidence claim the text check reports unenforced is re-checked
    against real test-execution data: if some single test executed both the
    claimant and the target, the claim moves from `unenforced` to
    `coverage_enforced` rather than staying a false negative. Absent, wrong
    path, or unreadable -> exactly today's text-only behavior, silently and
    correctly -- this must never raise or hang on missing coverage data, the
    CI scenario where the shard-producing jobs did not run on this PR.

    Coverage-rescue is scoped to HIGH-confidence findings only. A
    LOW-confidence claim's problem is that its resolved TARGET may be wrong
    (a different axis than enforcement); coverage evidence against a wrong
    target proves nothing about the claim the docstring actually makes.
    An ambiguous-target claim (`Claim.target_ambiguous`, see
    `claims._symbol_modules`) is excluded from both `unenforced` and
    coverage-rescue for the same reason and reported separately, in
    `unenforced_ambiguous_target` -- the resolved target is not necessarily
    the one the claim's author meant, so neither "this test doesn't
    reference it" nor "this test executes it" is evidence about anything in
    particular.
    """
    all_claims = claims(root, symbols=declared_symbols(root))
    symbol_claims = [c for c in all_claims if c.kind == "symbol"]
    module_claims = [c for c in all_claims if c.kind == "module"]
    resolvable = [c for c in symbol_claims if c.target is not None]
    unresolvable = [c for c in symbol_claims if c.target is None]

    reference_sets = test_reference_sets(tests_root)
    all_findings = unenforced(resolvable, reference_sets)
    findings = [c for c in all_findings if c.confidence == "high"]
    low_confidence = [c for c in all_findings if c.confidence != "high"]

    # An ambiguous-target high-confidence finding is not a trustworthy
    # finding at all -- `target` was picked by `_resolve_target`'s
    # first-match-wins rule among more than one same-named declaration
    # (Stage 4b found this affecting ~a third of the still-unenforced
    # population: `score`, `row`, `keys`, `__init__`, ...). Route it to its
    # own bucket, same treatment `unresolvable` already gets for a target
    # that isn't Python at all -- and split it out BEFORE coverage-rescue,
    # for the same reason low-confidence claims are excluded from rescue:
    # coverage evidence against a possibly-wrong target proves nothing.
    ambiguous_target = [c for c in findings if c.target_ambiguous]
    findings = [c for c in findings if not c.target_ambiguous]

    coverage_consulted = False
    coverage_rescued: list[Claim] = []
    contexts: dict[tuple[str, str], frozenset[str]] = {}
    if coverage_db is not None and coverage_db.exists():
        try:
            spans = function_spans(root)
            contexts = function_contexts(coverage_db, root, spans)
            coverage_consulted = True
        except Exception:
            # A malformed or foreign .coverage file must degrade to
            # text-only, not crash a report-only job. A broken root
            # (permission error, missing, etc.) must also degrade the same way.
            spans = {}
            contexts = {}
        for claim in findings:
            claimant_name = _resolve_dotted_name(spans, claim.module, claim.symbol)
            if claimant_name is None:
                continue
            claimant_key = (claim.module, claimant_name)
            target_module, target_name = _locate_target(spans, claim.target)
            if target_module is None:
                continue
            target_key = (target_module, target_name)
            if coverage_enforced(claimant_key, target_key, contexts):
                coverage_rescued.append(claim)
        findings = [c for c in findings if c not in coverage_rescued]

    finding_ids = {(c.module, c.symbol, c.lineno) for c in all_findings}
    unverified = [c for c in resolvable if (c.module, c.symbol, c.lineno) not in finding_ids]

    return {
        "counts": {
            "claims": len(all_claims),
            "resolvable": len(resolvable),
            "unenforced": len(findings),
            "unenforced_low_confidence": len(low_confidence),
            "unenforced_ambiguous_target": len(ambiguous_target),
            "unverified": len(unverified),
            "coverage_enforced": len(coverage_rescued),
            "coverage_consulted": coverage_consulted,
            "coverage_functions_with_data": len(contexts),
            "unresolvable": len(unresolvable),
            "module_level": len(module_claims),
            "test_files_scanned": len(reference_sets),
        },
        "unenforced": [_as_dict(c) for c in findings],
        "unenforced_low_confidence": [_as_dict(c) for c in low_confidence],
        "unenforced_ambiguous_target": [_as_dict(c) for c in ambiguous_target],
        "unverified": [_as_dict(c) for c in unverified],
        "coverage_enforced": [_as_dict(c) for c in coverage_rescued],
        "unresolvable": [_as_dict(c) for c in unresolvable],
        "module_level": [_as_dict(c) for c in module_claims],
    }


def _locate_target(
    spans: dict[str, list[tuple[str, int, int]]], target_name: str
) -> tuple[str, str] | tuple[None, None]:
    """The claim's `target` is a bare symbol NAME (claims.py resolves it
    from prose, not a module path), but `function_contexts` is keyed by
    (module, name) -- coverage cannot be checked without knowing which
    module the target lives in. Searches the already-computed function
    spans for a function with this exact name; the FIRST module found
    wins.

    A real limitation, stated rather than hidden: if the target name
    exists in more than one module, this can pick the wrong one, and the
    ambiguity is silent. Scoped narrowly on purpose -- coverage-rescue
    only applies to HIGH-confidence claims, which are already the subset
    least likely to collide on a name (see KNOWN_AMBIGUOUS in the
    shared-decisions detector for what a real per-name collision problem
    looks like at scale). A future pass could resolve this properly by
    carrying the target's module through from claims.py instead of
    re-deriving it here; out of scope for this plan.
    """
    for module, functions in spans.items():
        for name, _, _ in functions:
            if name == target_name or name.rsplit(".", 1)[-1] == target_name:
                return module, name
    return None, None


def _resolve_dotted_name(
    spans: dict[str, list[tuple[str, int, int]]], module: str, bare_name: str
) -> str | None:
    """Resolve a claim's bare declared-symbol name (claims.py does not
    distinguish a method from a module-level function -- both come from
    `node.name`) against the dotted qualified names `function_spans`
    produces for the SAME module. Exact match first, then the last dotted
    segment -- the same tolerance `_locate_target` already applies on the
    target side, applied here to the claimant side, which was missing it.

    Unlike `_locate_target`, the module is already known here (it's the
    claim's own declared module), so this never has the cross-module
    ambiguity `_locate_target`'s docstring names as a real limitation.
    """
    for name, _, _ in spans.get(module, []):
        if name == bare_name or name.rsplit(".", 1)[-1] == bare_name:
            return name
    return None


def main(argv: list[str]) -> int:
    _degrade_non_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--tests", type=Path, default=DEFAULT_TESTS)
    parser.add_argument("--coverage-db", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    inv = inventory(args.root, args.tests, coverage_db=args.coverage_db)
    if args.json:
        print(json.dumps(inv, indent=2))
        return 0

    counts = inv["counts"]
    print(SCOPE_NOTE.format(root=args.root, tests=args.tests))
    print()
    if counts["test_files_scanned"] == 0:
        # Every claim looks unenforced when nothing was scanned. That is a
        # broken run, not a perfect score, and it must not read as findings.
        print(
            f"NO TEST FILES SCANNED under {args.tests} -- every claim below "
            f"would be reported unenforced for that reason alone. Fix --tests "
            f"before reading this as a result."
        )
        print()

    print(
        f"{counts['claims']} claim(s); {counts['resolvable']} resolvable and "
        f"symbol-level; {counts['unenforced']} UNENFORCED (high confidence), "
        f"{counts['unverified']} unverified"
    )
    print(
        f"  {counts['unenforced_low_confidence']} further unenforced claim(s) "
        f"resolve a LOW-confidence target -- reported, not triaged: the "
        f"resolved symbol is often real but not what the claim equates"
    )
    print(
        f"  reported but not triaged: {counts['unresolvable']} unresolvable, "
        f"{counts['module_level']} module-level, "
        f"{counts['unenforced_ambiguous_target']} ambiguous-target"
    )
    print(f"  test files scanned: {counts['test_files_scanned']}")
    print(
        f"  coverage consulted: {counts['coverage_consulted']}"
        + (
            f" ({counts['coverage_functions_with_data']} function(s) with test data) "
            f"-- {counts['coverage_enforced']} claim(s) rescued from unenforced"
            if counts["coverage_consulted"]
            else ""
        )
    )
    print()

    for entry in sorted(inv["unenforced"], key=lambda e: (e["module"], e["lineno"])):
        print(f"  {entry['module']}:{entry['lineno']}  {entry['symbol']}")
        print(f"      --{entry['keyword']}--> {entry['target']}")
        print(f"      claim: {entry['window'][:100]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
