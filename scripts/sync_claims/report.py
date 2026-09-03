"""Report docstring sync claims that no test enforces. C0 is report-only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sync_claims.claims import Claim, claims, declared_symbols
from sync_claims.enforcement import test_reference_sets, unenforced

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_ROOT = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch"
DEFAULT_TESTS = REPO / "packages" / "python" / "goldenmatch" / "tests"

SCOPE_NOTE = (
    "scope: claims are read from docstrings under {root} and enforcement from "
    "{tests} only -- other packages, the TypeScript port and _archive are out "
    "of reach by construction, and their silence here is not a clean bill. "
    "Low-confidence findings and module-level claims are reported but NOT "
    "triaged. A module has no single "
    "symbol a test can reference. A claim listed as UNVERIFIED is not safe -- "
    "some test references both names, which does not mean it compares them."
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


def inventory(root: Path, tests_root: Path) -> dict:
    """Bucket every claim under `root` by enforcement state."""
    all_claims = claims(root, symbols=declared_symbols(root))
    symbol_claims = [c for c in all_claims if c.kind == "symbol"]
    module_claims = [c for c in all_claims if c.kind == "module"]
    resolvable = [c for c in symbol_claims if c.target is not None]
    unresolvable = [c for c in symbol_claims if c.target is None]

    reference_sets = test_reference_sets(tests_root)
    all_findings = unenforced(resolvable, reference_sets)
    # C1 triage measured that a LOW-confidence target is frequently a real
    # symbol the claim does not equate. Those stay reported, in their own
    # bucket, but are not the triage set and must not seed C3's ratchet floor.
    findings = [c for c in all_findings if c.confidence == "high"]
    low_confidence = [c for c in all_findings if c.confidence != "high"]
    finding_ids = {(c.module, c.symbol, c.lineno) for c in all_findings}
    unverified = [c for c in resolvable if (c.module, c.symbol, c.lineno) not in finding_ids]

    return {
        "counts": {
            "claims": len(all_claims),
            "resolvable": len(resolvable),
            "unenforced": len(findings),
            "unenforced_low_confidence": len(low_confidence),
            "unverified": len(unverified),
            "unresolvable": len(unresolvable),
            "module_level": len(module_claims),
            "test_files_scanned": len(reference_sets),
        },
        "unenforced": [_as_dict(c) for c in findings],
        "unenforced_low_confidence": [_as_dict(c) for c in low_confidence],
        "unverified": [_as_dict(c) for c in unverified],
        "unresolvable": [_as_dict(c) for c in unresolvable],
        "module_level": [_as_dict(c) for c in module_claims],
    }


def main(argv: list[str]) -> int:
    _degrade_non_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--tests", type=Path, default=DEFAULT_TESTS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    inv = inventory(args.root, args.tests)
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
        f"{counts['module_level']} module-level"
    )
    print(f"  test files scanned: {counts['test_files_scanned']}")
    print()

    for entry in sorted(inv["unenforced"], key=lambda e: (e["module"], e["lineno"])):
        print(f"  {entry['module']}:{entry['lineno']}  {entry['symbol']}")
        print(f"      --{entry['keyword']}--> {entry['target']}")
        print(f"      claim: {entry['window'][:100]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
