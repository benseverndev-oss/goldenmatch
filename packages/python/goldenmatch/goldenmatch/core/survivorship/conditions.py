"""Safe predicate evaluation + resolution ordering for conditional golden rules.

The `when:` mini-language is NOT Python eval. We parse with ast.parse(mode="eval")
and walk an explicit allowlist. Spec section 3.4.
"""
from __future__ import annotations

import ast


class PredicateError(ValueError):
    """Raised when a `when:` expression uses a disallowed node/operator."""


_ALLOWED_BOOLOP = (ast.And, ast.Or)
_ALLOWED_UNARYOP = (ast.Not, ast.USub, ast.UAdd)
_ALLOWED_CMPOP = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn)


def _validate(node: ast.AST) -> None:
    if isinstance(node, ast.Expression):
        _validate(node.body)
    elif isinstance(node, ast.BoolOp):
        if not isinstance(node.op, _ALLOWED_BOOLOP):
            raise PredicateError(f"operator {type(node.op).__name__} not allowed")
        for v in node.values:
            _validate(v)
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARYOP):
            raise PredicateError(f"unary {type(node.op).__name__} not allowed")
        _validate(node.operand)
    elif isinstance(node, ast.Compare):
        for op in node.ops:
            if not isinstance(op, _ALLOWED_CMPOP):
                raise PredicateError(f"comparison {type(op).__name__} not allowed")
        _validate(node.left)
        for c in node.comparators:
            _validate(c)
    elif isinstance(node, (ast.List, ast.Tuple)):
        if not isinstance(getattr(node, "ctx", ast.Load()), ast.Load):
            raise PredicateError("only load context allowed")
        for e in node.elts:
            _validate(e)
    elif isinstance(node, ast.Name):
        if not isinstance(node.ctx, ast.Load):
            raise PredicateError("only load context allowed")
    elif isinstance(node, ast.Constant):
        return
    else:
        raise PredicateError(f"node {type(node).__name__} not allowed")


def referenced_names(expr: str) -> set[str]:
    """Names a predicate reads (for resolution ordering). Raises PredicateError on bad nodes."""
    tree = ast.parse(expr, mode="eval")
    _validate(tree)
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def eval_predicate(expr: str, resolved: dict) -> bool:
    """Evaluate `expr` against `resolved`. Unknown name or None-operand -> miss (False),
    never an exception. Disallowed nodes raise PredicateError."""
    tree = ast.parse(expr, mode="eval")
    _validate(tree)

    class _Miss(Exception):
        pass

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.BoolOp):
            vals = [ev(v) for v in node.values]
            return all(vals) if isinstance(node.op, ast.And) else any(vals)
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return not ev(node.operand)
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else +v
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            for op, comp_node in zip(node.ops, node.comparators):
                right = ev(comp_node)
                try:
                    ok = _cmp(op, left, right)
                except (TypeError, ValueError):
                    raise _Miss()
                if not ok:
                    return False
                left = right
            return True
        if isinstance(node, (ast.List, ast.Tuple)):
            return [ev(e) for e in node.elts]
        if isinstance(node, ast.Name):
            if node.id not in resolved:
                raise _Miss()
            return resolved[node.id]
        if isinstance(node, ast.Constant):
            return node.value
        raise PredicateError(f"node {type(node).__name__} not allowed")

    try:
        return bool(ev(tree))
    except _Miss:
        return False


def _cmp(op, left, right) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.In):
        return left in right
    if isinstance(op, ast.NotIn):
        return left not in right
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    raise PredicateError(f"comparison {type(op).__name__} not allowed")


class ResolutionError(ValueError):
    """Circular or otherwise unresolvable `when:` dependency graph."""


def select_conditional_strategy(rule_or_list, resolved: dict):
    """Return the GoldenFieldRule whose `when:` is satisfied (first match), else the
    when-less default. A single (non-list) rule is returned as-is."""
    if not isinstance(rule_or_list, list):
        return rule_or_list
    default = None
    for r in rule_or_list:
        if r.when is None:
            default = r
            continue
        if eval_predicate(r.when, resolved):
            return r
    return default  # config guarantees exactly one default


def build_resolution_order(field_rules, groups, all_columns) -> list[str]:
    """Topologically order resolution units. Unit ids: a scalar column name, or
    'group:<name>' for a group. A `when:` reference to a group member becomes a
    dependency on that group's unit. Raises ResolutionError on a cycle."""
    owner: dict[str, str] = {}
    units: list[str] = []
    for g in groups:
        uid = f"group:{g.name}"
        units.append(uid)
        for c in g.columns:
            owner[c] = uid
    for col in field_rules:
        if col not in owner:
            owner[col] = col
            units.append(col)
    for col in all_columns:
        if col not in owner:
            owner[col] = col
            if col not in units:
                units.append(col)

    deps: dict[str, set[str]] = {u: set() for u in units}
    for col, rule in field_rules.items():
        clauses = rule if isinstance(rule, list) else [rule]
        unit = owner[col]
        for r in clauses:
            if r.when is None:
                continue
            for name in referenced_names(r.when):
                ref_unit = owner.get(name)
                if ref_unit is not None and ref_unit != unit:
                    deps[unit].add(ref_unit)

    order: list[str] = []
    resolved_set: set[str] = set()
    remaining = list(units)
    while remaining:
        progressed = False
        for u in list(remaining):
            if deps[u] <= resolved_set:
                order.append(u)
                resolved_set.add(u)
                remaining.remove(u)
                progressed = True
        if not progressed:
            raise ResolutionError(f"circular when: dependency among {remaining}")
    return order
