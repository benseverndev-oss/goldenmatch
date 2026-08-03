"""Semantic-graph: entity<->entity relationship edges from shared attributes.

Different entities that share a NON-identity attribute (a clinic phone, an
address) get a relationship edge keyed on their stable entity_ids, emitted in the
same resolve pass, idempotent across runs, and fan-out-capped.
"""
from __future__ import annotations

import json

import polars as pl
import pytest
from goldenmatch.config.schemas import RelationshipRule
from goldenmatch.identity import IdentityStore, resolve_clusters, to_graph_batch


@pytest.fixture()
def store(tmp_path):
    s = IdentityStore(path=str(tmp_path / "identity.db"))
    yield s
    s.close()


def _df(rows):
    out = []
    for i, r in enumerate(rows):
        rec = {"__row_id__": i, "__source__": "src"}
        rec.update(r)
        out.append(rec)
    return pl.DataFrame(out)


def _singletons(n):
    return {i: {"members": [i], "size": 1, "confidence": 1.0, "pair_scores": {}}
            for i in range(n)}


def _resolve(store, df, clusters, rules, run_name="r"):
    return resolve_clusters(
        clusters, df, [], "mk", store, run_name=run_name, source_pk_col="id",
        dataset="d", emit_singletons=True, relationships=rules,
    )


PHONE = [RelationshipRule(field="phone", kind="shares_phone")]


def test_shared_attribute_relates_distinct_entities(store):
    # 1 and 2 are different people sharing phone 555; 3 has its own phone.
    df = _df([{"id": "1", "name": "Al", "phone": "555"},
              {"id": "2", "name": "Bo", "phone": "555"},
              {"id": "3", "name": "Cy", "phone": "999"}])
    s = _resolve(store, df, _singletons(3), PHONE)

    a = store.find_entity_by_record("src:1")
    b = store.find_entity_by_record("src:2")
    c = store.find_entity_by_record("src:3")
    assert a != b != c
    assert s.relationships_added == 1
    assert store.count_relationships() == 1

    rels = store.get_relationships(a)
    assert len(rels) == 1
    assert rels[0]["other_entity_id"] == b
    assert rels[0]["kind"] == "shares_phone"
    assert rels[0]["shared_value"] == "555"
    # symmetric
    assert store.get_relationships(b)[0]["other_entity_id"] == a
    # c is unrelated (unique phone)
    assert store.get_relationships(c) == []


def test_reresolve_is_idempotent(store):
    df = _df([{"id": "1", "name": "Al", "phone": "555"},
              {"id": "2", "name": "Bo", "phone": "555"}])
    _resolve(store, df, _singletons(2), PHONE, run_name="r1")
    assert store.count_relationships() == 1
    s2 = _resolve(store, df, _singletons(2), PHONE, run_name="r2")
    assert store.count_relationships() == 1          # NOT 2
    assert s2.relationships_added in (0, 1)          # attempted, but de-duped in store


def test_same_entity_records_do_not_self_relate(store):
    # two records of the SAME person sharing a phone -> one entity, no self-edge.
    df = _df([{"id": "1", "name": "Al", "phone": "555"},
              {"id": "2", "name": "Al", "phone": "555"}])
    clusters = {0: {"members": [0, 1], "size": 2, "confidence": 1.0,
                    "pair_scores": {(0, 1): 0.99}}}
    _resolve(store, df, clusters, PHONE)
    assert store.count_relationships() == 0


def test_max_fanout_skips_hub_values(store):
    # 3 entities share a switchboard line; max_fanout=2 skips it as a hub.
    df = _df([{"id": "1", "name": "Al", "phone": "555"},
              {"id": "2", "name": "Bo", "phone": "555"},
              {"id": "3", "name": "Cy", "phone": "555"}])
    rules = [RelationshipRule(field="phone", kind="shares_phone", max_fanout=2)]
    _resolve(store, df, _singletons(3), rules)
    assert store.count_relationships() == 0
    # with a higher cap, all 3 pairwise edges appear
    store2_rules = [RelationshipRule(field="phone", kind="shares_phone", max_fanout=10)]
    _resolve(store, df, _singletons(3), store2_rules, run_name="r2")
    assert store.count_relationships() == 3          # 3 choose 2


def test_no_rules_no_relationships(store):
    df = _df([{"id": "1", "name": "Al", "phone": "555"},
              {"id": "2", "name": "Bo", "phone": "555"}])
    s = _resolve(store, df, _singletons(2), None)
    assert s.relationships_added == 0
    assert store.count_relationships() == 0


