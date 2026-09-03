"""Docstring claims that one piece of code must stay in step with another.

The codebase says where its traps are. 319 docstrings in goldenmatch assert a
synchronisation relationship -- "mirrors", "byte-identical to", "must match",
"keep in sync with" -- and nothing checks any of them. `MatchEngine._run_pipeline`
said "mirrors run_dedupe", stopped mirroring it, and shipped an ImportError on a
default install (6c89042c7).

TARGET RESOLUTION IS THE FILTER, deliberately. An earlier rule accepted a target
only in backticks or with a call suffix, and could not extract this phase's own
motivating example -- the incident names `run_dedupe` as a bare word. So any word
in the window after the claim keyword counts if it names a declared symbol, first
match wins. The cost is that a claim mentioning several symbols can resolve to the
wrong one; `Claim.window` carries the matched text so triage can see what it keyed
on.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

# Phrases that assert a relationship to another symbol. Kept narrow on purpose:
# every entry states that two things must AGREE, not merely that they are
# related. "see also" and "used by" are not claims.
CLAIM_PATTERN = re.compile(
    r"\b(mirror(s|ed|ing)?|keep (in|them) sync|in sync with|stay(s)? in sync|"
    r"parallel (to|of)|same (rule|logic|order|shape|contract) as|must match|"
    r"byte-identical to|identical to|counterpart|duplicat(e|ed|es) of|"
    r"copy of|port of)\b",
    re.IGNORECASE,
)

# How much text after the keyword can name the target. 200 characters is about
# two sentences -- long enough for "mirrors run_dedupe but returns EngineResult",
# short enough that an unrelated symbol three paragraphs down is not picked up.
WINDOW = 200

_WORD = re.compile(r"[A-Za-z_][\w.]*")

# A target the author MARKED UP as code: ``x``, `x`, or a Sphinx role such as
# :func:`~mod.x`. Preferred over a bare word, because a bare word is how the
# scan went wrong.
_MARKED = re.compile(r"``([A-Za-z_][\w.]*)``|`([A-Za-z_][\w.]*)`|:\w+:`~?([A-Za-z_][\w.]*)`")


# A target the author marked up is trusted further from the keyword than a bare
# word is. See `_confidence` for how these were chosen.
MARKED_WINDOW = 40
BARE_WINDOW = 12


def _confidence(
    window: str, known: set[str], claimant: str, target: str | None
) -> str:
    """"high" when the target sits where a claim's OBJECT actually sits.

    C1 triage found that a resolved target is frequently a real symbol that
    the claim does not equate: "Only gates the BATCHED bucket call; whether
    the per-block loop is native still follows ``_fs_native_eligible``"
    resolves to a correctly-spelled symbol and asserts no equivalence at all.
    The claim keyword and the symbol are both present; the symbol is simply
    elsewhere in the sentence.

    Proximity alone does not fix that -- measured, a same-sentence rule keeps
    6 of 7 known-wrong targets, because they ARE in the same sentence.
    Proximity combined with markup does: an author who wrote ``x`` meant the
    symbol, and a bare word immediately after the keyword ("mirrors
    run_dedupe") is the object by position.

    So: marked up within MARKED_WINDOW, or bare within the much tighter
    BARE_WINDOW. Measured on the real package -- 59 high-confidence findings
    of 167, rejecting 5 of 7 hand-identified wrong targets while keeping 4 of
    6 hand-identified right ones.

    BARE_WINDOW IS WHY THIS PHASE'S OWN INCIDENT STILL COUNTS. Requiring
    markup alone excludes it: `_run_pipeline`'s docstring reads "mirrors
    run_dedupe but returns EngineResult", and `run_dedupe` carries no markup.
    A high-confidence rule that cannot see the bug the detector exists to
    catch would be decoration, so the bare path is not a concession -- it is
    the case that matters most.

    Low confidence is NOT discarded. `report.py` puts those findings in their
    own bucket, reported and excluded from triage rather than hidden.
    """
    if target is None:
        return "low"
    marked = [
        group.split(".")[-1]
        for found in _MARKED.findall(window[:MARKED_WINDOW])
        for group in found
        if group
    ]
    if any(m in known and m != claimant for m in marked):
        return "high"
    bare = [
        word.rstrip(".").split(".")[-1] for word in _WORD.findall(window[:BARE_WINDOW])
    ]
    return "high" if any(b in known and b != claimant for b in bare) else "low"


def _resolve_target(window: str, known: set[str], claimant: str) -> str | None:
    """The symbol a claim names, preferring one the author wrote as code.

    MARKED-UP FIRST, bare word only as a fallback. This package declares
    thousands of symbols, many of them ordinary English words -- `slice`,
    `edge`, `native`, `value`, `pair`, `min`, `row` -- so almost any prose
    sentence contains one, and a plain first-match rule finds it instead of
    the real target. Measured during the C1 triage on the 50 strongest claims
    ("byte-identical to"), 7 of 8 sampled targets were wrong that way:
    `slice` came from "slice one bucket off the keyed frame", `min` from
    "Default ``min(cpu, 8)``", `edge` from "the shared edge set". The one
    correct target in that sample was the one in backticks.

    Preferring markup corrects 26 of the 216 resolvable claims outright --
    `slice` -> `score_buckets`, `native` -> `_fs_native_eligible`,
    `dedupe` -> `dedupe_df`, `value` -> `value_frequencies`.

    The bare-word fallback stays, and is not vestigial: 103 of 216 claims
    carry no markup at all, INCLUDING this phase's motivating incident, whose
    docstring reads "mirrors run_dedupe but returns EngineResult". Dropping
    the fallback would lose the one case the detector exists to catch.

    `_WORD`'s continuation class includes ".", so a word immediately followed
    by sentence-ending punctuation ("...mirrors helper.") matches WITH the
    period; rstrip before taking the dotted tail or "helper." never equals
    "helper".
    """
    for pattern, groups in ((_MARKED, True), (_WORD, False)):
        for found in pattern.findall(window):
            for word in found if groups else (found,):
                if not word:
                    continue
                tail = word.rstrip(".").split(".")[-1]
                if tail in known and tail != claimant:
                    return tail
    return None


@dataclass(frozen=True)
class Claim:
    """One docstring assertion that this code must stay in step with `target`."""

    module: str
    symbol: str
    kind: str  # "module" or "symbol"
    keyword: str
    window: str
    target: str | None
    lineno: int
    confidence: str  # "high" or "low" -- see `_confidence`


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
    except SyntaxError:
        return None


def declared_symbols(root: Path) -> set[str]:
    """Every function and class name declared under `root`."""
    out: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(node.name)
    return out


def claims(root: Path, *, symbols: set[str] | None = None) -> list[Claim]:
    """Every synchronisation claim under `root`, with targets resolved.

    `symbols` defaults to `declared_symbols(root)`. Pass it explicitly when the
    claims live in a fixture but must resolve against the real package.
    """
    known = declared_symbols(root) if symbols is None else symbols
    out: list[Claim] = []
    for path in sorted(root.rglob("*.py")):
        tree = _parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            doc = ast.get_docstring(node)
            if not doc:
                continue
            match = CLAIM_PATTERN.search(doc)
            if match is None:
                continue
            is_module = isinstance(node, ast.Module)
            name = "<module>" if is_module else node.name
            window = doc[match.end() : match.end() + WINDOW]
            target = _resolve_target(window, known, name)
            confidence = _confidence(window, known, name, target)
            out.append(
                Claim(
                    module=rel,
                    symbol=name,
                    kind="module" if is_module else "symbol",
                    keyword=match.group(0),
                    window=" ".join(window.split()),
                    target=target,
                    confidence=confidence,
                    lineno=0 if is_module else node.lineno,
                )
            )
    return out
