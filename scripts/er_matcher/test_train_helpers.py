"""Tests for the ER-matcher trainer's PURE helpers (no torch / no GPU).

Locks config load+validate, the measured max_seq_len policy, and the chat-target
construction (which must round-trip through the shared parse_verdict -- training
and serving share ONE contract). The heavy training loop (_train_runtime.py) is
GPU-only and out of scope here; these are the decisions it depends on."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import train as tr  # noqa: E402,I001
from goldenmatch.core.er_matcher.prompt import SERIALIZER_VERSION, parse_verdict  # noqa: E402


def test_load_config_roundtrips_committed_yaml():
    cfg = tr.load_config(Path(__file__).with_name("config.yaml"))
    assert cfg.base_model == "Qwen/Qwen2.5-3B-Instruct"
    assert cfg.packing is True
    assert cfg.serializer_version == SERIALIZER_VERSION


def test_load_config_rejects_unknown_key(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("base_model: x\nbogus_key: 1\n")
    with pytest.raises(ValueError, match="unknown config keys"):
        tr.load_config(p)


def test_config_validate_catches_bad_values():
    with pytest.raises(ValueError, match="seq_len_percentile"):
        tr.TrainConfig(seq_len_percentile=0).validate()
    with pytest.raises(ValueError, match="serializer_version"):
        tr.TrainConfig(serializer_version="v0").validate()
    with pytest.raises(ValueError, match="match_confidence"):
        tr.TrainConfig(match_confidence=0.2).validate()
    with pytest.raises(ValueError, match="nomatch_confidence"):
        tr.TrainConfig(nomatch_confidence=0.9).validate()


def test_measured_max_seq_len_p95_rounded_capped():
    # 100 lengths 1..100; P95 nearest-rank = 95 -> round up to mult 64 -> 128
    lengths = list(range(1, 101))
    assert tr.measured_max_seq_len(lengths, percentile=95.0, cap=1024, multiple_of=64) == 128
    # cap bites: huge lengths -> capped at 256
    assert tr.measured_max_seq_len([5000] * 10, percentile=95.0, cap=256, multiple_of=64) == 256
    # empty -> floor at multiple_of
    assert tr.measured_max_seq_len([], percentile=95.0, cap=1024, multiple_of=64) == 64
    # small lengths floor at multiple_of
    assert tr.measured_max_seq_len([3, 4, 5], percentile=95.0, cap=1024, multiple_of=64) == 64


def test_estimate_total_steps_concrete_arithmetic():
    # 10 rows of 100 tokens each = 1000 tokens; seq_len=200 -> 5 packed seqs
    # (1000/200); batch*accum=2 -> ceil(5/2)=3 steps/epoch; epochs=2 -> 6.
    lengths = [100] * 10
    assert tr.estimate_total_steps(
        lengths, seq_len=200, per_device_batch=1, grad_accum=2, epochs=2.0,
    ) == 6
    # non-integer packed/step-per-epoch results round UP (ceil), not down.
    # 999 tokens / 200 -> ceil(4.995) = 5 packed seqs; /2 -> ceil(2.5) = 3;
    # *1 epoch -> 3.
    assert tr.estimate_total_steps(
        [999], seq_len=200, per_device_batch=1, grad_accum=2, epochs=1.0,
    ) == 3


def test_estimate_total_steps_floors_at_one():
    # empty token_lengths -> sum=0 -> packed floors at 1 -> steps_per_epoch
    # floors at 1 -> epochs=1.0 -> 1 (never claims zero steps).
    assert tr.estimate_total_steps(
        [], seq_len=200, per_device_batch=16, grad_accum=2, epochs=1.0,
    ) == 1
    # a single tiny row still yields >=1 packed sequence and >=1 step/epoch.
    assert tr.estimate_total_steps(
        [1], seq_len=200, per_device_batch=16, grad_accum=2, epochs=3.0,
    ) == 3


def _row(match: bool) -> dict:
    return {
        "a": {"name": "Acme Corp", "city": "Boston", "id": "A1"},
        "b": ({"name": "Acme Corp", "city": "Boston", "id": "A1"} if match
              else {"name": "Beta LLC", "city": "Boston", "id": "B9"}),
        "label": "match" if match else "no_match",
        "domain": "org",
    }


def _assistant(msgs: list[dict]) -> str:
    return next(m["content"] for m in msgs if m["role"] == "assistant")


def test_row_confidence_overrides_constant():
    cfg = tr.TrainConfig()
    row = _row(match=True)
    row["confidence"] = 0.62
    msgs = tr.example_to_messages(row, cfg)
    assert '"confidence":0.62' in _assistant(msgs)


def test_missing_confidence_falls_back_to_constant():
    cfg = tr.TrainConfig()
    row = _row(match=True)
    row.pop("confidence", None)
    msgs = tr.example_to_messages(row, cfg)
    assert '"confidence":0.9' in _assistant(msgs)  # DEFAULT_MATCH_CONF


def test_example_to_messages_shape_and_roundtrip():
    cfg = tr.TrainConfig()
    for match in (True, False):
        msgs = tr.example_to_messages(_row(match), cfg)
        assert [m["role"] for m in msgs] == ["system", "user", "assistant"]
        # the assistant TARGET must parse back via the SHARED runtime parser to
        # the same verdict -- training target == serving contract.
        v = parse_verdict(msgs[-1]["content"])
        assert v is not None
        assert v["match"] is match
        expect_conf = cfg.match_confidence if match else cfg.nomatch_confidence
        assert v["confidence"] == pytest.approx(expect_conf)
        assert v["reason"]  # non-empty deterministic reason


def test_auto_reason_lists_agreements_and_conflicts():
    cfg = tr.TrainConfig()
    match_reason = parse_verdict(tr.example_to_messages(_row(True), cfg)[-1]["content"])["reason"]
    assert "agree on" in match_reason
    nomatch_reason = parse_verdict(tr.example_to_messages(_row(False), cfg)[-1]["content"])["reason"]
    assert "conflict on" in nomatch_reason or "insufficient" in nomatch_reason


def test_serialized_token_lengths_uses_injected_tokenizer():
    rows = [_row(True), _row(False)]
    # stub tokenize_messages: chat-template stand-in = whitespace-split the joined
    # message contents (mirrors apply_chat_template(msgs, tokenize=True)'s shape:
    # messages in, token sequence out). Lengths are positive + deterministic.
    stub = lambda msgs: " ".join(m["content"] for m in msgs).split()  # noqa: E731
    lens = tr.serialized_token_lengths(rows, stub)
    assert len(lens) == 2 and all(n > 0 for n in lens)


def test_read_jsonl(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text('{"a":1}\n\n{"b":2}\n')
    assert tr.read_jsonl(p) == [{"a": 1}, {"b": 2}]
