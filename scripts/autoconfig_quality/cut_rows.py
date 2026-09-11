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

CANDIDATES: tuple[CutRow, ...] = ()
