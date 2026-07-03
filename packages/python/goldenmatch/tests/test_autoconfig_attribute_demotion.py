"""Discriminative-power demotion of weighted-fuzzy ATTRIBUTE fields.

A workplace/locality attribute (a clinic ``address`` or an employer ``company``
name) shared by colleagues is not person-identity evidence -- as a full-weight
positive fuzzy feature it collapses distinct people at one practice into a
mega-cluster. The demotion measures, from the data, whether records sharing the
attribute value co-agree on the person's name; if not, it demotes the field to
blocking-only. Default ON as of v2.7.0 (kill-switch GOLDENMATCH_ATTRIBUTE_DEMOTION=0);
scoped so a real
person-name / identity field is never eligible; a no-op where the attribute
really is identity-correlated.

Motivated by a real MJH dermatology list where ~70 distinct dermatologists
sharing a clinic address + generic company name merged into one cluster.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.core.autoconfig import build_matchkeys, profile_columns
from goldenmatch.core.autoconfig_discriminative import should_demote_attribute_field


def _colleagues_df(clinic_rows: int = 40) -> pl.DataFrame:
    """A clinic of DISTINCT people sharing one address + company + phone line,
    plus a tail of varied singletons so the frame is not degenerate.

    Carries constant ``*_id`` metadata columns (classified ``identifier`` by the
    ``*_id`` name pattern) that replicate the real DERM shape: they pollute the
    broad #1351 co-agreement basket enough that the existing exact-key veto KEEPS
    a shared clinic phone, so this fixture actually isolates the group-attribute
    demotion (which measures name co-agreement only)."""
    first = [f"first{i}" for i in range(clinic_rows)]
    last = [f"last{i}" for i in range(clinic_rows)]
    address = ["100 MAIN ST STE 5"] * clinic_rows
    company = ["DERMATOLOGY ASSOCIATES"] * clinic_rows
    phone = ["5551002000"] * clinic_rows  # one shared switchboard line
    # tail: unrelated people at unique addresses/companies/phones
    for i in range(clinic_rows, clinic_rows + 40):
        first.append(f"tf{i}")
        last.append(f"tl{i}")
        address.append(f"{i} OAK AVE")
        company.append(f"Clinic {i}")
        phone.append(f"999200{i:04d}")
    n = len(first)
    return pl.DataFrame({
        "first_name": first, "last_name": last,
        "address1": address, "company": company, "phone": phone,
        # constant metadata (identifier-by-name) -> pollutes the broad basket
        "batch_id": ["B1"] * n, "f_id": ["47"] * n, "s_id": ["S9"] * n,
    })


def _personal_cell_df(n_people: int = 30) -> pl.DataFrame:
    """Phone IS identity-correlated: each person appears twice with the SAME name
    and the SAME personal cell -> shared-phone pairs co-agree on name -> KEEP."""
    first, last, phone = [], [], []
    for i in range(n_people):
        for _ in range(2):
            first.append(f"first{i}")
            last.append(f"last{i}")
            phone.append(f"555{i:07d}")
    return pl.DataFrame({"first_name": first, "last_name": last, "phone": phone})


def _true_dupes_df(n_people: int = 30) -> pl.DataFrame:
    """Address IS identity-correlated: each person appears twice at the SAME
    address with the SAME name (a genuine duplicate), so shared-address pairs
    co-agree on name -> address must be KEPT."""
    first, last, address = [], [], []
    for i in range(n_people):
        for _ in range(2):
            first.append(f"first{i}")
            last.append(f"last{i}")
            address.append(f"{i} HOME RD")
    return pl.DataFrame({"first_name": first, "last_name": last, "address1": address})


def _campaign_list_df(list_rows: int = 30, dupe_people: int = 15) -> pl.DataFrame:
    """A mailing-list identifier: `list_id` = 'CAMPAIGN_A' shared by many DIFFERENT
    people (one large group), plus small same-person duplicate groups on other
    ids. The large campaign group must be demoted; the column is mostly-unique
    overall, so a blind co-agreement AVERAGE would be rescued by the small
    same-person groups -- only the group-size-aware measure catches it."""
    first, last, list_id = [], [], []
    for i in range(list_rows):
        first.append(f"first{i}")
        last.append(f"last{i}")
        list_id.append("CAMPAIGN_A")
    for i in range(dupe_people):
        for _ in range(2):
            first.append(f"dp{i}")
            last.append(f"dp{i}")
            list_id.append(f"L{i}")
    return pl.DataFrame({"first_name": first, "last_name": last, "list_id": list_id})


_NAME_BASKET = [("first_name", True), ("last_name", True)]


def _profile(df: pl.DataFrame, col: str):
    for p in profile_columns(df):
        if p.name == col:
            return p
    raise AssertionError(f"no profile for {col}")


def test_shared_workplace_address_demoted(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ATTRIBUTE_DEMOTION", "1")
    df = _colleagues_df()
    addr = _profile(df, "address1")
    assert should_demote_attribute_field(
        df, "address1", addr.col_type, _NAME_BASKET, is_person_name=False
    )


def test_shared_workplace_phone_demoted(monkeypatch):
    """A shared clinic switchboard line -- the real DERM over-merge driver -- is
    demoted (records sharing it are different people)."""
    monkeypatch.setenv("GOLDENMATCH_ATTRIBUTE_DEMOTION", "1")
    df = _colleagues_df()
    ph = _profile(df, "phone")
    assert ph.col_type == "phone"
    assert should_demote_attribute_field(
        df, "phone", ph.col_type, _NAME_BASKET, is_person_name=False
    )


def test_personal_cell_kept(monkeypatch):
    """A personal cell (shared-value records co-agree on name) is KEPT."""
    monkeypatch.setenv("GOLDENMATCH_ATTRIBUTE_DEMOTION", "1")
    df = _personal_cell_df()
    ph = _profile(df, "phone")
    assert not should_demote_attribute_field(
        df, "phone", ph.col_type, _NAME_BASKET, is_person_name=False
    )


def test_group_list_identifier_demoted(monkeypatch):
    """A mailing-list / campaign identifier (large group of DIFFERENT people) is
    demoted, even though the column is mostly-unique overall."""
    monkeypatch.setenv("GOLDENMATCH_ATTRIBUTE_DEMOTION", "1")
    df = _campaign_list_df()
    # col_type passed explicitly to isolate the group-size logic from classification
    assert should_demote_attribute_field(
        df, "list_id", "identifier", _NAME_BASKET, is_person_name=False
    )


def test_small_group_identifier_kept(monkeypatch):
    """A real personal identifier only ever groups a person's few duplicates (no
    large group) -> insufficient support -> KEPT."""
    monkeypatch.setenv("GOLDENMATCH_ATTRIBUTE_DEMOTION", "1")
    first, last, pid = [], [], []
    for i in range(40):
        for _ in range(2):  # each id shared by 2 records of the SAME person
            first.append(f"first{i}")
            last.append(f"last{i}")
            pid.append(f"ID{i}")
    df = pl.DataFrame({"first_name": first, "last_name": last, "member_id": pid})
    assert not should_demote_attribute_field(
        df, "member_id", "identifier", _NAME_BASKET, is_person_name=False
    )


def test_person_name_never_demoted(monkeypatch):
    """Even shared and even with the flag on, a person-name field is out of scope."""
    monkeypatch.setenv("GOLDENMATCH_ATTRIBUTE_DEMOTION", "1")
    df = _colleagues_df()
    ln = _profile(df, "last_name")
    assert not should_demote_attribute_field(
        df, "last_name", ln.col_type, _NAME_BASKET, is_person_name=True
    )


def test_identity_correlated_address_kept(monkeypatch):
    """When shared-address records DO co-agree on name (true dupes), keep it."""
    monkeypatch.setenv("GOLDENMATCH_ATTRIBUTE_DEMOTION", "1")
    df = _true_dupes_df()
    addr = _profile(df, "address1")
    assert not should_demote_attribute_field(
        df, "address1", addr.col_type, _NAME_BASKET, is_person_name=False
    )


def test_default_on_demotes(monkeypatch):
    """Default ON as of v2.7.0: with the flag UNSET, a group attribute is demoted."""
    monkeypatch.delenv("GOLDENMATCH_ATTRIBUTE_DEMOTION", raising=False)
    df = _colleagues_df()
    addr = _profile(df, "address1")
    assert should_demote_attribute_field(
        df, "address1", addr.col_type, _NAME_BASKET, is_person_name=False
    )


def test_kill_switch_disables(monkeypatch):
    """GOLDENMATCH_ATTRIBUTE_DEMOTION=0 restores the pre-2.7.0 no-op behavior."""
    monkeypatch.setenv("GOLDENMATCH_ATTRIBUTE_DEMOTION", "0")
    df = _colleagues_df()
    addr = _profile(df, "address1")
    assert not should_demote_attribute_field(
        df, "address1", addr.col_type, _NAME_BASKET, is_person_name=False
    )


def test_df_none_fail_safe(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ATTRIBUTE_DEMOTION", "1")
    assert not should_demote_attribute_field(None, "address1", "address", _NAME_BASKET, is_person_name=False)


def test_empty_basket_fail_safe(monkeypatch):
    """No name/identity anchor to measure against -> keep (no demotion)."""
    monkeypatch.setenv("GOLDENMATCH_ATTRIBUTE_DEMOTION", "1")
    df = pl.DataFrame({"address1": ["100 MAIN ST"] * 40})
    addr = _profile(df, "address1")
    assert not should_demote_attribute_field(
        df, "address1", addr.col_type, _NAME_BASKET, is_person_name=False
    )


def _weighted_fuzzy_field_names(matchkeys) -> set[str]:
    out: set[str] = set()
    for mk in matchkeys:
        if mk.type == "weighted":
            for f in mk.fields:
                if (f.scorer or "") != "exact":
                    out.add(f.field)
    return out


def _exact_matchkey_fields(matchkeys) -> set[str]:
    """Single-column EXACT matchkey fields (e.g. exact_phone -> {'phone'})."""
    out: set[str] = set()
    for mk in matchkeys:
        if mk.type == "exact" and len(mk.fields) == 1:
            out.add(mk.fields[0].field)
    return out


def test_build_matchkeys_integration(monkeypatch):
    """End-to-end (exact-only scope): the shared-clinic ``phone`` EXACT matchkey
    is demoted when the flag is on, while the weighted ``address1`` fuzzy field is
    KEPT (a soft contributor is load-bearing on corrupted-name data; only exact
    force-merges are removed). Byte-identical under the kill-switch."""
    df = _colleagues_df()
    profiles = profile_columns(df)

    # Off branch = the kill-switch (demotion is DEFAULT ON as of v2.7.0).
    monkeypatch.setenv("GOLDENMATCH_ATTRIBUTE_DEMOTION", "0")
    mks_off = build_matchkeys(profiles, df=df)
    off_exact = _exact_matchkey_fields(mks_off)
    off_weighted = _weighted_fuzzy_field_names(mks_off)

    monkeypatch.setenv("GOLDENMATCH_ATTRIBUTE_DEMOTION", "1")
    mks_on = build_matchkeys(profiles, df=df)
    on_exact = _exact_matchkey_fields(mks_on)
    on_weighted = _weighted_fuzzy_field_names(mks_on)

    # The shared clinic phone backs an EXACT matchkey off, and is demoted on.
    assert "phone" in off_exact
    assert "phone" not in on_exact
    # The weighted address field is a soft contributor -- KEPT in BOTH (exact-only
    # scope), so corrupted-name recall is never sacrificed.
    assert "address1" in off_weighted
    assert "address1" in on_weighted
    # Person names always survive.
    assert {"first_name", "last_name"} & on_weighted
