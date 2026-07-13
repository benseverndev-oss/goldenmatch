"""D2s-a pins: exact-match + static-blocking entries accept a seam Frame.

The spine descent (plan 2026-07-12, D2s series) moves the arrow->polars
boundary below the collect; these fixtures pin that the two hot lazy-consumer
entries produce identical output for a pl.LazyFrame, a PolarsFrame, and an
ArrowFrame carrying the same rows.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.blocker import build_blocks
from goldenmatch.core.frame import ArrowFrame, PolarsFrame
from goldenmatch.core.scorer import _find_exact_match_ids, find_exact_matches


def _df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "__row_id__": pl.Series([0, 1, 2, 3, 4, 5], dtype=pl.Int64),
            "name": ["ann", "ann", "bob", None, "nan", "cat"],
            "__mk_k__": ["a1", "a1", "b2", None, "", "c3"],
        }
    )


def _entries():
    df = _df()
    return {
        "lazy": df.lazy(),
        "polars_frame": PolarsFrame(df),
        "arrow_frame": ArrowFrame(df.to_arrow()),
    }


@pytest.mark.parametrize("rep", ["lazy", "polars_frame", "arrow_frame"])
def test_find_exact_match_ids_dual_rep(rep):
    mk = MatchkeyConfig(name="k", type="exact", fields=[MatchkeyField(field="name")])
    ids_a, ids_b = _find_exact_match_ids(_entries()[rep], mk)
    assert sorted(zip(ids_a.tolist(), ids_b.tolist())) == [(0, 1)]


@pytest.mark.parametrize("rep", ["lazy", "polars_frame", "arrow_frame"])
def test_find_exact_matches_dual_rep(rep):
    mk = MatchkeyConfig(name="k", type="exact", fields=[MatchkeyField(field="name")])
    assert find_exact_matches(_entries()[rep], mk) == [(0, 1, 1.0)]


@pytest.mark.parametrize("rep", ["lazy", "polars_frame", "arrow_frame"])
def test_build_static_blocks_dual_rep(rep):
    # "nan" is a sentinel-filtered key; null drops; ann pair survives.
    cfg = BlockingConfig(keys=[BlockingKeyConfig(fields=["name"])])
    blocks = build_blocks(_entries()[rep], cfg)
    keyed = {b.block_key: sorted(b.materialize().column("__row_id__").to_list()) for b in blocks}
    assert keyed == {"ann": [0, 1]}


# -- D2s-b: Frame-level precompute_matchkey_transforms ------------------------------


def _mk_cfgs():
    from goldenmatch.config.schemas import NegativeEvidenceField

    return [
        MatchkeyConfig(
            name="a",
            type="weighted", threshold=0.8,
            fields=[
                MatchkeyField(field="first", transforms=["lowercase", "strip"], scorer="jaro_winkler", weight=1.0),
                MatchkeyField(field="last", transforms=["soundex"], scorer="jaro_winkler", weight=1.0),
                MatchkeyField(field="plain", scorer="exact", weight=1.0),
            ],
            negative_evidence=[
                NegativeEvidenceField(
                    field="full_name",
                    derive_from=["first", "last"],
                    scorer="jaro_winkler",
                    threshold=0.9,
                    penalty=0.5,
                )
            ],
        ),
        # Same (field, transforms) as mk "a" -> signature reuse, no dup column.
        MatchkeyConfig(
            name="b",
            type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="first", transforms=["lowercase", "strip"], scorer="jaro_winkler", weight=1.0)],
        ),
    ]


def test_precompute_frame_arrow_matches_legacy_polars():
    from goldenmatch.core.matchkey import (
        precompute_matchkey_transforms,
        precompute_matchkey_transforms_frame,
    )

    df = pl.DataFrame(
        {
            "__row_id__": pl.Series([0, 1, 2], dtype=pl.Int64),
            "first": ["  Ann ", None, "BOB"],
            "last": ["Smith", "Jones", None],
            "plain": ["x", None, "z"],
        }
    )
    mks = _mk_cfgs()
    legacy = precompute_matchkey_transforms(df, mks)
    arrow = precompute_matchkey_transforms_frame(ArrowFrame(df.to_arrow()), mks)
    # Column ORDER is not part of the contract (consumers read __xform_*__
    # by name; legacy batches python-fallback columns after all native ones).
    assert set(arrow.columns) == set(legacy.columns)
    for c in legacy.columns:
        assert arrow.column(c).to_list() == legacy[c].to_list(), c


def test_precompute_frame_polars_delegates_verbatim():
    from goldenmatch.core.matchkey import (
        precompute_matchkey_transforms,
        precompute_matchkey_transforms_frame,
    )

    df = pl.DataFrame({"__row_id__": [0, 1], "first": ["A", "b"]})
    mks = [
        MatchkeyConfig(
            name="a", type="weighted", threshold=0.8, fields=[MatchkeyField(field="first", transforms=["lowercase"], scorer="jaro_winkler", weight=1.0)]
        )
    ]
    out = precompute_matchkey_transforms_frame(PolarsFrame(df), mks)
    assert out.native.equals(precompute_matchkey_transforms(df, mks))


def test_precompute_frame_skips_record_embedding_and_missing_fields():
    from goldenmatch.core.matchkey import precompute_matchkey_transforms_frame

    df = pl.DataFrame({"__row_id__": [0], "first": ["a"]})
    mks = [
        MatchkeyConfig(
            name="a",
            type="weighted", threshold=0.8,
            fields=[
                MatchkeyField(field="first", scorer="record_embedding", columns=["first"], weight=1.0),
                MatchkeyField(field="absent", scorer="exact", weight=1.0),
            ],
        )
    ]
    out = precompute_matchkey_transforms_frame(ArrowFrame(df.to_arrow()), mks)
    assert list(out.columns) == ["__row_id__", "first"]


# -- D2s-d2a: the Frame-lane eligibility predicate ----------------------------------


def _base_cfg(**overrides):
    from goldenmatch.config.schemas import GoldenMatchConfig

    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="k",
                type="exact",
                fields=[MatchkeyField(field="name")],
            )
        ],
        **overrides,
    )
    return cfg


def _eligible(cfg, writes_outputs=False):
    from goldenmatch.core.pipeline import _frame_lane_eligible

    return _frame_lane_eligible(cfg, cfg.get_matchkeys(), writes_outputs=writes_outputs)


def test_frame_lane_eligible_baseline():
    assert _eligible(_base_cfg()) is True


def test_frame_lane_allows_writes_outputs():
    # W-2: write_output is dual-rep + build_lineage reads via the seam.
    assert _eligible(_base_cfg(), writes_outputs=True) is True


def test_frame_lane_declines_throughput_plan():
    cfg = _base_cfg()
    object.__setattr__(cfg, "_throughput_plan", object())
    assert _eligible(cfg) is False


def test_frame_lane_declines_preflight():
    cfg = _base_cfg()
    object.__setattr__(cfg, "_preflight_report", {"x": 1})
    assert _eligible(cfg) is False


def test_frame_lane_declines_probabilistic_mk():
    from goldenmatch.config.schemas import GoldenMatchConfig

    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="p",
                type="probabilistic",
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
                threshold=0.8,
            )
        ],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["name"])]),
    )
    assert _eligible(cfg) is False


def test_frame_lane_declines_ne_on_exact():
    from goldenmatch.config.schemas import NegativeEvidenceField

    cfg = _base_cfg()
    cfg.matchkeys[0].negative_evidence = [
        NegativeEvidenceField(
            field="name", scorer="jaro_winkler", threshold=0.9, penalty=0.5
        )
    ]
    assert _eligible(cfg) is False


# -- D2s-d2b: the Frame lane e2e ------------------------------------------------------


def _lane_csv(tmp_path):
    csv = tmp_path / "people.csv"
    rows = ["first,last,city"]
    names = [
        ("ann", "smith"), ("anne", "smith"), ("bob", "jones"), ("bobby", "jones"),
        ("cara", "lee"), ("kara", "lee"), ("dan", "kim"), ("erin", "park"),
    ]
    for i, (f, l) in enumerate(names):
        rows.append(f"{f},{l},c{i % 3}")
    csv.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return csv


def _lane_cfg(backend=None, mk_type="weighted"):
    from goldenmatch.config.schemas import (
        GoldenMatchConfig,
        QualityConfig,
        TransformConfig,
    )

    if mk_type == "exact":
        mks = [
            MatchkeyConfig(
                name="exact_name",
                type="exact",
                fields=[MatchkeyField(field="first"), MatchkeyField(field="last")],
            )
        ]
        blocking = None
    else:
        mks = [
            MatchkeyConfig(
                name="fuzzy_name",
                type="weighted",
                threshold=0.85,
                fields=[
                    MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0, transforms=["lowercase"]),
                    MatchkeyField(field="last", scorer="jaro_winkler", weight=1.0, transforms=["lowercase"]),
                ],
            )
        ]
        blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["last"], transforms=["lowercase"])])
    kw = dict(
        matchkeys=mks,
        quality=QualityConfig(mode="disabled"),
        transform=TransformConfig(mode="disabled"),
    )
    if blocking is not None:
        kw["blocking"] = blocking
    if backend is not None:
        kw["backend"] = backend
    return GoldenMatchConfig(**kw)


def _norm_result(r):
    g = r["golden"]
    rows = (
        sorted((tuple(sorted(x.items())) for x in g.to_pylist()), key=str)
        if g is not None
        else None
    )
    return (
        rows,
        len(r["clusters"]),
        r["dupes"].num_rows if r["dupes"] is not None else 0,
        r["unique"].num_rows if r["unique"] is not None else 0,
    )


@pytest.mark.parametrize("backend,mk_type", [(None, "exact"), (None, "weighted"), ("bucket", "weighted")])
def test_frame_lane_engages_and_matches_classic(tmp_path, monkeypatch, backend, mk_type):
    """The Frame lane (arrow spine past the collect) engages on an eligible
    eager-arrow run and reproduces the classic shim lane exactly."""
    import goldenmatch.core.pipeline as P

    monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")
    csv = _lane_csv(tmp_path)
    cfg = _lane_cfg(backend=backend, mk_type=mk_type)

    hits = []
    orig = P._frame_lane_eligible

    def spy(*a, **k):
        r = orig(*a, **k)
        hits.append(r)
        return r

    monkeypatch.setattr(P, "_frame_lane_eligible", spy)
    frame_lane = P.run_dedupe([(str(csv), "people")], cfg)
    assert hits and hits[0] is True, f"Frame lane did not engage: {hits}"

    monkeypatch.setenv("GOLDENMATCH_FRAME_LANE", "0")
    classic = P.run_dedupe([(str(csv), "people")], cfg)
    assert _norm_result(frame_lane) == _norm_result(classic)


def test_frame_lane_engages_when_quality_enabled(tmp_path, monkeypatch):
    """W-1 widening: default-on goldencheck quality no longer keeps the
    classic lane -- the predicate is consulted and the lane engages (the
    integration runs through the prep bridge)."""
    import goldenmatch.core.pipeline as P

    monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")
    csv = _lane_csv(tmp_path)
    from goldenmatch.config.schemas import GoldenMatchConfig

    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="exact_name",
                type="exact",
                fields=[MatchkeyField(field="first"), MatchkeyField(field="last")],
            )
        ]
    )
    hits = []
    orig = P._frame_lane_eligible
    monkeypatch.setattr(
        P, "_frame_lane_eligible", lambda *a, **k: (hits.append(orig(*a, **k)) or hits[-1])
    )
    res = P.run_dedupe([(str(csv), "people")], cfg)
    assert hits and hits[0] is True
    assert res["golden"] is None or hasattr(res["golden"], "num_rows")


def test_frames_path_fused_golden_gets_arrow_table(tmp_path, monkeypatch):
    """Deep-D2b: on the Frame lane the frames-path golden hands the fused
    kernel the lane-native pa.Table (no polars round-trip) and reproduces
    the classic lane exactly."""
    import goldenmatch.core.golden_fused as GF
    import goldenmatch.core.pipeline as P
    from goldenmatch.config.schemas import GoldenFieldRule, GoldenRulesConfig

    monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")
    csv = _lane_csv(tmp_path)
    cfg = _lane_cfg(mk_type="weighted")
    # field_rules -> _polars_native_eligible False -> the frames path tries
    # the fused kernel with the lane-native frame
    cfg.golden_rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"city": GoldenFieldRule(strategy="majority_vote")},
    )

    calls = []
    orig = GF.run_golden_fused_arrow

    def spy(columns, *a, **k):
        r = orig(columns, *a, **k)
        calls.append((type(columns).__name__, r is not None))
        return r

    monkeypatch.setattr(GF, "run_golden_fused_arrow", spy)
    frame_lane = P.run_dedupe([(str(csv), "people")], cfg)
    if not calls or not calls[0][1]:
        pytest.skip("native golden_fused kernel unavailable")
    assert calls[0][0] == "Table", calls
    monkeypatch.setenv("GOLDENMATCH_FRAME_LANE", "0")
    classic = P.run_dedupe([(str(csv), "people")], cfg)
    assert _norm_result(frame_lane) == _norm_result(classic)


def test_frame_lane_engages_on_default_config_with_prep_bridges(tmp_path, monkeypatch):
    """W-1 widening: a FULLY DEFAULT config (quality/transform default-ON)
    engages the Frame lane; the integrations run through the pa->pl->pa
    bridge in classic prep order and output matches the classic lane
    (including goldenflow's E.164 phone transform surviving the bridge)."""
    import goldenmatch.core.pipeline as P
    from goldenmatch.config.schemas import GoldenMatchConfig

    monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")
    csv = tmp_path / "people.csv"
    csv.write_text(
        "first,last,phone\n"
        "ann,smith,(267) 555-1234\n"
        "ann,smith,267-555-1234\n"
        "bob,jones,555 0001\n"
        "bob,jones,5550001\n"
        "cara,lee,\n"
        "dan,kim,9998887777\n",
        encoding="utf-8",
    )
    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="exact_name",
                type="exact",
                fields=[MatchkeyField(field="first"), MatchkeyField(field="last")],
            )
        ]
    )
    hits = []
    orig = P._frame_lane_eligible
    monkeypatch.setattr(
        P, "_frame_lane_eligible", lambda *a, **k: (hits.append(orig(*a, **k)) or hits[-1])
    )
    frame_lane = P.run_dedupe([(str(csv), "people")], cfg)
    assert hits and hits[0] is True, f"Frame lane did not engage on default config: {hits}"
    monkeypatch.setenv("GOLDENMATCH_FRAME_LANE", "0")
    classic = P.run_dedupe([(str(csv), "people")], cfg)
    assert _norm_result(frame_lane) == _norm_result(classic)
    # the transform bridge actually ran: phones are E.164 in golden
    golden_rows = frame_lane["golden"].to_pylist()
    assert any(str(r.get("phone", "")).startswith("+") for r in golden_rows)


