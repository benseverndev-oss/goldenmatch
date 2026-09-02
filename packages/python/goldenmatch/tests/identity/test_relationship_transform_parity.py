"""The two relationship-transform backends must offer the same vocabulary.

`identity/snowflake_backend.py:_rel_expr` says, in its own docstring:

    Snowflake port of ``store._rel_value_expr`` (store.py:59-96).
    Same FIXED transform vocabulary, same NULL-on-no-match semantics; only
    the string functions change.

That claim was true when this test was written and **nothing enforced it**.
It was surfaced by the phase-C sync-claim audit as an unenforced claim
(`docs/superpowers/specs/2026-09-02-c1-triage-findings.md`), and it is the
kind that matters: the two functions build SQL for the SAME relationship
rules on different engines, so a transform added to one and not the other
means a relationship rule silently keys edges on a different value
depending on which backend runs it. Different identity resolution, no error.

WHY AST RATHER THAN CALLING THEM. `_rel_expr` is a method on the Snowflake
backend class, and importing that module to instantiate one pulls the
Snowflake client. Reading the vocabulary out of the source needs nothing
installed and works in the bare unit lane. The cost is that this pins the
DISPATCH VOCABULARY, not the emitted SQL -- the SQL deliberately differs
(``btrim`` vs ``trim``, and sqlite degrades ``normalize_company`` to
``lower_trim``), which is exactly what the claim says.
"""

from __future__ import annotations

import ast
from pathlib import Path

GM = Path(__file__).resolve().parent.parent.parent / "goldenmatch"
STORE = GM / "identity" / "store.py"
SNOWFLAKE = GM / "identity" / "snowflake_backend.py"


def _function(path: Path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(
        f"{name} is gone from {path.name}. The claim this test enforces named it "
        f"explicitly; if it was renamed, update the docstring claim too."
    )


def _transform_vocabulary(path: Path, name: str) -> set[str]:
    """The transform names the function dispatches on (`if t == "..."`)."""
    out: set[str] = set()
    for node in ast.walk(_function(path, name)):
        if not isinstance(node, ast.Compare) or not isinstance(node.ops[0], ast.Eq):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == "t"):
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                out.add(comparator.value)
    return out


def test_the_two_backends_offer_the_same_transform_vocabulary():
    """The claim, enforced. A transform in one and not the other means the
    same relationship rule keys edges on different values per backend."""
    store = _transform_vocabulary(STORE, "_rel_value_expr")
    snowflake = _transform_vocabulary(SNOWFLAKE, "_rel_expr")

    assert store, "extracted nothing from store._rel_value_expr -- the dispatch shape changed"
    assert snowflake, "extracted nothing from snowflake._rel_expr -- the dispatch shape changed"

    only_store = store - snowflake
    only_snowflake = snowflake - store
    assert not only_store, (
        f"{sorted(only_store)} exist in store._rel_value_expr but not in the "
        f"Snowflake port. A relationship rule using one of these resolves on "
        f"sqlite/postgres and raises on Snowflake."
    )
    assert not only_snowflake, (
        f"{sorted(only_snowflake)} exist in the Snowflake port but not in "
        f"store._rel_value_expr. Same divergence, other direction."
    )


def test_the_vocabulary_is_the_one_the_claim_was_verified_against():
    """Pin the actual set, not just that the two agree.

    Two backends could agree while both losing a transform, and the
    set-equality test above would stay green. This names what was there when
    the claim was checked, so a deletion has to be deliberate.
    """
    assert _transform_vocabulary(STORE, "_rel_value_expr") == {
        "raw",
        "lower_trim",
        "zip3",
        "email_domain",
        "normalize_company",
    }


def test_both_backends_reject_an_unknown_transform():
    """The other half of the claim: "same NULL-on-no-match semantics".

    An unrecognised transform must raise rather than fall through to a
    silent default -- falling through would key edges on the raw field while
    the rule asked for a derived value.
    """
    for path, name in ((STORE, "_rel_value_expr"), (SNOWFLAKE, "_rel_expr")):
        raises = [
            node
            for node in ast.walk(_function(path, name))
            if isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and getattr(node.exc.func, "id", None) == "ValueError"
        ]
        assert raises, (
            f"{path.name}:{name} no longer raises ValueError on an unknown "
            f"transform. Falling through to a default would key edges on the "
            f"wrong value silently."
        )
