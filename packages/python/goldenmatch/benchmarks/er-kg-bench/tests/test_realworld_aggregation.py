"""Real-world (Wikidata) aggregation capability bench -- unit tests over the TINY
fixture (wheel-free for the loader/generator/CLI; wheel-gated for the runner)."""
from __future__ import annotations

import pytest
from erkgbench.qa_e2e.realworld import (
    _FIXTURE_DIR,
    generate_realworld_aggregation,
    load_realworld_entities,
)


def test_load_realworld_entities_maps_qid_canonical_aliases():
    ents = load_realworld_entities(_FIXTURE_DIR / "wikidata_companies_TINY.json")
    by_id = {e.id: e for e in ents}
    assert set(by_id) == {"Q1", "Q2", "Q3", "Q4"}
    assert by_id["Q1"].canonical == "Acme Holdings"
    assert "Acme" in by_id["Q1"].variants          # aliases -> variants
    assert by_id["Q1"].canonical not in by_id["Q1"].variants  # canonical excluded


def test_generate_realworld_aggregation_shapes_and_gold():
    docs, qs = generate_realworld_aggregation(
        _FIXTURE_DIR / "wikidata_companies_TINY.json", ambiguity=1.0, seed=7)
    # one doc per (anchor, member) edge = 3
    assert len(docs) == 3
    # each doc text mentions the relation words and ends with a period
    assert all("has subsidiary" in d.text and d.text.endswith(".") for d in docs)
    # list + count question for the single anchor
    lists = [q for q in qs if q.kind == "list"]
    assert len(lists) == 1
    q = lists[0]
    assert q.anchor_id == "Q1" and q.relation == "has_subsidiary"
    assert set(q.gold_members) == {"Q2", "Q3", "Q4"} and q.gold_count == 3
    # uniqueness invariant: no duplicate (anchor_id, relation) across list questions
    keys = [(q.anchor_id, q.relation) for q in lists]
    assert len(keys) == len(set(keys))
    # ambiguity=1.0 -> at least one mention uses a non-canonical alias somewhere
    all_text = " ".join(d.text for d in docs)
    assert "Acme" in all_text or "BETA" in all_text or "Beta Corporation" in all_text


def test_v1_fixture_loads_and_has_a_large_bucket_question():
    """Sanity-check the committed v1 fixture end-to-end (wheel-free): the loader +
    generator run over the real pull, and at least one anchor lands in the 11-20
    fan-out bucket (the bucket where the passage-window floor collapses)."""
    from erkgbench.qa_e2e.aggregation import size_bucket

    v1 = _FIXTURE_DIR / "wikidata_companies_v1.json"
    if not v1.exists():
        pytest.skip("wikidata_companies_v1.json fixture not committed")
    ents = load_realworld_entities(v1)
    assert len(ents) > 100
    _docs, qs = generate_realworld_aggregation(v1, ambiguity=0.6, seed=7)
    lists = [q for q in qs if q.kind == "list"]
    buckets = {size_bucket(q.gold_count) for q in lists}
    assert "11-20" in buckets       # the RAG-floor-collapse bucket is present
    # uniqueness invariant holds on the real pull too
    keys = [(q.anchor_id, q.relation) for q in lists]
    assert len(keys) == len(set(keys))


def test_run_realworld_aggregation_gg_beats_floor():
    try:
        import goldengraph_native  # noqa: F401
    except ImportError:
        pytest.skip("goldengraph-native wheel not installed")
    from erkgbench.qa_e2e.realworld import run_realworld_aggregation

    res = run_realworld_aggregation(
        _FIXTURE_DIR / "wikidata_companies_TINY.json",
        ambiguity=1.0, passage_k=2)
    # on the 3-member set, exact traversal should match all; the k=2 window can't
    gg = list(res.gg_setf1.values())
    assert gg and min(gg) >= 0.99            # exact traversal recovers the full set


