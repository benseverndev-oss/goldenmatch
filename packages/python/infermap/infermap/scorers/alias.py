"""Alias scorer — matches fields that are known synonyms of each other."""
from __future__ import annotations

from infermap.types import FieldInfo, ScorerResult

ALIASES: dict[str, list[str]] = {
    "first_name": ["fname", "first", "given_name", "first_nm", "forename"],
    "last_name": ["lname", "last", "surname", "family_name", "last_nm"],
    "email": ["email_address", "e_mail", "email_addr", "mail", "contact_email"],
    "phone": ["phone_number", "ph", "telephone", "tel", "mobile", "cell"],
    "address": ["addr", "street_address", "addr_line_1", "address_line_1", "mailing_address"],
    "city": ["town", "municipality"],
    "state": ["st", "province", "region"],
    "zip": ["zipcode", "zip_code", "postal_code", "postal", "postcode"],
    "name": ["full_name", "fullname", "customer_name", "display_name", "contact_name"],
    "company": ["organization", "org", "business", "employer", "firm", "company_name"],
    "dob": ["date_of_birth", "birth_date", "birthdate", "birthday"],
    "country": ["nation", "country_code"],
    "gender": ["sex"],
    "id": ["identifier", "record_id", "uid"],
    "created_at": ["signup_date", "create_date", "date_created"],
}

# Build reverse lookup: every alias (and canonical) maps to its canonical key
_ALIAS_LOOKUP: dict[str, str] = {}
for _canonical, _aliases in ALIASES.items():
    _ALIAS_LOOKUP[_canonical] = _canonical
    for _alias in _aliases:
        _ALIAS_LOOKUP[_alias] = _canonical


def _get_canonical(name: str) -> str | None:
    """Return the canonical form of *name*, or None if not in the alias table."""
    return _ALIAS_LOOKUP.get(name.strip().lower())


class AliasScorer:
    """Scores 0.95 when both fields resolve to the same canonical name via the alias table."""

    name: str = "AliasScorer"
    weight: float = 0.95

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        src_name = source.name.strip().lower()
        tgt_name = target.name.strip().lower()

        src_canonical = _get_canonical(src_name)
        tgt_canonical = _get_canonical(tgt_name)

        # Check schema-file declared aliases on target
        declared_aliases: list[str] = target.metadata.get("aliases", [])
        declared_aliases_lower = [a.strip().lower() for a in declared_aliases]

        target_has_declared = bool(declared_aliases)

        # If source name is in target's declared aliases → strong match
        if src_name in declared_aliases_lower:
            return ScorerResult(
                score=0.95,
                reasoning=(
                    f"'{source.name}' matches declared alias of target '{target.name}'"
                ),
            )

        # If neither field has a known alias and target has no declared aliases → abstain
        if src_canonical is None and tgt_canonical is None and not target_has_declared:
            return None

        # Both resolve to the same canonical → match
        if src_canonical is not None and src_canonical == tgt_canonical:
            return ScorerResult(
                score=0.95,
                reasoning=(
                    f"'{source.name}' and '{target.name}' share canonical name '{src_canonical}'"
                ),
            )

        # Different canonicals (or one is unknown) → no match
        return ScorerResult(
            score=0.0,
            reasoning=(
                f"'{source.name}' (canonical={src_canonical}) and "
                f"'{target.name}' (canonical={tgt_canonical}) are different"
            ),
        )
