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

``detect_domain`` behaviour is untouched — ``goldenpipe.stages.infer_schema``
depends on it.
"""
from __future__ import annotations

from goldencheck_types import (
    UNKNOWN_ROLE,
    DomainPack,
    IdentityLayer,
    LayerDetectionResult,
    RoleSpec,
    load_domain,
)

from infermap.detect import DEFAULT_MIN_SCORE, _tokens, detect_domain

#: A qualifier shorter than this is noise (`f_`, `x_`), not a party name.
_MIN_QUALIFIER_LEN = 3

#: Universal ATTRIBUTE tokens — they describe a property of an entity, never
#: the identity of one, in any vertical.
#:
#: This is the domain-free half of the stop-list, and it is load-bearing: a
#: pack's ``name_hints`` suppress vertical-specific type tokens, but when no
#: pack resolves (the unfamiliar-schema case the affix signal exists to serve)
#: nothing would stop ``name`` from grouping ``widget_owner_name`` with
#: ``shipper_name`` — two unrelated parties fused on a shared attribute suffix.
#: Kept deliberately small: only tokens that are attributes in every domain.
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

# Score weights. Deliberately interpretable rather than tuned: a layer's score
# is "how much evidence backs this party", and each term is one kind of
# evidence. They sum to 1.0 at full strength.
_W_BASE = 0.30  # a real qualifier group exists at all
_W_AFFIX = 0.35  # how many columns back it
_W_ROLE = 0.25  # the pack recognises this party
_W_TYPES = 0.10  # the layer's fields look like the role's typical types


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

    if not columns:
        return LayerDetectionResult(layers=[], unassigned=[], domain=resolved_domain)

    role_tokens = _role_token_index(pack)
    stop_tokens = _stop_tokens(pack, role_tokens)

    groups = _candidate_groups(columns, stop_tokens, role_tokens)
    layers = _build_layers(groups, pack, role_tokens, min_score)

    if not layers:
        # No party qualifiers anywhere. The honest reading is one homogeneous
        # population, not "no entities" — a plain customer table is the common
        # case, not a degenerate one.
        return LayerDetectionResult(
            layers=[
                IdentityLayer(
                    role=UNKNOWN_ROLE,
                    kind="unknown",
                    columns=list(columns),
                    score=0.5,
                    reason="singleton",
                    evidence={"note": "no party qualifiers found; treating frame as one population"},
                )
            ],
            unassigned=[],
            domain=resolved_domain,
        )

    assigned = {col for layer in layers for col in layer.columns}
    unassigned = [c for c in columns if c not in assigned]
    return LayerDetectionResult(
        layers=layers, unassigned=unassigned, domain=resolved_domain
    )


def _load_pack(domain: str | None) -> DomainPack | None:
    if not domain:
        return None
    try:
        return load_domain(domain)
    except (KeyError, FileNotFoundError):
        # An unknown domain name degrades to affix-only detection rather than
        # failing the call — the primary signal does not need a pack.
        return None


def _role_token_index(pack: DomainPack | None) -> dict[str, RoleSpec]:
    """Map every token of every role hint -> that role.

    Multi-token hints index under each of their tokens; a qualifier is a single
    token by construction, so single-token indexing is what the matcher needs.
    First declaration wins on collision, keeping the result deterministic in
    pack order.
    """
    index: dict[str, RoleSpec] = {}
    if pack is None:
        return index
    for role in pack.roles.values():
        for hint in role.name_hints:
            for tok in _tokens(hint):
                index.setdefault(tok, role)
    return index


def _stop_tokens(pack: DomainPack | None, role_tokens: dict[str, RoleSpec]) -> set[str]:
    """Tokens that must not open a layer because they name a FIELD TYPE.

    ``account_number`` and ``account_id`` share ``account``, but ``account`` is
    a field-type token — it must not open an "account party". The pack's own
    ``name_hints`` supply the stop-list, so the guard sharpens as packs improve
    instead of needing a hand-maintained list of its own.

    **Role declarations win.** A token that a pack explicitly declares as a role
    hint is a party name even if some field type also mentions it — e.g.
    finance's ``merchant`` type lists ``payee`` among its hints while ``payee``
    is also a declared role. Without this precedence the explicit declaration
    would be silently overridden by an incidental hint overlap.

    The pack-derived half is unioned with :data:`_ATTRIBUTE_TOKENS`, which
    covers the no-pack case.
    """
    stop: set[str] = set(_ATTRIBUTE_TOKENS)
    if pack is not None:
        for spec in pack.types.values():
            for hint in spec.name_hints:
                stop.update(_tokens(hint))
            stop.update(_tokens(spec.name))
    return stop - set(role_tokens)


def _candidates(tokens: list[str]) -> list[tuple[str, str, tuple[str, ...]]]:
    """Qualifier candidates for one column: ``(token, position, remainder)``.

    Leading and trailing tokens only. A party qualifier sits at one end in
    practice (``lender_name``, ``name_of_lender``); scanning interior tokens
    buys little and costs precision.
    """
    if not tokens:
        return []
    if len(tokens) == 1:
        # A column that IS just the qualifier (`bank`). Only ever accepted as a
        # layer when a role hint recognises it — see _build_layers.
        return [(tokens[0], "whole", ())]
    return [
        (tokens[0], "prefix", tuple(tokens[1:])),
        (tokens[-1], "suffix", tuple(tokens[:-1])),
    ]


def _candidate_groups(
    columns: list[str],
    stop_tokens: set[str],
    role_tokens: dict[str, RoleSpec],
) -> dict[str, list[tuple[str, str, tuple[str, ...]]]]:
    """Group columns by shared qualifier token -> [(column, position, remainder)].

    Prefix and suffix uses of the same token merge deliberately:
    ``lender_name`` and ``name_of_lender`` are the same party.
    """
    groups: dict[str, list[tuple[str, str, tuple[str, ...]]]] = {}
    for col in columns:
        for tok, position, remainder in _candidates(_tokens(col)):
            if len(tok) < _MIN_QUALIFIER_LEN:
                continue
            if tok in stop_tokens:
                continue
            groups.setdefault(tok, []).append((col, position, remainder))
    return {
        tok: members
        for tok, members in groups.items()
        if _group_is_viable(tok, members, role_tokens)
    }


def _group_is_viable(
    token: str,
    members: list[tuple[str, str, tuple[str, ...]]],
    role_tokens: dict[str, RoleSpec],
) -> bool:
    """Reject groups that share a token without sharing a party.

    Two rejections, both earning their place:

    * **Single-column groups**, unless a role hint recognises the token —
      otherwise every column becomes its own layer.
    * **Trivial remainders.** ``col_1``/``col_2``/``col_3`` share ``col`` but
      their remainders are bare numbers: a table-wide prefix, not a party. A
      real party's columns differ by *what they say about it*
      (``customer_id``/``customer_name``), so we require at least two distinct
      non-numeric remainders.
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


