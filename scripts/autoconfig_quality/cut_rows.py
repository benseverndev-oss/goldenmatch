"""Candidate link-cut routing rows, NOT shipped (spec 2026-09-11-fs-cut-rule-routing-design, P4).

Written from the DESIGN matrix only (run 34638483397), before any held-out result was read.
``cut_gate`` gates each one on the design AND held-out matrix:
- a row that passes both moves VERBATIM into ``goldenmatch.core.fs_cut_rules.ROWS``;
- a row that fails is deleted here, never tuned against the held-out result.

The ``n_fields`` bounds were chosen to exclude synth_person_d02 (A') and dblp_scholar (B) on
the design set, so only the held-out set tests them.
"""

from __future__ import annotations

from goldenmatch.core.fs_cut_rules import CutRow

CANDIDATES: tuple[CutRow, ...] = (
    # A'. Design: dblp_acm +0.0918, synth_biblio_d02 +0.0000.
    CutRow(
        name="sparse_prior_binds",
        rule="posterior_099",
        reason="match rate under 0.004, the prior cutoff above the midpoint, at most 3 fields",
        when=lambda d: (
            d.proportion_matched < 0.004 and d.prior_bits > d.midpoint_bits and d.n_fields <= 3
        ),
    ),
    # B. Design: musicbrainz_20k +0.2673, febrl3 +0.004, febrl4 +0.0002.
    CutRow(
        name="midpoint_binds_wide",
        rule="evidence_5",
        reason="match rate under 0.1, the midpoint cutoff above the prior, at least 5 fields",
        when=lambda d: (
            d.proportion_matched < 0.1 and d.midpoint_bits > d.prior_bits and d.n_fields >= 5
        ),
    ),
)
