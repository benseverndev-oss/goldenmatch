"""Every execution lane's `golden_rules` fallback must construct, and agree.

`GoldenMatchConfig.golden_rules` is optional, so each lane supplies its own
default when a run does not configure survivorship. Four lanes do this
independently -- single-box, DataFusion spine, Spark, and Ray -- and nothing
compared them, so one of them fell out of step: `distributed/pipeline.py` built
a bare `GoldenRulesConfig()`, which does not construct at all
(`_validate_default` requires `default_strategy` or `default`). The Ray lane
therefore died at the golden step AFTER matching and clustering had completed.

WHY THIS TEST IS AST-BASED. Reaching `distributed/pipeline.py` at runtime needs
Ray, which is not installed for the unit suite, and importing the module is
enough to require it. Reading the fallback expressions statically checks every
lane -- including ones this suite cannot execute -- which is exactly the set the
defect lived in. It also generalises: a fifth lane added tomorrow is checked the
day it is written, with no runtime harness.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from goldenmatch.config.schemas import GoldenRulesConfig

PACKAGE = Path(__file__).resolve().parent.parent / "goldenmatch"


def _golden_rules_fallbacks() -> list[tuple[str, int, ast.Call]]:
    """Every `<expr>.golden_rules or GoldenRulesConfig(...)` in the package."""
    found: list[tuple[str, int, ast.Call]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
        except SyntaxError:
            continue
        rel = path.relative_to(PACKAGE).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
                continue
            for left, right in zip(node.values, node.values[1:]):
                if not (isinstance(left, ast.Attribute) and left.attr == "golden_rules"):
                    continue
                if not isinstance(right, ast.Call):
                    continue
                name = getattr(right.func, "id", getattr(right.func, "attr", ""))
                if name == "GoldenRulesConfig":
                    found.append((rel, right.lineno, right))
    return found


def test_the_scan_finds_every_lane():
    """Guard the guard: a scan that finds nothing would pass vacuously."""
    sites = _golden_rules_fallbacks()
    modules = {module for module, _, _ in sites}
    assert modules >= {
        "core/pipeline.py",
        "backends/datafusion_spine.py",
        "spark/config_pipeline.py",
        "distributed/pipeline.py",
    }, f"a known lane fell out of the scan: {sorted(modules)}"


@pytest.mark.parametrize(
    "module,line,call",
    _golden_rules_fallbacks(),
    # Default ids stringify the ast.Call as `<ast.Call object at 0x...>`, so a
    # failure names a memory address instead of the lane that failed.
    ids=[f"{module}:{line}" for module, line, _ in _golden_rules_fallbacks()],
)
def test_each_lane_fallback_constructs(module: str, line: int, call: ast.Call):
    """A fallback that raises turns an unset optional into a late crash.

    `GoldenRulesConfig()` is not a valid config: `_validate_default` raises
    `GoldenRulesConfig requires 'default_strategy' or 'default'.`
    """
    kwargs = {}
    for keyword in call.keywords:
        if keyword.arg is None:
            pytest.skip(f"{module}:{line} splats kwargs; not statically evaluable")
        try:
            kwargs[keyword.arg] = ast.literal_eval(keyword.value)
        except ValueError:
            pytest.skip(f"{module}:{line} passes a non-literal {keyword.arg}")

    GoldenRulesConfig(**kwargs)  # must not raise


def test_the_lanes_agree_on_the_default_strategy():
    """Four lanes defaulting differently is a silent behaviour fork.

    This is the shared-decision shape from the phase-B audit: the fallback is a
    value the FIELD does not carry, so each reader invents one, and nothing
    makes them agree.
    """
    chosen: dict[str, object] = {}
    for module, line, call in _golden_rules_fallbacks():
        strategy = next(
            (
                ast.literal_eval(kw.value)
                for kw in call.keywords
                if kw.arg == "default_strategy"
            ),
            None,
        )
        chosen[f"{module}:{line}"] = strategy

    assert len(set(chosen.values())) == 1, (
        f"lanes disagree on the fallback survivorship strategy: {chosen}"
    )