def test_frame_lane_file_outputs_and_lineage(tmp_path, monkeypatch):
    """W-2: a Frame-lane run with file outputs enabled writes golden/dupes/
    unique (+ lineage sidecar) identical in content to the classic lane."""
    import json

    import goldenmatch.core.pipeline as P
    from goldenmatch.config.schemas import GoldenMatchConfig, OutputConfig

    monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")
    csv = _lane_csv(tmp_path)
    cfg_kw = dict(
        matchkeys=[
            MatchkeyConfig(
                name="exact_name",
                type="exact",
                fields=[MatchkeyField(field="first"), MatchkeyField(field="last")],
            )
        ],
    )
    from goldenmatch.config.schemas import QualityConfig, TransformConfig

    cfg_kw["quality"] = QualityConfig(mode="disabled")
    cfg_kw["transform"] = TransformConfig(mode="disabled")

    outs = {}
    for lane, envval in (("frame", None), ("classic", "0")):
        d = tmp_path / lane
        cfg = GoldenMatchConfig(
            **cfg_kw, output=OutputConfig(directory=str(d), format="csv")
        )
        if envval is not None:
            monkeypatch.setenv("GOLDENMATCH_FRAME_LANE", envval)
        hits = []
        orig = P._frame_lane_eligible
        monkeypatch.setattr(
            P,
            "_frame_lane_eligible",
            lambda *a, **k: (hits.append(orig(*a, **k)) or hits[-1]),
        )
        P.run_dedupe(
            [(str(csv), "people")], cfg,
            output_golden=True, output_dupes=True, output_unique=True,
        )
        monkeypatch.setattr(P, "_frame_lane_eligible", orig)
        if envval is not None:
            monkeypatch.delenv("GOLDENMATCH_FRAME_LANE")
        else:
            assert hits and hits[0] is True, f"lane not engaged: {hits}"
        outs[lane] = {
            f.name: f.read_bytes() for f in sorted(d.glob("*")) if f.suffix != ".json"
        }
        lineage = list(d.glob("*lineage*.json"))
        outs[lane]["__lineage_records__"] = (
            len(json.loads(lineage[0].read_text())) if lineage else None
        )
    assert set(outs["frame"]) == set(outs["classic"])
    for name in outs["classic"]:
        assert outs["frame"][name] == outs["classic"][name], name


