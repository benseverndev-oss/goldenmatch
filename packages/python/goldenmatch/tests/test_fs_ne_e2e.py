"""Task N5: the FS negative-evidence homonym success bar (E2E, default backend).

Spec: docs/superpowers/specs/2026-07-14-fs-negative-evidence-design.md ("Testing /
success bar")
Plan: docs/superpowers/plans/2026-07-14-fs-negative-evidence.md (Task N5)

This is the deterministic end-to-end test the whole feature exists to satisfy: a
Fellegi-Sunter (probabilistic) matchkey on agreeing name/city evidence merges two
DISTINCT people who happen to share a name and a city (the "homonym trap" /
fan-out failure mode) unless a hard-disagreeing field (phone) is wired in as
negative evidence. Both the EM-learned path and the fixed ``penalty_bits``
override must kill the trap while leaving genuine duplicates merged.

Run via ``dedupe_df`` on the DEFAULT backend (no ``backend=`` override) with
native ENABLED (no ``GOLDENMATCH_NATIVE=0``) -- the N4 native/fused/fast guards
decline NE-bearing matchkeys, so this exercises the real capability-gate
fallback rather than an env override standing in for it, and pins the N3
bucket-backend slim-projection fix (an NE-only field like ``phone`` must
survive ``GOLDENMATCH_BUCKET_SLIM_PROJECTION``, which defaults on).

Fixture design (why Config A genuinely merges the homonyms -- the delta this
test demonstrates, not a vacuous pass):

- 12 surnames, spread across distinct soundex codes (project fixture rule --
  see ``feedback_synthetic_surname_fixtures``) so blocking on ``last_name``
  produces one block per surname without an all-in-one-block blocking hang.
  Each block holds 4 distinct identities (3 true-duplicate pairs + 1 homonym
  trap), so within-block EM pair sampling sees genuine disagreement examples
  (different identities sharing a surname) alongside the agreement examples --
  without that variety, ``first_name``/``city`` saturate to m ~= 1 / u ~= 0 and
  swamp the EM-learned NE contribution in normalized [0, 1] space (verified
  empirically while building this fixture: a 2-row-per-block version produces
  extreme per-field weights that dwarf a real, correctly-signed NE veto).
- First names and cities are drawn from large (60-entry) real-word pools with
  no modulo-driven reuse across unrelated identities, so ``u`` (the
  random-pair, non-match rate) for those fields reflects genuine low
  incidental agreement -- small/reused pools inflate ``u`` and invert the
  field's discriminative power.
- True-duplicate pairs: same surname/city/phone, first name has a trailing
  single-character corruption (``"Jonathan"`` vs ``"Jonathanx"``) that still
  clears the ``jaro_winkler`` 0.8 partial threshold -- a realistic "same
  person, slightly different capture" pair.
- Homonym-trap pairs: same surname/first name/city (a genuine name+city
  collision between two DIFFERENT people) but a DIFFERENT phone number.
- ``link_threshold=0.98``: chosen because in this fixture, without NE, BOTH
  the true-duplicate and homonym-trap pairs normalize to 1.0 (both fields
  fully agree either way) -- there is no threshold that would separate them
  without NE, which is exactly the failure mode. With NE wired in (EM-learned
  or ``penalty_bits``), the true-duplicate pairs still normalize to 1.0 (phone
  agrees, NE never fires) while the homonym-trap pairs drop measurably below
  1.0 (phone disagrees, NE fires) -- 0.98 sits strictly between the two,
  demonstrating the same matchkey (all fields, blocking, threshold identical)
  flips behavior on the trap pairs purely from the ``negative_evidence`` block.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
import yaml
from goldenmatch.config.loader import load_config
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    NegativeEvidenceField,
)

SURNAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Garcia", "Nguyen", "Okafor", "Petrov",
    "Alvarez", "Kowalski", "Haddad", "Kim",
]
CITIES = [
    "Boston", "Denver", "Austin", "Seattle", "Phoenix", "Atlanta", "Portland", "Chicago",
    "Miami", "Dallas", "Newark", "Tampa", "Reno", "Salem", "Fresno", "Toledo", "Akron",
    "Eugene", "Boise", "Tulsa", "Naples", "Modesto", "Waco", "Provo", "Yuma", "Erie",
    "Wichita", "Duluth", "Macon", "Biloxi", "Laredo", "Odessa", "Bangor", "Casper",
    "Sheridan", "Helena", "Butte", "Pierre", "Fargo", "Rapid", "Topeka", "Salina", "Hays",
    "Enid", "Ardmore", "Muskogee", "Shawnee", "Ponca", "Guymon", "Woodward", "Alva",
    "Clinton", "Elgin", "Chandler", "Coalgate", "Wagoner", "Vinita", "Sallisaw", "Poteau",
    "Idabel",
]
FIRST_NAMES = [
    "Jonathan", "Michael", "Robert", "Susan", "Karen", "David", "Linda", "Steven", "Nancy",
    "Brian", "Patricia", "Kevin", "Laura", "Edward", "Diane", "Gregory", "Sandra", "Peter",
    "Carol", "Frank", "Deborah", "Raymond", "Cynthia", "Jack", "Amy", "Dennis", "Angela",
    "Jerry", "Melissa", "Tyler", "Brenda", "Aaron", "Emma", "Henry", "Julie", "Adam",
    "Joyce", "Douglas", "Virginia", "Nathan", "Victoria", "Zachary", "Kelly", "Kyle",
    "Christina", "Walter", "Joan", "Harold", "Evelyn", "Carl", "Judith", "Arthur", "Megan",
    "Roger", "Cheryl", "Keith", "Andrea", "Willie", "Hannah", "Roy",
]

LINK_THRESHOLD = 0.98
DUP_GROUPS_PER_SURNAME = 3


def _build_fixture() -> tuple[pl.DataFrame, list[tuple[int, int]], list[tuple[int, int]]]:
    """~96-row fixture: per surname, 3 true-duplicate pairs + 1 homonym trap.

    Returns (df, dup_row_id_pairs, homonym_row_id_pairs) where the pair ids
    are 0-based positional row indices matching ``dedupe_df``'s cluster
    ``members`` convention (see tests/test_exact_blank_exclusion.py for the
    same convention).
    """
    rows: list[dict] = []
    row_id = 0
    name_idx = 0
    dup_pairs: list[tuple[int, int]] = []
    homonym_pairs: list[tuple[int, int]] = []

    for surname in SURNAMES:
        for _ in range(DUP_GROUPS_PER_SURNAME):
            fn = FIRST_NAMES[name_idx % len(FIRST_NAMES)]
            name_idx += 1
            city = CITIES[name_idx % len(CITIES)]
            phone = f"555{1_000_000 + row_id:07d}"
            r1, r2 = row_id, row_id + 1
            row_id += 2
            rows.append({"first_name": fn, "last_name": surname, "city": city, "phone": phone})
            rows.append({"first_name": fn + "x", "last_name": surname, "city": city, "phone": phone})
            dup_pairs.append((r1, r2))

        # Homonym trap: same first_name + surname + city, DIFFERENT phone.
        fn = FIRST_NAMES[name_idx % len(FIRST_NAMES)]
        name_idx += 1
        city = CITIES[name_idx % len(CITIES)]
        phone_a = f"555{2_000_000 + row_id:07d}"
        phone_b = f"555{2_000_000 + row_id + 1:07d}"
        r1, r2 = row_id, row_id + 1
        row_id += 2
        rows.append({"first_name": fn, "last_name": surname, "city": city, "phone": phone_a})
        rows.append({"first_name": fn, "last_name": surname, "city": city, "phone": phone_b})
        homonym_pairs.append((r1, r2))

    return pl.DataFrame(rows), dup_pairs, homonym_pairs


def _fields() -> list[MatchkeyField]:
    return [
        MatchkeyField(field="first_name", scorer="jaro_winkler", levels=2, partial_threshold=0.8),
        MatchkeyField(field="city", scorer="exact", levels=2),
    ]


def _blocking() -> BlockingConfig:
    return BlockingConfig(
        strategy="static", keys=[BlockingKeyConfig(fields=["last_name"])],
        max_block_size=1000, skip_oversized=False,
    )


def _id_to_cluster(res) -> dict[int, int]:
    """Build a precise row-id -> cluster-id map from a DedupeResult."""
    clusters = res.clusters
    items = clusters.items() if isinstance(clusters, dict) else enumerate(clusters)
    mapping: dict[int, int] = {}
    for cid, info in items:
        members = info["members"] if isinstance(info, dict) else info
        for member_id in members:
            mapping[member_id] = cid
    return mapping


def _same_cluster(mapping: dict[int, int], a: int, b: int) -> bool:
    return a in mapping and b in mapping and mapping[a] == mapping[b]


class TestControlConfigADemonstratesTheFailure:
    """Without NE, the homonym trap genuinely merges -- the delta baseline."""

    def test_homonyms_merge_and_duplicates_merge(self):
        import goldenmatch as gm

        df, dup_pairs, homonym_pairs = _build_fixture()
        config = GoldenMatchConfig(
            matchkeys=[MatchkeyConfig(
                name="fs", type="probabilistic", fields=_fields(), link_threshold=LINK_THRESHOLD,
            )],
            blocking=_blocking(),
        )
        result = gm.dedupe_df(df, config=config)
        mapping = _id_to_cluster(result)

        for a, b in dup_pairs:
            assert _same_cluster(mapping, a, b), f"true duplicate pair {(a, b)} failed to merge"
        for a, b in homonym_pairs:
            assert _same_cluster(mapping, a, b), (
                f"homonym trap {(a, b)} did not merge under Config A -- fixture no longer "
                "demonstrates the failure this feature exists to fix; strengthen the "
                "shared evidence"
            )


class TestNegativeEvidenceSeparatesHomonyms:
    """Config B (EM-learned) and Config C (penalty_bits) both fix the trap."""

    @pytest.mark.parametrize(
        "ne_field",
        [
            pytest.param(
                NegativeEvidenceField(field="phone", scorer="exact", threshold=1.0),
                id="em-learned",
            ),
            pytest.param(
                NegativeEvidenceField(field="phone", scorer="exact", threshold=1.0, penalty_bits=6.0),
                id="penalty-bits",
            ),
        ],
    )
    def test_homonyms_separate_duplicates_still_merge(self, ne_field):
        import goldenmatch as gm

        df, dup_pairs, homonym_pairs = _build_fixture()
        config = GoldenMatchConfig(
            matchkeys=[MatchkeyConfig(
                name="fs", type="probabilistic", fields=_fields(),
                negative_evidence=[ne_field], link_threshold=LINK_THRESHOLD,
            )],
            blocking=_blocking(),
        )
        result = gm.dedupe_df(df, config=config)
        mapping = _id_to_cluster(result)

        for a, b in dup_pairs:
            assert _same_cluster(mapping, a, b), (
                f"true duplicate pair {(a, b)} STOPPED merging once NE was added -- "
                "NE fired on a genuine duplicate (should never happen: phone agrees)"
            )
        for a, b in homonym_pairs:
            assert not _same_cluster(mapping, a, b), (
                f"homonym trap {(a, b)} still merged with NE wired in -- "
                "the fan-out defense this feature exists to provide did not fire"
            )


class TestYamlRoundTripPreservesNegativeEvidence:
    """The EM-learned NE config surface round-trips through YAML (dump ->
    load_config) and still produces the fixed behavior end-to-end."""

    def test_ne_config_round_trips_and_still_separates_homonyms(self, tmp_path: Path):
        import goldenmatch as gm

        df, dup_pairs, homonym_pairs = _build_fixture()
        config = GoldenMatchConfig(
            matchkeys=[MatchkeyConfig(
                name="fs", type="probabilistic", fields=_fields(),
                negative_evidence=[
                    NegativeEvidenceField(field="phone", scorer="exact", threshold=1.0),
                ],
                link_threshold=LINK_THRESHOLD,
            )],
            blocking=_blocking(),
        )

        yaml_path = tmp_path / "fs_ne_config.yml"
        yaml_path.write_text(yaml.safe_dump(config.model_dump(exclude_none=True)), encoding="utf-8")

        reloaded = load_config(str(yaml_path))
        reloaded_mk = reloaded.matchkeys[0]
        assert reloaded_mk.negative_evidence is not None
        assert reloaded_mk.negative_evidence[0].field == "phone"
        assert reloaded_mk.negative_evidence[0].penalty_bits is None  # EM-learned, not fixed

        result = gm.dedupe_df(df, config=reloaded)
        mapping = _id_to_cluster(result)

        for a, b in dup_pairs:
            assert _same_cluster(mapping, a, b)
        for a, b in homonym_pairs:
            assert not _same_cluster(mapping, a, b)
