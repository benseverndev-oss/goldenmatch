"""Cross-device / channel stitching (#1110, epic #1108)."""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.identity import stitching as st
from goldenmatch.identity.stitching import (
    DEFAULT_CHANNEL_TRUST,
    StitchResult,
    adjust_score,
    channel_trust,
    classify_channel,
    cross_channel_factor,
    deterministic_stitch_pairs,
    stitch_frame,
)

# ── Channel classification + trust ──────────────────────────────────────────


def test_classify_explicit_channel_column_wins():
    assert classify_channel({"channel": "CRM", "__source__": "web_cookies"}) == "crm"


def test_classify_source_substring_hint():
    assert classify_channel({"__source__": "salesforce_export"}) == st.CHANNEL_CRM
    assert classify_channel({"__source__": "web_clickstream"}) == st.CHANNEL_WEB
    assert classify_channel({"__source__": "pos_terminal_3"}) == st.CHANNEL_OFFLINE


def test_classify_channel_map_override_beats_hint():
    # source "acme" carries no hint; map routes it to email.
    assert (
        classify_channel(
            {"__source__": "acme"}, channel_map={"acme": "email"}
        )
        == "email"
    )


def test_classify_unknown_default():
    assert classify_channel({"__source__": "mystery_feed"}) == st.CHANNEL_UNKNOWN
    assert classify_channel({}) == st.CHANNEL_UNKNOWN


def test_channel_trust_known_and_unknown():
    assert channel_trust("crm") == 1.0
    assert channel_trust("web") == 0.5
    # unmapped -> the unknown weight, never 0.
    assert channel_trust("does_not_exist") == DEFAULT_CHANNEL_TRUST["unknown"]
    assert channel_trust(None) == DEFAULT_CHANNEL_TRUST["unknown"]


def test_channel_trust_custom_map():
    assert channel_trust("web", {"web": 0.9}) == 0.9


# ── Cross-channel scoring adjustment ────────────────────────────────────────


def test_cross_channel_factor_geometric_mean():
    assert cross_channel_factor("crm", "crm") == pytest.approx(1.0)
    assert cross_channel_factor("web", "web") == pytest.approx(0.5)
    assert cross_channel_factor("crm", "web") == pytest.approx((1.0 * 0.5) ** 0.5)


def test_cross_channel_factor_symmetric():
    assert cross_channel_factor("crm", "web") == cross_channel_factor("web", "crm")


def test_adjust_score_downweights_cross_channel():
    # A 0.9 PII match across crm<->web is worth less than within crm.
    within = adjust_score(0.9, "crm", "crm")
    across = adjust_score(0.9, "crm", "web")
    assert within == pytest.approx(0.9)
    assert across < within
    assert across == pytest.approx(0.9 * (0.5 ** 0.5))


# ── Deterministic stitching ─────────────────────────────────────────────────


def test_deterministic_stitch_shared_device_key():
    df = pl.DataFrame({
        "__row_id__": [0, 1, 2],
        "cookie_id": ["c1", "c1", "c2"],
    })
    pairs = deterministic_stitch_pairs(df, ["cookie_id"])
    # rows 0,1 share c1 -> one star edge; row 2 alone -> none.
    assert pairs == [(0, 1, "cookie_id")]


def test_deterministic_stitch_ignores_null_and_blank():
    df = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "device_id": [None, "", "d", "d"],
    })
    pairs = deterministic_stitch_pairs(df, ["device_id"])
    assert pairs == [(2, 3, "device_id")]


def test_deterministic_stitch_star_not_all_pairs():
    df = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "login_id": ["u", "u", "u", "u"],
    })
    pairs = deterministic_stitch_pairs(df, ["login_id"])
    # star to anchor 0, NOT 6 all-pairs.
    assert pairs == [(0, 1, "login_id"), (0, 2, "login_id"), (0, 3, "login_id")]


def test_deterministic_stitch_missing_key_column():
    df = pl.DataFrame({"__row_id__": [0, 1], "name": ["a", "b"]})
    assert deterministic_stitch_pairs(df, ["cookie_id"]) == []


# ── stitch_frame: deterministic path ────────────────────────────────────────


def test_stitch_frame_device_stitch_across_channels():
    # Same person on CRM and web, joined by a shared cookie.
    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "__source__": ["salesforce", "web_cookies"],
        "cookie_id": ["abc", "abc"],
        "name": ["Jane Doe", None],
    })
    res = stitch_frame(df, device_keys=["cookie_id"])
    assert isinstance(res, StitchResult)
    assert len(res.multi_member_groups) == 1
    g = res.multi_member_groups[0]
    assert g.members == [0, 1]
    assert g.deterministic is True
    assert g.cross_channel is True
    assert set(g.channels) == {"crm", "web"}
    assert g.device_keys == ["cookie_id"]
    # Hard device link -> full confidence even across channels.
    assert g.confidence == 1.0


