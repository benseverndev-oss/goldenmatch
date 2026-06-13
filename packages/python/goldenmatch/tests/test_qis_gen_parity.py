"""Bit-identity regression for the vectorized QIS realistic generator.

The generator was rewritten from per-row Python loops to numpy/`np.char` for a
10-50x speed-up at scale (the single-threaded per-row expansion was the QIS gen
wall). These pinned SHA-256 hashes were captured from the PRE-vectorization
implementation; they lock the new code to byte-for-byte identical output so the
distributed scale rungs stay directly comparable to the single-box ladder
(a different fixture would make a cross-engine F1 delta un-attributable).

Lightweight by design (numpy only, no Ray, no dedupe) so it runs in the default
`python` lane -- unlike test_qis_harness.py, which CI --ignores.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import quality_invariant_scale as qis  # noqa: E402

# (n_rows, corruption) -> sha256(df.write_csv()) from the pre-vectorization gen.
_DF_GOLDEN = {
    (1000, "light"): "d7fe081918a6e4e92ba80c3dd9c009c05b11c7b2bf0b02f9852e037aefdc4ac2",
    (1000, "moderate"): "459a60e0b2a88587ecb28d66bc0f604cf3fa2fc4e8151e1dcf59fd99a4f6ae0d",
    (10000, "moderate"): "3f4db4a51db60c3012fc23a0a1f42d92fe3b2e62c4d9fd1c95544e69b76c41ca",
    (25005, "hard"): "4feebc19a6ea7b8b37e9fbf704c8f82a5db59f4fd053963c65f57c20991785c5",
}
# n_rows -> sha256(cids.tobytes()); corruption never changes the oracle.
_CIDS_GOLDEN = {
    1000: "ad655b1bba366a78851009bb16f2beea972de2db62305dee1a7cfe05c9fb514f",
    10000: "539ee417bee7aa511ff6b65409b2437b83fd2af3f2a63c6333590cf7400a0361",
    25005: "734061231c0a37a48e306aed478c2c6d8002ca7bd5ae01ef1f987763162959a7",
}


@pytest.mark.parametrize(("n_rows", "corruption"), sorted(_DF_GOLDEN))
def test_vectorized_realistic_gen_is_bit_identical(n_rows: int, corruption: str):
    df, cids = qis.generate_with_gt(n_rows, seed=0, shape="realistic",
                                    corruption=corruption)
    df_hash = hashlib.sha256(df.write_csv().encode()).hexdigest()
    cids_hash = hashlib.sha256(cids.tobytes()).hexdigest()
    assert df_hash == _DF_GOLDEN[(n_rows, corruption)], (
        f"vectorized realistic gen changed the {n_rows}-row {corruption} fixture; "
        f"this breaks cross-engine/cross-rung comparability."
    )
    assert cids_hash == _CIDS_GOLDEN[n_rows], (
        f"ground-truth cluster ids changed for {n_rows} rows; the oracle must be "
        f"corruption-invariant and stable across the gen rewrite."
    )


def test_embedded_frozen_config_matches_file():
    """The frozen config is embedded in the script (so it travels under
    `ray submit`, which ships only the .py). The committed JSON is the source of
    truth; this guards the embedded copy from silently drifting from it."""
    from goldenmatch.config.schemas import GoldenMatchConfig

    from_file = GoldenMatchConfig.model_validate_json(
        qis._FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    from_embed = GoldenMatchConfig.model_validate_json(qis._FROZEN_CONFIG_JSON)
    assert from_file.model_dump() == from_embed.model_dump(), (
        "_FROZEN_CONFIG_JSON drifted from qis_realistic_frozen_config.json; "
        "re-run the embed (json.dumps(json.load(open(file)), separators=(',',':')))."
    )
