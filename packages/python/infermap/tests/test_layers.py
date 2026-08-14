"""Identity-layer detection — hand-labelled corpus + the guards that bound it.

The corpus is the spec's evaluation set: multi-party schemas with known
answers, single-party controls (the common case must stay clean), and
adversarial shapes that a naive "shared token = party" rule gets wrong.
"""
from __future__ import annotations

import textwrap
from types import SimpleNamespace

import pytest
from goldencheck_types import SCHEMA_VERSION, UNKNOWN_ROLE, DomainPackError, clear_cache
from infermap.detect import detect_domain_detailed
from infermap.layers import detect_identity_layers


def frame(columns: list[str]):
    """Minimal stand-in for a DataFrame.

    Detection reads ``df.columns`` and nothing else, so the tests do not need
    polars — and avoid its documented import hang under parallel load.
    """
    return SimpleNamespace(columns=columns)


def layer_for(result, role: str):
    matches = [layer for layer in result.layers if layer.role == role]
    assert matches, f"no layer with role {role!r}; got {[layer.role for layer in result.layers]}"
    return matches[0]


# ── The hand-labelled multi-party corpus ──────────────────────────────────

LOAN_TAPE = [
    "loan_id", "origination_date",
    "lender_name", "lender_id", "lender_address",
    "borrower_name", "borrower_ssn", "borrower_dob",
]

CLAIMS = [
    "claim_id",
    "patient_name", "patient_dob",
    "provider_npi", "provider_name",
    "payer_name", "payer_id",
]

TELEMETRY = [
    "reading_ts",
    "machine_id", "machine_model", "machine_serial",
    "operator_name", "operator_badge",
    "plant_code",
]


def test_loan_tape_separates_lender_from_borrower():
    """The load-bearing case: two parties in one frame, never to be co-deduped."""
    result = detect_identity_layers(frame(LOAN_TAPE), domain="finance")

    lender = layer_for(result, "lender")
    assert lender.kind == "organization"
    assert set(lender.columns) == {"lender_name", "lender_id", "lender_address"}

    borrower = layer_for(result, "borrower")
    assert borrower.kind == "person"
    assert set(borrower.columns) == {"borrower_name", "borrower_ssn", "borrower_dob"}

    # Record-level attributes belong to no party.
    assert set(result.unassigned) == {"loan_id", "origination_date"}


def test_claims_detects_three_parties_with_distinct_kinds():
    result = detect_identity_layers(frame(CLAIMS), domain="healthcare")

    assert layer_for(result, "patient").kind == "person"
    assert layer_for(result, "provider").kind == "person"
    assert layer_for(result, "payer").kind == "organization"
    assert set(layer_for(result, "payer").columns) == {"payer_name", "payer_id"}


def test_telemetry_detects_machine_asset_and_place_kinds():
    """Non-person entities are first-class: an asset and a place, not just people."""
    result = detect_identity_layers(frame(TELEMETRY), domain="manufacturing")

    machine = layer_for(result, "machine")
    assert machine.kind == "asset"
    assert set(machine.columns) == {"machine_id", "machine_model", "machine_serial"}

    assert layer_for(result, "operator").kind == "person"
    assert layer_for(result, "plant").kind == "place"


def test_suffix_qualifiers_group_with_prefix_ones():
    """`name_of_lender` and `lender_name` are the same party."""
    result = detect_identity_layers(
        frame(["name_of_lender", "id_of_lender", "borrower_name", "borrower_ssn"]),
        domain="finance",
    )
    assert set(layer_for(result, "lender").columns) == {"name_of_lender", "id_of_lender"}
    assert set(layer_for(result, "borrower").columns) == {"borrower_name", "borrower_ssn"}


# ── Single-party controls: the common case must stay clean ────────────────

@pytest.mark.parametrize(
    "columns",
    [
        ["id", "name", "email", "city"],
        ["customer_id", "customer_name", "customer_email"],
    ],
    ids=["bare-attributes", "uniform-qualifier"],
)
def test_single_population_yields_exactly_one_layer(columns):
    result = detect_identity_layers(frame(columns))
    assert len(result.layers) == 1
    assert set(result.layers[0].columns) == set(columns)
    assert result.unassigned == []


def test_empty_frame_yields_no_layers():
    result = detect_identity_layers(frame([]))
    assert result.layers == []
    assert result.unassigned == []