def _build_layers(
    groups: dict[str, list[tuple[str, str, tuple[str, ...]]]],
    pack: DomainPack | None,
    role_tokens: dict[str, RoleSpec],
    min_score: float,
) -> list[IdentityLayer]:
    """Score candidate groups and assign each column to exactly one layer.

    A column can qualify for two groups (its leading and trailing token). It is
    awarded to the better-evidenced one; ties break on group size then token
    text, so the result is deterministic regardless of dict ordering.
    """
    scored: list[tuple[float, str, RoleSpec | None, list, float]] = []
    for token, members in groups.items():
        role = role_tokens.get(token)
        score, corroborated = _score_group(members, role, pack)
        scored.append((score, token, role, members, corroborated))

    scored.sort(key=lambda s: (-s[0], -len(s[3]), s[1]))

    layers: list[IdentityLayer] = []
    claimed: set[str] = set()
    for score, token, role, members, corroborated in scored:
        cols = [col for col, _, _ in members if col not in claimed]
        if not cols:
            continue
        # Re-check viability after losing columns to a stronger layer: a group
        # reduced to one unrecognised column is no longer evidence of a party.
        if len(cols) < 2 and token not in role_tokens:
            continue

        claimed.update(cols)
        n = len(cols)
        affix_strength = min(1.0, (n - 1) / 2.0)
        reason = _reason(affix_strength, role is not None, score, min_score)
        layers.append(
            IdentityLayer(
                role=role.name if role else UNKNOWN_ROLE,
                kind=role.kind if role else "unknown",
                columns=cols,
                score=round(score, 4),
                reason=reason,
                evidence={
                    "qualifier": token,
                    "positions": sorted({pos for c, pos, _ in members if c in cols}),
                    "n_columns": n,
                    "role_matched": role is not None,
                    "type_corroboration": round(corroborated, 4),
                },
            )
        )

    layers.sort(key=lambda layer: (-layer.score, layer.role, layer.evidence["qualifier"]))
    return layers


def _score_group(
    members: list[tuple[str, str, tuple[str, ...]]],
    role: RoleSpec | None,
    pack: DomainPack | None,
) -> tuple[float, float]:
    """Return ``(score, type_corroboration)`` for one candidate group."""
    n = len(members)
    affix_strength = min(1.0, (n - 1) / 2.0)
    corroborated = _type_corroboration(members, role, pack)
    score = (
        _W_BASE
        + _W_AFFIX * affix_strength
        + (_W_ROLE if role is not None else 0.0)
        + _W_TYPES * corroborated
    )
    return min(1.0, score), corroborated


def _type_corroboration(
    members: list[tuple[str, str, tuple[str, ...]]],
    role: RoleSpec | None,
    pack: DomainPack | None,
) -> float:
    """Fraction of a layer's columns whose remainder looks like a typical type.

    Corroboration only — a role with no ``typical_types``, or a layer whose
    fields are unusual, simply scores 0.0 here and is never vetoed for it.
    """
    if role is None or pack is None or not role.typical_types:
        return 0.0

    expected: set[str] = set()
    for type_name in role.typical_types:
        spec = pack.types.get(type_name)
        if spec is None:
            continue
        expected.update(_tokens(spec.name))
        for hint in spec.name_hints:
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
    visible as marginal — it is still returned, with its columns and evidence
    intact, rather than being dropped.
    """
    if score < min_score:
        return "low_confidence"
    if affix_strength > 0:
        return "affix+role_hint" if role_matched else "affix"
    return "role_hint" if role_matched else "singleton"


__all__ = ["detect_identity_layers"]
