"""Fixed corruption channels for synthetic duplicate generation (Phase 1b).

Each channel is ``fn(value: str, rng, *, rate) -> str``: pure, seeded only by
the injected ``rng`` (never global randomness), identity at ``rate=0.0``. Used
by :func:`corrupt_record` to turn an entity's own record into a realistic
"duplicate" -- the non-trivial variation that makes a positive match pair
interesting instead of a byte-identical copy.

``corrupt_record`` NEVER mutates ``strong_id``: that field is what still
identifies the record as the same entity (or, when compared against a
DIFFERENT entity that happens to share a name, is what makes the pair a true
negative). Every other field is corrupted independently and probabilistically
at the profile's base rate.
"""

from __future__ import annotations

import re

_NICKNAMES: dict[str, tuple[str, ...]] = {
    "robert": ("Rob", "Bob", "Bobby"),
    "william": ("Will", "Bill", "Billy"),
    "richard": ("Rich", "Rick", "Dick"),
    "james": ("Jim", "Jimmy", "Jamie"),
    "john": ("Jack", "Johnny"),
    "michael": ("Mike", "Mikey"),
    "elizabeth": ("Liz", "Beth", "Betty"),
    "margaret": ("Meg", "Maggie", "Peggy"),
    "katherine": ("Kate", "Katie", "Kathy"),
    "patricia": ("Pat", "Patty", "Trish"),
    "charles": ("Chuck", "Charlie"),
    "christopher": ("Chris", "Topher"),
    "joseph": ("Joe", "Joey"),
    "thomas": ("Tom", "Tommy"),
    "daniel": ("Dan", "Danny"),
    "matthew": ("Matt", "Matty"),
    "andrew": ("Andy", "Drew"),
    "anthony": ("Tony",),
    "jennifer": ("Jen", "Jenny"),
    "deborah": ("Deb", "Debbie"),
    "susan": ("Sue", "Susie"),
    "jonathan": ("Jon", "Jonny"),
    "samuel": ("Sam", "Sammy"),
    "benjamin": ("Ben", "Benny"),
}

_ALPHABET = "abcdefghijklmnopqrstuvwxyz"

PROFILES: dict[str, float] = {"light": 0.15, "heavy": 0.4}


def char_typo(value: str, rng, *, rate: float) -> str:
    """Insert/delete/swap/substitute one char with probability ``rate``.

    At ``rate=1.0`` the result is *guaranteed* different from ``value`` (for
    any non-empty input): insert/delete always change the length, and
    swap/substitute are steered away from picks that would silently no-op
    (swapping two identical adjacent chars, substituting a char with itself).
    """
    if not value or rng.random() >= rate:
        return value
    n = len(value)
    # delete/swap are no-ops on a single-char string; exclude them there.
    ops = ("insert", "delete", "swap", "substitute") if n > 1 else ("insert", "substitute")
    op = rng.choice(ops)
    if op == "insert":
        pos = rng.randint(0, n)
        ch = rng.choice(_ALPHABET)
        return value[:pos] + ch + value[pos:]
    if op == "delete":
        pos = rng.randint(0, n - 1)
        return value[:pos] + value[pos + 1 :]
    if op == "swap":
        # Prefer an adjacent pair that actually differs so the swap is
        # visible; fall back to substitute if every adjacent pair is equal
        # (e.g. "aaaa").
        candidates = [i for i in range(n - 1) if value[i] != value[i + 1]]
        if not candidates:
            return _substitute(value, rng)
        pos = rng.choice(candidates)
        chars = list(value)
        chars[pos], chars[pos + 1] = chars[pos + 1], chars[pos]
        return "".join(chars)
    # substitute
    return _substitute(value, rng)


def _substitute(value: str, rng) -> str:
    """Replace one char with a different randomly-chosen lowercase letter."""
    pos = rng.randint(0, len(value) - 1)
    original = value[pos]
    choices = [c for c in _ALPHABET if c != original.lower()]
    ch = rng.choice(choices)
    return value[:pos] + ch + value[pos + 1 :]


def case_ws(value: str, rng, *, rate: float) -> str:
    """Random case flip on one char, or a stray inserted/doubled whitespace.

    At ``rate=1.0`` the result is guaranteed different from ``value`` (for any
    non-empty input): the "case" op only considers alpha positions (flipping
    a space/digit/punctuation is a silent no-op), falling back to the
    whitespace op -- which always changes length -- when there are none.
    """
    if not value or rng.random() >= rate:
        return value
    alpha_positions = [i for i, ch in enumerate(value) if ch.isalpha()]
    op = rng.choice(("case", "whitespace")) if alpha_positions else "whitespace"
    if op == "case":
        pos = rng.choice(alpha_positions)
        ch = value[pos]
        flipped = ch.lower() if ch.isupper() else ch.upper()
        return value[:pos] + flipped + value[pos + 1 :]
    # whitespace: double an existing space, or pad an edge
    if " " in value:
        pos = value.index(" ")
        return value[:pos] + "  " + value[pos + 1 :]
    return value + " "