# ── Adversarial shapes ────────────────────────────────────────────────────

def test_field_type_tokens_do_not_open_a_party():
    """`account_number`/`account_id` share `account` — a FIELD type, not a party."""
    result = detect_identity_layers(
        frame(["account_number", "account_id", "txn_amount"]), domain="finance"
    )
    assert [layer.role for layer in result.layers] == [UNKNOWN_ROLE]
    assert result.layers[0].reason == "singleton"


def test_table_wide_numeric_prefix_is_not_a_party():
    """`col_1`/`col_2`/`col_3` share a token but differ only by number."""
    result = detect_identity_layers(frame(["col_1", "col_2", "col_3"]))
    assert len(result.layers) == 1
    assert result.layers[0].reason == "singleton"


def test_shared_attribute_suffix_does_not_fuse_unrelated_parties():
    """Regression: `name` is an attribute, so it must not group two parties.

    Without the domain-free attribute stop-list this fused `widget_owner_name`
    with `shipper_name` into one bogus layer while stranding their siblings —
    and no domain pack is loaded here, so nothing else would have caught it.
    """
    result = detect_identity_layers(
        frame(["widget_owner_name", "widget_owner_id", "shipper_name", "shipper_code"])
    )
    grouped = {frozenset(layer.columns) for layer in result.layers}
    assert grouped == {
        frozenset({"widget_owner_name", "widget_owner_id"}),
        frozenset({"shipper_name", "shipper_code"}),
    }


def test_role_hints_override_the_field_type_stop_list():
    """`payee` is a hint of finance's `merchant` TYPE and a declared ROLE.

    An explicit role declaration must win, otherwise an incidental hint overlap
    silently suppresses a party the pack went out of its way to name.
    """
    result = detect_identity_layers(
        frame(["payee_name", "payee_account", "payor_name", "payor_account"]),
        domain="finance",
    )
    assert {layer.role for layer in result.layers} == {"payee", "payor"}


# ── Contract invariants ───────────────────────────────────────────────────

ALL_CORPUS = [
    (LOAN_TAPE, "finance"),
    (CLAIMS, "healthcare"),
    (TELEMETRY, "manufacturing"),
    (["id", "name", "email"], None),
    (["account_number", "account_id"], "finance"),
]


@pytest.mark.parametrize("columns,domain", ALL_CORPUS)
def test_every_column_is_accounted_for_exactly_once(columns, domain):
    """No column is dropped, and none lands in two layers."""
    result = detect_identity_layers(frame(columns), domain=domain)

    assigned = [col for layer in result.layers for col in layer.columns]
    assert len(assigned) == len(set(assigned)), "a column was assigned to two layers"
    assert set(assigned) | set(result.unassigned) == set(columns)
    assert not set(assigned) & set(result.unassigned)


@pytest.mark.parametrize("columns,domain", ALL_CORPUS)
def test_detection_is_deterministic(columns, domain):
    a = detect_identity_layers(frame(columns), domain=domain)
    b = detect_identity_layers(frame(columns), domain=domain)
    assert [(x.role, x.columns, x.score) for x in a.layers] == [
        (y.role, y.columns, y.score) for y in b.layers
    ]


def test_column_order_does_not_change_the_partition():
    """Layer membership must not depend on the order columns arrive in."""
    forward = detect_identity_layers(frame(LOAN_TAPE), domain="finance")
    reverse = detect_identity_layers(frame(list(reversed(LOAN_TAPE))), domain="finance")
    assert {frozenset(x.columns) for x in forward.layers} == {
        frozenset(y.columns) for y in reverse.layers
    }


@pytest.mark.parametrize("columns,domain", ALL_CORPUS)
def test_reported_kinds_and_reasons_are_in_the_declared_vocabularies(columns, domain):
    from goldencheck_types import IDENTITY_KINDS, LAYER_REASONS

    result = detect_identity_layers(frame(columns), domain=domain)
    for layer in result.layers:
        assert layer.kind in IDENTITY_KINDS
        assert layer.reason in LAYER_REASONS
        assert 0.0 <= layer.score <= 1.0