def test_multiple_rules(store):
    df = _df([{"id": "1", "name": "Al", "phone": "555", "zip": "07001"},
              {"id": "2", "name": "Bo", "phone": "555", "zip": "07001"}])
    rules = [RelationshipRule(field="phone", kind="shares_phone"),
             RelationshipRule(field="zip", kind="same_area")]
    _resolve(store, df, _singletons(2), rules)
    kinds = {r["kind"] for r in store.get_relationships(
        store.find_entity_by_record("src:1"))}
    assert kinds == {"shares_phone", "same_area"}


# ── GoldenGraph export ──────────────────────────────────────────────────────

def test_to_graph_batch_shape(store):
    df = _df([{"id": "1", "name": "Al", "phone": "555"},
              {"id": "2", "name": "Bo", "phone": "555"}])
    _resolve(store, df, _singletons(2), PHONE)
    a = store.find_entity_by_record("src:1")
    b = store.find_entity_by_record("src:2")

    batch = to_graph_batch(store, "d", name_field="name")

    # GoldenGraph StoreBatch shape + JSON-serializable (the append contract).
    assert set(batch) == {"entities", "edges"}
    json.dumps(batch)  # must not raise
    assert len(batch["entities"]) == 2
    assert len(batch["edges"]) == 1

    # each entity carries its stable entity_id as the reconciliation record_key
    by_key = {e["record_keys"][0]: e for e in batch["entities"]}
    assert set(by_key) == {a, b}
    assert {e["canonical_name"] for e in batch["entities"]} == {"Al", "Bo"}
    for e in batch["entities"]:
        assert e["typ"] == "entity"
        assert isinstance(e["local_id"], int)

    # the edge is the (subj_local, predicate, obj_local) triple over local ids
    edge = batch["edges"][0]
    assert edge["predicate"] == "shares_phone"
    locals_ = {e["local_id"]: e["record_keys"][0] for e in batch["entities"]}
    assert {locals_[edge["subj_local"]], locals_[edge["obj_local"]]} == {a, b}
    assert edge["source_refs"] == ["555"]


def test_to_graph_batch_empty(store):
    df = _df([{"id": "1", "name": "Al", "phone": "555"},
              {"id": "2", "name": "Bo", "phone": "999"}])
    _resolve(store, df, _singletons(2), PHONE)  # no shared phone -> no edges
    batch = to_graph_batch(store, "d")
    assert batch == {"entities": [], "edges": []}


def test_to_graph_batch_roundtrips_into_goldengraph(store):
    """If goldengraph is installed, the exported batch appends into its PyStore
    and the edge is queryable -- i.e. it feeds the path-retrieval layer."""
    gg_store = pytest.importorskip("goldengraph.core")
    PyStore = getattr(gg_store, "PyStore", None)
    if PyStore is None:
        pytest.skip("goldengraph PyStore unavailable")
    df = _df([{"id": "1", "name": "Al", "phone": "555"},
              {"id": "2", "name": "Bo", "phone": "555"}])
    _resolve(store, df, _singletons(2), PHONE)
    batch = to_graph_batch(store, "d", name_field="name")

    ps = PyStore()
    ps.append(json.dumps(batch))
    dump = json.loads(ps.query())
    preds = {e["predicate"] for e in dump.get("edges", [])}
    assert "shares_phone" in preds


# ── v2: authoritative reconciliation (desired-vs-existing) ──────────────────

def test_reconcile_deletes_stale_edges(store):
    """A warm re-run where a shared value disappears DELETES the edge, not leaves
    it stale -- the whole point of desired-vs-existing reconciliation."""
    df1 = _df([{"id": "1", "name": "Al", "phone": "555"},
               {"id": "2", "name": "Bo", "phone": "555"}])
    _resolve(store, df1, _singletons(2), PHONE, run_name="r1")
    assert store.count_relationships() == 1
    # r2: same records/entities, but Bo's phone changed -> no longer shared.
    df2 = _df([{"id": "1", "name": "Al", "phone": "555"},
               {"id": "2", "name": "Bo", "phone": "999"}])
    s2 = _resolve(store, df2, _singletons(2), PHONE, run_name="r2")
    assert store.count_relationships() == 0
    assert s2.relationships_added == 0
    assert s2.relationships_deleted == 1


def test_reconcile_idempotent_no_churn(store):
    """Same data twice -> second run inserts 0 AND deletes 0 (no churn)."""
    df = _df([{"id": "1", "name": "Al", "phone": "555"},
              {"id": "2", "name": "Bo", "phone": "555"}])
    s1 = _resolve(store, df, _singletons(2), PHONE, run_name="r1")
    assert s1.relationships_added == 1 and s1.relationships_deleted == 0
    s2 = _resolve(store, df, _singletons(2), PHONE, run_name="r2")
    assert s2.relationships_added == 0 and s2.relationships_deleted == 0
    assert store.count_relationships() == 1


# ── v2: derived / transform edge fields (edge on ANY field, not just raw) ────