def nickname(value: str, rng, *, rate: float) -> str:
    """Map a known formal first name to a random nickname; identity if unknown."""
    if rng.random() >= rate:
        return value
    options = _NICKNAMES.get(value.lower())
    if not options:
        return value
    return rng.choice(options)


def token_drop(value: str, rng, *, rate: float) -> str:
    """Drop one random whitespace-separated token from a multi-token string."""
    if rng.random() >= rate:
        return value
    tokens = value.split()
    if len(tokens) <= 1:
        return value
    idx = rng.randint(0, len(tokens) - 1)
    del tokens[idx]
    return " ".join(tokens)


def format_variant(value: str, rng, *, rate: float, kind: str) -> str:
    """Reformat ``value`` to an equivalent representation, keyed by ``kind``.

    ``kind`` in {"phone", "email", "address"}. Identity for unknown kinds or
    values that don't match the expected shape.
    """
    if rng.random() >= rate:
        return value
    if kind == "phone":
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) != 10:
            return value
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
    if kind == "email":
        if "@" not in value:
            return value
        local, _, domain = value.partition("@")
        variant = rng.choice(("lower", "dot"))
        if variant == "lower":
            return f"{local.lower()}@{domain.lower()}"
        # dot-variant: replace a "." separator with "_" (common alias style)
        if "." in local:
            return f"{local.replace('.', '_', 1)}@{domain}"
        return value
    if kind == "address":
        return _address_variant(value, rng)
    return value


# Whole-word street-suffix pairs, either direction abbreviates/expands the
# OTHER member. Matched anywhere in the string (mid-token, not just at the
# end) so compound comma-joined addresses like "476 River Dr, Burlington, VT
# 05483" get corrupted too, not just a bare "123 Main St".
_STREET_SUFFIX_PAIRS = (
    ("Street", "St"),
    ("Avenue", "Ave"),
    ("Boulevard", "Blvd"),
    ("Drive", "Dr"),
    ("Lane", "Ln"),
    ("Road", "Rd"),
)


def _address_variant(value: str, rng) -> str:
    """Abbreviate/expand one whole-word street-suffix token anywhere in ``value``.

    Identity if no suffix token is present (e.g. a bare city name).
    """
    candidates: list[tuple[int, int, str]] = []
    for full, abbr in _STREET_SUFFIX_PAIRS:
        for token, counterpart in ((full, abbr), (abbr, full)):
            for m in re.finditer(rf"\b{re.escape(token)}\b", value):
                candidates.append((m.start(), m.end(), counterpart))
    if not candidates:
        return value
    start, end, counterpart = rng.choice(candidates)
    return value[:start] + counterpart + value[end:]


# Per-field channel plan: which channels apply, and how each field maps to a
# format_variant "kind" hint (fields not listed here get no format_variant).
# Person names (given names) are the only fields nickname() can ever match --
# company names ("Smith LLC") never hit the formal->nickname dict, so routing
# nickname() at them was pure dead code.
_PERSON_NAME_FIELDS = frozenset({"first", "last"})
_COMPANY_NAME_FIELDS = frozenset({"name", "legal_name", "dba"})
_PHONE_FIELDS = frozenset({"phone"})
_EMAIL_FIELDS = frozenset({"email"})
# city has no street suffix, so format_variant(kind="address") only applies
# to street/address; case_ws + token_drop still apply to all three.
_ADDRESS_FIELDS = frozenset({"street", "address", "city"})
_ADDRESS_FORMAT_FIELDS = frozenset({"street", "address"})


def corrupt_record(rec: dict, *, strong_id: str, rng, profile: str) -> dict:
    """Return a corrupted copy of ``rec`` with ``strong_id`` left untouched.

    Deterministic given ``rng``: every draw goes through the injected rng, so
    the same ``rng`` seed + ``profile`` always yields the same output.
    """
    rate = PROFILES[profile]
    out = dict(rec)
    for field, value in rec.items():
        if field == strong_id or not isinstance(value, str):
            continue

        if field in _PERSON_NAME_FIELDS:
            value = nickname(value, rng, rate=rate)
            value = char_typo(value, rng, rate=rate)
            value = case_ws(value, rng, rate=rate)
        elif field in _COMPANY_NAME_FIELDS:
            value = char_typo(value, rng, rate=rate)
            value = case_ws(value, rng, rate=rate)
        elif field in _PHONE_FIELDS:
            value = format_variant(value, rng, rate=rate, kind="phone")
            value = char_typo(value, rng, rate=rate)
        elif field in _EMAIL_FIELDS:
            value = format_variant(value, rng, rate=rate, kind="email")
            value = char_typo(value, rng, rate=rate)
        elif field in _ADDRESS_FIELDS:
            value = token_drop(value, rng, rate=rate)
            if field in _ADDRESS_FORMAT_FIELDS:
                value = format_variant(value, rng, rate=rate, kind="address")
            value = case_ws(value, rng, rate=rate)
        else:
            value = char_typo(value, rng, rate=rate)
            value = case_ws(value, rng, rate=rate)

        out[field] = value

    return out
