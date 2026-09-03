"""`keys` vs `passes` is ONE decision, and every backend must make it the same.

`BlockingConfig` accepts the block keys in either `keys` or `passes`, and which
one wins depends on `strategy`. That rule was written out seven times across
the package and the copies disagreed, twice, on configs the schema accepts:

- `strategy="multi_pass"` carrying only `keys` -- valid, and the validator's
  own message advertises it ("requires 'keys' or 'passes'") -- made
  `_build_multi_pass_blocks` produce ZERO blocks and zero candidate pairs,
  silently, on the MAIN blocking path.
- `strategy="static"` carrying both made `distributed/scoring.py` shuffle on
  `passes` while `core/blocker.py` blocked on `keys`.

Both are the `1c843c8a5` shape: a silent wrong answer from two modules
resolving the same field pair differently.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pyarrow as pa
import pytest
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.blocker import build_blocks

PACKAGE = Path(__file__).resolve().parent.parent / "goldenmatch"


def _key(name: str) -> BlockingKeyConfig:
    return BlockingKeyConfig(fields=[name])


@pytest.mark.parametrize(
    "strategy,keys,passes,expected",
    [
        ("multi_pass", None, ["a", "b"], ["a", "b"]),
        # The shape that produced zero pairs.
        ("multi_pass", ["a"], None, ["a"]),
        ("multi_pass", ["a"], ["b"], ["b"]),
        ("static", ["a"], None, ["a"]),
        # The shape distributed/scoring.py got backwards.
        ("static", ["a"], ["b"], ["a"]),
    ],
)
def test_resolved_keys_covers_every_schema_valid_shape(strategy, keys, passes, expected):
    config = BlockingConfig(
        strategy=strategy,
        keys=[_key(k) for k in keys] if keys else [],
        passes=[_key(p) for p in passes] if passes else None,
    )
    assert [k.fields[0] for k in config.resolved_keys()] == expected


def test_resolved_keys_is_not_a_serialised_field():
    """A `@property` would land in `model_dump` and change the config wire form."""
    config = BlockingConfig(strategy="static", keys=[_key("a")])
    assert "resolved_keys" not in config.model_dump()


def test_multi_pass_with_only_keys_produces_blocks():
    """END TO END. The regression: this returned zero blocks, silently.

    Not a unit test of the resolver -- it drives `build_blocks`, the main
    path, because the defect was that the main path never consulted `keys`.
    """
    table = pa.table(
        {
            "id": ["1", "2", "3", "4"],
            "surname": ["smith", "smith", "jones", "jones"],
        }
    )
    key = _key("surname")

    def pairs(config: BlockingConfig) -> int:
        blocks = build_blocks(table, config)
        return sum(b.n_rows() * (b.n_rows() - 1) // 2 for b in blocks)

    baseline = pairs(BlockingConfig(strategy="static", keys=[key]))
    assert baseline == 2, "fixture no longer produces the pairs this test compares against"

    assert pairs(BlockingConfig(strategy="multi_pass", passes=[key])) == baseline
    assert pairs(BlockingConfig(strategy="multi_pass", keys=[key])) == baseline


def test_the_validator_still_accepts_multi_pass_with_only_keys():
    """Pin the premise. If this shape were rejected, the bug above could not
    occur -- and the fix would be dead code guarding an impossible config."""
    config = BlockingConfig(strategy="multi_pass", keys=[_key("a")])
    assert config.passes is None


def _attr_name(node: ast.expr) -> str | None:
    """The field name one operand of an `X or Y` chain resolves to, for the
    two shapes that actually shipped bugs: a bare attribute (`X.passes`) and
    a `list(X.passes or [])`-wrapped one -- the shape 6 of autoconfig.py's
    own "plan-building" sites used (constructing a NEW multi_pass config from
    an existing one's active keys/passes) before they were routed through
    `resolved_keys()`. Both normalize a value's LIST-ness away identically
    (`X.passes` is already `list[...] | None`; `list(X.passes or [])` is the
    same thing spelled defensively), so both are the same decision made by
    hand and both must resolve to `resolved_keys()` instead."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "list"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.BoolOp)
        and isinstance(node.args[0].op, ast.Or)
        and node.args[0].values
        and isinstance(node.args[0].values[0], ast.Attribute)
    ):
        return node.args[0].values[0].attr
    return None


def test_no_module_resolves_keys_versus_passes_by_hand():
    """The decision has ONE home. A new copy is how the last two drifted.

    Scans for `X.passes or X.keys` (and the reverse), bare or `list(...)`-
    wrapped, outside schemas.py. Deliberately narrow: it catches the shapes
    that actually shipped defects, not every mention of both fields --
    unions (`list(keys or []) + list(passes or [])`, note the `+`, not
    `or`) and existence checks (`if keys or passes`) are precedence-free
    and legitimate.
    """
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "schemas.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
                continue
            attrs = [n for n in (_attr_name(v) for v in node.values) if n is not None]
            if len(attrs) < 2:
                continue
            for left, right in zip(attrs, attrs[1:]):
                if {left, right} == {"passes", "keys"}:
                    rel = path.relative_to(PACKAGE).as_posix()
                    offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        f"{offenders} resolve keys-vs-passes by hand. Call "
        f"`config.resolved_keys()` instead -- that rule lives on BlockingConfig "
        f"so the backends cannot disagree about it."
    )
