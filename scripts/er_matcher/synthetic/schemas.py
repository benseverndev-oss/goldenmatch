"""Synthetic domain schemas (crm_contact / organization / business).

Each :class:`Domain` declares a field set, a ``strong_id`` field (stays
distinct per real-world entity -- a same-name pair with a different
strong id is a true non-match), and a ``name_field`` (the name-bearing
field hard negatives collide on). ``build_record`` composes a plausible
record for a domain using only the injected ``rng`` (directly, or via
``vocab.sample_*`` which themselves take ``rng``), so output is
deterministic per seed.
"""

from __future__ import annotations

from dataclasses import dataclass

_COMPANY_SUFFIXES = ("Inc", "LLC", "Group", "Corp", "Co")

_JOB_TITLES = (
    "Sales Manager",
    "Account Executive",
    "Operations Director",
    "Marketing Specialist",
    "Customer Success Lead",
    "Product Manager",
    "Business Analyst",
    "Office Administrator",
)


@dataclass(frozen=True)
class Domain:
    fields: list[str]
    strong_id: str
    name_field: str


DOMAINS: dict[str, Domain] = {
    "crm_contact": Domain(
        fields=[
            "first",
            "last",
            "email",
            "phone",
            "company",
            "title",
            "street",
            "city",
            "state",
            "zip",
        ],
        strong_id="email",
        name_field="last",
    ),
    "organization": Domain(
        fields=["legal_name", "dba", "website_domain", "ein", "address"],
        strong_id="ein",
        name_field="legal_name",
    ),
    "business": Domain(
        fields=["name", "email", "phone", "city", "state", "website"],
        strong_id="website",
        name_field="name",
    ),
}


def _slugify(text: str) -> str:
    """Lowercase alnum-only slug (drops spaces/punctuation)."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _company_name(surname: str, rng) -> str:
    return f"{surname} {rng.choice(_COMPANY_SUFFIXES)}"


def _digits(rng, n: int) -> str:
    """Zero-padded seeded digit string of length ``n`` (e.g. phone/EIN)."""
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


def _build_crm_contact(vocab, rng) -> dict:
    first, last = vocab.sample_person_name(rng)
    company_surname, _ = vocab.sample_person_name(rng)
    company = _company_name(company_surname, rng)
    slug = _slugify(company)
    addr = vocab.sample_address(rng)
    return {
        "first": first,
        "last": last,
        "email": f"{first.lower()}.{last.lower()}@{slug}.com",
        "phone": _digits(rng, 10),
        "company": company,
        "title": rng.choice(_JOB_TITLES),
        "street": addr["street"],
        "city": addr["city"],
        "state": addr["state"],
        "zip": addr["zip"],
    }


def _build_organization(vocab, rng) -> dict:
    surname, _ = vocab.sample_person_name(rng)
    legal_name = _company_name(surname, rng)
    slug = _slugify(legal_name)
    addr = vocab.sample_address(rng)
    address = f"{addr['street']}, {addr['city']}, {addr['state']} {addr['zip']}"
    return {
        "legal_name": legal_name,
        "dba": surname,  # trading name variant: legal_name minus the entity suffix
        "website_domain": f"{slug}.com",
        "ein": _digits(rng, 9),
        "address": address,
    }


def _build_business(vocab, rng) -> dict:
    surname, _ = vocab.sample_person_name(rng)
    name = _company_name(surname, rng)
    slug = _slugify(name)
    addr = vocab.sample_address(rng)
    return {
        "name": name,
        "email": f"contact@{slug}.com",
        "phone": _digits(rng, 10),
        "city": addr["city"],
        "state": addr["state"],
        "website": f"https://{slug}.com",
    }


_BUILDERS = {
    "crm_contact": _build_crm_contact,
    "organization": _build_organization,
    "business": _build_business,
}


def build_record(domain: str, vocab, rng) -> dict:
    """Compose a plausible record for ``domain`` using ``vocab`` + ``rng``.

    Deterministic given ``rng``'s seed: every random draw goes through the
    injected ``rng`` (directly or via ``vocab.sample_*``).
    """
    return _BUILDERS[domain](vocab, rng)