def test_transform_email_domain_relates_same_company(store):
    """A derived-field rule (transform='email_domain') keys edges on the domain,
    so two entities at the same company relate even though their emails differ,
    and someone at a different domain does not."""
    df = _df([{"id": "1", "name": "Al", "email": "al@acme.com"},
              {"id": "2", "name": "Bo", "email": "bo@acme.com"},
              {"id": "3", "name": "Cy", "email": "cy@other.com"}])
    rule = [RelationshipRule(field="email", kind="same_company",
                             transform="email_domain")]
    s = _resolve(store, df, _singletons(3), rule)
    assert s.relationships_added == 1
    assert store.count_relationships() == 1
    rels = store.list_relationships("d")
    assert rels[0]["shared_value"] == "acme.com"


def test_transform_lower_trim_collapses_casing(store):
    """transform='lower_trim' relates entities whose specialty differs only by
    case/whitespace -- 'Cardiology' and ' cardiology ' become one group."""
    df = _df([{"id": "1", "name": "Al", "spec": "Cardiology"},
              {"id": "2", "name": "Bo", "spec": " cardiology "}])
    rule = [RelationshipRule(field="spec", kind="same_specialty",
                             transform="lower_trim")]
    s = _resolve(store, df, _singletons(2), rule)
    assert s.relationships_added == 1


# ── v2: auto-detect (the program suggests good edge fields) ─────────────────

def test_suggest_relationship_rules_ranks_shared_fields(store):
    """The profiler suggests a rule for a SHARED field (clinic), skips a UNIQUE
    field (ssn -> no edges) and a HUB field (country -> everyone, over fanout)."""
    from goldenmatch.identity import suggest_relationship_rules
    rows = [{"id": str(i), "name": f"P{i}", "ssn": f"s{i}",
             "clinic": "A" if i < 3 else "B", "country": "US"}
            for i in range(6)]
    _resolve(store, _df(rows), _singletons(6), [])  # populate, no rules needed
    rules = suggest_relationship_rules(store, dataset="d", max_fanout=4, top_k=5)
    fields = [r.field for r in rules]
    assert "clinic" in fields          # 3 + 3 entities -> both in [2, 4]
    assert "ssn" not in fields         # all unique -> no edges
    assert "country" not in fields     # shared by 6 > max_fanout 4 -> hub
    assert all(r.kind == f"shares_{r.field}" for r in rules)


def test_pg_transform_sql_has_no_placeholder_collision():
    r"""`_pg_sql` maps every '?' to a bind placeholder (naive str.replace), so a
    transform's SQL must never contain a literal '?'. A regex '?' in
    normalize_company ('\.?$') once broke the query at 14M ('4 placeholders but 3
    parameters'); it never showed in sqlite tests (normalize_company degrades to
    lower_trim there). This guards the whole class without needing Postgres."""
    from goldenmatch.identity.store import _rel_value_expr
    raw = "payload ->> 'x'"
    for t in (None, "raw", "lower_trim", "zip3", "email_domain", "normalize_company"):
        expr = _rel_value_expr(raw, t, "postgres")
        assert "?" not in expr, f"transform {t!r} PG SQL has a '?': {expr}"


def test_suggest_picks_transform_and_rejects_hubs(store):
    """Full-data profiling fixes the two things the old sampled heuristic got wrong:
    a unique-looking field surfaces under its TRANSFORM (email -> email_domain groups
    by company), and a HUB attribute (specialty held by ~everyone) is rejected, not
    ranked first."""
    rows = [{"id": str(i), "name": f"P{i}",
             "email": f"u{i}@acme.com" if i < 3 else f"u{i}@beta.com",
             "specialty": "IM"}                       # all 6 share it -> hub
            for i in range(6)]
    _resolve(store, _df(rows), _singletons(6), [])
    from goldenmatch.identity import suggest_relationship_rules
    rules = suggest_relationship_rules(store, dataset="d", max_fanout=4, top_k=5)
    by_field = {r.field: r for r in rules}
    assert "email" in by_field
    assert by_field["email"].transform == "email_domain"   # raw email is unique
    assert "specialty" not in by_field                      # 6 > max_fanout 4 -> hub


def test_suggest_prefers_rare_specific_values(store):
    """At equal coverage, a field linked through small specific groups outranks one
    linked through a large common group (rarity/specificity weighting)."""
    vals = ["a", "a", "b", "b", "c", "c"]           # 'rare': three pairs
    rows = [{"id": str(i), "name": f"P{i}", "rare": vals[i], "common": "X"}
            for i in range(6)]                       # 'common': all six share "X"
    _resolve(store, _df(rows), _singletons(6), [])
    from goldenmatch.identity import suggest_relationship_rules
    fields = [r.field for r in
              suggest_relationship_rules(store, dataset="d", max_fanout=10, top_k=5)]
    assert fields.index("rare") < fields.index("common")


