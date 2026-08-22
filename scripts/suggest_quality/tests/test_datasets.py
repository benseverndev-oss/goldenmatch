"""Smoke tests for the suggest_quality dataset registry.

Mirrors ``scripts.autoconfig_quality.tests.test_datasets`` — same assertions,
adapted for the suggest_quality registry and the extra ``synthetic`` entry.
"""
import polars as pl

from scripts.suggest_quality.datasets import REGISTRY, Dataset, _pairs_to_row_index


def test_registry_is_non_empty():
    assert len(REGISTRY) >= 1


def test_anchors_always_load():
    by_name = {d.name: d for d in REGISTRY}
    for n in ("anchor_sparse_zip", "anchor_shared_email", "anchor_person_match"):
        d = by_name[n]
        assert isinstance(d, Dataset)
        assert d.kind == "anchor"
        loaded = d.loader()
        assert loaded is not None
        df, gt = loaded
        assert df.height > 0


def test_synthetic_always_loads():
    by_name = {d.name: d for d in REGISTRY}
    d = by_name["synthetic"]
    assert d.kind == "real"
    loaded = d.loader()
    assert loaded is not None
    df, gt = loaded
    assert df.height > 0
    assert len(gt) > 0  # gen_labeled produces GT


def test_person_anchor_has_gt_others_empty():
    by_name = {d.name: d for d in REGISTRY}
    _, gt = by_name["anchor_person_match"].loader()
    assert len(gt) > 0
    _, gt2 = by_name["anchor_sparse_zip"].loader()
    assert gt2 == set()


def test_real_loader_skips_when_absent():
    by_name = {d.name: d for d in REGISTRY}
    dblp = by_name["dblp_acm"]
    assert dblp.kind == "real"
    res = dblp.loader()
    assert res is None or (isinstance(res, tuple) and len(res) == 2)


def test_pairs_to_row_index_maps_and_canonicalizes():
    df = pl.DataFrame({"id": ["a", "b", "c"]})
    gt = _pairs_to_row_index(df, "id", {("c", "a"), ("b", "b"), ("x", "a")})
    assert gt == {(0, 2)}


def test_historical_50k_registered_full_scan():
    by_name = {d.name: d for d in REGISTRY}
    h = by_name["historical_50k"]
    assert h.full_scan is True


# ── vendored DBLP-ACM (#2635) ────────────────────────────────────────────────
#
# These are the durable guard for the reason the corpus is committed at all: the
# suggest-quality gate blesses a per-dataset scorecard, and a blessed dataset
# that cannot be LOADED becomes a permanent MISSING failure (#2566). A
# fetched-at-runtime corpus is absent whenever upstream is unreachable, so
# "dblp_acm loads with no network" is a gate precondition, not a nicety.
# If someone deletes the vendored copy to save 852 KB, these fail loudly here
# rather than silently turning the gate red on main.


def test_dblp_acm_is_vendored_and_loads():
    """The committed corpus resolves and parses without any network access."""
    from scripts.suggest_quality.datasets import _dblp_acm, _dblp_acm_dir

    d = _dblp_acm_dir()
    assert d is not None, (
        "DBLP-ACM not found. The vendored copy under "
        "scripts/suggest_quality/vendored/DBLP-ACM/ is committed on purpose "
        "(#2635) so the gate can run this dataset offline -- see its "
        "PROVENANCE.md before removing it."
    )
    loaded = _dblp_acm()
    assert loaded is not None, f"DBLP-ACM present at {d} but failed to parse"
    df, gt = loaded
    # 2616 DBLP + 2294 ACM records, 2224 cross-source ground-truth pairs.
    assert df.height == 4910, f"expected 4910 rows, got {df.height}"
    assert len(gt) == 2224, f"expected 2224 gt pairs, got {len(gt)}"


def test_dblp_acm_vendored_copy_is_preferred():
    """Vendored path wins over the gitignored runtime-download location.

    Ordering is load-bearing: a stale/partial local download must not shadow the
    committed corpus the gate is blessed against.
    """
    from scripts.suggest_quality.datasets import _VENDORED, _dblp_acm_dir

    assert _dblp_acm_dir() == _VENDORED / "DBLP-ACM"


def test_dblp_acm_carries_year_but_is_not_a_matchkey_column():
    """`year` is present on the frame -- the premise of #2633.

    The candidate-set ceiling there is caused by auto-suggest drawing blocking
    candidates only from matchkey columns (title/authors/venue), so `year` can
    never be proposed even though every ground-truth pair agrees on it. If this
    assertion ever fails the corpus changed shape and #2633's analysis needs
    re-deriving.
    """
    from scripts.suggest_quality.datasets import _dblp_acm

    df, _ = _dblp_acm()
    assert "year" in df.columns


def test_dblp_acm_vendored_checksums_match_provenance():
    """The corpus on disk is the corpus PROVENANCE.md says it is.

    PROVENANCE.md records a sha256 per file but nothing asserted them, so a
    corrupted, truncated or silently re-fetched corpus would still load and the
    gate would happily bless numbers measured on different data. Parsing the
    doc rather than hardcoding the digests here keeps one source of truth: if
    someone re-fetches upstream and updates the files, this fails until the
    doc is updated to match (and vice versa).
    """
    import hashlib
    import re

    from scripts.suggest_quality.datasets import _VENDORED

    d = _VENDORED / "DBLP-ACM"
    prov = (d / "PROVENANCE.md").read_text(encoding="utf-8")

    # rows look like: | `DBLP2.csv` | `<64 hex>` |
    documented = dict(
        re.findall(r"\|\s*`([^`]+\.csv)`\s*\|\s*`([0-9a-f]{64})`\s*\|", prov)
    )
    assert len(documented) == 3, (
        f"expected 3 checksum rows in PROVENANCE.md, parsed {len(documented)}: "
        f"{sorted(documented)}"
    )

    for fname, expected in sorted(documented.items()):
        actual = hashlib.sha256((d / fname).read_bytes()).hexdigest()
        assert actual == expected, (
            f"{fname} does not match PROVENANCE.md: expected {expected}, got "
            f"{actual}. Either the vendored file changed (re-fetched or "
            f"corrupted) or the doc is stale -- reconcile before trusting any "
            f"number measured on this corpus."
        )
