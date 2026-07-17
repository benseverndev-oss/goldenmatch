"""Native FS name-scorer wiring (increment 4c).

The reference-data name scorers ``name_freq_weighted_jw`` /
``given_name_aliased_jw`` are FS-kernel scorer ids 4/5, dispatched inside
``goldenmatch-fs-core::score_fs_pair`` to the process-registered census / alias
tables. These tests lock the *Python-side gating* (which decides native vs numpy)
and the refdata export seam — both run without the built native extension. The
byte-parity of the kernel output vs the numpy scorer is asserted in the native
lane (``tests/test_native_parity.py`` against the real wheel).
"""
from __future__ import annotations

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import (
    _NAME_SCORER_IDS,
    _NATIVE_FS_SCORER_IDS,
    _fs_native_eligible,
)


def _mk_name(scorer="name_freq_weighted_jw", field="last_name"):
    return MatchkeyConfig(
        name="t",
        type="probabilistic",
        fields=[MatchkeyField(field=field, scorer=scorer, levels=3, partial_threshold=0.8)],
    )


def test_name_scorers_have_reserved_kernel_ids():
    # 0..=3 are score_one; 4/5 are the reserved reference-data name scorers,
    # matching goldenmatch-fs-core FS_SCORER_NAME_FREQ_WEIGHTED / _GIVEN_NAME_ALIASED.
    assert _NATIVE_FS_SCORER_IDS["name_freq_weighted_jw"] == 4
    assert _NATIVE_FS_SCORER_IDS["given_name_aliased_jw"] == 5
    assert _NAME_SCORER_IDS == {"name_freq_weighted_jw", "given_name_aliased_jw"}


class _FakeKernel:
    """A kernel advertising the pre-name-scorer capabilities. Toggle
    ``FS_SUPPORTS_NAME_SCORERS`` / ``FS_SUPPORTS_TF_ADJUSTMENT`` to simulate old
    vs new wheels."""

    FS_SUPPORTS_MISSING_NEUTRAL = True
    FS_SUPPORTS_LEVEL_THRESHOLDS = True

    def __init__(self, name_scorers: bool, tf: bool = False):
        if name_scorers:
            self.FS_SUPPORTS_NAME_SCORERS = True
        if tf:
            self.FS_SUPPORTS_TF_ADJUSTMENT = True

    def score_block_pairs_fs(self, *a, **kw):  # pragma: no cover - not invoked
        raise NotImplementedError


def _patch(monkeypatch, *, kernel_name_scorers, pack_available, kernel_tf=False):
    from goldenmatch.core import probabilistic as p

    monkeypatch.setattr(p, "_fs_native_enabled", lambda: True)
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module",
        lambda: _FakeKernel(kernel_name_scorers, tf=kernel_tf),
    )
    monkeypatch.setattr(p, "_fs_name_refdata_available", lambda scorers: pack_available)


def _mk_tf():
    return MatchkeyConfig(
        name="t",
        type="probabilistic",
        fields=[MatchkeyField(field="city", scorer="exact", levels=2, tf_adjustment=True)],
    )


def test_tf_field_declines_on_old_wheel(monkeypatch):
    # tf_adjustment field, wheel lacks FS_SUPPORTS_TF_ADJUSTMENT -> numpy path.
    _patch(monkeypatch, kernel_name_scorers=False, pack_available=True, kernel_tf=False)
    assert _fs_native_eligible(_mk_tf()) is False


def test_tf_field_native_on_new_wheel(monkeypatch):
    _patch(monkeypatch, kernel_name_scorers=False, pack_available=True, kernel_tf=True)
    assert _fs_native_eligible(_mk_tf()) is True


def test_old_wheel_without_flag_declines_name_scorer(monkeypatch):
    # Pack loaded, but the wheel lacks FS_SUPPORTS_NAME_SCORERS -> numpy path.
    _patch(monkeypatch, kernel_name_scorers=False, pack_available=True)
    assert _fs_native_eligible(_mk_name()) is False


def test_new_wheel_with_flag_and_pack_accepts_name_scorer(monkeypatch):
    _patch(monkeypatch, kernel_name_scorers=True, pack_available=True)
    assert _fs_native_eligible(_mk_name("name_freq_weighted_jw")) is True
    assert _fs_native_eligible(_mk_name("given_name_aliased_jw", field="first_name")) is True


def test_missing_pack_declines_even_with_flag(monkeypatch):
    # The kernel would degrade to plain JW (no table) -> diverges from numpy's own
    # is_available gate, so we decline to keep the two paths identical.
    _patch(monkeypatch, kernel_name_scorers=True, pack_available=False)
    assert _fs_native_eligible(_mk_name()) is False


def test_name_scorer_not_a_valid_negative_evidence_scorer():
    # NE never uses a reference-data name scorer: the config layer rejects it
    # outright (it's not in NegativeEvidenceField's allowed scorer list), so the
    # kernel NE path (score_one 0..=3) is never asked to run one. The defensive
    # `_fs_native_eligible` NE gate is belt-and-suspenders for this invariant.
    import pytest
    from goldenmatch.config.schemas import NegativeEvidenceField

    for scorer in _NAME_SCORER_IDS:
        with pytest.raises(Exception):
            NegativeEvidenceField(field="x", scorer=scorer, threshold=0.9, penalty_bits=10.0)


def test_refdata_export_seam_non_empty():
    # The seam that feeds set_name_reference_data. Real refdata packs are bundled.
    from goldenmatch.refdata.given_names import export_alias_forms
    from goldenmatch.refdata.surnames import export_counts

    counts = export_counts()
    forms = export_alias_forms()
    assert len(counts) > 1000  # ~10k census surnames
    assert all(isinstance(n, str) and isinstance(c, int) for n, c in counts[:5])
    assert len(forms) > 100
    assert all(isinstance(f, str) and isinstance(cs, list) for f, cs in forms[:5])