# --- Phase 1.5: non-oracle aggregation (real ER + aggregation compounded) ---------------
#
# NOTE ON THE TINY-FIXTURE SHAPE (a small, honest deviation from the plan's single-test
# wording): the plan's 1.5a asks to assert the alias variants of a MEMBER merge into ONE
# store node. In the aggregation corpus each member appears in exactly ONE document
# (rendered as one surface), so only the ANCHOR (which appears once per member) can ever
# span multiple surfaces across documents -- a member never fragments. And at
# `ambiguity=1.0` the anchor's two *variant* surfaces ("Acme", "Acme Holdings Inc.") land
# in DIFFERENT zero-config goldenmatch clusters, so a merged store node with full recovery
# is not achievable on this 4-entity fixture. So we prove the Phase 1.5 claims where they
# are honestly observable: (1) real resolution genuinely CLUSTERS variants and is not the
# oracle km; (2) the real pipeline recovers the full member set when the anchor resolves
# coherently; (3) imperfect real ER fragments the anchor and dips GG below the oracle arm
# -- the honest ER contribution the E2E quantifies by bucket. All three are deterministic.


def _wheel_or_skip():
    try:
        import goldengraph_native  # noqa: F401
    except ImportError:
        pytest.skip("goldengraph-native wheel not installed")


def test_real_resolution_clusters_variants_and_is_not_oracle():
    """1.5a (resolution happened): the `real` km is a genuine goldenmatch clustering of
    the surface universe -- it merges >=1 entity's distinct surfaces into one shared key
    (variants resolved), it is NOT the oracle km (a conservative real resolver keeps more
    keys than the entity count), and on this clean fixture it makes no wrong cross-entity
    merge."""
    _wheel_or_skip()
    from collections import defaultdict

    from erkgbench.qa_e2e.realworld import _realworld_entity_surfaces, _resolution_km

    rows = _realworld_entity_surfaces(_FIXTURE_DIR / "wikidata_companies_TINY.json")
    real = _resolution_km(rows, resolve_mode="real")
    oracle = _resolution_km(rows, resolve_mode="oracle")

    # oracle collapses every entity's surfaces to a single (qid) key; real does not.
    assert oracle == {(eid, s): eid for eid, s, _t in rows}
    assert real != oracle

    keys_by_entity: dict[str, set[str]] = defaultdict(set)
    entities_by_key: dict[str, set[str]] = defaultdict(set)
    surfaces_per_entity_key: dict[tuple[str, str], int] = defaultdict(int)
    for (qid, s), key in real.items():
        keys_by_entity[qid].add(key)
        entities_by_key[key].add(qid)
        surfaces_per_entity_key[(qid, key)] += 1

    # (a) real resolution MERGED variants: some entity has >=2 of its surfaces under one key.
    assert any(n >= 2 for n in surfaces_per_entity_key.values())
    # (b) not oracle: at least one entity kept >1 cluster key (conservative real ER).
    assert any(len(ks) > 1 for ks in keys_by_entity.values())
    # (c) no wrong merge: no cluster key spans two different ground-truth entities.
    assert all(len(qs) == 1 for qs in entities_by_key.values())


def test_real_mode_recovers_full_set_when_anchor_resolves_coherently():
    """1.5a (real pipeline recovers the set): with the anchor rendered coherently (seed 7),
    the store built through the REAL resolver recovers the full 3-member set -- the real
    end-to-end path, not the oracle km."""
    _wheel_or_skip()
    from erkgbench.qa_e2e.realworld import run_realworld_aggregation

    res = run_realworld_aggregation(
        _FIXTURE_DIR / "wikidata_companies_TINY.json",
        ambiguity=1.0, passage_k=2, resolve_mode="real")
    gg = list(res.gg_setf1.values())
    assert gg and min(gg) >= 0.99


def test_oracle_default_is_byte_identical_to_explicit_oracle():
    """1.5b invariant: the default (no resolve_mode) is the Phase 0 oracle path, unchanged."""
    _wheel_or_skip()
    from erkgbench.qa_e2e.realworld import run_realworld_aggregation

    tiny = _FIXTURE_DIR / "wikidata_companies_TINY.json"
    default = run_realworld_aggregation(tiny, ambiguity=1.0, passage_k=2)
    oracle = run_realworld_aggregation(tiny, ambiguity=1.0, passage_k=2, resolve_mode="oracle")
    assert default.gg_setf1 == oracle.gg_setf1
    assert default.floor_setf1 == oracle.floor_setf1
    assert default.gg_count_acc == oracle.gg_count_acc


