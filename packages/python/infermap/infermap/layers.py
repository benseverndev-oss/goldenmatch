"""Identity-layer detection — which *parties* a frame refers to.

``detect_domain`` answers "how finance-y is this table". This module answers a
different question: **who is in it**. A loan tape refers to a lender, a
borrower and a servicer; a claims file to an insurer, a provider and a patient.
Each is an identity layer — its own entity population, with its own natural key
and its own right answer for how to match it.

The load-bearing reframe: **an identity layer is a group of columns describing
one party**, not a per-column label. ``lender_name``/``lender_id`` are one
layer, ``borrower_name``/``borrower_ssn`` another. That makes this a *labelling*
pass over column names — many-to-many by construction — so it never touches
``engine.py``'s deliberately 1:1 assignment, which cannot express one role
spanning many columns.

Two signals:

1. **Affix clustering** (primary, domain-free). Columns sharing a leading or
   trailing token that qualifies the rest. Real schemas mark parties this way
   far more reliably than any vertical vocabulary, so this carries most of the
   weight and works on schemas no domain pack has seen.
2. **Role hints** (corroborating, pack-declared). A pack's ``roles:`` block
   names the parties its vertical expects and their ``kind``.

**The Rust kernel is the source of truth.** ``infermap-core::detect_identity_layers``
owns the scoring and decision; ``_layers_core_pure`` below is the byte-identical
classified fallback for environments without the compiled wheel — the same
smart-pipe / dumb-kernel split ``detect.py`` uses for ``detect_domain``. The
host's job is loading the domain pack and flattening it into plain lists; every
semantic choice lives in the kernel so the surfaces cannot drift.

``detect_domain`` behaviour is untouched — ``goldenpipe.stages.infer_schema``
depends on it.
"""
from __future__ import annotations

from goldencheck_types import (
    UNKNOWN_ROLE,
    DomainPack,
    IdentityLayer,
    LayerDetectionResult,
    load_domain,
)

from infermap._native_loader import native_enabled, native_module
from infermap.detect import DEFAULT_MIN_SCORE, _tokens, detect_domain

#: A qualifier shorter than this is noise (`f_`, `x_`), not a party name.
#: Mirrors ``infermap-core::MIN_QUALIFIER_LEN``.
_MIN_QUALIFIER_LEN = 3

#: Universal ATTRIBUTE tokens — they describe a property of an entity, never the
#: identity of one, in any vertical. **Mirror of ``infermap-core::ATTRIBUTE_TOKENS``**;
#: the kernel owns the list, this copy exists only for the pure fallback and must
#: not drift (``test_native_parity.py`` pins it).
#:
#: Load-bearing when no pack resolves (the unfamiliar-schema case the affix
#: signal exists to serve): without it, ``name`` groups ``widget_owner_name``
#: with ``shipper_name``, fusing two unrelated parties.
_ATTRIBUTE_TOKENS: frozenset[str] = frozenset(
    {
        "name", "names", "id", "ids", "key", "code", "codes", "num", "number",
        "date", "dt", "time", "ts", "timestamp", "year", "month", "day",
        "type", "status", "flag", "amount", "amt", "value", "val", "total",
        "count", "qty", "quantity", "desc", "description", "note", "notes",
        "address", "addr", "email", "phone", "city", "state", "zip", "country",
        "first", "last", "middle", "full", "line", "row", "col", "column",
        "created", "updated", "modified", "version", "source", "record",
    }
)

# Score weights — mirror of the kernel's W_* constants.
_W_BASE = 0.30
_W_AFFIX = 0.35
_W_ROLE = 0.25
_W_TYPES = 0.10


def detect_identity_layers(
    df,
    domain: str | None = None,
    min_score: float = DEFAULT_MIN_SCORE,
) -> LayerDetectionResult:
    """Detect the identity layers (parties) present in ``df``.

    Reads **column names only** — ``df`` is used solely for ``df.columns``,
    matching ``detect_domain``'s cost profile. Value-based disambiguation is a
    later wave.

    ``domain`` pins the domain pack; omitted, it is auto-detected via
    ``detect_domain``. When no pack resolves, affix clustering still runs —
    the primary signal is domain-free by design.

    Every candidate layer is returned, including ones scoring below
    ``min_score`` (marked ``reason="low_confidence"``) and unrecognised parties
    (``role=UNKNOWN_ROLE``). Nothing is silently dropped; consumers filter on
    ``score``. Columns in no candidate group land in ``unassigned``.
    """
    columns = [str(c) for c in df.columns]
    resolved_domain = domain if domain is not None else detect_domain(df)
    pack = _load_pack(resolved_domain)
    roles, type_hints = _pack_inputs(pack)

    raw_layers, unassigned = _layers_core(columns, roles, type_hints, min_score)

    layers = [
        IdentityLayer(
            role=role,
            kind=kind,
            columns=list(cols),
            score=score,
            reason=reason,
            evidence=_evidence(qualifier, positions, len(cols), role_matched, corroboration),
        )
        for (role, kind, cols, score, reason, qualifier, positions, role_matched, corroboration)
        in raw_layers
    ]
    return LayerDetectionResult(
        layers=layers, unassigned=list(unassigned), domain=resolved_domain
    )