def test_frame_lane_validation_and_autofix(tmp_path, monkeypatch):
    """W-3: validation rules (quarantine split) + auto_fix run ON the Frame
    lane; quarantine/valid outputs match the classic lane."""
    import goldenmatch.core.pipeline as P
    from goldenmatch.config.schemas import (
        GoldenMatchConfig,
        QualityConfig,
        TransformConfig,
        ValidationConfig,
        ValidationRuleConfig,
    )

    monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")
    csv = tmp_path / "people.csv"
    csv.write_text(
        "first,last,email\n"
        "ann,smith,a@x.com\n"
        "ann,smith,a@x.com\n"
        "bob,jones,not-an-email\n"
        "cara,lee,c@y.com\n",
        encoding="utf-8",
    )
    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="exact_name",
                type="exact",
                fields=[MatchkeyField(field="first"), MatchkeyField(field="last")],
            )
        ],
        quality=QualityConfig(mode="disabled"),
        transform=TransformConfig(mode="disabled"),
        validation=ValidationConfig(
            auto_fix=True,
            rules=[
                ValidationRuleConfig(
                    column="email",
                    rule_type="format",
                    params={"type": "email"},
                    action="quarantine",
                )
            ],
        ),
    )
    hits = []
    orig = P._frame_lane_eligible
    monkeypatch.setattr(
        P, "_frame_lane_eligible", lambda *a, **k: (hits.append(orig(*a, **k)) or hits[-1])
    )
    frame_lane = P.run_dedupe([(str(csv), "people")], cfg)
    assert hits and hits[0] is True, f"lane not engaged: {hits}"
    monkeypatch.setenv("GOLDENMATCH_FRAME_LANE", "0")
    classic = P.run_dedupe([(str(csv), "people")], cfg)

    def q(r):
        qd = r["quarantine"]
        return sorted(map(str, qd.to_pylist())) if qd is not None else None

    assert q(frame_lane) == q(classic)
    assert q(frame_lane) is not None and len(q(frame_lane)) == 1  # bob quarantined
    assert _norm_result(frame_lane) == _norm_result(classic)