def test_stitch_frame_no_links_all_singletons():
    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "__source__": ["crm", "web"],
        "cookie_id": ["a", "b"],
    })
    res = stitch_frame(df, device_keys=["cookie_id"])
    assert res.multi_member_groups == []
    assert len(res.groups) == 2
    assert all(g.confidence == 1.0 and not g.deterministic for g in res.groups)


# ── stitch_frame: probabilistic + cross-channel confidence ──────────────────


def test_stitch_frame_probabilistic_cross_channel_downweighted():
    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "__source__": ["salesforce", "web_cookies"],
    })
    # PII match of 0.9, no shared device -> probabilistic only.
    res = stitch_frame(df, scored_pairs=[(0, 1, 0.9)])
    assert len(res.multi_member_groups) == 1
    g = res.multi_member_groups[0]
    assert g.deterministic is False
    assert g.cross_channel is True
    # confidence is the downweighted cross-channel score, not 0.9.
    assert g.confidence == pytest.approx(0.9 * (0.5 ** 0.5), abs=1e-6)


def test_stitch_frame_probabilistic_within_channel_not_downweighted():
    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "__source__": ["salesforce", "salesforce_eu"],
    })
    res = stitch_frame(df, scored_pairs=[(0, 1, 0.9)])
    g = res.multi_member_groups[0]
    assert g.confidence == pytest.approx(0.9)


def test_stitch_frame_prob_threshold_drops_weak_edges():
    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "__source__": ["web_a", "web_b"],
    })
    # 0.6 * sqrt(0.5*0.5) = 0.3 < threshold 0.5 -> edge dropped, no group.
    res = stitch_frame(df, scored_pairs=[(0, 1, 0.6)], prob_threshold=0.5)
    assert res.multi_member_groups == []
    assert res.n_probabilistic_pairs == 0


def test_stitch_frame_no_adjust_keeps_raw_score():
    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "__source__": ["crm", "web"],
    })
    res = stitch_frame(
        df, scored_pairs=[(0, 1, 0.8)], adjust_cross_channel=False
    )
    assert res.multi_member_groups[0].confidence == pytest.approx(0.8)


def test_stitch_frame_confidence_is_weakest_edge():
    # Chain 0-1 (det, 1.0) and 1-2 (prob, downweighted) -> group of 3 whose
    # confidence is the weaker probabilistic edge.
    df = pl.DataFrame({
        "__row_id__": [0, 1, 2],
        "__source__": ["crm", "crm", "web_cookies"],
        "cookie_id": ["x", "x", None],
    })
    res = stitch_frame(
        df, scored_pairs=[(1, 2, 0.95)], device_keys=["cookie_id"]
    )
    assert len(res.multi_member_groups) == 1
    g = res.multi_member_groups[0]
    assert g.members == [0, 1, 2]
    assert g.deterministic is True  # has the device link
    assert g.cross_channel is True
    # weakest edge = the crm<->web probabilistic one.
    assert g.confidence == pytest.approx(0.95 * (0.5 ** 0.5), abs=1e-6)


def test_stitch_frame_empty_frame():
    df = pl.DataFrame({"__row_id__": []}, schema={"__row_id__": pl.Int64})
    res = stitch_frame(df)
    assert res.groups == []


def test_stitch_result_as_dict_counts():
    df = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "__source__": ["crm", "web_cookies", "crm", "web"],
        "cookie_id": ["a", "a", None, None],
    })
    res = stitch_frame(
        df, scored_pairs=[(2, 3, 0.99)], device_keys=["cookie_id"]
    )
    d = res.as_dict()
    assert d["n_multi_member"] == 2
    assert d["n_deterministic_pairs"] == 1
    assert d["n_probabilistic_pairs"] == 1
    assert d["n_device_stitched"] == 1
    assert d["n_cross_channel"] == 2


# ── Config-driven defaults ──────────────────────────────────────────────────


def test_stitch_frame_reads_config_defaults():
    from goldenmatch.config.schemas import ChannelStitchConfig

    cfg = ChannelStitchConfig(
        enabled=True,
        device_keys=["my_key"],
        channel_column="chan",
        channel_trust={"crm": 1.0, "web": 0.2},
        prob_threshold=0.0,
    )
    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "chan": ["crm", "web"],
        "my_key": ["k", "k"],
    })
    res = stitch_frame(df, config=cfg)
    # device_keys + channel_column came from config.
    g = res.multi_member_groups[0]
    assert g.deterministic is True
    assert set(g.channels) == {"crm", "web"}
    assert g.device_keys == ["my_key"]


def test_explicit_kwarg_overrides_config():
    from goldenmatch.config.schemas import ChannelStitchConfig

    cfg = ChannelStitchConfig(device_keys=["cfg_key"])
    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "kwarg_key": ["k", "k"],
        "cfg_key": ["x", "y"],
    })
    # explicit device_keys wins -> stitch on kwarg_key (shared), not cfg_key.
    res = stitch_frame(df, config=cfg, device_keys=["kwarg_key"])
    assert len(res.multi_member_groups) == 1
    assert res.multi_member_groups[0].members == [0, 1]