def test_real_mode_imperfect_er_dips_gg_below_oracle():
    """1.5a (the ER contribution is real): on a seed where the anchor renders two variant
    surfaces the zero-config resolver leaves in separate clusters, the anchor FRAGMENTS in
    the real arm -> GG recall drops below the oracle arm (which pre-merges variants). This
    dip IS the honest ER signal the oracle-vs-real delta measures."""
    _wheel_or_skip()
    from erkgbench.qa_e2e.aggregation import goldengraph_aggregate, set_f1
    from erkgbench.qa_e2e.realworld import _build_realworld_store_for_mode

    tiny = _FIXTURE_DIR / "wikidata_companies_TINY.json"

    def gg_f1(mode):
        sg, cov, _docs, qs, _a, _s = _build_realworld_store_for_mode(
            tiny, ambiguity=1.0, seed=0, resolve_mode=mode)
        q = next(q for q in qs if q.kind == "list")
        got = goldengraph_aggregate(sg, cov, q.anchor_id, q.relation)
        return set_f1(got, set(q.gold_members))["f1"]

    oracle_f1 = gg_f1("oracle")
    real_f1 = gg_f1("real")
    assert oracle_f1 >= 0.99          # oracle pre-merges the anchor's variants
    assert real_f1 < oracle_f1        # imperfect real ER fragments it -> lower recall


# ---------------------------------------------------------------------------
# Phase 1.5 -- one extra proof the rewrite above does not cover: an anchor rendered
# under TWO distinct same-cluster surfaces collapses to ONE store node (the literal
# "variants merge into one node" claim), verified by the anchor node count.
# ---------------------------------------------------------------------------
_TINY = _FIXTURE_DIR / "wikidata_companies_TINY.json"


def test_real_resolution_merges_variants_into_one_node_and_recovers_set():
    """Task 1.5a: with the store built via the REAL resolver (no oracle km), an anchor
    rendered under two DISTINCT same-cluster surfaces ('Acme Holdings' + 'Acme Holdings
    Inc.') is resolved into ONE store node -- so exactly one node covers the anchor qid
    (a naive per-surface store would leave two) -- AND goldengraph still recovers the
    full 3-member subsidiary set.

    NOTE (plan deviation): the plan specified `ambiguity=1.0`, but at ambiguity=1.0 the
    canonical never renders and the two rendered VARIANTS ('Acme' vs 'Acme Holdings Inc.')
    land in DIFFERENT real clusters (goldenmatch does not merge the abbreviation on this
    tiny universe), so a real (non-oracle) merge is impossible there. Exercising a genuine
    merge needs the canonical + a same-cluster variant to co-render, i.e. ambiguity<1.0."""
    try:
        import goldengraph_native  # noqa: F401
    except ImportError:
        pytest.skip("goldengraph-native wheel not installed")
    from erkgbench.qa_e2e.aggregation import goldengraph_aggregate, set_f1
    from erkgbench.qa_e2e.realworld import (
        _build_realworld_store_for_mode,
        _realworld_entity_surfaces,
        _resolution_km,
    )

    amb, seed = 0.5, 1
    # precondition (deterministic): the two surfaces this render puts on the anchor share
    # a real cluster; if a goldenmatch version ever declusters them, skip (never fake-pass).
    rows = _realworld_entity_surfaces(_TINY)
    km = _resolution_km(rows, resolve_mode="real")
    if km[("Q1", "Acme Holdings")] != km[("Q1", "Acme Holdings Inc.")]:
        pytest.skip("goldenmatch did not cluster the anchor variant pair on this build")

    sg, cov, docs, qs, _anchor_surfaces, _s2c = _build_realworld_store_for_mode(
        _TINY, ambiguity=amb, seed=seed, resolve_mode="real")
    anchor_renders = {d.src_surface for d in docs}
    assert anchor_renders == {"Acme Holdings", "Acme Holdings Inc."}  # 2 distinct surfaces
    # real resolution merged them: exactly ONE store node covers the anchor qid Q1
    q1_nodes = [e for e in sg.entities() if "Q1" in cov.get(e["entity_id"], set())]
    assert len(q1_nodes) == 1                       # merged, not fragmented
    # goldengraph recovers the full member set through that merged anchor node
    q = next(x for x in qs if x.kind == "list")
    got = goldengraph_aggregate(sg, cov, q.anchor_id, q.relation)
    assert set_f1(got, set(q.gold_members))["f1"] >= 0.99


