"""Count candidate pairs when it is FREE, instead of skipping on block count.

`score_blocks_parallel` skips its candidate-count loop above
`GOLDENMATCH_CANDIDATE_COUNT_MAX_BLOCKS` blocks (default 10,000), because
`Block.n_rows()` calls `materialize()` and doing that serially for tens of
thousands of blocks is real time. That gate is right for LAZY blocks. It is
pure loss for the rest:

  * `RowIdBlock.n_rows()` is `len(self._ids)` -- an array length;
  * `Block.materialize()` RETURNS IMMEDIATELY when `df` is already a `Frame`,
    and only `.collect()`s a polars LazyFrame.

So above the gate the profile reported `candidates_compared=0` /
`candidates_counted=False` even when the number was one attribute read away.
That is what made #2639's two consumers unusable at scale and forced the
diagnostic in #2646 to buy the count back with a deliberate serial pass.

The rule here is "count when free, never pay to count": if every block can be
measured without materialising, the total is real and `candidates_counted` is
True regardless of how many blocks there are. If ANY block would have to
collect, behaviour falls back to exactly the existing gate -- so the lazy path
this protects is untouched.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.blocker import BlockResult as Block
from goldenmatch.core.matchkey import _xform_sig
from goldenmatch.core.profile_emitter import profile_capture
from goldenmatch.core.scorer import cheap_n_rows, score_blocks_parallel


def _mk() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="t", type="weighted", threshold=0.7,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
    )


def _block(key: str, names: list[str], start_id: int) -> Block:
    col = _xform_sig(MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0))
    return Block(block_key=key, df=pl.DataFrame({
        "__row_id__": list(range(start_id, start_id + len(names))),
        "name": names,
        col: names,
    }))


def test_cheap_n_rows_reads_an_eager_block_for_free():
    assert cheap_n_rows(_block("a", ["alice", "alica"], 0)) == 2


def test_cheap_n_rows_declines_a_lazy_block():
    """A LazyFrame would have to `.collect()`. Declining is the whole point:
    this must never be the thing that makes scoring slower."""
    lazy = Block(block_key="a", df=pl.DataFrame({"__row_id__": [0, 1]}).lazy())
    assert cheap_n_rows(lazy) is None


def test_cheap_n_rows_reads_a_row_id_block_for_free():
    """`RowIdBlock` carries an int array and no frame at all."""
    from goldenmatch.core.blocker import RowIdBlock

    blk = RowIdBlock(block_key="a", ids=[1, 2, 3], blocking_fields=("name",))
    assert cheap_n_rows(blk) == 3


def test_count_survives_the_gate_when_every_block_is_free(monkeypatch):
    """The live gap. With the gate set to 1, two blocks are 'too many' and the
    old code reported nothing -- yet both counts are attribute reads."""
    monkeypatch.setenv("GOLDENMATCH_CANDIDATE_COUNT_MAX_BLOCKS", "1")
    blocks = [
        _block("a", ["alice", "alica", "alise"], 0),
        _block("b", ["robert", "robbert", "roberto"], 100),
        _block("c", ["carol", "caroll", "carola"], 200),
    ]
    with profile_capture() as emitter:
        score_blocks_parallel(blocks, _mk(), set(), max_workers=2)

    sp = emitter.scoring
    assert sp.candidates_counted is True, "both blocks were free to measure"
    assert sp.route == "scorer.parallel"
    # 3 rows per block -> 3 within-block pairs each.
    assert sp.candidates_compared == 9


def test_gate_still_applies_when_a_block_would_collect(monkeypatch):
    """The protection this must not remove: one lazy block among many, past the
    gate, and the count is skipped exactly as before rather than paying for a
    collect nobody asked for."""
    monkeypatch.setenv("GOLDENMATCH_CANDIDATE_COUNT_MAX_BLOCKS", "1")
    col = _xform_sig(MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0))
    lazy = Block(block_key="lazy", df=pl.DataFrame({
        "__row_id__": [200, 201], "name": ["zed", "zedd"], col: ["zed", "zedd"],
    }).lazy())
    # THREE blocks, not two: `score_blocks_parallel` short-circuits to a
    # sequential path at `len(blocks) <= 2` which counts unconditionally, so a
    # two-block fixture never reaches the gate this test is about. The route
    # field caught that -- the first version came back `scorer.small`.
    blocks = [_block("a", ["alice", "alica"], 0),
              _block("b", ["bob", "bobb"], 50), lazy]

    with profile_capture() as emitter:
        score_blocks_parallel(blocks, _mk(), set(), max_workers=2)

    assert emitter.scoring.route == "scorer.parallel"
    assert emitter.scoring.candidates_counted is False
    assert emitter.scoring.candidates_compared == 0


def test_counting_does_not_change_the_pairs(monkeypatch):
    """Counting is measurement. It must not move a pair."""
    blocks = lambda: [_block("a", ["alice", "alica", "alise"], 0)]  # noqa: E731
    monkeypatch.setenv("GOLDENMATCH_CANDIDATE_COUNT_MAX_BLOCKS", "0")
    off = score_blocks_parallel(blocks(), _mk(), set(), max_workers=2)
    monkeypatch.setenv("GOLDENMATCH_CANDIDATE_COUNT_MAX_BLOCKS", "1000000")
    on = score_blocks_parallel(blocks(), _mk(), set(), max_workers=2)

    key = lambda ps: sorted((min(a, b), max(a, b), round(s, 6)) for a, b, s in ps)  # noqa: E731
    assert key(off) == key(on)


@pytest.mark.parametrize("n_blocks", [3, 5, 8])
def test_counted_total_matches_the_closed_form(monkeypatch, n_blocks):
    """`candidates_compared` must be the real within-block pair total, not an
    approximation: 3 rows per block is 3 pairs, so n blocks is 3n."""
    monkeypatch.setenv("GOLDENMATCH_CANDIDATE_COUNT_MAX_BLOCKS", "1")
    blocks = [
        _block(f"k{i}", ["alice", "alica", "alise"], i * 100)
        for i in range(n_blocks)
    ]
    with profile_capture() as emitter:
        score_blocks_parallel(blocks, _mk(), set(), max_workers=2)

    assert emitter.scoring.candidates_compared == 3 * n_blocks