def _evidence(
    qualifier: str,
    positions: list[str],
    n_columns: int,
    role_matched: bool,
    corroboration: float,
) -> dict:
    if not qualifier:
        # The whole-frame singleton fallback: there is no qualifier to report.
        return {"note": "no party qualifiers found; treating frame as one population"}
    return {
        "qualifier": qualifier,
        "positions": list(positions),
        "n_columns": n_columns,
        "role_matched": role_matched,
        "type_corroboration": corroboration,
    }


def _load_pack(domain: str | None) -> DomainPack | None:
    if not domain:
        return None
    try:
        return load_domain(domain)
    except (KeyError, FileNotFoundError):
        # An unknown domain name degrades to affix-only detection rather than
        # failing the call — the primary signal does not need a pack.
        return None


def _pack_inputs(
    pack: DomainPack | None,
) -> tuple[list[tuple[str, str, list[str], list[str]]], list[str]]:
    """Flatten a pack into the kernel's plain-list inputs.

    Host-side work by design: pack loading and ``typical_types`` resolution are
    host concerns, so the kernel never learns about YAML. Roles are emitted in
    pack-declaration order because the kernel resolves token collisions
    first-declaration-wins.
    """
    if pack is None:
        return [], []

    roles: list[tuple[str, str, list[str], list[str]]] = []
    for role in pack.roles.values():
        typical_hints: list[str] = []
        for type_name in role.typical_types:
            spec = pack.types.get(type_name)
            if spec is None:
                continue
            typical_hints.append(spec.name)
            typical_hints.extend(spec.name_hints)
        roles.append((role.name, role.kind, list(role.name_hints), typical_hints))

    type_hints: list[str] = []
    for spec in pack.types.values():
        type_hints.extend(spec.name_hints)
        type_hints.append(spec.name)
    return roles, type_hints


def _layers_core(
    columns: list[str],
    roles: list[tuple[str, str, list[str], list[str]]],
    type_hints: list[str],
    min_score: float,
):
    """Dispatch the layer scoring+decision to the native kernel when gated, else pure."""
    if native_enabled("detect_identity_layers"):
        return native_module().detect_identity_layers(columns, roles, type_hints, min_score)
    return _layers_core_pure(columns, roles, type_hints, min_score)


