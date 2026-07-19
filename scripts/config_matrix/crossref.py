"""Cross-reference the rest of a package's docs against the config-matrix source
of truth, so drift is caught from one place.

The config-matrix registry knows every env knob a package actually reads (scanned
from source). This checks that the package's OTHER docs-site pages don't document
an env var that no longer exists -- the classic "renamed/removed a flag but a doc
still lists it as live" drift. Anchored on the same registry, so one change to the
code surface flags every stale mention across the docs.

Two false-positive classes are excluded, because naming a removed knob there is
correct, not drift:
  - migration / changelog / upgrade pages (they document what was removed);
  - a mention on a line that itself says removed / deprecated / renamed / etc.
A per-package `env_allow` set covers anything those heuristics miss.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .render import MARKER_START, ROOT, _resolve_gloss, _resolve_vocab, scan_env_vars

# Pages whose whole job is to describe old/removed knobs.
_EXCLUDE_STEMS = re.compile(
    r"(migrat|changelog|release-notes?|upgrad|^v\d|-to-v\d|-vs-v\d)", re.IGNORECASE
)
# A line that frames the token as gone is not documenting it as live.
_REMOVAL_CTX = re.compile(
    r"remov|deprecat|renamed|no longer|dropped|\bgone\b|replaced by|\blegacy\b|"
    r"used to|previously|pre-v?\d|before v?\d",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RefFinding:
    package: str
    page: str
    token: str
    line_no: int


def _canonical_env(spec) -> set[str]:
    return {n for names in scan_env_vars(spec.env_prefix, spec.src_dirs).values() for n in names}


def _prose_pages(spec) -> list[Path]:
    doc_dir = (ROOT / spec.doc_path).parent
    pages = []
    for p in sorted(doc_dir.rglob("*.mdx")):
        if _EXCLUDE_STEMS.search(p.stem):
            continue
        pages.append(p)
    return pages


def _scannable_text(page: Path) -> str:
    """The prose region of a page to check for dead references. For the config
    matrix, only the hand-authored region ABOVE the generated marker can rot --
    the generated block is rendered from the source of truth, so scanning it would
    be circular. Every other page is scanned whole."""
    text = page.read_text(encoding="utf-8", errors="ignore")
    if page.name == "config-matrix.mdx":
        cut = text.find(MARKER_START)
        if cut != -1:
            text = text[:cut]
    return text


# Backticked value tokens in a doc. Includes `.`/`-` so dotted registry names
# (analyzer `frame.summary`, stage `goldenmatch.dedupe`) match as one token.
_TOKEN_RE = re.compile(r"`([a-z0-9_][a-z0-9_.-]*)`")


def _canonical_set(target: str) -> set[str]:
    """The canonical value set for a doc_coverage target. `_resolve_vocab` already
    handles `module:CONST` (frozenset / enum / Literal alias / callable) and
    `module:Model.field` (a pydantic Literal field)."""
    return set(_resolve_vocab(target))


def undocumented_vocab(spec) -> list[RefFinding]:
    """Every canonical value that is NOT mentioned in its reference doc -- so a new
    scorer/strategy/etc. must be propagated to the topical page, not just the matrix."""
    findings: list[RefFinding] = []
    doc_dir = (ROOT / spec.doc_path).parent
    for page, target in getattr(spec, "doc_coverage", ()):
        path = doc_dir / page
        if not path.exists():
            continue
        present = set(_TOKEN_RE.findall(path.read_text(encoding="utf-8", errors="ignore")))
        rel = path.relative_to(ROOT).as_posix()
        for value in sorted(_canonical_set(target) - present):
            findings.append(RefFinding(spec.name, rel, value, 0))
    return findings


def vocab_column_gaps(spec) -> list[RefFinding]:
    """Within a vocab, every value must carry every extra column that ANY value
    carries -- so a decision hint (`best_for`) or `range` column is never
    half-filled, leaving blank cells that read to a human as 'no guidance here'.
    Mechanical vocabs with plain-string glosses have no columns and are exempt."""
    findings: list[RefFinding] = []
    for entry in getattr(spec, "vocabs", ()):
        title, target = entry[0], entry[1]
        gloss = entry[3] if len(entry) > 3 else None
        glosses = _resolve_gloss(target, gloss)
        cols: set[str] = set()
        for g in glosses.values():
            if isinstance(g, dict):
                cols |= {k for k in g if k != "meaning"}
        if not cols:
            continue
        for value in sorted(set(_resolve_vocab(target))):
            g = glosses.get(value)
            have = (
                {k for k in cols if str(g.get(k, "")).strip()}
                if isinstance(g, dict)
                else set()
            )
            for missing in sorted(cols - have):
                findings.append(RefFinding(spec.name, f"{title} vocab", f"{value}.{missing}", 0))
    return findings


def stale_env_refs(spec) -> list[RefFinding]:
    canon = _canonical_env(spec)
    allow = set(getattr(spec, "env_allow", ()))
    prefix = spec.env_prefix.rstrip("_")
    rx = re.compile(re.escape(prefix) + r"_[A-Z0-9_]+")
    findings: list[RefFinding] = []
    for page in _prose_pages(spec):
        for i, line in enumerate(_scannable_text(page).splitlines(), 1):
            if _REMOVAL_CTX.search(line):
                continue
            for tok in set(rx.findall(line)):
                if tok.endswith("_") or tok in canon or tok in allow:
                    continue  # glob/family mention, live var, or explicitly allowed
                findings.append(RefFinding(spec.name, page.relative_to(ROOT).as_posix(), tok, i))
    return findings
