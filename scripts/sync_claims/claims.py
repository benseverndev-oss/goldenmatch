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
            out.append(
                Claim(
                    module=rel,
                    symbol=name,
                    kind="module" if is_module else "symbol",
                    keyword=match.group(0),
                    window=" ".join(window.split()),
                    target=target,
                    lineno=0 if is_module else node.lineno,
                )
            )
    return out
