"""Parity: the pipeline short-circuits a covered Fellegi-Sunter dedupe to the
fused ``match_fused_fs`` kernel when the controller flagged the run
(``config._use_fused_match``) AND the config-driven divergence gate is clear
(#1804 item 2, the FS twin of ``test_pipeline_fused_match.py``).

Capacity-survival mode: the fused-routed FS run sheds ``scored_pairs`` /
``review_pairs`` + per-cluster confidence, but CLUSTER MEMBERSHIP + GOLDEN are
byte-identical to the classic block->score->cluster FS path -- both train the
SAME (seeded) EM and run the SAME kernel FS math, so the link-threshold pairs
and their connected components match. ``match_fused_capacity_mode=True`` marks
the shed so it is never silent.
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    GoldenRulesConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.fused_match import (
    match_fused_fs_multipass_ready,
    match_fused_fs_ready,
)
from goldenmatch.core.fused_routing import config_needs_artifacts
from goldenmatch.core.pipeline import run_dedupe_df
from polars.testing import assert_frame_equal


def _kernel_present() -> bool:
    try:
        from goldenmatch.core._native_loader import native_module

        return hasattr(native_module(), "match_fused_fs")
    except Exception:
        return False


requires_kernel = pytest.mark.skipif(
    not _kernel_present(),
    reason="match_fused_fs native kernel not built (build_native.py); CI builds it",
)


def _people_df(n_clusters: int = 8, members: int = 3, n_singletons: int = 5) -> pl.DataFrame:
    """Personlike frame: ``n_clusters`` groups sharing a zip block + an identical
    name (FS exact-agreement -> high weight -> link), plus ``n_singletons`` rows
    on their own unique zip block. A second orthogonal ``city`` block key gives
    the multi-pass config something real to union on."""
    rows: list[dict] = []
    for c in range(n_clusters):
        for _m in range(members):
            rows.append(
                {"name": f"Cluster Person {c}", "zip": f"200{c:02d}", "city": f"town{c % 4}"}
            )
    for s in range(n_singletons):
        rows.append(
            {"name": f"Solo Human {s}", "zip": f"900{s:02d}", "city": f"solo{s}"}
        )
    return pl.DataFrame(rows)


def _fs_config(scorer: str = "jaro_winkler") -> GoldenMatchConfig:
    """Static single-key blocking (zip) + one probabilistic matchkey (name) --
    the match_fused_fs-covered shape. auto_split off + quality_weighting off + no
    identity/lineage -> config_needs_artifacts False, so the short-circuit is
    allowed when the flag is set."""
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="name_fs",
                type="probabilistic",
                link_threshold=0.5,
                fields=[MatchkeyField(
                    field="name", scorer=scorer, levels=3, partial_threshold=0.8,
                )],
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["zip"])],
            max_block_size=1000,
            skip_oversized=False,
        ),
        golden_rules=GoldenRulesConfig(
            default_strategy="most_complete",
            auto_split=False,
            quality_weighting=False,
        ),
    )


def _fs_multipass_config(scorer: str = "jaro_winkler") -> GoldenMatchConfig:
    """multi_pass blocking (zip + city, orthogonal) + one probabilistic matchkey
    -- the compound-union shape the single-key gate declines (#1798)."""
    cfg = _fs_config(scorer)
    cfg.blocking = BlockingConfig(
        strategy="multi_pass",
        keys=[BlockingKeyConfig(fields=["zip"])],
        passes=[BlockingKeyConfig(fields=["zip"]), BlockingKeyConfig(fields=["city"])],
        max_block_size=1000,
        skip_oversized=False,
    )
    return cfg


def _flag(cfg: GoldenMatchConfig) -> GoldenMatchConfig:
    """Simulate the controller post-step setting ExecutionPlan.use_fused_match."""
    cfg._use_fused_match = True
    return cfg


def _multi_partition(clusters: dict) -> set[frozenset[int]]:
    return {frozenset(c["members"]) for c in clusters.values() if c["size"] > 1}


def _golden_content(g) -> pl.DataFrame:
    if not isinstance(g, pl.DataFrame):
        g = pl.from_arrow(g)
    cols = [c for c in g.columns if c not in ("__cluster_id__", "__golden_confidence__")]
    return g.select(sorted(cols)).sort(sorted(cols))


def test_fs_config_covered_and_artifact_free():
    assert match_fused_fs_ready(_fs_config()) is True
    assert config_needs_artifacts(_fs_config()) is False
    assert match_fused_fs_multipass_ready(_fs_multipass_config()) is True
    assert config_needs_artifacts(_fs_multipass_config()) is False


@requires_kernel
def test_fs_fused_parity_single_key(monkeypatch):
    """Flag set + FS-covered + artifact-free -> short-circuit to match_fused_fs;
    membership + golden byte-identical to classic FS, empty scored_pairs +
    capacity marker."""
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    df = _people_df()

    classic = run_dedupe_df(df, _fs_config())
    assert classic.get("match_fused_capacity_mode") is not True

    fused = run_dedupe_df(df, _flag(_fs_config()))
    assert fused["match_fused_capacity_mode"] is True
    assert fused["scored_pairs"] == []
    assert fused["review_pairs"] == []

    assert _multi_partition(fused["clusters"]) == _multi_partition(classic["clusters"])
    assert set(fused["dupes"]["__row_id__"].to_pylist()) == set(
        classic["dupes"]["__row_id__"].to_pylist()
    )
    assert set(fused["unique"]["__row_id__"].to_pylist()) == set(
        classic["unique"]["__row_id__"].to_pylist()
    )
    assert fused["golden"] is not None and classic["golden"] is not None
    assert_frame_equal(
        _golden_content(fused["golden"]), _golden_content(classic["golden"])
    )


@requires_kernel
def test_fs_fused_parity_multipass(monkeypatch):
    """The multi-pass FS fused path (compound-union blocking, the #1798 shape) is
    byte-identical to the classic multi-pass FS dedupe."""
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    df = _people_df()

    classic = run_dedupe_df(df, _fs_multipass_config())
    fused = run_dedupe_df(df, _flag(_fs_multipass_config()))
    assert fused["match_fused_capacity_mode"] is True

    assert _multi_partition(fused["clusters"]) == _multi_partition(classic["clusters"])
    assert set(fused["dupes"]["__row_id__"].to_pylist()) == set(
        classic["dupes"]["__row_id__"].to_pylist()
    )
    assert fused["golden"] is not None and classic["golden"] is not None
    assert_frame_equal(
        _golden_content(fused["golden"]), _golden_content(classic["golden"])
    )


@requires_kernel
def test_fs_kill_switch_uses_classic(monkeypatch):
    """GOLDENMATCH_MATCH_FUSED=0 -> the FS short-circuit declines even with the
    flag set; classic FS runs byte-identical."""
    df = _people_df()
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    classic = run_dedupe_df(df, _fs_config())

    monkeypatch.setenv("GOLDENMATCH_MATCH_FUSED", "0")
    killed = run_dedupe_df(df, _flag(_fs_config()))
    assert killed.get("match_fused_capacity_mode") is not True
    assert _multi_partition(killed["clusters"]) == _multi_partition(classic["clusters"])
    assert_frame_equal(
        _golden_content(killed["golden"]), _golden_content(classic["golden"])
    )


@requires_kernel
def test_fs_fused_declines_uncovered_falls_through(monkeypatch):
    """Flag set but the FS config is NOT covered (a valid classic FS scorer that
    is outside the fused-FS scorer set) -> both the weighted and FS
    short-circuits decline and classic FS runs unchanged."""
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    df = _people_df()
    # qgram is a valid FS block scorer but NOT in _FUSED_FS_SCORER_IDS.
    assert match_fused_fs_ready(_fs_config(scorer="qgram")) is False

    plain = run_dedupe_df(df, _fs_config(scorer="qgram"))
    flagged = run_dedupe_df(df, _flag(_fs_config(scorer="qgram")))
    assert flagged.get("match_fused_capacity_mode") is not True
    assert _multi_partition(flagged["clusters"]) == _multi_partition(plain["clusters"])
    assert_frame_equal(
        _golden_content(flagged["golden"]), _golden_content(plain["golden"])
    )


def test_fs_no_flag_uses_classic(monkeypatch):
    """No _use_fused_match flag (no est-RSS pressure) -> classic FS, no capacity
    marker. Runs without the kernel (classic path only)."""
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    df = _people_df()
    result = run_dedupe_df(df, _fs_config())
    assert result.get("match_fused_capacity_mode") is not True
    # #2417: the classic B2c FS path leaves `scored_pairs` None and carries
    # the Arrow backing, so read it the way real consumers do. Asserting
    # non-EMPTY (not merely non-None) keeps the original intent -- the
    # classic path ran and produced pairs.
    from goldenmatch.core.pairs import materialize_scored_pairs
    assert materialize_scored_pairs(result)


# ── extended scorer coverage: the fused FS kernel now scores each field through
# fs-core's `field_similarity` (the classic dispatch), so the reference-data name
# scorers (ids 4/5) + ensemble (id 6) are covered, not just score_one 0..=3. ──


def _given_names_available() -> bool:
    try:
        from goldenmatch.core.probabilistic import _fs_name_refdata_available

        return _fs_name_refdata_available({"given_name_aliased_jw"})
    except Exception:
        return False


requires_given_names = pytest.mark.skipif(
    not _given_names_available(),
    reason="given-name alias refdata pack not loaded",
)


def _alias_df() -> pl.DataFrame:
    """Within each zip block, given-name ALIASES the alias table links but a plain
    string scorer would not (William/Bill/Will). Alias agreement is a clean 1.0,
    so EM is stable and both paths link the three 3-member groups deterministically."""
    rows: list[dict] = []
    for c, forms in enumerate(
        [["William", "Bill", "Will"], ["Robert", "Bob", "Bobby"], ["Elizabeth", "Beth", "Liz"]]
    ):
        for gn in forms:
            rows.append({"given_name": gn, "zip": f"300{c:02d}"})
    rows.append({"given_name": "Zelda", "zip": "39999"})
    return pl.DataFrame(rows)


def _alias_config() -> GoldenMatchConfig:
    cfg = _fs_config(scorer="given_name_aliased_jw")
    cfg.get_matchkeys()[0].fields[0].field = "given_name"
    return cfg


def test_fs_fused_covers_ensemble():
    """`ensemble` (FS id 6) is now a covered fused-FS field scorer (was declined
    pre-extension: the fused kernel scored id 6 via score_one's 0.0 catch-all)."""
    assert match_fused_fs_ready(_fs_config(scorer="ensemble")) is True


@requires_kernel
def test_fs_fused_parity_ensemble(monkeypatch):
    """Fused == classic on an `ensemble`-scorer FS matchkey (membership + golden)."""
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    df = _people_df()
    classic = run_dedupe_df(df, _fs_config(scorer="ensemble"))
    fused = run_dedupe_df(df, _flag(_fs_config(scorer="ensemble")))
    assert fused["match_fused_capacity_mode"] is True
    assert _multi_partition(fused["clusters"]) == _multi_partition(classic["clusters"])
    assert_frame_equal(
        _golden_content(fused["golden"]), _golden_content(classic["golden"])
    )


def _cluster_partition_from_fused_table(tbl) -> set[frozenset[int]]:
    """(__row_id__, __cluster_id__) fused-kernel Table -> multi-member partition,
    same shape as ``_multi_partition`` over the classic ``clusters`` dict."""
    import collections

    by_cluster: dict[int, list[int]] = collections.defaultdict(list)
    for rid, cid in zip(
        tbl.column("__row_id__").to_pylist(), tbl.column("__cluster_id__").to_pylist()
    ):
        by_cluster[cid].append(rid)
    return {frozenset(members) for members in by_cluster.values() if len(members) > 1}


@requires_kernel
def test_fused_fs_assignments_mirrors_inmemory_short_circuit(monkeypatch):
    """backends.fs_out_of_core._fused_fs_assignments's docstring claims its
    column gathering + ``GoldenMatchConfig`` shape "mirror the in-memory
    ``_run_fused_fs_short_circuit`` [now ``_run_fused_fs_match_short_circuit``]
    exactly (byte-parity by construction)". Drive both from the SAME trained EM
    over the SAME data and assert the fused-kernel cluster partition each
    produces is identical."""
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    import goldenmatch.core.probabilistic as probabilistic_mod
    from goldenmatch.backends.fs_out_of_core import _fused_fs_assignments
    from goldenmatch.core.blocker import build_blocks, collect_blocking_fields
    from goldenmatch.core.frame import to_frame as _tf
    from goldenmatch.core.probabilistic import load_or_train_em

    # Needs a __row_id__ column -- the pipeline adds one (_add_row_ids) before
    # either short-circuit runs; add it once up front so both sides + the EM
    # trainer see the identical ids.
    df = _people_df().with_row_index("__row_id__")
    cfg = _fs_config()
    mk = cfg.get_matchkeys()[0]

    blocking_fields = collect_blocking_fields(cfg.blocking, for_em=True)
    blocks = list(build_blocks(df.lazy(), cfg.blocking))
    em_result = load_or_train_em(df, mk, blocks=blocks, blocking_fields=blocking_fields)

    # Streaming side: fs_out_of_core._fused_fs_assignments, called directly with
    # the pre-trained EM (exactly how run_fs_dedupe_sequential feeds it).
    base = _tf(df).to_arrow()
    streaming_tbl = _fused_fs_assignments(base, cfg.blocking, mk, em_result, mk.link_threshold)
    assert streaming_tbl is not None
    streaming_partition = _cluster_partition_from_fused_table(streaming_tbl)

    # In-memory side: force _run_fused_fs_match_short_circuit (via run_dedupe_df)
    # to reuse the SAME trained EM instead of retraining its own, so both sides
    # run the identical fused kernel call over the identical model.
    monkeypatch.setattr(probabilistic_mod, "load_or_train_em", lambda *a, **k: em_result)
    inmem = run_dedupe_df(df, _flag(cfg))
    assert inmem["match_fused_capacity_mode"] is True
    inmem_partition = _multi_partition(inmem["clusters"])

    assert streaming_partition == inmem_partition


@requires_given_names
def test_fs_fused_covers_name_scorer():
    """A reference-data name scorer (`given_name_aliased_jw`, FS id 5) is now a
    covered fused-FS field scorer when the alias pack is loaded + the wheel carries
    FUSED_FS_SUPPORTS_NAME_SCORERS. Was declined pre-extension."""
    assert match_fused_fs_ready(_alias_config()) is True


@requires_kernel
@requires_given_names
def test_fs_fused_parity_name_scorer(monkeypatch):
    """The fused kernel reaches the injected alias table: William/Bill/Will link
    into a 3-member cluster (a plain scorer would not), byte-identical to the
    classic native FS path -- proving the name-scorer dispatch, not a silent JW."""
    monkeypatch.delenv("GOLDENMATCH_MATCH_FUSED", raising=False)
    df = _alias_df()
    classic = run_dedupe_df(df, _alias_config())
    fused = run_dedupe_df(df, _flag(_alias_config()))
    assert fused["match_fused_capacity_mode"] is True
    # The alias table is genuinely consulted: three 3-member alias groups link.
    assert sorted(len(c) for c in _multi_partition(fused["clusters"])) == [3, 3, 3]
    assert _multi_partition(fused["clusters"]) == _multi_partition(classic["clusters"])
    assert set(fused["dupes"]["__row_id__"].to_pylist()) == set(
        classic["dupes"]["__row_id__"].to_pylist()
    )
    assert_frame_equal(
        _golden_content(fused["golden"]), _golden_content(classic["golden"])
    )


def test_fs_fused_name_scorer_declines_on_old_wheel(monkeypatch):
    """Wheel-skew guard: a wheel whose fused kernel lacks the field_similarity
    dispatch (no FUSED_FS_SUPPORTS_NAME_SCORERS) declines a name/ensemble field to
    the classic path, so it is never scored 0.0 via the old score_one catch-all."""
    class _OldWheel:
        # Every OTHER FS capability present, but NOT the fused name-scorer dispatch.
        FS_SUPPORTS_MISSING_NEUTRAL = True
        FS_SUPPORTS_NE = True
        FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS = True
        FUSED_FS_SUPPORTS_NAME_SCORERS = False

        def __getattr__(self, name):  # match_fused_fs present, flags default False
            return None

    # String target keeps the file on a single `_native_loader` import style
    # (the module-level `from ... import native_module` in `_kernel_present`);
    # `_fused_fs_matchkey_covered` re-imports the symbol at call time, so patching
    # the module attribute is picked up.
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module", lambda: _OldWheel()
    )
    # ensemble needs the fused dispatch flag but no refdata pack -> isolates the
    # capability probe from the pack-availability gate.
    assert match_fused_fs_ready(_fs_config(scorer="ensemble")) is False