# --- Phase 2: co-occurrence corpus + ER-blind floor (compounded ER + aggregation) ------
def test_cooccurrence_renders_members_under_distinct_aliases():
    """Phase 2 corpus: a member with >=2 real surfaces is rendered in >=2 docs, EACH under
    a DISTINCT surface; gold_count is unchanged (the TRUE member set)."""
    from erkgbench.qa_e2e.realworld import _FIXTURE_DIR, generate_realworld_cooccurrence

    docs, qs = generate_realworld_cooccurrence(
        _FIXTURE_DIR / "wikidata_companies_TINY.json", mentions_per_member=3, seed=7)
    q = next(q for q in qs if q.kind == "list")
    assert q.gold_count == 3  # TINY anchor Q1 has members Q2,Q3,Q4
    # Q2 "Beta Corp" has canonical + 2 aliases -> rendered in 3 docs under 3 distinct surfaces.
    q2_docs = [d for d in docs if d.id.startswith("Q1::has_subsidiary::Q2::")]
    assert len(q2_docs) == 3
    assert len({d.dst_surface for d in q2_docs}) == 3  # all DISTINCT aliases


def test_er_blind_floor_overcounts_while_oracle_floor_is_correct():
    """The compounded-win mechanism: on the co-occurrence corpus a member recurs under
    several aliases. The ER-BLIND floor (naive normalization) counts each un-mergeable
    alias separately -> OVER-counts; the oracle floor (surface->qid) counts each member
    ONCE. goldenmatch's real dedup lands between the two -- that is the ER contribution."""
    from erkgbench.qa_e2e.realworld import (
        _FIXTURE_DIR,
        _realworld_entity_surfaces,
        er_blind_floor_count,
        generate_realworld_cooccurrence,
        oracle_floor_count,
    )

    fixture = _FIXTURE_DIR / "wikidata_companies_TINY.json"
    docs, qs = generate_realworld_cooccurrence(fixture, mentions_per_member=3, seed=7)
    rows = _realworld_entity_surfaces(fixture)
    q = next(q for q in qs if q.kind == "list")
    gold_count = q.gold_count

    anchor_surfaces = {s for eid, s, _t in rows if eid == q.anchor_id}
    member_surfaces = {s for eid, s, _t in rows if eid != q.anchor_id}
    surface_to_qid = {s: eid for eid, s, _t in rows}

    er_blind = er_blind_floor_count(
        docs, anchor_surfaces, passage_k=100, member_surfaces=member_surfaces)
    oracle = oracle_floor_count(
        docs, anchor_surfaces, passage_k=100, surface_to_qid=surface_to_qid)

    assert oracle == gold_count            # perfect ER -> exact count
    assert er_blind > gold_count           # ER-blind -> over-counts the aliases
    assert er_blind >= oracle              # ER-blindness can only inflate the count


def test_cooccurrence_run_gg_beats_er_blind_floor_on_count():
    """Wheel-gated compounded win: on the co-occurrence corpus, goldengraph's real-ER
    count-accuracy is >= the ER-blind floor's (its dedup merges aliases the naive floor
    over-counts). The oracle floor is the perfect-ER ceiling."""
    _wheel_or_skip()
    from erkgbench.qa_e2e.realworld import _FIXTURE_DIR, run_realworld_cooccurrence

    res = run_realworld_cooccurrence(
        _FIXTURE_DIR / "wikidata_companies_TINY.json", mentions_per_member=3, passage_k=100)
    # single 3-member anchor -> one bucket
    (gg,) = list(res["gg_count_acc"].values())
    (orc,) = list(res["oracle_floor_count_acc"].values())
    (erb,) = list(res["er_blind_count_acc"].values())
    assert orc >= gg >= erb            # real ER lands between perfect-ER and no-ER
    assert gg > erb                    # the compounded win: dedup beats the ER-blind floor