def _layers_core_pure(
    columns: list[str],
    roles: list[tuple[str, str, list[str], list[str]]],
    type_hints: list[str],
    min_score: float,
):
    """Byte-identical reference for ``infermap-core::detect_identity_layers``.

    Returns ``(layers, unassigned)`` where each layer is the 9-tuple
    ``(role, kind, columns, score, reason, qualifier, positions, role_matched,
    type_corroboration)``.

    Scores are UNROUNDED on purpose: ``round()`` differs between Python's
    banker's rounding, Rust's half-away-from-zero and JS ``Math.round``, so
    rounding here would manufacture a cross-language divergence.
    """
    if not columns:
        return [], []

    # token -> index into `roles`; first declaration wins.
    role_tokens: dict[str, int] = {}
    for i, (_name, _kind, name_hints, _typical) in enumerate(roles):
        for hint in name_hints:
            for tok in _tokens(hint):
                role_tokens.setdefault(tok, i)

    # Field-type tokens must not open a party (`account_number`/`account_id`
    # share `account`). ROLE DECLARATIONS WIN — finance lists `payee` among the
    # `merchant` type's hints while `payee` is also a declared role, and without
    # this precedence the explicit declaration loses to an incidental overlap.
    stop = set(_ATTRIBUTE_TOKENS)
    for hint in type_hints:
        stop.update(_tokens(hint))
    stop -= set(role_tokens)

    groups: dict[str, list[tuple[int, str, tuple[str, ...]]]] = {}
    for idx, col in enumerate(columns):
        for tok, position, remainder in _candidates(_tokens(col)):
            if len(tok) < _MIN_QUALIFIER_LEN or tok in stop:
                continue
            groups.setdefault(tok, []).append((idx, position, remainder))

    scored = []
    for token, members in groups.items():
        if not _group_is_viable(token, members, role_tokens):
            continue
        role_idx = role_tokens.get(token)
        role = roles[role_idx] if role_idx is not None else None
        corroboration = _type_corroboration(members, role)
        affix_strength = min(1.0, (len(members) - 1) / 2.0)
        score = min(
            1.0,
            _W_BASE
            + _W_AFFIX * affix_strength
            + (_W_ROLE if role is not None else 0.0)
            + _W_TYPES * corroboration,
        )
        scored.append((score, token, role, members, corroboration))

    scored.sort(key=lambda s: (-s[0], -len(s[3]), s[1]))

    layers = []
    claimed: set[int] = set()
    for score, token, role, members, corroboration in scored:
        kept = [m for m in members if m[0] not in claimed]
        if not kept:
            continue
        # Re-check viability after losing columns to a stronger layer.
        if len(kept) < 2 and token not in role_tokens:
            continue
        claimed.update(m[0] for m in kept)
        n = len(kept)
        affix_strength = min(1.0, (n - 1) / 2.0)
        layers.append(
            (
                role[0] if role else UNKNOWN_ROLE,
                role[1] if role else "unknown",
                [columns[m[0]] for m in kept],
                score,
                _reason(affix_strength, role is not None, score, min_score),
                token,
                sorted({m[1] for m in kept}),
                role is not None,
                corroboration,
            )
        )

    if not layers:
        # No party qualifiers anywhere. The honest reading is one homogeneous
        # population, not "no entities".
        return (
            [(UNKNOWN_ROLE, "unknown", list(columns), 0.5, "singleton", "", [], False, 0.0)],
            [],
        )

    layers.sort(key=lambda t: (-t[3], t[0], t[5]))
    assigned = {col for layer in layers for col in layer[2]}
    unassigned = [c for c in columns if c not in assigned]
    return layers, unassigned


def _candidates(tokens: list[str]) -> list[tuple[str, str, tuple[str, ...]]]:
    """Qualifier candidates for one column: ``(token, position, remainder)``.

    Leading and trailing tokens only — a party qualifier sits at one end in
    practice (``lender_name``, ``name_of_lender``); scanning interior tokens
    buys little and costs precision.
    """
    if not tokens:
        return []
    if len(tokens) == 1:
        # A column that IS just the qualifier (`bank`). Only ever accepted when
        # a role hint recognises it — see _group_is_viable.
        return [(tokens[0], "whole", ())]
    return [
        (tokens[0], "prefix", tuple(tokens[1:])),
        (tokens[-1], "suffix", tuple(tokens[:-1])),
    ]


def _group_is_viable(
    token: str,
    members: list[tuple[int, str, tuple[str, ...]]],
    role_tokens: dict[str, int],
) -> bool:
    """Reject groups that share a token without sharing a party.

    Single-column groups unless a role hint recognises the token (otherwise
    every column becomes its own layer), and trivial remainders —
    ``col_1``/``col_2``/``col_3`` share ``col`` but differ only by number: a
    table-wide prefix, not a party.
    """
    recognised = token in role_tokens
    if len(members) < 2:
        return recognised
    distinct = {
        remainder
        for _, _, remainder in members
        if remainder and not all(t.isdigit() for t in remainder)
    }
    return len(distinct) >= 2 or recognised


def _type_corroboration(
    members: list[tuple[int, str, tuple[str, ...]]],
    role: tuple[str, str, list[str], list[str]] | None,
) -> float:
    """Fraction of a group's columns whose remainder looks like a typical type.

    Corroboration only — a role with no ``typical_types``, or a layer whose
    fields are unusual, scores 0.0 here and is never vetoed for it.
    """
    if role is None or not role[3]:
        return 0.0
    expected: set[str] = set()
    for hint in role[3]:
        expected.update(_tokens(hint))
    if not expected:
        return 0.0
    hits = sum(1 for _, _, remainder in members if expected.intersection(remainder))
    return hits / len(members)


def _reason(
    affix_strength: float, role_matched: bool, score: float, min_score: float
) -> str:
    """Why this layer was proposed, or that it fell short.

    ``low_confidence`` overrides the evidence reason so a marginal layer is
    visible as marginal — it is still returned, with columns and evidence
    intact, rather than being dropped.
    """
    if score < min_score:
        return "low_confidence"
    if affix_strength > 0:
        return "affix+role_hint" if role_matched else "affix"
    return "role_hint" if role_matched else "singleton"


__all__ = ["detect_identity_layers"]