def test_suggest_downweights_matchkeys(store):
    """Two fields with IDENTICAL sharing, but one is a declared matchkey -> the
    matchkey (an identity signal, not a relationship) ranks below the other."""
    rows = [{"id": str(i), "name": f"P{i}",
             "clinic": "A" if i < 3 else "B",
             "team": "A" if i < 3 else "B"} for i in range(6)]
    _resolve(store, _df(rows), _singletons(6), [])
    from goldenmatch.identity import suggest_relationship_rules
    fields = [r.field for r in suggest_relationship_rules(
        store, dataset="d", max_fanout=4, top_k=5, matchkey_fields={"team"})]
    assert "clinic" in fields and "team" in fields
    assert fields.index("clinic") < fields.index("team")


def test_suggest_excludes_near_unique_matchkeys(store):
    """A matchkey shared only in near-PAIRS is a near-unique identifier (the same
    entity duplicated -- a missed merge), not a relationship, so it is excluded;
    a matchkey shared in larger groups (a clinic phone) is kept."""
    rows = [{"id": str(i), "name": f"P{i}",
             "ssn": "A" if i < 2 else "B",   # two pairs -> mean group 2 (near-unique)
             "phone": "X"} for i in range(4)]  # one group of 4 -> mean group 4
    _resolve(store, _df(rows), _singletons(4), [])
    from goldenmatch.identity import suggest_relationship_rules
    fields = [r.field for r in suggest_relationship_rules(
        store, dataset="d", max_fanout=10, top_k=5,
        matchkey_fields={"ssn", "phone"})]
    assert "phone" in fields        # larger groups -> real relationship
    assert "ssn" not in fields      # near-pair matchkey -> identifier, excluded


# ── deterministic merge (collapse entities sharing an authoritative id) ──────

def test_deterministic_merge_collapses_shared_id(store):
    """Two distinct entities that share an NPI (a missed merge) collapse into one:
    records reassign to the surviving (lowest) id; idempotent on re-run."""
    df = _df([{"id": "1", "name": "Robert Smith", "npi": "999"},
              {"id": "2", "name": "Bob Smith", "npi": "999"}])
    _resolve(store, df, _singletons(2), None)
    a = store.find_entity_by_record("src:1")
    b = store.find_entity_by_record("src:2")
    assert a != b
    assert store.merge_by_shared_field("d", "npi") == (1, 1)
    assert store.find_entity_by_record("src:1") == store.find_entity_by_record("src:2")
    assert store.find_entity_by_record("src:1") == min(a, b)
    assert store.merge_by_shared_field("d", "npi") == (0, 0)   # idempotent


def test_deterministic_merge_hub_guard(store):
    """A value shared by more entities than max_group is a placeholder id -> skipped."""
    df = _df([{"id": str(i), "name": f"P{i}", "npi": "000"} for i in range(4)])
    _resolve(store, df, _singletons(4), None)
    assert store.merge_by_shared_field("d", "npi", max_group=2) == (0, 0)  # 4 > cap
    assert store.merge_by_shared_field("d", "npi", max_group=10) == (3, 1)  # collapses


def test_config_lineage_records_run_and_resolves_entity_to_config(store):
    """A resolve stamps identity_runs with the config fingerprint, and an entity's
    events (which carry run_name) resolve back to that config_id."""
    df = _df([{"id": "1", "name": "Al", "phone": "555"}])
    resolve_clusters(
        _singletons(1), df, [], "mk", store, run_name="run1", source_pk_col="id",
        dataset="d", emit_singletons=True,
        config_id="sha256:deadbeef", config_schema_version=1,
        config_json='{"matchkeys": []}',
    )
    run = store.run_config("run1")
    assert run is not None
    assert run["config_id"] == "sha256:deadbeef"
    assert run["schema_version"] == 1
    assert run["config_json"] == '{"matchkeys": []}'
    # entity -> its events' run_name -> the recorded config_id
    ent = store.find_entity_by_record("src:1")
    run_names = {ev.run_name for ev in store.history(ent)}
    assert "run1" in run_names
    assert store.run_config("run1")["config_id"] == "sha256:deadbeef"


def test_config_lineage_absent_when_no_fingerprint(store):
    """No config_id threaded through -> no identity_runs row (byte-identical path)."""
    df = _df([{"id": "1", "name": "Al", "phone": "555"}])
    _resolve(store, df, _singletons(1), None, run_name="bare")
    assert store.run_config("bare") is None


def test_config_lineage_idempotent_on_repeated_run_name(store):
    """First writer wins on a repeated run_name (idempotent replay)."""
    store.record_run("rx", config_id="sha256:aaaa", schema_version=1)
    store.record_run("rx", config_id="sha256:bbbb", schema_version=2)
    assert store.run_config("rx")["config_id"] == "sha256:aaaa"