def test_unknown_domain_degrades_to_affix_only_rather_than_raising():
    result = detect_identity_layers(frame(LOAN_TAPE), domain="not_a_real_domain")
    # Parties are still found by the domain-free signal; they are just unnamed.
    assert len(result.layers) == 2
    assert {layer.role for layer in result.layers} == {UNKNOWN_ROLE}


def test_result_stamps_the_wire_schema_version():
    result = detect_identity_layers(frame(LOAN_TAPE), domain="finance")
    assert result.schema_version == SCHEMA_VERSION


# ── detect_domain must not move ───────────────────────────────────────────

@pytest.mark.parametrize("columns", [LOAN_TAPE, CLAIMS, TELEMETRY, ["id", "name"]])
def test_detect_domain_behaviour_is_unchanged_by_the_roles_block(columns):
    """`roles:` is additive — goldenpipe.infer_schema depends on this path."""
    result = detect_domain_detailed(frame(columns))
    assert result.reason in {"confident", "tie", "below_min_score", "no_data"}
    # Scores are a coverage fraction over columns; adding roles must not alter it.
    assert 0.0 <= result.score <= 1.0


# ── Domain-pack `roles:` loading ──────────────────────────────────────────

def write_pack(tmp_path, body: str):
    (tmp_path / "testdom.yaml").write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


@pytest.fixture
def pack_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENCHECK_TYPES_TEST_DIR", str(tmp_path))
    clear_cache()
    yield tmp_path
    clear_cache()


def test_roles_block_is_optional(pack_dir):
    from goldencheck_types import load_domain

    write_pack(pack_dir, """
        description: "no roles here"
        types:
          widget:
            name_hints: ["widget"]
    """)
    assert load_domain("testdom").roles == {}


def test_roles_block_parses_and_populates_name_from_key(pack_dir):
    from goldencheck_types import load_domain

    write_pack(pack_dir, """
        description: "with roles"
        types:
          widget:
            name_hints: ["widget"]
        roles:
          supplier:
            kind: organization
            name_hints: ["supplier", "vendor"]
            typical_types: ["widget"]
    """)
    role = load_domain("testdom").roles["supplier"]
    assert role.name == "supplier"
    assert role.kind == "organization"
    assert role.typical_types == ["widget"]


def test_unknown_kind_is_rejected_loudly(pack_dir):
    from goldencheck_types import load_domain

    write_pack(pack_dir, """
        description: "bad kind"
        types: {}
        roles:
          supplier:
            kind: organisation
            name_hints: ["supplier"]
    """)
    with pytest.raises(DomainPackError, match="kind"):
        load_domain("testdom")


def test_missing_kind_is_rejected(pack_dir):
    from goldencheck_types import load_domain

    write_pack(pack_dir, """
        description: "no kind"
        types: {}
        roles:
          supplier:
            name_hints: ["supplier"]
    """)
    with pytest.raises(DomainPackError, match="missing required 'kind'"):
        load_domain("testdom")


def test_malformed_roles_shape_is_rejected(pack_dir):
    from goldencheck_types import load_domain

    write_pack(pack_dir, """
        description: "roles not a mapping"
        types: {}
        roles: ["supplier"]
    """)
    with pytest.raises(DomainPackError, match="'roles' must be a mapping"):
        load_domain("testdom")


def test_role_name_must_agree_with_its_key(pack_dir):
    from goldencheck_types import load_domain

    write_pack(pack_dir, """
        description: "name disagreement"
        types: {}
        roles:
          supplier:
            name: vendor
            kind: organization
            name_hints: ["supplier"]
    """)
    with pytest.raises(DomainPackError, match="must agree"):
        load_domain("testdom")


def test_shipped_packs_declare_loadable_roles():
    """The four packs this wave declares roles for must parse and be consistent."""
    from goldencheck_types import IDENTITY_KINDS, load_domain

    for name in ("finance", "insurance", "healthcare", "manufacturing"):
        pack = load_domain(name)
        assert pack.roles, f"{name} declares no roles"
        for role_name, role in pack.roles.items():
            assert role.name == role_name
            assert role.kind in IDENTITY_KINDS
            assert role.name_hints, f"{name}.{role_name} has no hints"
            # typical_types corroborate, so they must name types that exist.
            for type_name in role.typical_types:
                assert type_name in pack.types, (
                    f"{name}.{role_name}.typical_types references unknown type {type_name!r}"
                )
