"""A row key (unique on every row) must not become negative evidence.

Auto-config promoted an `id` column to NE because its name scores as an
identity field. But a row key never agrees on any pair, so the penalty fired on
every pair, and a zero-config dedupe of 12 obvious customer records merged only
one duplicate pair out of four.
"""

import pyarrow as pa
from goldenmatch.config.schemas import GoldenMatchConfig, MatchkeyConfig, MatchkeyField
from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence
from goldenmatch.core.complexity_profile import ColumnPrior

# 12 records, 8 people: Maria x3, James/Jim x2, Priya x2, Chen/Wei x2, and
# three singletons. Thomas Becker and Thomas Baker share a zip but are different.
CUSTOMERS = pa.table(
    {
        "id": [str(i) for i in range(1, 13)],
        "name": [
            "Maria Gonzalez",
            "Maria Gonzales",
            "María González",
            "James O'Neil",
            "Jim O'Neill",
            "Priya Raman",
            "Priya Ramen",
            "Thomas Becker",
            "Aisha Bello",
            "Chen Wei",
            "Wei Chen",
            "Thomas Baker",
        ],
        "email": [
            "maria.gonzalez@example.com",
            "mgonzalez@example.com",
            "maria.gonzalez@example.com",
            "jim.oneil@example.com",
            "jim.oneil@example.com",
            "priya.raman@example.com",
            "p.raman@example.com",
            "tbecker@example.com",
            "aisha.bello@example.com",
            "chen.wei@example.com",
            "chen.wei@example.com",
            "thomas.baker@example.com",
        ],
        "phone": [
            "555-201-3344",
            "(555) 201-3344",
            "5552013344",
            "555-883-1200",
            "555.883.1200",
            "555-410-7788",
            "555-410-7788",
            "555-667-0192",
            "555-339-2471",
            "555-722-9031",
            "555-722-9031",
            "555-120-4455",
        ],
        "address": [
            "14 Oak Street",
            "14 Oak St",
            "14 Oak St.",
            "902 Pine Avenue",
            "902 Pine Ave",
            "3 Birch Road",
            "3 Birch Rd",
            "77 Elm Court",
            "510 Cedar Lane",
            "8 Maple Drive",
            "8 Maple Dr",
            "41 Walnut Way",
        ],
        "city": [
            "Springfield",
            "Springfield",
            "Springfield",
            "Riverton",
            "Riverton",
            "Lakeside",
            "Lakeside",
            "Fairview",
            "Greenville",
            "Oakdale",
            "Oakdale",
            "Fairview",
        ],
        "zip": [
            "62704",
            "62704",
            "62704",
            "84065",
            "84065",
            "49116",
            "49116",
            "97024",
            "29601",
            "95361",
            "95361",
            "97024",
        ],
    }
)
EXPECTED_GROUPS = {(0, 1, 2), (3, 4), (5, 6), (9, 10)}


def _config() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="exact_email",
                type="exact",
                threshold=None,
                fields=[MatchkeyField(field="email", transforms=[], scorer=None, weight=None)],
            )
        ]
    )


_PRIORS = {
    "id": ColumnPrior(identity_score=0.9, corruption_score=0.0),
    "phone": ColumnPrior(identity_score=0.85, corruption_score=0.0),
}


def _ne_fields(config: GoldenMatchConfig) -> set[str]:
    return {n.field for mk in config.matchkeys for n in (mk.negative_evidence or [])}


def test_row_key_is_not_promoted_on_the_full_frame():
    df = pa.table(
        {
            "id": [str(i) for i in range(10)],
            "email": ["a@x.com"] * 10,
            "phone": [f"555-{1000 + i}" for i in range(10)],
        }
    )
    fields = _ne_fields(promote_negative_evidence(_config(), df, _PRIORS))
    assert "id" not in fields
    # A contact field unique on every row is still promoted: it can agree
    # after normalisation, so its disagreement carries signal.
    assert "phone" in fields


def test_row_key_verdict_needs_the_full_frame():
    # On a sample, uniqueness cannot tell a row key from a real identifier,
    # so the historical promotion stands.
    df = pa.table({"id": [str(i) for i in range(10)], "email": ["a@x.com"] * 10})
    config = promote_negative_evidence(_config(), df, _PRIORS, full_frame=False)
    assert "id" in _ne_fields(config)


def test_zero_config_dedupe_finds_duplicates_despite_an_id_column():
    from goldenmatch import dedupe_df

    result = dedupe_df(CUSTOMERS)
    groups = {tuple(sorted(c["members"])) for c in result.clusters.values() if c["size"] > 1}
    assert groups == EXPECTED_GROUPS
