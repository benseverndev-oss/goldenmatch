"""Issue #1837 — the learned-blocking oversized-DROP cap must never sit below
the budget auto-config used to SELECT the blocking key.

Learned blocking runs with ``skip_oversized=True``, and ``apply_learned_blocks``
drops any block larger than ``max_block_size`` outright. If that cap is below
``_compute_max_safe_block`` (the budget the key was chosen against), the selector
accepts a key whose blocks the runtime then silently discards — a RECALL-only
loss (precision stays 1.0, so nothing else flags it).

It surfaces as a SCALE-INVARIANCE regression: a key under the cap at 500K crosses
it at 1M and gets discarded. Measured on the zero-config quality gate: 1M pairwise
recall 0.82 / precision 1.0 / fp=0 while 50K-500K sat at ~1.0.
"""

from __future__ import annotations

from goldenmatch.core.autoconfig import _compute_max_safe_block, _learned_block_cap


class TestLearnedBlockCap:
    def test_cap_never_below_selection_budget(self):
        """The core invariant: whatever key-selection deemed safe must survive
        the runtime drop guard."""
        for n in (50_000, 100_000, 200_000, 500_000, 1_000_000, 5_000_000):
            for native in (True, False):
                cap = _learned_block_cap(n, 5000, native)
                assert cap >= _compute_max_safe_block(n, native), (n, native, cap)

    def test_raises_cap_at_1m_native(self):
        """The #1837 case: native lifts max_safe_block to 25K on a 1M frame; the
        default 5000 cap would drop those blocks (recall 0.82)."""
        assert _compute_max_safe_block(1_000_000, True) == 25_000
        assert _learned_block_cap(1_000_000, 5000, True) == 25_000

    def test_raise_only_leaves_small_data_untouched(self):
        """Below ~200K, height//40 < 5000 -- the cap must NOT tighten (that would
        drop blocks that are kept today and regress recall the other way)."""
        assert _compute_max_safe_block(100_000, True) == 2500  # < 5000
        assert _learned_block_cap(100_000, 5000, True) == 5000  # unchanged
        assert _learned_block_cap(50_000, 5000, True) == 5000

    def test_never_lowers_an_explicit_cap(self):
        """A caller-configured cap above the budget is preserved."""
        assert _learned_block_cap(1_000_000, 40_000, True) == 40_000
